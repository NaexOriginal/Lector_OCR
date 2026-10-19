"""Paso 5 del Docs Agent: la carpeta del matter, con las reglas de la firma.

    from lector_ocr.ubicar import ubicar

    u = ubicar(carpetas, tipo="Summons and complaint", texto=texto, clave=clave,
               caratula=extraido.caratula, demandados={"601201": ["Experian", ...]})
    u.accion          # 'file' | 'rename' | 'pending'
    u.carpeta         # 'Rivera, Ismael - 601201 - 0'
    u.renombrar_a     # 'Rivera, Ismael - 601201 - 507148-2026'
    u.match_rule      # 'index' | 'name' | 'defendants' | ''
    u.confidence      # 'exact' | 'confirmed' | 'pending'

EL COMPLAINT ES SIEMPRE EL PRIMER DOCUMENTO DE UN CASO, y es el unico que puede poner
el Index en el nombre de la carpeta. Por eso las reglas son dos juegos:

    CUALQUIER DOCUMENTO   Regla 1: el Index. Si no hay carpeta con ese Index, a
                          pendientes: el complaint ya tendria que haberlo puesto.
    EL COMPLAINT          Regla 1: el Index, si alguna carpeta ya lo lleva.
                          Regla 2: el nombre del cliente, si deja UNA carpeta sin Index.
                          Regla 3: si deja varias, los demandados del complaint contra
                          los de cada matter en HubSpot.
                          Despues se renombra el '- 0' de la carpeta con el Index.

DE DONDE SALE EL INDEX IMPORTA (filing rules de Mario, seccion 4). El sello de NYSCEF
o ECF y el asunto del aviso los pone el juzgado: deciden solos ('exact'). El Index que
solo esta en el TEXTO del escrito puede ser de otro caso -- los abogados reutilizan un
escrito como plantilla y el Index viejo se queda en la caratula --, asi que solo
decide si el apellido del cliente de esa carpeta tambien esta en el documento
('confirmed'). Si no esta, a pendientes con la carpeta como candidata.

REGLA 3, la puntuacion: demandados del complaint que estan tambien en el matter,
divididos por los demandados del complaint. Se descuentan los ocupantes genericos
('John Doe', '...said names being fictitious'): no estan nunca en HubSpot y bajarian
la puntuacion de todos por igual. Con la mejor:

    1.0                     match: se archiva y se renombra
    0.5 o mas, sin empate   match con aviso: se renombra y se avisa
    menos, empate, o algun  pendientes. Un matter sin demandados cargados no es
    candidato sin demandados  'no casa', es 'sin evidencia': no se puede descartar

LOS DEMANDADOS DE CADA MATTER LOS PASA QUIEN LLAMA (`demandados`, por ID interno o por
nombre de carpeta). Este paquete no habla con HubSpot, igual que no habla con
SharePoint: recibe las listas y decide.

NUNCA SE ADIVINA Y NUNCA SE CREA UNA CARPETA. Lo que no se decide va a pendientes con
los candidatos y el por que.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .expediente import (CARATULA, NO_DISTINGUEN, Caso, apellido_dentro, catalogo,
                         indices_del_documento, indices_en, normalizar, plantilla_de)

LARGO_MINIMO = 4

# Los tipos de clasificacion.py que abren un caso.
COMPLAINTS = {"Summons and complaint", "Complaint", "Amended complaint"}

MATCH_PLENO = 1.0
MATCH_CON_AVISO = 0.5


@dataclass
class Ubicacion:
    accion: str = "pending"          # 'file' | 'rename' | 'pending'
    carpeta: str = ""
    carpeta_id: str = ""
    id_interno: str = ""
    indice: str = ""                 # el Index del documento con que se decidio o se renombra
    plantilla: str | None = None
    renombrar_a: str = ""            # el nombre nuevo de la carpeta, si hay que renombrarla
    match_rule: str = ""             # 'index' | 'name' | 'defendants'
    confidence: str = "pending"      # 'exact' | 'confirmed' | 'pending'
    puntuacion: float | None = None  # la de la regla 3
    aviso: str = ""                  # el match vale pero alguien tiene que saberlo
    razon: str = ""
    candidatos: list[dict] = field(default_factory=list)

    def campos(self) -> dict:
        """Los campos de la ficha y del 'matter' del mensaje al Analyst."""
        return {
            "case_folder": self.carpeta or None,
            "case_folder_id": self.carpeta_id or None,
            "internal_case_id": self.id_interno or None,
            "case_index": self.indice or None,
            "case_action": self.accion,
            "case_rename_to": self.renombrar_a or None,
            "match_rule": self.match_rule or None,
            "confidence": self.confidence,
            "match_score": self.puntuacion,
            "case_warning": self.aviso or None,
            "case_because": self.razon,
            "case_candidates": self.candidatos or None,
        }


# --------------------------------------------------------------------------- #
# Demandados: normalizar y comparar

# Palabras que no distinguen a un demandado de otro: la forma juridica y el relleno.
# 'SELECT PORTFOLIO SERVICING, INC.' y 'Select Portfolio' tienen que ser el mismo.
RELLENO_DEMANDADO = frozenset(
    """inc incorporated llc l l c corp corporation co company ltd limited lp llp pllc pc
    na n a the of and as a an its in for by fka aka dba f k a d b a not individual
    capacity solely trustee successor merger""".split())
SUFIJO_SUELTO = re.compile(
    r"^(?:inc|incorporated|llc|l\.?l\.?c|corp|corporation|co|company|ltd|limited|l\.?p|n\.?a|"
    r"pllc|p\.?c|jr|sr|ii|iii)\.?$", re.I)
OCUPANTES = re.compile(r"\bjohn\s+doe|\bjane\s+doe|fictitious|unknown|occupant|tenant|"
                       r"\bet\s+al\b|said\s+names?|persons?\s+or\s+parties", re.I)
MINIMO_JACCARD = 0.6


def _fichas(nombre: str) -> frozenset[str]:
    return frozenset(t for t in normalizar(nombre).split() if t not in RELLENO_DEMANDADO)


def mismo_demandado(a: str, b: str) -> bool:
    """'SELECT PORTFOLIO SERVICING, INC.' == 'Select Portfolio'. Por palabras que
    distinguen: una contenida en la otra, o casi las mismas."""
    x, y = _fichas(a), _fichas(b)
    if not x or not y:
        return False
    if x <= y or y <= x:
        return True
    return len(x & y) / len(x | y) >= MINIMO_JACCARD


def demandados_del_complaint(caratula) -> list[str]:
    """Los demandados de la caratula, uno por entrada y sin los ocupantes genericos.

    La caratula del paso 4 solo parte por ';'. Para PUNTUAR hace falta contar, asi que
    aqui se parte tambien por comas y ' and ', y los sufijos que quedan sueltos ('Inc',
    'LLC', 'N.A.') se devuelven a la entrada de antes.
    """
    if caratula is None or not caratula.demandados_texto:
        return []
    salida: list[str] = []
    for trozo in re.split(r";|,|\band\b", caratula.demandados_texto, flags=re.I):
        trozo = trozo.strip(" .")
        if not trozo:
            continue
        if salida and SUFIJO_SUELTO.match(trozo):
            salida[-1] = f"{salida[-1]}, {trozo}"
            continue
        salida.append(trozo)
    return [d for d in salida if _fichas(d) and not OCUPANTES.search(d)]


def puntuar(del_complaint: list[str], del_matter: list[str]) -> float | None:
    """Demandados del complaint que estan en el matter / demandados del complaint.
    None si alguno de los dos lados no tiene demandados: no hay evidencia."""
    if not del_complaint or not del_matter:
        return None
    hallados = sum(1 for d in del_complaint if any(mismo_demandado(d, m) for m in del_matter))
    return round(hallados / len(del_complaint), 3)


# --------------------------------------------------------------------------- #
# Las reglas


def _nombre_con_indice(caso: Caso, indice: str) -> str:
    """'Rivera, Ismael - 601201 - 0' -> 'Rivera, Ismael - 601201 - 507148-2026'.
    Se cambia solo el ultimo tramo; lo demas se deja como esta."""
    cabeza = caso.carpeta.replace("\xa0", " ").rstrip()
    return re.sub(r"\s-\s*(?:0|-|s/n)?\s*$", "", cabeza) + f" - {indice}"


def _vista(caso: Caso, **extra) -> dict:
    return {"folder": caso.carpeta, "folder_id": caso.id or None, **extra}


def _llenar(u: Ubicacion, caso: Caso) -> Ubicacion:
    u.carpeta, u.carpeta_id, u.id_interno = caso.carpeta, caso.id, caso.id_interno
    u.plantilla = plantilla_de(caso)
    return u


def _indices_del_juzgado(clave, asunto: str, nombre_archivo: str) -> list[str]:
    """Los Index que pone el juzgado: el del sello o el aviso (paso 1), el del asunto y
    el del nombre del archivo de la bandeja (que sale de NYSCEF)."""
    salida: list[str] = []
    for indice in ([clave.caso] if clave is not None and clave.caso else []) + \
            indices_en(asunto) + indices_en(nombre_archivo):
        if indice and indice not in salida:
            salida.append(indice)
    return salida


def _por_indice(indices: list[str], por_indice: dict[str, list[Caso]]) -> tuple[str, list[Caso]]:
    for indice in indices:
        if indice in por_indice:
            casos = por_indice[indice]
            activos = [c for c in casos if not c.cerrado]
            return indice, (activos or casos)
    return "", []


def ubicar(carpetas: Sequence[Any], *, tipo: str = "", texto: str = "", clave=None,
           caratula=None, asunto: str = "", nombre_archivo: str = "",
           demandados: Mapping[str, Sequence[str]] | None = None) -> Ubicacion:
    """La carpeta del documento por las reglas de la firma. Ver el docstring del modulo.

    `carpetas`: las carpetas de caso de las DOS bibliotecas de Active Matters (nombres o
    dicts de Graph con name, id, createdDateTime). Mirar solo una da falsos 'no existe'.
    `demandados`: ID interno (o nombre de carpeta) -> demandados del matter en HubSpot.
    """
    por_indice, casos = catalogo(carpetas)
    palabras = set(normalizar(" ".join([asunto, texto])).split())
    del_juzgado = _indices_del_juzgado(clave, asunto, nombre_archivo)
    del_texto = [i for i in indices_del_documento(texto or "") if i not in del_juzgado]
    es_complaint = tipo in COMPLAINTS

    # ---- Regla 1: el Index que pone el juzgado ----------------------------------
    indice, hallados = _por_indice(del_juzgado, por_indice)
    if len(hallados) > 1:
        return Ubicacion(razon=f"{len(hallados)} active folders carry index {indice}",
                         indice=indice, candidatos=[_vista(c) for c in hallados])
    if hallados:
        u = _llenar(Ubicacion("file", indice=indice, match_rule="index", confidence="exact"),
                    hallados[0])
        u.razon = f"index {indice} from the court stamp or notice is in the folder name"
        return u

    # ---- Regla 1 con el Index del texto: solo si el apellido tambien esta ---------
    indice, hallados = _por_indice(del_texto, por_indice)
    if hallados:
        confirmados = [c for c in hallados if apellido_dentro(c, palabras)]
        if len(confirmados) == 1:
            u = _llenar(Ubicacion("file", indice=indice, match_rule="index",
                                  confidence="confirmed"), confirmados[0])
            u.razon = (f"index {indice} in the document text and the client's name "
                       "in the document")
            return u
        if not es_complaint:
            return Ubicacion(indice=indice, candidatos=[_vista(c) for c in hallados], razon=(
                f"index {indice} appears only in the document text and the client's name "
                "does not: it may be a filing reused from another case"
                if not confirmados else f"{len(confirmados)} folders match index {indice}"))

    indice_nuevo = next(iter(del_juzgado + del_texto), "")
    if not es_complaint:
        return Ubicacion(indice=indice_nuevo, razon=(
            f"no folder carries index {indice_nuevo}" if indice_nuevo
            else "the document carries no index number"))

    # ---- Complaint, Regla 2: el nombre del cliente --------------------------------
    # En la caratula y el asunto, no en todo el cuerpo: el cuerpo nombra a terceros, y
    # el apellido de un demandado puede ser el de la carpeta de otro cliente. Todos los
    # apellidos de la carpeta, completos y de 4 letras o mas (como la regla 'cliente'
    # de reglas.py): 'Lee' o 'Rao' salen en cualquier escrito.
    zona = " ".join([asunto, caratula.evidencia if caratula is not None and caratula.hay
                     else (texto or "")[:CARATULA]])
    en_caratula = set(normalizar(zona).split())
    clientes = []
    for c in casos:
        propios = c.apellidos - NO_DISTINGUEN
        if (propios and not c.cerrado and all(len(a) >= LARGO_MINIMO for a in propios)
                and propios <= en_caratula):
            clientes.append(c)
    sin_indice = [c for c in clientes if not c.tiene_indice]
    if not sin_indice:
        razon = ("no open folder matches the client's name" if not clientes else
                 f"the client's {len(clientes)} open folder(s) already carry another index: "
                 "a new case with no folder yet")
        return Ubicacion(indice=indice_nuevo, razon=razon, candidatos=[_vista(c) for c in clientes][:10])
    if len(sin_indice) == 1:
        return _renombrar(_llenar(Ubicacion(match_rule="name", confidence="confirmed"),
                                  sin_indice[0]), sin_indice[0], indice_nuevo,
                          "one folder without index matches the client's name")

    # ---- Complaint, Regla 3: los demandados contra HubSpot --------------------------
    del_complaint = demandados_del_complaint(caratula)
    puntos = []
    for c in sin_indice:
        del_matter = list((demandados or {}).get(c.id_interno)
                          or (demandados or {}).get(c.carpeta) or [])
        puntos.append((puntuar(del_complaint, del_matter), c))
    vista = [_vista(c, score=p) for p, c in puntos]
    if not del_complaint:
        return Ubicacion(indice=indice_nuevo, candidatos=vista, razon=(
            f"{len(sin_indice)} folders of the client without index, and no defendants "
            "were read from the complaint caption"))
    if any(p is None for p, _ in puntos):
        return Ubicacion(indice=indice_nuevo, candidatos=vista, razon=(
            "a candidate matter has no defendants loaded in HubSpot: no evidence to rule "
            "it out"))
    puntos.sort(key=lambda x: -x[0])
    mejor, caso = puntos[0]
    if len(puntos) > 1 and puntos[1][0] == mejor:
        return Ubicacion(indice=indice_nuevo, candidatos=vista,
                         razon=f"tie: {sum(1 for p, _ in puntos if p == mejor)} matters score {mejor}")
    if mejor < MATCH_CON_AVISO:
        return Ubicacion(indice=indice_nuevo, candidatos=vista,
                         razon=f"best defendant overlap is {mejor}, below {MATCH_CON_AVISO}")
    u = _llenar(Ubicacion(match_rule="defendants", confidence="confirmed", puntuacion=mejor,
                          candidatos=vista), caso)
    if mejor < MATCH_PLENO:
        u.aviso = (f"matched by defendants with a partial overlap of {mejor}: "
                   "check the folder before relying on it")
    return _renombrar(u, caso, indice_nuevo,
                      f"defendant overlap {mejor} against {len(sin_indice)} folders of the client")


def _renombrar(u: Ubicacion, caso: Caso, indice: str, razon: str) -> Ubicacion:
    """El complaint pone el Index en la carpeta. Sin Index que poner se archiva igual y
    se dice: el renombrado tendra que esperar a un documento que lo traiga."""
    u.indice = indice
    if indice:
        u.accion, u.renombrar_a = "rename", _nombre_con_indice(caso, indice)
        u.razon = f"{razon}; the folder takes index {indice}"
    else:
        u.accion = "file"
        u.razon = f"{razon}; the complaint carries no index yet, the folder keeps its '- 0'"
        u.aviso = u.aviso or "complaint filed without an index number: folder not renamed"
    return u
