"""De un documento suelto a la carpeta de su caso: por el asunto, el nombre y lo que dice.

    from lector_ocr.expediente import identificar

    caso = identificar(texto, carpetas, asunto="New filing: nyed-1:25-cv-06123",
                       nombre_archivo="task-379 - 709633-2025 - Doc 19 - ORDER.pdf")
    caso.carpeta       # 'Cato, Lamar - 100002 - 709633-2025'
    caso.regla         # 'Index en el asunto o el nombre del archivo'
    caso.se_decidio    # 'el nombre del archivo'

`carpetas` son las carpetas de caso tal y como las da SharePoint: nombres sueltos, o
diccionarios con `name`, `id` y `createdDateTime` (lo que devuelve Graph al listar).
La fecha de creacion hace falta para la regla de 'la mas reciente'. Este modulo no
habla con SharePoint: quien llama ya tiene esa lista y la pasa.

QUE SE PRUEBA Y EN QUE ORDEN NO ESTA AQUI: esta en reglas_ruteo.toml (ver reglas.py).
Por defecto, las reglas de la firma:

    1. El Index Number en el asunto o en el nombre del archivo; si no, en el documento.
    2. Sin Index Number, el nombre del cliente.
    3. Si el cliente tiene varios casos, la carpeta sin Index Number y, si no hay, la
       creada mas recientemente.

Aqui vive lo que esas reglas necesitan: reconocer un Index en todas sus formas y
descomponer el nombre de una carpeta de caso.

EL INDICE identifica el caso, no a la persona: es el unico identificador duro que
comparten el juzgado, HubSpot y la firma. EL NOMBRE del cliente se busca AL REVES de
lo que parece natural: no 'que personas menciona el documento', sino 'el apellido de
ESTA carpeta, aparece dentro?'. Asi no depende de un catalogo de clientes.

UN DOCUMENTO CITA VARIOS INDICES. La caratula lleva el del caso, pero el cuerpo cita
el de la accion previa o el de un caso relacionado. Por eso se toman todos y van
primero el etiquetado ('INDEX NO.') y los de la cabecera.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Sequence

# Cuanto texto es 'la caratula' si las reglas no dicen otra cosa.
CARATULA = 2500

# --------------------------------------------------------------------------- #
# Indices
#
# ESTATAL (Nueva York): secuencia y anio, '709633-2025', '709633/25', '9339-2014'.
#   [1-9]\d{3,6}  sin cero a la izquierda: descarta exhibits ('0001-001')
#   [-/_]         separador pegado: descarta cuenta + rango de fechas ('7000- 08.20.24')
#   \d{4}|\d{2}   el anio es de 4 o 2 digitos, nunca de 3
#   (?!\.\d)      descarta fechas encadenadas ('...-01.10.25')
#   \d{2}(?!,\d)  '5001-10,000' es un rango con separador de miles, no 5001-2010
PATRON_INDICE = re.compile(r"(?<![\w.])([1-9]\d{3,6})[-/_](\d{4}|\d{2}(?!,\d))(?!\d)(?!\.\d)")
# Un ZIP+4 con la sigla del estado tiene la forma de un indice: 'Brooklyn, NY 11228-2016'.
PATRON_ZIP4 = re.compile(r"\b[A-Z]{2}\.?\s+\d{5}-\d{4}\b")
# El que viene ETIQUETADO es el del caso, no una cita: 'INDEX NO. 850209/2016',
# 'Index No.: 709633-2025', 'Index #709633/25'.
PATRON_INDICE_ETIQUETADO = re.compile(
    r"\bINDEX\s*(?:NO\.?|NUMBER|#)\s*[:.]?\s*([1-9]\d{3,6}\s*[-/]\s*\d{2,4})\b", re.I)
# FEDERAL: distrito, anio, tipo y numero. Se escribe de muchas formas y todas son el
# mismo caso: '1:25-cv-06123' (caratula y sello ECF), 'nyed-1:25-cv-06123' (asunto
# de DocketBird), '1-25-cv-06123' y '1_26-cv-06417' (nombres de carpeta). DocketBird
# escribe a veces el anio con cuatro cifras: 'nyed-1:2026-cv-04354'.
PATRON_FEDERAL = re.compile(
    r"(?<![\w])(\d)\s*[:_-]\s*(\d{2}|\d{4})\s*[-_]\s*(cv|bk|mc|cr|ap)\s*[-_]?\s*(\d{3,6})(?!\d)", re.I)


def _normalizar_estatal(secuencia: str, anio: str) -> str | None:
    """'709633/25' -> '709633-2025'. None si en realidad es una fecha."""
    sec, an = int(secuencia), int(anio)
    # Un indice empieza con un consecutivo, no con un anio: '2024-12' es una fecha.
    if 1990 <= sec <= 2035:
        return None
    if len(anio) == 4:
        if not 1990 <= an <= 2035:
            return None
    elif len(anio) == 2:
        an = 2000 + an if an <= 35 else 1900 + an
    else:
        return None
    return f"{sec}-{an}"


def _normalizar_federal(distrito: str, anio: str, tipo: str, numero: str) -> str:
    """Todas las formas a '1-25-cv-06123'. El anio a dos cifras y el numero a cinco:
    '2026' y '26', '6417' y '06417' son el mismo caso."""
    return f"{int(distrito)}-{anio[-2:]}-{tipo.lower()}-{int(numero):05d}"


def indices_en(texto: str) -> list[str]:
    """Los indices de un texto, estatales y federales, ya normalizados, en el orden
    en que aparecen. Vale para un asunto, un nombre de archivo o un documento."""
    texto = str(texto or "")
    hallados: list[tuple[int, str]] = []
    for m in PATRON_FEDERAL.finditer(texto):
        hallados.append((m.start(), _normalizar_federal(*m.groups())))
    # Lo federal fuera antes de buscar lo estatal: un trozo suyo podria parecerlo.
    resto = PATRON_FEDERAL.sub(lambda m: " " * len(m.group(0)), texto)
    resto = PATRON_ZIP4.sub(lambda m: " " * len(m.group(0)), resto)
    for m in PATRON_INDICE.finditer(resto):
        normalizado = _normalizar_estatal(*m.groups())
        if normalizado:
            hallados.append((m.start(), normalizado))
    salida: list[str] = []
    for _, indice in sorted(hallados):
        if indice not in salida:
            salida.append(indice)
    return salida


def indices_del_documento(texto: str, caratula: int = CARATULA) -> list[str]:
    """Los indices de un documento, el del caso primero.

    Orden: los etiquetados ('INDEX NO.'), luego los de la caratula, luego el resto.
    Un escrito cita otros pleitos en el cuerpo; el suyo esta en la cabecera.
    """
    texto = str(texto or "")
    salida: list[str] = []
    for m in PATRON_INDICE_ETIQUETADO.finditer(texto):
        for indice in indices_en(m.group(1).replace(" ", "")):
            if indice not in salida:
                salida.append(indice)
    for indice in indices_en(texto[:caratula]) + indices_en(texto):
        if indice not in salida:
            salida.append(indice)
    return salida


# --------------------------------------------------------------------------- #
# Carpetas
#
# El nombre de una carpeta de caso es 'Apellido, Nombre - ID - Index', con la marca
# de cerrado detras del nombre ('... - Closed - 600926 - 9339-2014') o, en el formato
# viejo, delante ('Closed - ...'). Se entienden los dos. Un caso al que aun no se le
# conoce el Index lleva '0' en ese tramo.
PREFIJO_CERRADO = "closed - "
COLA_CERRADO = " - closed"
SIN_INDICE = {"", "0", "-", "s/n"}

# Palabras que dan nombre a una carpeta sin distinguir a nadie: un documento
# cualquiera las contiene. 'petroff' y 'amshen' son la propia firma, que aparece en
# todos los escritos que redacta.
NO_DISTINGUEN = frozenset(
    """llc inc corp ltd the and new york city county court group llp pllc company
    associates properties realty holdings trust estate management enterprises
    partners development construction ventures capital consulting services
    incorporated corporation limited avenue street road place house
    petroff amshen
    action federal page plaintiff defendant defendants exhibit index supreme
    district eastern southern western northern civil case matter file closed""".split()
)


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", sin_tildes.lower())).strip()


@dataclass(frozen=True)
class Caso:
    """Una carpeta de caso, descompuesta."""

    carpeta: str
    base: str            # 'Cato, Lamar'
    id_interno: str      # '100002'
    indice: str          # '709633-2025' o '1-25-cv-06123', ya normalizado; '' si no tiene
    cerrado: bool
    id: str = ""         # el id de la carpeta en SharePoint, si se paso
    creada: str = ""     # createdDateTime ISO, si se paso: '2026-10-01T14:03:00Z'

    @property
    def apellidos(self) -> frozenset[str]:
        """Los tokens del nombre que pueden ser el apellido del cliente.

        Con coma ('Peters, Lennon') el apellido es lo que va delante, y solo eso: si
        valiera el nombre de pila, 'Oliveira, Maria' casaria con cualquier Maria.
        Sin coma no se sabe cual es cual y valen todos.
        """
        base = self.base.split(",")[0] if "," in self.base else self.base
        return frozenset(t for t in normalizar(re.sub(r"[.\-_&]", " ", base)).split()
                         if len(t) > 2 and t not in NO_DISTINGUEN)

    @property
    def nombre_completo(self) -> frozenset[str]:
        return frozenset(t for t in normalizar(self.base).split()
                         if len(t) > 2 and t not in NO_DISTINGUEN)

    @property
    def tiene_indice(self) -> bool:
        return bool(self.indice)


def descomponer(carpeta: str, id: str = "", creada: str = "") -> Caso:
    """'Gubicza, Ferenc - Closed - 600926 - 9339-2014' -> Caso.

    Se parte por la DERECHA y solo dos veces: la base puede llevar ' - ' dentro, pero
    el ID y el indice son siempre los dos ultimos campos. Lo que no tiene esa forma
    no se adivina: queda todo en la base, sin ID ni indice.
    """
    # El espacio duro (caracter 160) parece un espacio y rompe toda comparacion: esta en
    # nombres de carpeta y en dealnames de HubSpot. Las marcas de estado '[CLOSED]' y
    # '[DROPPED]' no son parte del nombre: se quitan y cuentan como cerrado.
    texto = re.sub(r"\s+", " ", str(carpeta).replace("\xa0", " ")).strip()
    marca = re.search(r"\s*\[(?:CLOSED|DROPPED)\]\s*", texto, re.I)
    if marca:
        texto = (texto[:marca.start()] + " " + texto[marca.end():]).strip()
    cerrado = bool(marca)
    if texto.lower().startswith(PREFIJO_CERRADO):
        texto, cerrado = texto[len(PREFIJO_CERRADO):], True
    partes = [p.strip() for p in texto.rsplit(" - ", 2)]
    if len(partes) == 3 and re.fullmatch(r"\d{3,7}", partes[1]):
        base, id_interno, crudo = partes
    else:
        base, id_interno, crudo = texto, "", ""
    if base.lower().endswith(COLA_CERRADO):
        base, cerrado = base[: -len(COLA_CERRADO)].strip(), True
    indice = "" if crudo.lower() in SIN_INDICE else next(iter(indices_en(crudo)), "")
    return Caso(str(carpeta), base, id_interno, indice, cerrado, str(id or ""), str(creada or ""))


def plantilla_de(caso: Caso | str | None) -> str | None:
    """La plantilla de carpetas del caso por su ID interno: 6xxxxx es Foreclosure y el
    resto FCRA. None si la carpeta no tiene ID interno.

    Del ID y no del numero de expediente: en 'nyed-1:25-cv-06123' hay un 6 que no
    tiene nada que ver con la materia del caso."""
    if caso is None:
        return None
    if isinstance(caso, str):
        caso = descomponer(caso)
    if not caso.id_interno:
        return None
    return "Foreclosure" if caso.id_interno.startswith("6") else "FCRA"


def _clave(item: Any) -> tuple[str, str, str]:
    """(nombre, id, creada) de una carpeta, venga como venga."""
    if isinstance(item, str):
        return item, "", ""
    if isinstance(item, dict):
        return (str(item.get("name") or item.get("nombre") or ""),
                str(item.get("id") or ""),
                str(item.get("createdDateTime") or item.get("creada") or ""))
    nombre, *resto = tuple(item)
    return str(nombre), str(resto[0] if resto else ""), str(resto[1] if len(resto) > 1 else "")


@functools.lru_cache(maxsize=8)
def _catalogo_de(claves: tuple[tuple[str, str, str], ...]) -> tuple[dict[str, list[Caso]], list[Caso]]:
    casos = [descomponer(*c) for c in claves if c[0]]
    por_indice: dict[str, list[Caso]] = {}
    for caso in casos:
        if caso.indice:
            por_indice.setdefault(caso.indice, []).append(caso)
    return por_indice, casos


def catalogo(carpetas: Sequence[Any]) -> tuple[dict[str, list[Caso]], list[Caso]]:
    """indice -> casos, y la lista de casos. Se arma una vez por lista de carpetas."""
    return _catalogo_de(tuple(_clave(c) for c in carpetas))


def apellido_dentro(caso: Caso, palabras: set[str]) -> bool:
    """El apellido de la carpeta, esta entre esas palabras? Por palabra completa: por
    subcadena, 'Rao' aparece dentro de 'ratio'. Admite erratas del OCR o de tecleo
    ('Vaughn' / 'Vaughan')."""
    return any(a in palabras or any(len(p) > 3 and SequenceMatcher(None, a, p).ratio() >= 0.85
                                    for p in palabras if p[:1] == a[:1])
               for a in caso.apellidos)


# --------------------------------------------------------------------------- #
@dataclass
class Identificacion:
    """A que caso pertenece un documento, y con que respaldo."""

    carpeta: str | None = None
    carpeta_id: str = ""
    id_interno: str = ""
    indice: str = ""
    # La plantilla del caso encontrado (Foreclosure / FCRA), por su ID interno.
    plantilla: str | None = None
    # Que regla del .toml lo decidio, y con que fuente: 'el asunto', 'el nombre del
    # archivo', 'el documento', 'la caratula'. Vacios si no se decidio.
    regla: str = ""
    se_decidio: str = ""
    # Index y apellido de acuerdo: se puede archivar sin que nadie mire. Las reglas
    # deciden aunque no lo este; esto queda para auditar.
    confirmado: bool = False
    razon: str = "nothing in the subject, the file name or the document ties it to a case"
    candidatos: list[str] = field(default_factory=list)
    indices_en_documento: list[str] = field(default_factory=list)


def identificar(texto: str, carpetas: Sequence[Any], *, asunto: str = "",
                nombre_archivo: str = "", reglas=None) -> Identificacion:
    """El caso de un documento, aplicando las reglas en orden (reglas_ruteo.toml).

    `texto` es el documento leido (puede venir vacio si no se pudo leer: entonces
    solo cuentan el asunto y el nombre). `reglas` es un juego cargado con
    reglas.cargar(); sin el, el de la firma (lector_ocr/reglas_ruteo.toml).
    """
    from .reglas import aplicar, por_defecto

    return aplicar(reglas or por_defecto(), texto, carpetas,
                   asunto=asunto, nombre_archivo=nombre_archivo)
