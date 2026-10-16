"""Paso 1 del Docs Agent: la clave de un documento y la regla de 'gana la primera copia'.

    from lector_ocr.recepcion import clave_del_documento, RegistroDeClaves

    clave = clave_del_documento(datos, "pdf", asunto=asunto, nombre_archivo=nombre)
    clave.clave        # 'nyed:1-26-cv-04354:29'  o  'nyscef:kings:507148-2026:12'
    clave.huella       # 'sha256:...' del contenido, siempre

    registro = RegistroDeClaves("salida/claves.jsonl")
    nuevo, primera = registro.anotar(clave, origen="DocketBird email")
    if not nuevo:
        ...   # se vincula a `primera` y NO se archiva otra vez

POR QUE UNA CLAVE Y NO SOLO EL HASH. El mismo escrito llega por PACER y por DocketBird,
o a dos buzones, y no siempre con los mismos bytes: uno trae el sello ECF y otro no, o
el correo lo recomprime. Lo que no cambia es QUE documento es: tribunal, caso y numero
de documento en el expediente judicial. Por eso la spec pide

    federal   {tribunal}:{caso}:{numero}            'nyed:1-26-cv-04354:29'
    estatal   nyscef:{condado}:{index}:{numero}     'nyscef:kings:507148-2026:12'

El hash del contenido se calcula igual y se usa de respaldo: dos archivos con los
mismos bytes son el mismo documento aunque no se les haya podido sacar la clave.

TODO SALE SIN OCR. El paso 1 va antes que el OCR, asi que solo usa lo barato:
    el asunto del aviso      'New filing in ... (nyed-1:2026-cv-04354)'
    el nombre del archivo    'task-4 · 850209-2016 · Doc 279 · REFEREE REPORT.pdf'
    la capa de texto de la primera pagina, donde NYSCEF y ECF estampan su sello:
        FILED: KINGS COUNTY CLERK 09/24/2026 10:58 AM   INDEX NO. 850209/2016
        NYSCEF DOC. NO. 279                       RECEIVED NYSCEF: 09/24/2026
        Case 1:18-cv-04039-ENV-RML Document 23-1 Filed 12/21/18 Page 1 of 14
Un escaneo sin sello no da clave aqui; se queda con la huella y no pasa nada.

SIN TODAS LAS PIEZAS NO HAY CLAVE. Un numero de caso federal sin tribunal se repite
entre distritos ('1:25-cv-06123' existe en Brooklyn y en Manhattan), y un index sin
numero de documento es el expediente entero, no un escrito. Una clave incompleta
haria pasar por duplicados documentos distintos, que es peor que no detectar uno:
el duplicado no detectado se ve en la carpeta; el documento descartado no se ve nunca.

EL SELLO MANDA SOBRE EL NOMBRE. El nombre del archivo lo escribe una persona o un
script; el sello lo pone el juzgado. Si discrepan, gana el sello y la discrepancia
queda anotada en `avisos`.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .expediente import _normalizar_federal, indices_en

# --------------------------------------------------------------------------- #
# De donde sale cada pieza

# Asunto de DocketBird: el codigo del tribunal va pegado al numero de caso.
#   'New filing in Brewster v. Ava Finance, Inc. et al: REPLY in Support re [28] (nyed-1:2026-cv-04354)'
# '[28]' es el documento al que RESPONDE, no el suyo: no se usa como numero.
TRIBUNAL_Y_CASO = re.compile(
    r"\b([a-z]{2,4}[a-z]?\d?)-(\d)\s*:\s*(\d{4}|\d{2})\s*-\s*(cv|bk|mc|cr|ap)\s*-\s*(\d{3,6})(?!\d)",
    re.I)

# Sello ECF: 'Case 1:18-cv-04039-ENV-RML Document 23-1 Filed 12/21/18 Page 1 of 14'.
# '23-1' es el anexo 1 del documento 23: es otro archivo y lleva su propia clave.
SELLO_ECF = re.compile(
    r"\bCase\s+(\d)\s*:\s*(\d{2}|\d{4})-(cv|bk|mc|cr|ap)-(\d{3,6})\b.*?\bDocument\s+(\d+(?:-\d+)?)\b",
    re.I)

# Sello NYSCEF, las tres piezas por separado: a veces vienen en lineas sueltas o
# reordenadas, igual que en ocr.SELLOS.
SELLO_CONDADO = re.compile(r"\bFILED:\s*([A-Z][A-Z .'-]*?)\s+COUNTY CLERK\b", re.I)
SELLO_APELACION = re.compile(r"\bFILED:\s*(APPELLATE DIVISION\s*-\s*\w+\s+DEPT)\b", re.I)
SELLO_NYSCEF_DOC = re.compile(r"\bNYSCEF DOC\.?\s*NO\.?\s*(\d+)\b", re.I)
SELLO_INDICE = re.compile(r"\bINDEX NO\.?\s*:?\s*([1-9]\d{3,6}\s*[-/]\s*\d{2,4})\b", re.I)

# Nombre del archivo de la bandeja: 'task-4 · 850209-2016 · Doc 279 · REFEREE REPORT.pdf',
# 'task-379 - 709633-2025 - Doc 19 - ORDER.pdf'.
DOC_EN_NOMBRE = re.compile(r"\bDoc(?:ument)?\s*(?:No\.?|#)?\s*(\d+(?:-\d+)?)\b", re.I)

# La primera pagina basta: el sello va en todas y la caratula esta en la primera.
PAGINAS_DEL_SELLO = 1


def _slug(texto: str) -> str:
    """'NEW YORK' -> 'new-york'. Para que la clave no dependa de mayusculas ni espacios."""
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")


def capa_de_texto(datos: bytes, extension: str, paginas: int = PAGINAS_DEL_SELLO) -> str:
    """El texto que el PDF ya trae, sin OCR. Vacio si no es PDF, si esta cerrado o si
    no tiene capa de texto: en esos casos la clave sale del asunto y del nombre."""
    # Sin cabecera %PDF no se le pasa a pypdf: solo serviria para llenar el log.
    if extension.lower().lstrip(".") != "pdf" or b"%PDF" not in (datos or b"")[:1024]:
        return ""
    try:
        from pypdf import PdfReader

        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted:
            return ""
        return "\n".join((pagina.extract_text() or "")
                         for pagina in lector.pages[:paginas])
    except Exception:
        # Un PDF roto no impide recibirlo: se queda con la huella y el resto del
        # flujo dira por que no se pudo leer.
        return ""


# --------------------------------------------------------------------------- #
# La clave


@dataclass
class Clave:
    """Lo que identifica a un documento al recibirlo."""

    huella: str                      # 'sha256:...' de los bytes; siempre
    clave: str = ""                  # 'nyed:1-26-cv-04354:29'; vacia si falta alguna pieza
    tipo: str = ""                   # 'federal' | 'nyscef' | ''
    tribunal: str = ""               # 'nyed' | 'kings' | 'appellate-division-2nd-dept'
    caso: str = ""                   # '1-26-cv-04354' | '507148-2026'
    numero: str = ""                 # '29' | '23-1'
    se_decidio: dict[str, str] = field(default_factory=dict)  # pieza -> de donde salio
    falta: list[str] = field(default_factory=list)            # piezas que no aparecieron
    avisos: list[str] = field(default_factory=list)           # discrepancias entre fuentes

    @property
    def llaves(self) -> list[str]:
        """Todo lo que la identifica: la clave si la hay, y la huella siempre."""
        return [k for k in (self.clave, self.huella) if k]


def _primera(*candidatos: tuple[str, str]) -> tuple[str, str, list[tuple[str, str]]]:
    """De (valor, fuente) en orden de prioridad: el primero con valor, su fuente y
    los demas que tambien traian valor (para detectar discrepancias)."""
    con_valor = [(v, f) for v, f in candidatos if v]
    if not con_valor:
        return "", "", []
    return con_valor[0][0], con_valor[0][1], con_valor[1:]


def clave_del_documento(datos: bytes, extension: str = "pdf", *, asunto: str = "",
                        nombre_archivo: str = "", texto: str | None = None) -> Clave:
    """La clave del documento a partir del aviso, el nombre y el sello.

    `texto` es la capa de texto si quien llama ya la tiene; si no se pasa, se lee la
    de la primera pagina (sin OCR).
    """
    resultado = Clave(huella="sha256:" + hashlib.sha256(datos or b"").hexdigest())
    if texto is None:
        texto = capa_de_texto(datos, extension)
    texto = texto or ""
    asunto = asunto or ""
    nombre_archivo = nombre_archivo or ""

    doc_nombre = DOC_EN_NOMBRE.search(nombre_archivo)
    doc_nombre = doc_nombre.group(1) if doc_nombre else ""

    # ---- federal: el tribunal solo lo da el asunto de DocketBird ----------------
    asunto_fed = TRIBUNAL_Y_CASO.search(asunto)
    ecf = SELLO_ECF.search(texto)
    if asunto_fed or ecf:
        resultado.tipo = "federal"
        caso_asunto = _normalizar_federal(*asunto_fed.groups()[1:]) if asunto_fed else ""
        caso_ecf = _normalizar_federal(*ecf.groups()[:4]) if ecf else ""
        caso, fuente, otros = _primera((caso_ecf, "el sello ECF"), (caso_asunto, "el asunto"))
        resultado.caso = caso
        if fuente:
            resultado.se_decidio["caso"] = fuente
        for valor, otra in otros:
            if valor != caso:
                resultado.avisos.append(f"caso: {fuente} dice {caso}, {otra} dice {valor}")
        if asunto_fed:
            resultado.tribunal = asunto_fed.group(1).lower()
            resultado.se_decidio["tribunal"] = "el asunto"
        numero, fuente, otros = _primera((ecf.group(5) if ecf else "", "el sello ECF"),
                                         (doc_nombre, "el nombre del archivo"))
        resultado.numero = numero
        if fuente:
            resultado.se_decidio["numero"] = fuente
        for valor, otra in otros:
            if valor != numero:
                resultado.avisos.append(f"numero: {fuente} dice {numero}, {otra} dice {valor}")
        resultado.falta = [p for p in ("tribunal", "caso", "numero") if not getattr(resultado, p)]
        if not resultado.falta:
            resultado.clave = f"{resultado.tribunal}:{resultado.caso}:{resultado.numero}"
        return resultado

    # ---- estatal (NYSCEF) -------------------------------------------------------
    condado = SELLO_CONDADO.search(texto) or SELLO_APELACION.search(texto)
    doc_sello = SELLO_NYSCEF_DOC.search(texto)
    indice_sello = SELLO_INDICE.search(texto)
    indices_nombre = indices_en(nombre_archivo)
    if not (condado or doc_sello or indice_sello or indices_nombre or doc_nombre):
        resultado.falta = ["tribunal", "caso", "numero"]
        return resultado

    resultado.tipo = "nyscef"
    if condado:
        resultado.tribunal = _slug(condado.group(1))
        resultado.se_decidio["tribunal"] = "el sello NYSCEF"

    indice_sello = indices_en(indice_sello.group(1).replace(" ", "")) if indice_sello else []
    caso, fuente, otros = _primera((indice_sello[0] if indice_sello else "", "el sello NYSCEF"),
                                   (indices_nombre[0] if indices_nombre else "", "el nombre del archivo"))
    resultado.caso = caso
    if fuente:
        resultado.se_decidio["caso"] = fuente
    for valor, otra in otros:
        if valor != caso:
            resultado.avisos.append(f"caso: {fuente} dice {caso}, {otra} dice {valor}")

    numero, fuente, otros = _primera((doc_sello.group(1) if doc_sello else "", "el sello NYSCEF"),
                                     (doc_nombre, "el nombre del archivo"))
    resultado.numero = numero
    if fuente:
        resultado.se_decidio["numero"] = fuente
    for valor, otra in otros:
        if valor != numero:
            resultado.avisos.append(f"numero: {fuente} dice {numero}, {otra} dice {valor}")

    resultado.falta = [p for p in ("tribunal", "caso", "numero") if not getattr(resultado, p)]
    if not resultado.falta:
        resultado.clave = f"nyscef:{resultado.tribunal}:{resultado.caso}:{resultado.numero}"
    return resultado


# --------------------------------------------------------------------------- #
# Gana la primera copia


class RegistroDeClaves:
    """Lo ya recibido, en un JSONL que solo crece: una linea por documento NUEVO.

    `anotar` responde si el documento es nuevo y, si no lo es, devuelve la linea de
    la primera copia para vincularlo a ella. Un duplicado no se anota: la primera
    copia es la unica que existe para el registro.

    Se busca por clave Y por huella: lo que coincida en cualquiera de las dos es el
    mismo documento.

    Un proceso, un registro. El candado protege a los hilos de un mismo proceso;
    varios procesos escribiendo el mismo fichero se pisarian. Un servicio con varias
    instancias necesita un almacen con escritura condicional (una tabla con la llave
    como clave primaria) que implemente los mismos `buscar` y `anotar`.
    """

    def __init__(self, ruta: str | Path) -> None:
        self.ruta = Path(ruta)
        self._candado = threading.Lock()
        self._por_llave: dict[str, dict[str, Any]] = {}
        if self.ruta.exists():
            # Por "\n" y no con splitlines(): un texto puede llevar U+2028 o U+0085.
            for linea in self.ruta.read_text(encoding="utf-8").split("\n"):
                if linea.strip():
                    self._indexar(json.loads(linea))

    def _indexar(self, registro: dict[str, Any]) -> None:
        for llave in (registro.get("clave"), registro.get("huella")):
            if llave:
                self._por_llave.setdefault(llave, registro)

    def buscar(self, clave: Clave) -> dict[str, Any] | None:
        """La primera copia de este documento, o None si nunca llego."""
        for llave in clave.llaves:
            if llave in self._por_llave:
                return self._por_llave[llave]
        return None

    def anotar(self, clave: Clave, **datos: Any) -> tuple[bool, dict[str, Any]]:
        """(True, su linea) si es nuevo y queda anotado; (False, la primera copia) si no.

        `datos` es lo que quien llama quiera guardar para vincular despues: el
        origen, el nombre, el id del correo o del archivo en SharePoint.
        """
        with self._candado:
            primera = self.buscar(clave)
            if primera is not None:
                return False, primera
            registro = {
                "clave": clave.clave,
                "huella": clave.huella,
                "recibido": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **datos,
            }
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            with self.ruta.open("a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            self._indexar(registro)
            return True, registro


def como_dict(clave: Clave) -> dict[str, Any]:
    """Para meterla en una ficha o en el mensaje al Analyst."""
    return asdict(clave)
