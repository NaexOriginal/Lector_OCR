"""La ficha de un archivo: una linea de JSONL con lo que se sabe de el.

    from lector_ocr import ficha_de

    registro = ficha_de("Notice of Motion.pdf", datos, "Foreclosure")
    json.dumps(registro, ensure_ascii=False)     # una linea del JSONL

POR QUE JSONL Y NO JSON. Un expediente crece: hoy se describen tres archivos y
manana treinta. Con JSONL se AÑADEN lineas al final sin releer ni reescribir lo que
ya habia, y se puede recorrer a trozos -- un caso de cuatro mil archivos no obliga a
cargar veinte megas en memoria para mirar uno. Un JSON con una lista dentro obliga a
las dos cosas.

La convencion que usamos: la PRIMERA linea es un resumen y se reconoce porque lleva
`matter`; las demas son archivos y llevan `file_name`.

LOS DATOS PERSONALES SE CUENTAN SIEMPRE. `contains_ssn` va en cada ficha aunque no se
enmascare nada. Quien decide si el texto sale en claro es quien manda; que el dato
este a la vista no es negociable, porque una decision tomada sin saber el numero no
es una decision.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .archivar import PAGINAS, donde_archivar
from .expediente import Identificacion, identificar, indices_del_documento
from .limpieza import limpiar_documento
from .lectores import leer_con_detalle

# Un numero de la Seguridad Social de EE.UU. tal y como se escribe en un documento.
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# LECTURA COMPLETA POR DEFECTO: todas las paginas y sin tope de tamano (0 = sin
# tope). La ficha es el texto del documento, no una muestra: con cuatro paginas y
# 25 MB, los escaneos largos -- justo los que solo tienen texto si se les hace OCR --
# quedaban a medias sin que la ficha lo dijera.
#
# Quien necesite un tope (un servidor que no quiera que un archivo enorme bloquee
# al resto) lo pasa: ficha_de(..., paginas=4, max_mb=25).
PAGINAS_POR_DEFECTO = 0
MAX_MB = 0


def enmascarar(texto: str) -> str:
    """Deja los cuatro ultimos digitos, que son los que sirven para cotejar."""
    return SSN.sub(lambda m: f"XXX-XX-{m.group(0)[-4:]}", texto)


# Con plantilla="auto" la plantilla sale del caso encontrado (su ID interno). Si no se
# encuentra caso, el documento va a sin clasificar y se clasifica con esta: da igual
# cual, pero tiene que ser una.
PLANTILLA_SIN_CASO = "Foreclosure"


def ficha_de(nombre: str, datos: bytes, plantilla: str = "Foreclosure", *,
             subcarpeta: str = "", paginas: int = PAGINAS_POR_DEFECTO,
             con_texto: bool = True, enmascarar_ssn: bool = False,
             max_mb: float = MAX_MB, carpetas: Sequence[Any] | None = None,
             asunto: str = "", reglas=None) -> dict:
    """Todo lo que se sabe de un archivo, listo para volcar como una linea de JSONL.

    `datos` es el contenido en bytes. `plantilla` decide en que estructura se busca
    la carpeta destino ("Foreclosure", "FCRA" o "auto"). `subcarpeta` es donde esta
    el archivo, si se sabe.

    `carpetas`, si se pasa, son las carpetas de caso (nombres, o dicts de Graph con
    `name`, `id` y `createdDateTime`): entonces la ficha dice tambien A QUE CASO
    pertenece el documento, aplicando las `reglas` (reglas_ruteo.toml si no se pasan)
    sobre el `asunto` del correo, el nombre del archivo y lo que dice por dentro. Con
    plantilla="auto", la plantilla sale de ese caso. Sin `carpetas` no se busca caso,
    pero `index_numbers` va siempre.

    Los campos de control van primero y el texto al final, a proposito: es lo mas
    largo con diferencia, y quien abra el fichero quiere ver antes de que va el
    documento que su transcripcion.
    """
    tamano = len(datos) / 2 ** 20
    base = {
        "file_name": nombre,
        "subfolder": subcarpeta or "(folder root)",
        "size_mb": round(tamano, 2),
    }
    # 0 BYTES NO ES 'SIN TEXTO': no hay nada que leer, y quien reintenta tiene que
    # saber que no va a cambiar. La frase es la que ya usa la lectura de Matters.
    if not datos:
        return {**base, "was_read": False, "read_with": None,
                "not_read_because": "empty file (0 bytes)",
                **_campos_caso(_caso(carpetas, "", nombre, asunto, reglas))}
    if max_mb and tamano > max_mb:
        return {**base, "was_read": False, "read_with": None,
                "not_read_because": f"larger than {max_mb} MB",
                **_campos_caso(_caso(carpetas, "", nombre, asunto, reglas))}

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    calidad: dict = {}
    try:
        texto, fallo, con_que = leer_con_detalle(extension, datos, paginas, extras=calidad)
    except Exception as error:  # noqa: BLE001
        return {**base, "was_read": False, "read_with": None,
                "not_read_because": f"{type(error).__name__}: {error}"[:120],
                **_campos_caso(_caso(carpetas, "", nombre, asunto, reglas))}

    # PASO 3: SE LIMPIA ANTES DE EXTRAER. El caso, la subcarpeta, los index y los SSN
    # salen del texto limpio, que es tambien el que se guarda: lo que decide y lo que
    # queda escrito son el mismo texto. `leido` es lo que importa para was_read: si la
    # regla 1 vacia el texto, el archivo se leyo igual.
    leido = bool(texto.strip())
    limpio = limpiar_documento(texto, con_que, control_por_pagina="ocr_warning" in calidad)
    texto = limpio.texto

    # PRIMERO EL CASO, DESPUES LA SUBCARPETA: la plantilla (Foreclosure o FCRA) sale
    # del ID interno del caso, y con la plantilla equivocada la subcarpeta seria de la
    # OTRA estructura, que en ese expediente no existe.
    caso = _caso(carpetas, texto, nombre, asunto, reglas)
    if plantilla == "auto":
        plantilla = (caso.plantilla if caso else None) or PLANTILLA_SIN_CASO

    # Se clasifica con el texto que ya se leyo (su principio): leerlo otra vez
    # seria un segundo OCR del mismo escaneo.
    destino = donde_archivar(nombre, datos, plantilla, PAGINAS,
                             texto=texto, con_que=con_que)
    lleva_ssn = bool(SSN.search(texto))
    ficha = {
        **base,
        "was_read": leido,
        "read_with": con_que or None,
        "not_read_because": fallo or None,
        # PASO 2: si alguna pagina quedo sin texto fiable tras el reintento. Solo lo
        # dan los lectores con OCR (PDF e imagen); en el resto no aplica y va False.
        "ocr_warning": bool(calidad.get("ocr_warning")),
        "ocr_pages_failed": calidad.get("pages_failed") or None,
        "pages": calidad.get("pages_by_status") or None,
        "document_type": destino.tipo or None,
        "template": plantilla,
        "goes_to": destino.carpeta or None,
        "decided_by": destino.se_decidio or None,
        "decided_because": destino.evidencia or None,
        "contains_ssn": lleva_ssn,
        # El del caso primero: los etiquetados ('INDEX NO.') y los de la caratula
        # van delante de los que el cuerpo cita de otros pleitos.
        "index_numbers": indices_del_documento(texto)[:10],
        **_campos_caso(caso),
        **limpio.campos(),
        "extracted_text_length": len(texto),
    }
    if con_texto:
        ficha["extracted_text"] = (enmascarar(texto) if enmascarar_ssn else texto) or None
    return ficha


def _caso(carpetas, texto: str, nombre: str, asunto: str, reglas) -> Identificacion | None:
    if carpetas is None:
        return None
    return identificar(texto, carpetas, asunto=asunto, nombre_archivo=nombre, reglas=reglas)


def _campos_caso(caso: Identificacion | None) -> dict:
    """Los campos del caso para la ficha; nada si no se pasaron carpetas."""
    if caso is None:
        return {}
    campos = {
        "case_folder": caso.carpeta,
        "case_folder_id": caso.carpeta_id or None,
        "case_rule": caso.regla or None,
        "case_found_by": caso.se_decidio or None,
        "case_confirmed": caso.confirmado,
        "case_because": caso.razon,
    }
    if caso.candidatos:
        campos["case_candidates"] = caso.candidatos
    return campos


# VARIOS ARCHIVOS A LA VEZ. Un correo trae varios adjuntos y una bandeja varios
# archivos: leerlos uno detras de otro hace esperar a todos por el escaneo mas largo.
# Con hilos, lo que no necesita OCR (capas de texto, Word, correo) se lee mientras
# otro archivo esta en el OCR. El OCR en si sigue siendo uno a la vez y con un solo
# modelo (paddle_ocr): el paralelismo que ayuda es el de todo lo demas.
HILOS = 4


def fichas_de(archivos: Iterable[tuple[str, bytes]], plantilla: str = "Foreclosure", *,
              hilos: int = HILOS, **opciones) -> list[dict]:
    """Las fichas de varios archivos, en el mismo orden en que llegaron.

        fichas_de([("Order.pdf", datos1), ("Exhibit A.pdf", datos2)], "FCRA",
                  carpetas=carpetas, asunto=asunto)

    Las `opciones` son las de ficha_de y valen para todos. Un archivo que falla no
    tumba a los demas: ficha_de nunca lanza por un archivo malo, devuelve su motivo.
    """
    lista = list(archivos)
    if len(lista) <= 1 or hilos <= 1:
        return [ficha_de(nombre, datos, plantilla, **opciones) for nombre, datos in lista]
    with ThreadPoolExecutor(max_workers=min(hilos, len(lista))) as grupo:
        return list(grupo.map(lambda a: ficha_de(a[0], a[1], plantilla, **opciones), lista))


def cabecera(matter: str, fichas: list[dict], *, parcial: bool = False,
             enmascarado: bool = False) -> dict:
    """La primera linea del JSONL: el resumen del expediente.

    Dice cuantos archivos describe y, sobre todo, si estan TODOS. Un fichero parcial
    que no se declare parcial se lee como completo, y entonces la ausencia de un
    documento parece una afirmacion de que no existe.
    """
    return {
        "matter": matter,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_described": len(fichas),
        "files_read": sum(1 for f in fichas if f.get("was_read")),
        "files_with_ssn": sum(1 for f in fichas if f.get("contains_ssn")),
        "partial": parcial,
        "ssn_masked": enmascarado,
        "note": ("One JSON object per line. First line is this summary; the rest are "
                 "documents. More document lines can be appended later."),
    }
