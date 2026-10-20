"""Paso 8 del Docs Agent: el Claude-{internal_case_id}.jsonl de cada matter, al dia.

    from lector_ocr.jsonl_matter import anotar, mover, conciliar

    r = anotar(contenido_actual, ficha, sharepoint_id="01ABC...", matter=carpeta,
               carpeta_id=id_carpeta, es_complaint=False)
    r.accion       # 'added' | 'replaced' | 'created' | 'not_written'
    r.contenido    # bytes del JSONL nuevo (None si no se escribe)

VIVE EN LA RAIZ DE LA CARPETA DEL MATTER: Matters/<carpeta>/Claude-<ID>.jsonl, para
cualquier anio. Este modulo no habla con SharePoint: recibe el contenido actual y
devuelve el nuevo. Quien lo sube tiene que usar el control de version de SharePoint
(If-Match con el eTag) y reintentar si cambio: dos documentos del mismo matter que
llegan a la vez se pisarian la linea.

EL MISMO FORMATO QUE LOS QUE YA EXISTEN (describir_casos / armar_casos y la limpieza
de Petroff): primera linea, la cabecera ('matter', 'files_described',
'files_in_matter', 'coverage', 'files_read', 'files_with_ssn', 'partial'...); despues,
una linea por archivo con su 'sharepoint_id' delante. Lo que no se toca se conserva:
'sharepoint_folder_id', el bloque 'cleaning', 'matter_context' y lo que traiga.

CADA LINEA SE IDENTIFICA POR EL ID DE SHAREPOINT, no por el nombre (Mario, seccion 6):
el 45% de los archivos comparte nombre con otro del mismo matter. Mismo id: la linea
se REEMPLAZA (el archivo se releyo). Mismo nombre e id distinto: son dos archivos.

SE ESCRIBE INCREMENTAL: se anade o se reemplaza una linea y se recalcula la cabecera.
Ya no es una foto del momento en que se genero.

SOLO EL COMPLAINT CREA EL JSONL de un matter (decision de Rafael, 20-oct): es el primer
documento de un caso. Si llega otro a un matter sin JSONL, no se crea: el documento se
archiva igual y se avisa ('not_written'). Los de anios anteriores los genera la
lectura masiva.

EL TEXTO VA EN CLARO, sin la regla 5 de la limpieza (decision de RevOps del 24-sep,
confirmada el 20-oct): igual que los JSONL que ya hay. 'files_with_ssn' los cuenta.

SI UN ARCHIVO SE MUEVE DE MATTER se mueven con el su linea y los contadores de las dos
cabeceras (`mover`). Al moverlo dentro de la biblioteca conserva su id, asi que un
vigilante que compare cada JSONL con su carpeta (`conciliar`) puede detectarlo solo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

NOTA = ("One JSON object per line. First line is this summary; the rest are documents. "
        "Each document line is keyed by its SharePoint id. Compare files_described with "
        "files_in_matter to know what is still pending. Full extracted text, unmasked - "
        "contains document contents verbatim, including any personal data they carry.")

# Lo que la ficha lleva y no va en la linea del JSONL: es del flujo, no del archivo.
FUERA_DE_LA_LINEA = {"subfolder", "file_name"}


def nombre_del_jsonl(id_interno: str) -> str:
    return f"Claude-{id_interno}.jsonl"


# --------------------------------------------------------------------------- #
# Leer y escribir


@dataclass
class Jsonl:
    cabecera: dict | None
    lineas: list[dict] = field(default_factory=list)   # documentos y otras (matter_context)
    invalidas: int = 0

    @property
    def documentos(self) -> list[dict]:
        return [l for l in self.lineas if "file_name" in l]

    def a_bytes(self) -> bytes:
        registros = ([self.cabecera] if self.cabecera else []) + self.lineas
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros).encode("utf-8")


def leer(contenido: bytes | str | None) -> Jsonl:
    """El JSONL de un matter. Partido por '\\n' y NUNCA con splitlines(): un texto puede
    llevar U+2028 o U+0085 dentro, y splitlines lo corta y la linea se pierde."""
    if not contenido:
        return Jsonl(None)
    texto = contenido.decode("utf-8-sig", errors="replace") if isinstance(contenido, bytes) else contenido
    jsonl = Jsonl(None)
    for renglon in texto.split("\n"):
        if not renglon.strip():
            continue
        try:
            registro = json.loads(renglon)
        except json.JSONDecodeError:
            jsonl.invalidas += 1
            continue
        if jsonl.cabecera is None and not jsonl.lineas and "matter" in registro \
                and "file_name" not in registro:
            jsonl.cabecera = registro
        else:
            jsonl.lineas.append(registro)
    return jsonl


def linea_de(ficha: dict, sharepoint_id: str) -> dict:
    """La linea del JSONL a partir de la ficha: el id delante, el archivo con el nombre y
    la seccion con que QUEDA guardado, y el resto de la ficha detras."""
    guardado = ficha.get("new_name") or ficha.get("file_name")
    seccion = ficha.get("goes_to") or ficha.get("subfolder")
    resto = {k: v for k, v in ficha.items() if k not in FUERA_DE_LA_LINEA}
    return {"sharepoint_id": sharepoint_id, "file_name": guardado,
            "subfolder": seccion or "(folder root)", **resto}


def recalcular(jsonl: Jsonl, *, matter: str = "", carpeta_id: str = "",
               archivos_en_matter: int | None = None, anadidos: int = 0) -> dict:
    """La cabecera con los contadores al dia. Lo que no son contadores se conserva.

    `archivos_en_matter`: cuantos archivos tiene la carpeta, si quien llama lo sabe. Si
    no, se suma `anadidos` al que habia (un archivo nuevo en el matter es uno mas)."""
    viejo = dict(jsonl.cabecera or {})
    docs = jsonl.documentos
    total = archivos_en_matter
    if total is None and viejo.get("files_in_matter") is not None:
        total = int(viejo["files_in_matter"]) + anadidos
    if total is not None:
        total = max(total, len(docs))
    nueva = {
        **({"sharepoint_folder_id": carpeta_id or viejo.get("sharepoint_folder_id")}
           if carpeta_id or viejo.get("sharepoint_folder_id") else {}),
        "matter": matter or viejo.get("matter", ""),
        **{k: v for k, v in viejo.items() if k not in ("sharepoint_folder_id", "matter")},
        "updated": datetime.now().isoformat(timespec="seconds"),
        "files_described": len(docs),
        "files_in_matter": total,
        "coverage": round(len(docs) / total, 3) if total else None,
        "files_read": sum(1 for d in docs if d.get("was_read")),
        "files_with_ssn": sum(1 for d in docs if d.get("contains_ssn")),
        "partial": total is None or len(docs) < total,
    }
    nueva.setdefault("generated", nueva["updated"])
    nueva.setdefault("note", NOTA)
    return nueva


# --------------------------------------------------------------------------- #
# Anadir, mover, conciliar


@dataclass
class Resultado:
    accion: str                    # 'added' | 'replaced' | 'created' | 'not_written'
    contenido: bytes | None = None
    razon: str = ""
    cabecera: dict | None = None

    def campos(self) -> dict:
        return {"jsonl_action": self.accion, "jsonl_updated": self.contenido is not None,
                "jsonl_because": self.razon}


def anotar(contenido: bytes | str | None, ficha: dict, *, sharepoint_id: str, matter: str,
           carpeta_id: str = "", es_complaint: bool = False,
           archivos_en_matter: int | None = None) -> Resultado:
    """La linea del documento en el JSONL de su matter, y la cabecera recalculada."""
    jsonl = leer(contenido)
    if jsonl.cabecera is None and not jsonl.lineas:
        if not es_complaint:
            return Resultado("not_written", razon=(
                "the matter has no JSONL and only a complaint creates one: the document is "
                "filed, its line is not written"))
        accion = "created"
    else:
        accion = "added"

    linea = linea_de(ficha, sharepoint_id)
    anadidos = 1
    for i, existente in enumerate(jsonl.lineas):
        if existente.get("sharepoint_id") == sharepoint_id:
            jsonl.lineas[i] = linea
            accion, anadidos = "replaced", 0
            break
    else:
        jsonl.lineas.append(linea)

    jsonl.cabecera = recalcular(jsonl, matter=matter, carpeta_id=carpeta_id,
                                archivos_en_matter=archivos_en_matter,
                                anadidos=anadidos if accion != "created" else 0)
    if accion == "created" and archivos_en_matter is None:
        jsonl.cabecera.update(files_in_matter=None, coverage=None, partial=True)
    razon = {"created": "first document of the matter (complaint): JSONL created",
             "added": "line added and summary recalculated",
             "replaced": "the file already had a line (same SharePoint id): replaced"}[accion]
    if jsonl.invalidas:
        razon += f"; {jsonl.invalidas} unreadable line(s) in the old JSONL were dropped"
    return Resultado(accion, jsonl.a_bytes(), razon, jsonl.cabecera)


def mover(origen: bytes | str | None, destino: bytes | str | None, sharepoint_id: str, *,
          matter_destino: str, carpeta_id_destino: str = "", cambios: dict | None = None
          ) -> tuple[bytes | None, bytes | None, str]:
    """El archivo pasa de un matter a otro: su linea sale del JSONL de origen y entra en
    el de destino, y se recalculan las dos cabeceras. `cambios` son los campos de la
    linea que cambian con el traslado (case_folder, subfolder, file_name...).

    Devuelve (origen nuevo, destino nuevo, por que). Si el destino no tiene JSONL se
    crea: el matter existe y el documento ya esta dentro."""
    a, b = leer(origen), leer(destino)
    linea = next((l for l in a.lineas if l.get("sharepoint_id") == sharepoint_id), None)
    if linea is None:
        return None, None, f"no line with SharePoint id {sharepoint_id} in the source JSONL"
    a.lineas = [l for l in a.lineas if l.get("sharepoint_id") != sharepoint_id]
    a.cabecera = recalcular(a, anadidos=-1)
    b.lineas = [l for l in b.lineas if l.get("sharepoint_id") != sharepoint_id]
    b.lineas.append({**linea, **(cambios or {})})
    b.cabecera = recalcular(b, matter=matter_destino, carpeta_id=carpeta_id_destino, anadidos=1)
    return a.a_bytes(), b.a_bytes(), "line moved with the file; both summaries recalculated"


def conciliar(contenido: bytes | str | None, ids_en_la_carpeta: Iterable[str]) -> dict:
    """Lo que el JSONL dice frente a lo que hay de verdad en la carpeta del matter.

    `ids_en_la_carpeta`: los ids de SharePoint de todos los archivos del matter. Dice que
    lineas describen archivos que ya no estan (movidos o borrados) y que archivos no
    tienen linea. No cambia nada: con eso, quien vigila decide si llama a `mover`."""
    jsonl = leer(contenido)
    presentes = set(ids_en_la_carpeta)
    descritos = {l.get("sharepoint_id") for l in jsonl.documentos if l.get("sharepoint_id")}
    return {
        "lines_without_file": sorted(descritos - presentes),
        "files_without_line": sorted(presentes - descritos),
        "lines_without_id": sum(1 for l in jsonl.documentos if not l.get("sharepoint_id")),
        "in_sync": descritos == presentes,
    }
