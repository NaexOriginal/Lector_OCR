"""Paso 9 del Docs Agent: el mensaje al Analyst, con el formato que fijo Cristian en el
Issue #1 de Petroff-Amshen-LLP/docket.

    from lector_ocr.analyst import mensaje, ColaAnalyst

    m = mensaje(clave=clave, source="nyscef", origen="", ubicacion=u, clasificacion=c,
                extraccion=e, seccion=s, jsonl=r, ocr_warning=False,
                sharepoint_url="https://...", deal_id="12345", renombrada=True)
    ColaAnalyst.desde_entorno()(m)

QUE LLEGO, DONDE QUEDO, POR QUE Y CON QUE CONFIANZA. El Docs Agent no decide que hacer
con el documento: eso es del Analyst. Este modulo solo traduce lo que ya salio de los
pasos 1 a 8 al JSON del Issue, campo por campo:

    event_id         la primera llave del documento (la del juzgado si la hay; si no, qxh:)
    matter           deal_id (HubSpot, por la carpeta), internal_case_id, index_number,
                     match_rule y confidence ('pending' de ubicar va como 'not_placed')
    document         type, sent_by, filer_name, doc_number, dates_found
    file             sharepoint_url, subfolder y su porque, jsonl_updated, ocr_warning
    action_taken     filed | already_filed_manual | folder_renamed | not_placed

UN DUPLICADO NO SE AVISA (Rafael, 22-oct): mientras SharePoint no este organizado, el
duplicado se vincula a la primera copia y no se archiva otra vez, y el Analyst no recibe
nada. Quien llama no llama a `mensaje` para un duplicado.

LA COLA es analyst-eventos, sin caducidad: por defecto Azure borra a los 7 dias y el
Analyst todavia no existe; sus mensajes tienen que esperarlo. Un mensaje de mas de 60 KB
va a un blob (analyst/{id}.json) y por la cola viaja solo la referencia, como en el Email
Agent. La Storage es la del Docs Agent (DOCS_AGENT_STORAGE) salvo que
DOCS_AGENT_ANALYST_STORAGE diga otra.

`clasificacion` puede ser un archivar.Destino o cualquier cosa con `.tipo`; `seccion`, un
subcarpeta.Subcarpeta. El paquete no trae azure-storage-*: solo hace falta donde se use
ColaAnalyst.desde_entorno (pip install azure-storage-queue azure-storage-blob).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

COLA_ANALYST = "analyst-eventos"
CONTENEDOR = "ocr-trabajo"
MAX_MENSAJE = 60 * 1024  # una cola de Azure admite 64 KB por mensaje

FILED = "filed"
MANUAL = "already_filed_manual"
RENOMBRADA = "folder_renamed"
SIN_UBICAR = "not_placed"

INDICE_ESTATAL = re.compile(r"^(\d{4,7})-(\d{4})$")


def index_number(indice: str) -> str | None:
    """'507148-2026' -> '507148/2026', como lo escribe el juzgado y el ejemplo del Issue.
    Un numero federal ('1-26-cv-04354') se deja como esta."""
    if not indice:
        return None
    m = INDICE_ESTATAL.match(indice)
    return f"{m.group(1)}/{m.group(2)}" if m else indice


def _numero(doc_number: str | None) -> int | str | None:
    """'12' -> 12; un anexo federal ('23-1') se queda como texto."""
    if not doc_number:
        return None
    return int(doc_number) if doc_number.isdigit() else doc_number


def accion_tomada(ubicacion, *, origen: str = "", renombrada: bool = False) -> str:
    if origen == "manual":
        return MANUAL
    if ubicacion is None or ubicacion.accion == "pending":
        return SIN_UBICAR
    if ubicacion.accion == "rename" and renombrada:
        return RENOMBRADA
    return FILED


def mensaje(*, clave, source: str, ubicacion, clasificacion=None, extraccion=None, seccion=None,
            jsonl=None, ocr_warning: bool = False, sharepoint_url: str | None = None,
            deal_id: str | None = None, origen: str = "", renombrada: bool = False,
            recibido: datetime | None = None) -> dict[str, Any]:
    """El JSON del Issue #1. `renombrada`: la carpeta '- 0' se renombro de verdad (lo dice
    quien la renombro); sin eso, un 'rename' que no se pudo hacer cuenta como 'filed'."""
    accion = accion_tomada(ubicacion, origen=origen, renombrada=renombrada)
    ubicado = accion != SIN_UBICAR
    remitente = extraccion.remitente if extraccion is not None else None
    return {
        "event_id": clave.llaves[0] if clave.llaves else None,
        "from": "docs_agent",
        "received_at": (recibido or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "source": source or None,
        "matter": {
            "deal_id": deal_id if ubicado else None,
            "internal_case_id": (ubicacion.id_interno or None) if ubicado else None,
            "index_number": index_number(ubicacion.indice if ubicacion is not None else ""),
            "match_rule": (ubicacion.match_rule or None) if ubicado else None,
            "confidence": ubicacion.confidence if ubicado and ubicacion.confidence != "pending" else SIN_UBICAR,
        },
        "document": {
            "type": (clasificacion.tipo or None) if clasificacion is not None else None,
            "sent_by": (remitente.sent_by or None) if remitente is not None else None,
            "filer_name": (remitente.filer_name or None) if remitente is not None else None,
            "doc_number": _numero(clave.numero),
            "dates_found": [{"what": f.what, "date": f.date} for f in extraccion.fechas]
                           if extraccion is not None else [],
        },
        "file": {
            "sharepoint_url": sharepoint_url,
            "subfolder": seccion.carpeta if seccion is not None and ubicado else None,
            "subfolder_reason": seccion.razon if seccion is not None and ubicado else None,
            "jsonl_updated": bool(jsonl is not None and jsonl.contenido is not None),
            "ocr_warning": bool(ocr_warning),
        },
        "action_taken": accion,
        "not_placed_reason": (ubicacion.razon if ubicacion is not None else "no matter was searched")
                             if not ubicado else None,
    }


class ColaAnalyst:
    """Manda cada mensaje a analyst-eventos. Se construye con los clientes de Azure (la
    cola y el contenedor de blobs) para poder probarlo sin Azure; en produccion,
    `desde_entorno`."""

    def __init__(self, cola, blobs):
        self._cola, self._blobs = cola, blobs

    @classmethod
    def desde_entorno(cls) -> "ColaAnalyst":
        from azure.storage.blob import BlobServiceClient
        from azure.storage.queue import QueueServiceClient

        conexion = os.environ.get("DOCS_AGENT_ANALYST_STORAGE") or os.environ.get("DOCS_AGENT_STORAGE")
        if not conexion:
            raise RuntimeError("Falta DOCS_AGENT_STORAGE (o DOCS_AGENT_ANALYST_STORAGE) para la cola del Analyst")
        cola = QueueServiceClient.from_connection_string(conexion).get_queue_client(
            os.environ.get("DOCS_AGENT_COLA_ANALYST") or COLA_ANALYST)
        try:
            cola.create_queue()
        except Exception:  # noqa: BLE001  ya existe
            pass
        blobs = BlobServiceClient.from_connection_string(conexion).get_container_client(
            os.environ.get("DOCS_AGENT_CONTENEDOR") or CONTENEDOR)
        return cls(cola, blobs)

    def __call__(self, m: dict) -> None:
        texto = json.dumps(m, ensure_ascii=False)
        if len(texto.encode("utf-8")) > MAX_MENSAJE:
            ruta = f"analyst/{uuid.uuid4()}.json"
            self._blobs.get_blob_client(ruta).upload_blob(texto.encode("utf-8"), overwrite=True)
            texto = json.dumps({"event_id": m.get("event_id"), "blob": ruta}, ensure_ascii=False)
        # Texto plano y SIN CADUCIDAD (time_to_live=-1): el Analyst aun no existe.
        self._cola.send_message(texto, time_to_live=-1)
