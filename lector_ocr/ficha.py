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
from datetime import datetime, timezone

from .archivar import PAGINAS, donde_archivar
from .lectores import leer_con_detalle

# Un numero de la Seguridad Social de EE.UU. tal y como se escribe en un documento.
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Por encima de esto no se lee. Un escaneo de cuatrocientas paginas cuesta minutos y
# no dice mas que uno de veinte sobre que documento es. En un servidor esto ademas
# es el limite que impide que un archivo enorme bloquee al resto.
MAX_MB = 25


def enmascarar(texto: str) -> str:
    """Deja los cuatro ultimos digitos, que son los que sirven para cotejar."""
    return SSN.sub(lambda m: f"XXX-XX-{m.group(0)[-4:]}", texto)


def ficha_de(nombre: str, datos: bytes, plantilla: str = "Foreclosure", *,
             subcarpeta: str = "", paginas: int = PAGINAS,
             con_texto: bool = True, enmascarar_ssn: bool = False,
             max_mb: float = MAX_MB) -> dict:
    """Todo lo que se sabe de un archivo, listo para volcar como una linea de JSONL.

    `datos` es el contenido en bytes. `plantilla` decide en que estructura se busca
    la carpeta destino. `subcarpeta` es donde esta el archivo, si se sabe.

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
    if tamano > max_mb:
        return {**base, "was_read": False, "read_with": None,
                "not_read_because": f"larger than {max_mb} MB"}

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    try:
        texto, fallo, con_que = leer_con_detalle(extension, datos, paginas)
    except Exception as error:  # noqa: BLE001
        return {**base, "was_read": False, "read_with": None,
                "not_read_because": f"{type(error).__name__}: {error}"[:120]}

    destino = donde_archivar(nombre, datos, plantilla, paginas)
    lleva_ssn = bool(SSN.search(texto))
    ficha = {
        **base,
        "was_read": bool(texto.strip()),
        "read_with": con_que or None,
        "not_read_because": fallo or None,
        "document_type": destino.tipo or None,
        "goes_to": destino.carpeta or None,
        "decided_by": destino.se_decidio or None,
        "decided_because": destino.evidencia or None,
        "contains_ssn": lleva_ssn,
        "extracted_text_length": len(texto),
    }
    if con_texto:
        ficha["extracted_text"] = (enmascarar(texto) if enmascarar_ssn else texto) or None
    return ficha


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
