"""Paso 6 del Docs Agent: la seccion del matter que le toca a un documento.

    from lector_ocr.subcarpeta import elegir

    s = elegir("Notice of motion", secciones=["00_Unfiled", "03_Complaint", "09_Motions"],
               id_interno="900123")
    s.carpeta      # '09_Motions'
    s.plantilla    # 'A'
    s.pendiente    # False
    s.razon        # "type 'Notice of motion' goes to 09_Motions in template A"

LAS REGLAS SON LAS DE MARIO (correccion_mario/2026-10-19_filing-rules 1.md, la version
corregida del mismo dia, con cuatro tipos mas), medidas
sobre 42.147 documentos clasificados. Lo que cambian frente a clasificacion.py:

    TRES PLANTILLAS, NO DOS. A (litigio / FCRA), B (foreclosure: cuatro secciones, y
    03_Litigation se queda todo lo que toco un juzgado) y C (QWR: como A con la
    numeracion corrida una posicion, hasta 11_Subpoena). Mas las extendidas de
    octubre: 15_Prior Actions, 16_Bankruptcy, 17_Appeal, 18_Loss Mitigation,
    19_Correspondence, 20_Landlord & Tenant.

    LA PRIMERA QUE EXISTA. Cada tipo da una lista por plantilla ('17_Appeal' y si no
    '09_Motions'); se toma la primera que el matter YA tiene. NUNCA SE CREA UNA
    SECCION: crearlas a ciegas partio 108 matters en octubre y hubo que deshacerlo. Si
    el matter no tiene ninguna, el documento va a 00_Unfiled y a pendientes.

LA SECCION SE RECONOCE POR SU NOMBRE, NO SOLO POR SU NUMERO. En el arbol de Matters
la misma seccion lleva numeros distintos segun el matter: '15_Loss Mitigation' en 82
matters donde la tabla dice 18, '18_Appeal' donde dice 17, '06_Case Management Plan
Initial Conference' donde dice '06_Case Management Plan'. Se prueba primero el nombre
exacto y despues la etiqueta sin el numero. Las secciones SIN numero ('Motions',
'Notices') no cuentan: Mario las da por duplicados que hay que fusionar.

LA PLANTILLA SALE DE LAS SECCIONES QUE TIENE EL MATTER y, si no lo dicen (715 matters
del arbol solo tienen 00_Unfiled y poco mas), del ID interno: 6 foreclosure (B), 7
RESPA/QWR (C), el resto A.

LOS QUE EL TIPO NO SABE COLOCAR (seccion 3 de Mario): 'Letter', 'Servicer
correspondence' y 'Email' van a la seccion de correspondencia si el matter la tiene;
un correo del propio cliente, a lo que el cliente aporta. Si no, a pendientes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from . import clasificacion

SIN_CLASIFICAR = "00_Unfiled"

# --------------------------------------------------------------------------- #
# La tabla (seccion 2 de Mario). Cada tipo: plantilla -> destinos en orden de
# preferencia. [] = esa plantilla no tiene sitio para el tipo ('—' en la tabla).

_A, _B, _C = "A", "B", "C"


def _fila(a: list[str], b: list[str], c: list[str]) -> dict[str, list[str]]:
    return {_A: a, _B: b, _C: c}


_LITIGIO_B = ["03_Litigation"]
_DEMANDA = _fila(["03_Complaint"], _LITIGIO_B, ["02_Complaint"])
_RESPUESTA = _fila(["05_Answers"], _LITIGIO_B, ["04_Answers"])
_NOTIFICACION = _fila(["04_Service of Process"], _LITIGIO_B, ["03_Service of Process"])
_AVISO = _fila(["11_Notices"], _LITIGIO_B, ["10_Notices"])
_MOCION = _fila(["09_Motions"], _LITIGIO_B, ["08_Motions"])
_DISCOVERY = _fila(["07_Discovery"], _LITIGIO_B, ["06_Discovery"])
_DEPOSICION = _fila(["08_Depositions"], _LITIGIO_B, ["07_Depositions"])
_CALENDARIO = _fila(["06_Case Management Plan"], _LITIGIO_B, ["05_Case Management Plan"])
_ACUERDO = _fila(["12_Settlement Documents"], _LITIGIO_B, [])
_ESTIPULACION = _fila(["10_Stipulations"], _LITIGIO_B, ["09_Stipulations"])
_FINANZAS = _fila(["18_Loss Mitigation", "01_Documents from client"], ["02_Loss Mitigation"],
                  ["01_QWR Documents"])
_DEL_CLIENTE = _fila(["01_Documents from client"], ["01_General"], ["01_QWR Documents"])
_CREDITO = _fila(["02_FCRA Dispute"], ["01_General"], ["01_QWR Documents"])
_COBRO = _fila(["14_Billing and Invoices"], ["01_General"], [])

REGLAS: dict[str, dict[str, list[str]]] = {
    # Court filings
    "Summons and complaint": _DEMANDA, "Complaint": _DEMANDA, "Amended complaint": _DEMANDA,
    "Answer": _RESPUESTA, "Verification": _RESPUESTA,
    "Affidavit of service": _NOTIFICACION, "Waiver of service": _NOTIFICACION,
    "Notice of appearance": _AVISO, "Consent to change attorney": _AVISO,
    # Motions
    **{t: _MOCION for t in (
        "Notice of motion", "Summary judgment motion", "Rule 12 motion to dismiss",
        "Opposition to motion to dismiss", "Memorandum of law", "Order to show cause",
        "Pro hac vice motion", "Pre-motion conference letter", "Response to PMC letter",
        "Itemized damages", "Affirmation in support or opposition",
        # Version del 19-oct (M. Mikhaylova). Mario las corrigio a la plantilla B: la
        # peticion decia 'C (foreclosure)', pero C es la de QWR y no tiene 03_Litigation.
        "Declaration", "Reply / Affirmation in reply", "Statement of material facts")},
    # Discovery
    **{t: _DISCOVERY for t in (
        "Discovery demands", "Responses to discovery demands", "Discovery deficiency letter",
        "Rule 26(a)(1) initial disclosures", "Expert witness report",
        # No esta en la tabla de Mario: es la version del demandado de las initial
        # disclosures, y va con ellas.
        "Defendant Rule 26(a)(1)")},
    "Subpoena": _fila(["07_Discovery"], _LITIGIO_B, ["11_Subpoena"]),
    "Notice of deposition": _DEPOSICION, "Deposition transcript": _DEPOSICION,
    # Court output and scheduling
    "Court order": _MOCION, "Notice of entry": _MOCION, "Judgment of foreclosure and sale": _MOCION,
    "Case management plan": _CALENDARIO, "Request for preliminary conference": _CALENDARIO,
    "Adjournment request": _CALENDARIO,
    "Status report": _fila(["13_Status Reports"], _LITIGIO_B, []),
    "Notice of appeal": _fila(["17_Appeal", "09_Motions"], ["17_Appeal", "03_Litigation"],
                              ["17_Appeal", "08_Motions"]),
    # No esta en la tabla de Mario: el aviso de NYSCEF de que un documento esta en el
    # expediente. Es un aviso de tramite, con los demas avisos.
    "Notice of availability (efiling)": _AVISO,
    # Resolution
    "Settlement agreement": _ACUERDO, "Ex-parte settlement letter": _ACUERDO,
    "Notice of settlement": _fila(["12_Settlement Documents"], _LITIGIO_B, ["10_Notices"]),
    "Stipulation": _ESTIPULACION, "Stipulation of dismissal": _ESTIPULACION,
    "Stipulation of extension of time": _ESTIPULACION,
    "Stipulation of adjournment": _ESTIPULACION,        # version del 19-oct
    # PENDIENTE (Mario, 19-oct): 'Affirmation / Affidavit' a mociones. No se construye
    # hasta que digan cual de las tres lecturas es: tomado al pie de la letra moveria
    # 2.637 documentos que no son mociones, entre ellos todo Affidavit of service.
    "Discontinuance": _AVISO, "Notice of dismissal": _AVISO,
    # Client financials
    **{t: _FINANZAS for t in (
        "Mortgage statement", "Bank statement", "Pay stub", "Tax return or W-2",
        "Hardship letter or financial worksheet", "Utility bill", "Insurance")},
    "Identifying document": _DEL_CLIENTE,
    # Credit and FCRA
    "Credit report": _CREDITO, "Dispute letter": _CREDITO,
    # Property records
    "Mortgage, note or ACRIS record": _DEL_CLIENTE, "Deed or title": _DEL_CLIENTE,
    "Closing or title affidavit": _DEL_CLIENTE,
    "Tax lien": _fila(["16_Bankruptcy", "01_Documents from client"], ["01_General"],
                      ["01_QWR Documents"]),
    # Administrative
    "Invoice or fees": _COBRO, "Check or payment receipt": _COBRO,
    "Retainer": _DEL_CLIENTE, "Client notes or statement": _DEL_CLIENTE,
}

# Seccion 3 de Mario: el tipo solo no los coloca.
CORRESPONDENCIA = {"Letter", "Servicer correspondence", "Email"}
SECCIONES_DE_CORRESPONDENCIA = ["19_Correspondence", "04_Correspondence"]

# --------------------------------------------------------------------------- #
# La plantilla de un matter

# Las secciones que solo tiene una plantilla. Las compartidas no dicen nada
# ('07_Discovery' es de A; '06_Discovery' de C).
PROPIAS = {
    _A: {"01_Documents from client", "02_FCRA Dispute", "03_Complaint", "04_Service of Process",
         "05_Answers", "06_Case Management Plan", "07_Discovery", "08_Depositions", "09_Motions",
         "10_Stipulations", "11_Notices", "12_Settlement Documents", "13_Status Reports",
         "14_Billing and Invoices"},
    _B: {"01_General", "02_Loss Mitigation", "03_Litigation", "04_Correspondence"},
    _C: {"01_QWR Documents", "02_Complaint", "03_Service of Process", "04_Answers",
         "05_Case Management Plan", "06_Discovery", "07_Depositions", "08_Motions",
         "09_Stipulations", "10_Notices", "11_Subpoena"},
}
POR_MATERIA = {"6": _B, "7": _C}   # el primer digito del ID interno; el resto, A

NUMERADA = re.compile(r"^(\d{2})_(.+)$")


def _limpia(seccion: str) -> str:
    """Sin espacios duros ni dobles: 'Settlement Documents\\xa0' rompia toda comparacion."""
    return re.sub(r"\s+", " ", str(seccion).replace("\xa0", " ")).strip()


def _etiqueta(seccion: str) -> str:
    """'15_Loss Mitigation' -> 'loss mitigation'."""
    s = _limpia(seccion)
    m = NUMERADA.match(s)
    return (m.group(2) if m else s).strip().lower()


def _es(seccion: str, propia: str) -> bool:
    """La seccion es esa de la tabla: mismo numero y el nombre empieza igual
    ('06_Case Management Plan Initial Conference' es '06_Case Management Plan')."""
    return seccion == propia or (seccion[:3] == propia[:3] and (
        _etiqueta(seccion) == _etiqueta(propia)
        or _etiqueta(seccion).startswith(_etiqueta(propia) + " ")))


def plantilla_del_matter(secciones: Sequence[str] | None, id_interno: str = "") -> tuple[str, str]:
    """(plantilla, por que). Por las secciones propias que tiene; si no lo dicen, por
    el ID interno."""
    if secciones:
        limpias = [_limpia(s) for s in secciones if NUMERADA.match(_limpia(s))]
        puntos = {p: sum(1 for s in limpias if any(_es(s, propia) for propia in propias))
                  for p, propias in PROPIAS.items()}
        mejor = max(puntos.values())
        ganadoras = [p for p, n in puntos.items() if n == mejor]
        if mejor and len(ganadoras) == 1:
            return ganadoras[0], f"the matter has {mejor} section(s) of template {ganadoras[0]}"
    if id_interno:
        p = POR_MATERIA.get(id_interno[:1], _A)
        return p, f"the sections do not tell; internal id {id_interno} starts with {id_interno[:1]}"
    return _A, "no sections and no internal id: template A by default"


# --------------------------------------------------------------------------- #
# Elegir


@dataclass
class Subcarpeta:
    carpeta: str = SIN_CLASIFICAR
    plantilla: str = ""
    pendiente: bool = True
    existe: bool | None = None      # None: no se sabe que secciones tiene el matter
    razon: str = ""

    def campos(self) -> dict:
        return {"section_template": self.plantilla or None, "goes_to": self.carpeta,
                "goes_to_pending": self.pendiente, "goes_to_exists": self.existe,
                "goes_to_because": self.razon}


def _existente(destino: str, secciones: Sequence[str]) -> str:
    """La seccion del matter que ES ese destino: por nombre exacto o por etiqueta.
    '' si no la tiene."""
    limpias = {_limpia(s): s for s in secciones}
    if destino in limpias:
        return limpias[destino]
    buscada = _etiqueta(destino)
    for limpia, original in limpias.items():
        if not NUMERADA.match(limpia):
            continue            # 'Motions' sin numero: duplicado por fusionar, no destino
        etiqueta = _etiqueta(limpia)
        # '06_Case Management Plan Initial Conference' es '06_Case Management Plan'.
        if etiqueta == buscada or etiqueta.startswith(buscada + " "):
            return original
    return ""


def elegir(tipo: str, secciones: Sequence[str] | None = None, *, id_interno: str = "",
           del_cliente: bool = False) -> Subcarpeta:
    """La seccion para un documento de `tipo` en un matter con esas `secciones`.

    `secciones`: las subcarpetas de primer nivel del matter, TODAS (tambien las vacias).
    None si no se conocen: entonces se propone el primer destino de la tabla y se dice
    que no esta comprobado (`existe=None`).
    `del_cliente`: el documento lo mando el propio cliente (para los correos).
    """
    plantilla, por_que = plantilla_del_matter(secciones, id_interno)
    sale = Subcarpeta(plantilla=plantilla)

    if tipo in CORRESPONDENCIA:
        opciones = (REGLAS["Retainer"][plantilla] if tipo == "Email" and del_cliente
                    else SECCIONES_DE_CORRESPONDENCIA)
        motivo = ("an email from the client goes with what the client provides"
                  if tipo == "Email" and del_cliente
                  else f"'{tipo}' cannot be placed by type alone: the correspondence section")
    elif tipo in REGLAS:
        opciones = REGLAS[tipo][plantilla]
        motivo = f"type '{tipo}' in template {plantilla}"
    else:
        sale.razon = (f"unknown document type: {SIN_CLASIFICAR} and pending" if not tipo
                      else f"type '{tipo}' has no filing rule: {SIN_CLASIFICAR} and pending")
        sale.existe = _hay(SIN_CLASIFICAR, secciones)
        return sale

    if not opciones:
        sale.razon = (f"{motivo} has no home (— in the rules): {SIN_CLASIFICAR} and pending")
        sale.existe = _hay(SIN_CLASIFICAR, secciones)
        return sale

    if secciones is None:
        sale.carpeta, sale.pendiente, sale.existe = opciones[0], False, None
        sale.razon = f"{motivo} goes to {opciones[0]} (the matter's sections were not checked)"
        return sale

    for destino in opciones:
        real = _existente(destino, secciones)
        if real:
            sale.carpeta, sale.pendiente, sale.existe = real, False, True
            sale.razon = (f"{motivo} goes to {destino}"
                          + (f", which this matter calls '{real}'" if real != destino else "")
                          + f" ({por_que})")
            return sale

    sale.razon = (f"{motivo} would go to {' or '.join(opciones)}, and the matter has none: "
                  f"sections are never created, so {SIN_CLASIFICAR} and pending ({por_que})")
    sale.existe = _hay(SIN_CLASIFICAR, secciones)
    return sale


def _hay(seccion: str, secciones: Sequence[str] | None) -> bool | None:
    return None if secciones is None else bool(_existente(seccion, secciones))


def revisar() -> list[str]:
    """Todo tipo de la tabla tiene que existir en el clasificador: un nombre torcido aqui
    no falla, deja ese tipo sin regla y todo lo suyo acaba en pendientes."""
    nombres = {t.nombre for t in clasificacion.TIPOS}
    return [f"'{t}' is not a type of clasificacion.py" for t in REGLAS if t not in nombres]
