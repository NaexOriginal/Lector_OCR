"""Paso 7 del Docs Agent: el nombre con que se guarda el documento.

    from lector_ocr.nombre import nombre_estandar

    n = nombre_estandar("task-4 - 507148-2026 - Doc 12.pdf", tipo="Notice of motion",
                        doc_number="12", fechas=[...], sent_by="opposing_counsel",
                        filer_firm="Locke Lord LLP")
    n.nombre      # '2026-10-14 Doc 12 Notice of motion, Locke Lord LLP.pdf'
    n.renombrar   # True
    n.fecha_de    # 'filing_date'

EL FORMATO ES EL DE LA SPEC, '{fecha} Doc {n} {tipo}, {remitente}', con lo que decidio
Rafael el 19-oct:

    FECHA       la del documento, no la de hoy: la de radicacion (el sello de NYSCEF o
                el 'Filed' de ECF) y, si no hay, la que firma el escrito ('Dated:').
                Solo si el documento no trae ninguna, la del dia en que se renombra.
                En AAAA-MM-DD: asi SharePoint, que ordena por nombre, los deja en orden
                cronologico (con MM-DD-AAAA, enero de 2027 iria antes que octubre de 2026).
    DOC {n}     el numero del expediente judicial (paso 1); 'Doc 0' si no tiene. Los
                anexos federales llevan el suyo entero: 'Doc 23-1'.
    {tipo}      la etiqueta del clasificador. SIN TIPO NO SE RENOMBRA: el documento se
                queda con su nombre, en 00_Unfiled y en pendientes, para que alguien lo mire.
    {remitente} el despacho que firma; 'Petroff Amshen' si somos nosotros; el juez si se
                lee y si no 'Court'; un tercero solo si se sabe quien es. Si no hay
                remitente claro, se omite ', {remitente}' en vez de inventarlo.

LAS CARGAS MANUALES NO SE TOCAN: la persona ya eligio carpeta y nombre (regla 6 de la
spec). Se describen y se notifican, nada mas.

EL NOMBRE ORIGINAL SE GUARDA SIEMPRE junto al nuevo (regla 5: conservar el original).

LO QUE SHAREPOINT NO ADMITE (" * : < > ? / \\ |, espacios o puntos al final) se quita
del nombre. Y si en la seccion ya hay un archivo que se llama igual -- dos estados de
cuenta del mismo dia, sin numero, darian el mismo nombre -- se le anade ' (2)'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

# El prefijo de las fechas, en orden de preferencia.
FECHAS_DEL_DOCUMENTO = ("filing_date", "document_date")
NUESTRA_FIRMA = "Petroff Amshen"
LA_CORTE = "Court"
SIN_NUMERO = "0"
PROHIBIDOS = re.compile(r'["*:<>?/\\|\x00-\x1f]')
LARGO_MAXIMO = 180          # el nombre sin extension; la ruta entera tiene tope en SharePoint
ORIGEN_MANUAL = "manual"


@dataclass
class Nombre:
    nombre: str              # el nombre con que se guarda (el original si no se renombra)
    original: str
    renombrar: bool
    fecha: str = ""          # '2026-10-14'
    fecha_de: str = ""       # 'filing_date' | 'document_date' | 'rename_date'
    razon: str = ""

    def campos(self) -> dict:
        return {"original_name": self.original, "new_name": self.nombre,
                "renamed": self.renombrar, "name_date": self.fecha or None,
                "name_date_from": self.fecha_de or None, "name_because": self.razon}


def _limpiar(texto: str) -> str:
    texto = PROHIBIDOS.sub(" ", str(texto or "").replace("\xa0", " "))
    return re.sub(r"\s+", " ", texto).strip(" .")


def remitente_para_nombre(sent_by: str, filer_name: str = "", filer_firm: str = "") -> str:
    """Quien firma, tal como va en el nombre. '' si no se sabe: no se inventa."""
    if sent_by == "our_firm":
        return NUESTRA_FIRMA
    if sent_by == "court":
        if not re.match(r"hon\b", filer_name or "", re.I):
            return LA_CORTE
        # El juez viene como lo escribe la orden, a veces todo en mayusculas.
        return filer_name.title() if filer_name.isupper() else filer_name
    if sent_by == "opposing_counsel":
        return filer_firm or filer_name or ""
    return filer_name or ""      # un tercero: solo si se sabe quien es


def fecha_para_nombre(fechas: Iterable[dict], hoy: date | None = None) -> tuple[str, str]:
    """(fecha, de donde salio). La del documento antes que la de hoy."""
    lista = list(fechas or [])
    for que in FECHAS_DEL_DOCUMENTO:
        for f in lista:
            if f.get("what") == que and f.get("date"):
                return f["date"], que
    return (hoy or date.today()).isoformat(), "rename_date"


def nombre_estandar(original: str, *, tipo: str = "", doc_number: str | None = None,
                    fechas: Iterable[dict] = (), sent_by: str = "", filer_name: str = "",
                    filer_firm: str = "", origen: str = "", hoy: date | None = None) -> Nombre:
    """El nombre nuevo del documento, o el original con el porque si no se renombra."""
    if origen == ORIGEN_MANUAL:
        return Nombre(original, original, False,
                      razon="manual upload: the person already chose its folder and name")
    if not tipo:
        return Nombre(original, original, False,
                      razon="unknown document type: kept its name, 00_Unfiled and pending")

    fecha, fecha_de = fecha_para_nombre(fechas, hoy)
    quien = _limpiar(remitente_para_nombre(sent_by, filer_name, filer_firm))
    # 'Reply / Affirmation in reply': la barra no vale en SharePoint; mejor un guion que
    # pegar las dos palabras.
    etiqueta = _limpiar(tipo.replace("/", "-"))
    base = f"{fecha} Doc {doc_number or SIN_NUMERO} {etiqueta}" + (f", {quien}" if quien else "")
    base = _limpiar(base)[:LARGO_MAXIMO].rstrip(" .,")
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    nuevo = f"{base}.{extension}" if extension else base
    partes = [f"date from the {fecha_de.replace('_', ' ')}",
              f"doc number {doc_number}" if doc_number else "no doc number: Doc 0",
              f"filer '{quien}'" if quien else "no clear filer: omitted"]
    return Nombre(nuevo, original, True, fecha, fecha_de, "; ".join(partes))


def sin_choque(nombre: str, existentes: Iterable[str]) -> str:
    """'X.pdf' -> 'X (2).pdf' si en la seccion ya hay un 'X.pdf' (sin distinguir mayusculas,
    como SharePoint)."""
    ocupados = {e.lower() for e in existentes}
    if nombre.lower() not in ocupados:
        return nombre
    base, punto, extension = nombre.rpartition(".")
    if not punto:
        base, extension = nombre, ""
    n = 2
    while True:
        candidato = f"{base} ({n})" + (f".{extension}" if extension else "")
        if candidato.lower() not in ocupados:
            return candidato
        n += 1
