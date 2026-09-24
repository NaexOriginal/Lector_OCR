r"""Que ES cada documento y a que subcarpeta de la plantilla le toca ir.

`equivalencias.py` responde por el nombre de la CARPETA: 'LITIGATION' va a
'03_Litigation'. Eso sirve cuando la carpeta vieja ya se parecia a la plantilla.
Aqui se responde por el CONTENIDO del ARCHIVO, que es lo unico que queda cuando la
carpeta no dice nada -- o cuando el archivo llega suelto, sin carpeta ninguna, que
es el caso de los documentos que entran al despacho.

    equivalencias.py    carpeta -> carpeta      (mudanza por estructura)
    clasificacion.py    documento -> carpeta    (mudanza por lectura)

Las dos tablas se leen igual y tienen las mismas dos columnas, porque el destino
DEPENDE DE LA PLANTILLA: una mocion va a '03_Litigation/Motions' en un foreclosure y
a '09_Motions' en un FCRA. None = esa plantilla no tiene sitio para esto, y entonces
el archivo NO se coloca: se queda donde esta y se anota. Inventarle una carpeta es
peor que dejarlo quieto -- quien busque la mocion en 09_Motions y no la encuentre va
a concluir que no existe.

EL ORDEN DE LA LISTA ES LA REGLA DE DESEMPATE. Va de lo especifico a lo generico y
gana el primero que casa, porque 'amended complaint' tambien contiene 'complaint' y
'notice of deposition' tambien contiene 'notice'. Al agregar un tipo nuevo hay que
ponerlo ARRIBA del tipo generico que lo contiene.

DONDE aparece la frase importa tanto como que aparezca. En la cabecera del documento
es el titulo; trescientas lineas mas abajo es una mencion de pasada -- una demanda
nombra la mocion de la contraparte sin ser una mocion. Por eso `tipo_de_texto`
devuelve tambien si fue titulo o mencion, y quien decide da mas peso al titulo.

Uso:
    .venv\Scripts\python.exe clasificacion.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Los primeros caracteres del texto extraido. La caratula de un escrito judicial y el
# membrete de una carta caben de sobra; una mencion de pasada, no.
CABECERA = 900

# La ventana del TITULO. Medida sobre los 314 documentos con texto del lote de control:
# con 250 no alcanza y con 350 empieza a romper clasificaciones buenas.
TITULO = 300


@dataclass(frozen=True)
class Tipo:
    """Un tipo de documento y adonde va en cada plantilla."""

    nombre: str
    patron: re.Pattern
    foreclosure: str | None
    fcra: str | None
    certeza: str
    en_nombre: re.Pattern | None = None  # si el nombre del archivo lo delata

    # 'Letter' y 'Email' dicen el MEDIO, no lo que el documento hace: un reporte de
    # credito mandado por carta sigue siendo un reporte de credito. Se marcan para
    # que no ganen en la ventana del titulo, donde a veces son lo unico que ha
    # casado todavia -- 'TransUnion.pdf' empieza con un 'Dear' de membrete y por eso
    # salia como 'Letter' en vez de 'Credit report'.
    generico: bool = False

    def destino(self, plantilla: str) -> str | None:
        return self.fcra if plantilla == "FCRA" else self.foreclosure


def _p(expresion: str) -> re.Pattern:
    return re.compile(expresion, re.I | re.M)


# --------------------------------------------------------------------------- #
# De lo mas especifico a lo mas generico. El primero que casa manda.
TIPOS: list[Tipo] = [
    # --- escritos de la fase Rule 12, lo mas anidado de la plantilla FCRA -----
    Tipo("Response to PMC letter",
         _p(r"response to .{0,25}pre-?motion conference|response .{0,20}pmc letter"),
         "03_Litigation/Motions",
         "09_Motions/Rule 12/Plaintiff Response to Rule 12 PMC Letter", "proposed"),
    Tipo("Pre-motion conference letter",
         _p(r"pre-?motion conference (letter|request)|request .{0,25}pre-?motion conference"),
         "03_Litigation/Motions",
         "09_Motions/Rule 12/Defendants Rule 12 PMC Letter", "proposed"),
    Tipo("Opposition to motion to dismiss",
         _p(r"(opposition|response) to .{0,40}motion to dismiss"
            r"|memorandum of law in opposition"),
         "03_Litigation/Motions",
         "09_Motions/Rule 12/Plaintiff Response to 12 Motion", "proposed"),
    Tipo("Rule 12 motion to dismiss",
         _p(r"rule 12\s*\(\s*b\s*\)|motion to dismiss the (amended )?complaint|12\(b\)\(6\)"),
         "03_Litigation/Motions",
         "09_Motions/Rule 12/Defendants Rule 12 Motion", "proposed"),
    Tipo("Pro hac vice motion",
         _p(r"pro hac vice"),
         "03_Litigation/Motions", "09_Motions/Pro Hac Vice Motions", "clear"),
    Tipo("Adjournment request",
         _p(r"request .{0,25}adjourn|letter motion .{0,25}adjourn|adjournment request"),
         "03_Litigation/Motions", "09_Motions/Adjournment Requests", "proposed"),

    # --- demanda y contestacion ----------------------------------------------
    Tipo("Amended complaint",
         _p(r"amended (verified )?complaint"),
         "03_Litigation/Pleadings", "03_Complaint/Amended Complaint", "clear",
         en_nombre=_p(r"amended complaint|\bamd\.? ?compl")),
    Tipo("Summons and complaint",
         _p(r"summons (and|&) (verified )?complaint|you are hereby summoned"),
         "03_Litigation/Pleadings", "03_Complaint", "clear",
         en_nombre=_p(r"summons|\bs ?& ?c\b")),
    # La conferencia preliminar del estado de Nueva York, que es el equivalente de
    # la conferencia inicial federal: se pide con este formulario y con el RJI. Va
    # DELANTE de la demanda a proposito -- el formulario dice 'the nature of this
    # action is' y el patron de la demanda busca 'nature of the action', o sea que
    # los separa una palabra. Depender de eso seria depender de la suerte.
    Tipo("Request for preliminary conference",
         _p(r"request for (a )?preliminary conference|requests? a preliminary conference"
            r"|request for judicial intervention|\brji\b"),
         "03_Litigation", "06_Case Management Plan Initial Conference", "clear",
         en_nombre=_p(r"prelim\w* conf|\brji\b|request for judicial")),

    # 'jury trial demanded' y 'preliminary statement' van sueltos porque en una
    # demanda federal la palabra 'complaint' aparece en el pie de la caratula, a
    # veces mas alla de los 900 caracteres que se miran, y entonces no casaba nada.
    Tipo("Complaint",
         _p(r"verified complaint|nature of the action|preliminary statement"
            r"|jury trial demanded|demand for a jury trial"
            r"|complaint.{0,40}(jury trial demanded|demand for a jury)"),
         "03_Litigation/Pleadings", "03_Complaint", "clear",
         en_nombre=_p(r"\bcomplaint\b|\bcompl\b")),
    Tipo("Answer",
         _p(r"answer and affirmative defenses|answering the (verified )?complaint"
            r"|answer to the (verified |amended )?complaint"),
         "03_Litigation/Pleadings", "05_Answers", "clear",
         en_nombre=_p(r"\banswer\b")),

    # --- notificacion ---------------------------------------------------------
    # La verificacion va cosida a un escrito y viaja con el, no sola.
    Tipo("Verification",
         _p(r"\bverification\b.{0,60}(sworn|deposes)|individual verification"),
         "03_Litigation/Pleadings", "03_Complaint", "proposed",
         en_nombre=_p(r"\bverification\b")),
    # Consent to Change Attorney: cambia quien lleva el caso y se presenta al juzgado.
    Tipo("Consent to change attorney",
         _p(r"consent to change attorney"),
         "03_Litigation/Pleadings", "11_Notices", "clear",
         en_nombre=_p(r"consent to change attorney|\bctca\b")),
    Tipo("Affidavit of service",
         _p(r"affidavit of service|proof of service|certificate of service"
            r"|deponent .{0,30}served"),
         "03_Litigation/Pleadings", "04_Service of Process", "clear",
         en_nombre=_p(r"(affidavit|affirmation|proof|certificate) of service|\baos\b")),
    Tipo("Waiver of service",
         _p(r"waiver of (the )?service of summons"),
         "03_Litigation/Pleadings", "04_Service of Process", "clear"),

    # --- avisos ---------------------------------------------------------------
    Tipo("Subpoena",
         _p(r"\bsubpoena\b"),
         "03_Litigation/Discovery", "07_Discovery", "clear",
         en_nombre=_p(r"\bsubpoena\b")),
    Tipo("Notice of deposition",
         _p(r"notice (to take|of) deposition|deposition upon oral examination"),
         "03_Litigation/Discovery", "08_Depositions/Notices of Deposition", "clear",
         en_nombre=_p(r"\bdeposition\b|\bebt\b")),
    # Aviso de NYSCEF, no un escrito de parte. Es tramite del expediente.
    Tipo("Notice of availability (efiling)",
         _p(r"notice of availability"),
         "03_Litigation", "11_Notices", "proposed",
         en_nombre=_p(r"notice of availability")),
    Tipo("Notice of entry",
         _p(r"notice of entry"),
         "03_Litigation/Orders", "11_Notices", "clear",
         en_nombre=_p(r"notice of entry|\bnoe\b")),
    Tipo("Notice of appearance",
         _p(r"notice of appearance"),
         "03_Litigation", "11_Notices/Notice of Appearance", "clear",
         en_nombre=_p(r"notice of appearance|\bnoa\b")),
    Tipo("Notice of appeal",
         _p(r"notice of appeal"),
         "03_Litigation", "11_Notices/Notice of Appeal", "clear",
         en_nombre=_p(r"notice of appeal")),
    Tipo("Discontinuance",
         _p(r"\bdiscontinuance\b|stipulation of discontinuance"),
         "03_Litigation", "11_Notices/Notice of Dismissal", "proposed",
         en_nombre=_p(r"\bdiscontinuance\b|\bdisc\b")),
    Tipo("Notice of dismissal",
         _p(r"notice of (voluntary )?dismissal"),
         "03_Litigation", "11_Notices/Notice of Dismissal", "clear"),
    Tipo("Notice of settlement",
         _p(r"notice of settlement"),
         "03_Litigation", "11_Notices/Notice of Settlement", "clear",
         en_nombre=_p(r"notice of settlement")),

    # --- estipulaciones -------------------------------------------------------
    Tipo("Stipulation of extension of time",
         _p(r"stipulation .{0,30}extension of time|extend .{0,25}time to (answer|respond)"),
         "03_Litigation", "10_Stipulations/Extension of Time", "clear",
         en_nombre=_p(r"stip.{0,20}extension|extension of time")),
    Tipo("Stipulation of dismissal",
         _p(r"stipulation of (voluntary )?dismissal|rule 41\s*\(\s*a\s*\)"),
         "03_Litigation", "10_Stipulations/Voluntary Dismissal", "clear",
         en_nombre=_p(r"stip.{0,20}dismissal")),
    Tipo("Stipulation",
         _p(r"it is hereby stipulated and agreed|\bstipulation\b"),
         "03_Litigation", "10_Stipulations", "clear",
         en_nombre=_p(r"\bstip(ulation)?\b")),

    # --- conferencia inicial y descubrimiento ---------------------------------
    Tipo("Defendant Rule 26(a)(1)",
         _p(r"defendant'?s?.{0,25}rule 26\s*\(\s*a\s*\)\s*\(\s*1\s*\)"),
         "03_Litigation/Discovery",
         "06_Case Management Plan Initial Conference/Rule 26(a)(1)/Defendant Rule 26(a)(1)",
         "proposed"),
    Tipo("Rule 26(a)(1) initial disclosures",
         _p(r"rule 26\s*\(\s*a\s*\)\s*\(\s*1\s*\)|initial disclosures"),
         "03_Litigation/Discovery",
         "06_Case Management Plan Initial Conference/Rule 26(a)(1)", "clear"),
    Tipo("Case management plan",
         _p(r"civil case management plan|case management plan and scheduling order"
            r"|initial pretrial conference"),
         "03_Litigation", "06_Case Management Plan Initial Conference", "clear",
         en_nombre=_p(r"case management|\bcmp\b|initial conference")),
    Tipo("Discovery deficiency letter",
         _p(r"deficiency letter|deficien(t|cies) .{0,30}(response|discovery)"),
         "03_Litigation/Discovery", "07_Discovery/Deficiency Letters", "proposed"),
    Tipo("Expert witness report",
         _p(r"expert (witness )?report|rule 26\s*\(\s*a\s*\)\s*\(\s*2\s*\)"),
         "03_Litigation/Discovery", "07_Discovery", "proposed"),
    Tipo("Responses to discovery demands",
         _p(r"responses? (and objections )?to .{0,35}"
            r"(demands?|interrogatories|requests? for)"),
         "03_Litigation/Discovery", "07_Discovery", "clear"),
    Tipo("Discovery demands",
         _p(r"\binterrogator(y|ies)\b|requests? for (the )?production of documents"
            r"|notice (to produce|for discovery)|demand for a bill of particulars"
            r"|requests? for admission"),
         "03_Litigation/Discovery", "07_Discovery", "clear",
         en_nombre=_p(r"\bdiscovery\b|interrogator|demand for|bill of particulars")),

    # --- resoluciones del juez ------------------------------------------------
    # La sentencia de ejecucion, delante de la orden generica: las dos acaban en
    # Orders, pero la etiqueta es lo que lee una persona en el informe y 'judgment
    # of foreclosure and sale' dice mucho mas que 'court order'. Ponerla antes es
    # seguro porque su patron no casa con 'summary judgment motion', que era de
    # quien habia que cuidarla.
    Tipo("Judgment of foreclosure and sale",
         _p(r"judgment of foreclosure( and sale)?"),
         "03_Litigation/Orders", "11_Notices", "clear",
         en_nombre=_p(r"judgment of foreclosure|\bjfs\b")),
    # La orden to show cause va ANTES que la orden a secas y no va con las ordenes:
    # equivalencias.py ya mandaba la carpeta 'OSC' a Motions, porque una OSC es una
    # peticion al juez y no una resolucion suya. Si las dos tablas dijeran cosas
    # distintas, el mismo papel acabaria en un sitio u otro segun por que ruta
    # entrase, y nadie sabria a cual creerle.
    Tipo("Order to show cause",
         _p(r"\border to show cause\b"),
         "03_Litigation/Motions", "09_Motions", "proposed",
         en_nombre=_p(r"\bosc\b|show cause")),
    Tipo("Court order",
         _p(r"it is (hereby )?ordered|so ordered|decision (and|&) order"),
         "03_Litigation/Orders", "11_Notices", "proposed",
         en_nombre=_p(r"\border\b|\bdecision\b")),

    # --- mociones genericas (van despues de todas las especificas) ------------
    Tipo("Summary judgment motion",
         _p(r"summary judgment"),
         "03_Litigation/Motions", "09_Motions", "clear",
         en_nombre=_p(r"\bmsj\b|summary judgment")),
    Tipo("Memorandum of law",
         _p(r"memorandum of law"),
         "03_Litigation/Motions", "09_Motions", "clear",
         en_nombre=_p(r"memorandum of law|\bmol\b")),
    Tipo("Deposition transcript",
         _p(r"transcript of .{0,30}(deposition|examination|proceedings)"
            r"|examination before trial"),
         "03_Litigation/Discovery", "08_Depositions", "proposed",
         en_nombre=_p(r"transcript")),
    Tipo("Notice of motion",
         _p(r"notice of motion|upon the annexed (affirmation|affidavit)"),
         "03_Litigation/Motions", "09_Motions", "clear",
         en_nombre=_p(r"notice of motion|\bmotion\b|\bmsj\b|summary judgment")),
    # Papeles de CIERRE, no de pleito. Van delante de la affirmation generica porque
    # se llaman 'affidavit' y ese patron se los llevaba a Motions: un 'Affidavit of
    # Compliance with Smoke Detector Requirement' se firma al transferir la
    # escritura y no tiene nada que ver con una mocion. Aqui el titulo lo dice todo,
    # solo habia que mirarlo antes de quedarse con la palabra 'affidavit'.
    Tipo("Closing or title affidavit",
         _p(r"affidavit of compliance|smoke detector requirement"
            r"|carbon monoxide (alarm|detector)|real property transfer report"
            r"|\brp-?5217\b|\btp-?584\b|affidavit of title"),
         "01_General", None, "clear",
         en_nombre=_p(r"affidavit of compliance|smoke detector|affidavit of title"
                      r"|\brp-?5217\b|\btp-?584\b")),
    Tipo("Affirmation in support or opposition",
         _p(r"affirmation in (support|opposition)|affidavit in (support|opposition)"),
         "03_Litigation/Motions", "09_Motions", "proposed",
         en_nombre=_p(r"affirmation|affidavit|\baff\b|\breply\b")),

    # --- el paquete FCRA -------------------------------------------------------
    # El nombre de los buros NO puede estar en el patron de contenido: toda demanda
    # FCRA nombra a Experian, TransUnion y Equifax porque son los demandados, y con
    # eso una demanda entera se clasificaba como reporte de credito. En el nombre
    # del ARCHIVO si vale -- 'Experian.pdf' es un reporte -- porque ahi el buro es
    # de lo que trata el papel y no una parte del pleito.
    Tipo("Credit report",
         _p(r"\bcredit report\b|credit (report|file) (disclosure|number)"
            r"|personal credit report"),
         "01_General", "02_FCRA Dispute/Credit Report", "clear",
         en_nombre=_p(r"credit report|experian|transunion|equifax")),
    Tipo("Dispute letter",
         _p(r"i am writing to dispute|15 u\.?s\.?c\.?\s*.{0,6}1681"
            r"|fair credit reporting act|dispute letter"),
         "01_General", "02_FCRA Dispute/Dispute Letters", "clear"),
    Tipo("Identifying document",
         _p(r"driver'?s? licen[sc]e|social security (card|number)|\bpassport\b"
            r"|identification card"),
         "01_General", "02_FCRA Dispute/Identifying Documents", "proposed",
         en_nombre=_p(r"licen[sc]e|passport|\bssn\b|social security|\bid card\b")),

    # --- el paquete de loss mitigation ----------------------------------------
    # El INSTRUMENTO hipotecario, no el estado de cuenta mensual: la nota, la
    # hipoteca inscrita y lo que se baja de ACRIS. Va delante del estado de cuenta
    # porque los dos hablan de principal y de prestamo, y el instrumento es lo
    # especifico. En un foreclosure es la prueba central del caso.
    Tipo("Mortgage, note or ACRIS record",
         _p(r"\bacris\b|promissory note|note and mortgage"
            r"|mortgage.{0,40}(recorded|dated)"),
         "01_General", None, "clear",
         en_nombre=_p(r"\bacris\b|\bmtge\b|note and mortgage")),
    Tipo("Mortgage statement",
         _p(r"mortgage statement|principal balance|escrow (account|balance)|loan number"),
         "02_Loss Mitigation/Mortgage Statements", None, "clear",
         en_nombre=_p(r"mortgage (stmt|statement)")),
    Tipo("Bank statement",
         _p(r"beginning balance|ending balance|account summary|statement period"),
         "02_Loss Mitigation/Bank Statements", None, "clear",
         en_nombre=_p(r"bank (stmt|statement)")),
    Tipo("Pay stub",
         _p(r"earnings statement|gross pay|net pay|\bpay ?stub\b|year[- ]to[- ]date"),
         "02_Loss Mitigation/Pay Stubs", None, "clear",
         en_nombre=_p(r"pay ?stub|payroll")),
    Tipo("Tax return or W-2",
         _p(r"form 1040|individual income tax return|wage and tax statement|\bw-?2\b"),
         "02_Loss Mitigation/Taxes", None, "clear",
         en_nombre=_p(r"\b1040\b|\bw-?2\b|tax return")),
    Tipo("Utility bill",
         _p(r"con ?edison|national grid|\bpseg\b|water (bill|board)|utility bill"
            r"|service address"),
         "02_Loss Mitigation/Utility Bills", None, "clear",
         en_nombre=_p(r"utility|con ?ed\b|national grid|water bill")),
    Tipo("Hardship letter or financial worksheet",
         _p(r"hardship (letter|affidavit)|financial worksheet"
            r"|request for mortgage assistance|loss mitigation application"),
         "02_Loss Mitigation", None, "clear",
         en_nombre=_p(r"hardship|financial worksheet|\brma\b")),

    # --- acuerdo y cierre ------------------------------------------------------
    Tipo("Ex-parte settlement letter",
         _p(r"ex[- ]?parte .{0,25}settlement"),
         "01_General", "12_Settlement Documents/Ex-Parte Settlement Letter", "proposed"),
    Tipo("Itemized damages",
         _p(r"itemized damages|damages calculation"),
         "01_General", "12_Settlement Documents/Itemized Damages to Defendants", "proposed"),
    Tipo("Settlement agreement",
         _p(r"settlement agreement|general release|release and settlement"),
         "01_General", "12_Settlement Documents", "clear",
         en_nombre=_p(r"settlement agreement|\brelease\b")),
    Tipo("Status report",
         _p(r"status report|joint status letter"),
         "01_General", "13_Status Reports", "clear",
         en_nombre=_p(r"status report")),

    # --- administracion --------------------------------------------------------
    Tipo("Invoice or fees",
         _p(r"\binvoice\b|billing statement|fee agreement"),
         "01_General", "14_Billing and Invoices", "clear",
         en_nombre=_p(r"invoice|billing")),
    # UN RESUMEN DEL CASO NO ES NINGUNA DE LAS PIEZAS QUE MENCIONA, y por el nombre
    # se atrapa antes de leerlo, que es lo unico que funciona: por dentro casa con
    # todo. Un 'Case Overview' que dice '...First Amended Complaint drafted' se
    # clasificaba como demanda enmendada; un 'Timeline' que narra la comparecencia,
    # como notice of appearance. Se archivaban dentro de una fase a la que no
    # pertenecen, que es la peor forma de perder un documento.
    Tipo("Client notes or statement",
         _p(r"notes from (the )?client|client statement"),
         "01_General", "01_Documents from client", "proposed",
         en_nombre=_p(r"notes from client|client statement|case summary|"
                      r"case[ _-]?overview|\btimeline\b|\bchronology\b")),
    # Seguro de la vivienda: HOI, force-placed, hazard. Faltaba en la tabla y sus
    # documentos casaban con 'Mortgage statement' porque llevan 'Loan Number:'
    # impreso igual que un estado hipotecario. Van a Loss Mitigation, pero NO a la
    # subcarpeta de estados: son cosas distintas.
    Tipo("Insurance",
         _p(r"\bhomeowners?\b.{0,20}insurance|force[- ]?placed|hazard insurance|"
            r"\bdeclarations? page\b|insurance policy"),
         "02_Loss Mitigation", "01_Documents from client", "proposed",
         en_nombre=_p(r"\bhoi\b|force[- ]?placed|\bhazard\b|prop\.? ?ins\b|"
                      r"\binsurance\b")),
    # Un pago suelto: cheque, giro, comprobante. En foreclosure no hay carpeta de
    # facturacion, asi que va al cajon general.
    Tipo("Check or payment receipt",
         _p(r"\bmoney order\b|payment receipt|\bremittance\b"),
         "01_General", "14_Billing and Invoices", "proposed",
         en_nombre=_p(r"\bchecks?\b|\breceipts?\b|money order")),
    Tipo("Tax lien",
         _p(r"\btax lien\b|notice of federal tax lien"),
         "01_General", None, "clear", en_nombre=_p(r"tax lien")),
    Tipo("Retainer",
         _p(r"retainer agreement|attorney[- ]client (agreement|retainer)"),
         "01_General", "01_Documents from client", "clear",
         en_nombre=_p(r"retainer")),
    Tipo("Deed or title",
         _p(r"\b(bargain and sale|quitclaim|warranty) deed\b|grantor.{0,25}grantee"),
         "01_General", None, "clear", en_nombre=_p(r"\bdeed\b")),

    # --- lo mas generico de todo ----------------------------------------------
    # La plantilla FCRA no tiene carpeta de correspondencia. No se le inventa: se
    # devuelve None, el archivo se queda donde esta y queda anotado para que
    # alguien del despacho diga donde va.
    Tipo("Email",
         _p(r"^\s*(from|subject)\s*:"),
         "04_Correspondence", None, "clear",
         en_nombre=_p(r"\.msg$|^re[: ]|^fwd?[: ]"),
         generico=True),
    # Carta del servicer del prestamo. Se reconoce por el nombre de la empresa
    # porque estas cartas se archivan como 'NewRez 07.17.25.pdf': la fecha y el
    # servicer, sin decir nunca que son. La lista es la de los servicers que operan
    # en Nueva York, no una invencion.
    Tipo("Servicer correspondence",
         _p(r"\b(newrez|shellpoint|ocwen|nationstar|mr\.? cooper|select portfolio"
            r"|specialized loan servicing|rushmore|carrington|loancare|cenlar"
            r"|flagstar|freedom mortgage|fay servicing|roundpoint)\b"),
         "04_Correspondence", None, "proposed"),
    Tipo("Letter",
         _p(r"^\s*dear\b|\bsincerely,|very truly yours"),
         "04_Correspondence", None, "proposed",
         en_nombre=_p(r"\bletter\b|\bltr\b|correspondence"),
         generico=True),
]


# Que HACE cada tipo de documento, en ingles y en una frase. Es lo que hay que
# contestarle a alguien que pregunta 'de que va este papel' y no quiere que le
# repitan su contenido.
#
# Vive aqui y no en el informe que lo usa porque es una propiedad del tipo, igual
# que su destino: si manana se agrega un tipo nuevo, las dos cosas se escriben en
# el mismo sitio y no hay forma de que una quede a medias.
QUE_HACE: dict[str, str] = {
    'Response to PMC letter':
        "Plaintiff answers the defendant's pre-motion conference letter, arguing why the proposed motion to dismiss should not be allowed.",
    'Pre-motion conference letter':
        'Defendant asks the judge for permission to file a motion to dismiss and outlines the grounds.',
    'Opposition to motion to dismiss':
        'Plaintiff argues against the motion to dismiss and asks the court to let the case proceed.',
    'Rule 12 motion to dismiss':
        'Defendant asks the court to throw out the complaint before answering it.',
    'Pro hac vice motion':
        'An out-of-state attorney asks to be admitted to appear in this one case.',
    'Adjournment request':
        'A party asks the court to push back a deadline or a scheduled appearance.',
    'Amended complaint':
        'A corrected or expanded version of the complaint that replaces the original.',
    'Summons and complaint':
        'The paper that starts the lawsuit: it notifies the defendant and states the claims.',
    'Request for preliminary conference':
        'A party asks the state court to schedule the first conference and set the discovery calendar.',
    'Complaint':
        'States the claims and the facts the plaintiff intends to prove.',
    'Answer':
        "The defendant's formal response to the complaint, admitting or denying each allegation and raising defenses.",
    'Verification':
        'A sworn statement confirming the truth of a pleading. It travels attached to the pleading it verifies.',
    'Consent to change attorney':
        'Records that the client is changing counsel and who now represents them.',
    'Affidavit of service':
        'Sworn proof that a paper was delivered to the other side, and how.',
    'Waiver of service':
        'The defendant agrees to accept the summons without formal service.',
    'Subpoena':
        'Orders a person or company to produce documents or appear to testify.',
    'Notice of deposition':
        'Tells the other side when and where a witness will be examined under oath.',
    'Notice of availability (efiling)':
        'A NYSCEF housekeeping notice that a document is available in the electronic file.',
    'Notice of entry':
        'Notifies the parties that the court has entered an order, which starts the appeal clock.',
    'Notice of appearance':
        'An attorney tells the court and the parties that they now represent someone in the case.',
    'Notice of appeal':
        'Starts an appeal of a decision to a higher court.',
    'Discontinuance':
        'Ends the case, or ends it as to one party, without a trial.',
    'Notice of dismissal':
        'Notifies that the case, or a claim in it, has been dismissed.',
    'Notice of settlement':
        'Notifies the court and the parties that the matter has been settled.',
    'Stipulation of extension of time':
        'Both sides agree to give a party more time to answer or respond.',
    'Stipulation of dismissal':
        'Both sides agree to end the case, usually as part of a settlement.',
    'Stipulation':
        'A written agreement between the parties on a procedural point, filed with the court.',
    'Defendant Rule 26(a)(1)':
        "The defendant's initial disclosures: witnesses, documents and damages it may rely on.",
    'Rule 26(a)(1) initial disclosures':
        'The required opening exchange of witnesses, documents and damages before discovery begins.',
    'Case management plan':
        'The schedule the court sets for discovery, motions and trial.',
    'Discovery deficiency letter':
        'One side tells the other that its discovery responses are incomplete and demands the rest.',
    'Expert witness report':
        "An expert's written opinion and the basis for it, exchanged before trial.",
    'Responses to discovery demands':
        "Answers and objections to the other side's document requests or interrogatories.",
    'Discovery demands':
        'Requests for documents, written questions or admissions directed at the other side.',
    'Judgment of foreclosure and sale':
        "The court's decision allowing the property to be sold to satisfy the mortgage debt.",
    'Order to show cause':
        'An urgent application asking the judge to order the other side to justify why relief should not be granted.',
    'Court order':
        "The judge's ruling on an application, and what the parties must now do.",
    'Summary judgment motion':
        'Asks the court to decide the case, or part of it, without a trial because the facts are not in dispute.',
    'Memorandum of law':
        'The legal argument supporting or opposing a motion, with the authorities relied on.',
    'Deposition transcript':
        'The word-for-word record of a witness examined under oath.',
    'Notice of motion':
        'The cover paper that formally puts a motion before the court and states the relief sought.',
    'Closing or title affidavit':
        'A sworn statement given at a property closing, such as compliance with the smoke detector requirement.',
    'Affirmation in support or opposition':
        "An attorney's sworn statement of the facts backing or opposing a motion.",
    'Credit report':
        'The consumer file held by a credit bureau, and the entries the dispute is about.',
    'Dispute letter':
        'The consumer tells a bureau or furnisher that an entry is wrong and asks for it to be corrected.',
    'Identifying document':
        'Proof of identity supplied by the client, such as a licence, passport or social security card.',
    'Mortgage, note or ACRIS record':
        'The loan instrument itself or its recorded copy. In a foreclosure it is the central piece of evidence.',
    'Mortgage statement':
        "The servicer's periodic statement of what is owed on the loan.",
    'Bank statement':
        "The client's account activity, used to document income and hardship.",
    'Pay stub':
        "Proof of the client's earnings, used in a loss mitigation package.",
    'Tax return or W-2':
        "The client's declared income, used in a loss mitigation package.",
    'Utility bill':
        'A household bill, used to document occupancy or expenses.',
    'Hardship letter or financial worksheet':
        "The client's account of why they fell behind and what they can afford to pay.",
    'Ex-parte settlement letter':
        'A settlement communication addressed to the court outside the presence of the other side.',
    'Itemized damages':
        'A breakdown of the amounts being claimed and how each was calculated.',
    'Settlement agreement':
        'The terms on which the parties agree to end the dispute, and the releases given.',
    'Status report':
        'An update to the court on where the case stands.',
    'Invoice or fees':
        'A bill for legal work or a statement of fees and costs.',
    'Client notes or statement':
        "The client's own account of the facts, or the firm's notes of it.",
    'Check or payment receipt':
        'Evidence that a payment was made, and of how much.',
    'Tax lien':
        'A recorded claim by a tax authority against the property.',
    'Retainer':
        'The engagement agreement: it names the client and states what the firm was hired to do.',
    'Deed or title':
        'The instrument transferring or evidencing ownership of the property.',
    'Email':
        'Correspondence exchanged by email.',
    'Servicer correspondence':
        'A letter from the loan servicer about the account, such as a reinstatement quote or a default notice.',
    'Letter':
        'General correspondence on the matter.',
}


def que_hace(nombre_de_tipo: str) -> str:
    """La frase que explica ese tipo, o cadena vacia si no se reconocio."""
    return QUE_HACE.get(nombre_de_tipo, "")

# --------------------------------------------------------------------------- #
def _renglon(texto: str, coincidencia: re.Match) -> str:
    """El renglon del que salio el hallazgo. Es la prueba, recortada."""
    inicio = texto.rfind("\n", 0, coincidencia.start()) + 1
    fin = texto.find("\n", coincidencia.end())
    linea = texto[inicio: fin if fin > 0 else len(texto)]
    return re.sub(r"\s+", " ", linea).strip()[:150]


def tipo_de_texto(texto: str) -> tuple[Tipo | None, str, str]:
    """(tipo, evidencia, donde). `donde` es 'titulo' o 'mencion'.

    Se mira primero la cabecera entera y solo despues el cuerpo: un escrito que
    NOMBRA una mocion no es una mocion, y esa diferencia es la que separa
    clasificar de adivinar.

    Y SE EMPIEZA POR UNA VENTANA ESTRECHA, la del TITULO. Con la cabecera entera de
    900 caracteres entraba el certificado de servicio, que lo lleva casi todo lo que
    se presenta en un juzgado, y como se devolvia el primer tipo de la TABLA que
    casara en cualquier parte, 'Notice of Appearance' salia como 'Affidavit of
    service':

        Affidavit of service   tabla#13   posicion 802
        Notice of appearance   tabla#19   posicion 226

    El mismo documento en .pdf salia bien de pura suerte: su certificado caia en la
    posicion 889 y la ventana eran 900, asi que el patron se salia por unas letras.

    LA VENTANA SE MIDIO, no se eligio. Sobre los 314 documentos con texto del lote de control,
    reclasificando con cada tamano: con 250 el Notice seguia mal; con 350 empezaban a
    romperse cosas que estaban bien ('Motion to Compel' pasaba a 'Letter'); 300 es
    donde se arregla sin romper nada.

    Y EN ESA VENTANA NO JUEGAN LOS GENERICOS. Sin eso, 'TransUnion.pdf' pasaba de
    'Credit report' a 'Letter', porque en los primeros 300 caracteres de un reporte
    de credito lo unico que ha casado todavia es el 'Dear' del membrete.
    """
    if not texto or not texto.strip():
        return None, "", ""
    ventanas = (("titulo", texto[:TITULO], True),
                ("titulo", texto[:CABECERA], False),
                ("mencion", texto, False))
    for donde, trozo, solo_especificos in ventanas:
        for tipo in TIPOS:
            if solo_especificos and tipo.generico:
                continue
            encontrado = tipo.patron.search(trozo)
            if encontrado:
                return tipo, _renglon(trozo, encontrado), donde
    return None, "", ""


def tipo_de_nombre(nombre) -> tuple[Tipo | None, str]:
    """Lo que dice el NOMBRE del archivo. Aqui la firma es bastante descriptiva.

    Se prueba contra el nombre crudo Y contra una version con los separadores
    convertidos en espacios. Hace falta: lo que se baja de NYSCEF llega como
    '..._NOTICE_OF_MOTION_104.pdf', y para una expresion regular el guion bajo es
    una letra mas, asi que '\\bmotion\\b' NO casa dentro de '_MOTION_'. Sin esto se
    perdian archivos que decian en el nombre exactamente lo que eran.
    """
    crudo = str(nombre)
    aireado = re.sub(r"[_\-.]+", " ", crudo)
    for tipo in TIPOS:
        patron = tipo.en_nombre or tipo.patron
        if patron.search(crudo):
            return tipo, crudo
        if patron.search(aireado):
            return tipo, aireado
    return None, ""


def por_nombre(nombre_de_tipo: str) -> Tipo | None:
    return next((t for t in TIPOS if t.nombre == nombre_de_tipo), None)


# --------------------------------------------------------------------------- #
def _rutas(plantilla: dict, prefijo: str = "") -> set[str]:
    salida = set()
    for nombre, hijos in plantilla.items():
        ruta = f"{prefijo}{nombre}"
        salida.add(ruta)
        salida |= _rutas(hijos, f"{ruta}/")
    return salida


def revisar() -> list[str]:
    """Todo destino de la tabla tiene que existir en su plantilla. Si no, es un typo.

    Un nombre torcido aqui no falla ruidosamente: crearia una carpeta nueva en
    produccion, y eso no se nota hasta que alguien no encuentra sus papeles.
    """
    from .plantillas import PLANTILLA_FCRA, PLANTILLA_FORECLOSURE

    validas = {"Foreclosure": _rutas(PLANTILLA_FORECLOSURE), "FCRA": _rutas(PLANTILLA_FCRA)}
    problemas = []
    for tipo in TIPOS:
        for plantilla in ("Foreclosure", "FCRA"):
            destino = tipo.destino(plantilla)
            if destino and destino not in validas[plantilla]:
                problemas.append(f"{tipo.nombre}: '{destino}' does not exist in {plantilla}")
    return problemas


def main() -> None:
    import pandas as pd

    from ver_matters import tabla, titulo

    t = pd.DataFrame([
        {"Document type": x.nombre,
         "In Foreclosure": x.foreclosure or "(no place)",
         "In FCRA": x.fcra or "(no place)",
         "Confidence": x.certeza}
        for x in TIPOS
    ])
    titulo(f"DOCUMENT TYPES RECOGNIZED ({len(TIPOS)})")
    print(tabla(t))

    titulo("BY CONFIDENCE")
    print(tabla(t["Confidence"].value_counts()
                .rename_axis("Confidence").reset_index(name="Types")))

    problemas = revisar()
    titulo("DESTINATIONS THAT DO NOT EXIST IN THE TEMPLATE")
    print("\n".join(f"  {p}" for p in problemas) if problemas
          else "  none: every destination in the table exists")


if __name__ == "__main__":
    main()
