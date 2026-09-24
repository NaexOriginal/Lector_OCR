r"""A que carpeta de la plantilla va cada subcarpeta que ya existe.

La mudanza se decide aqui. En Active y Closed Matters hay 22.002 subcarpetas de
primer nivel con 1.321 nombres distintos, pero 60 nombres cubren el 91%: la firma
repite la misma estructura caso tras caso, sin el prefijo numerico que pusimos
nosotros y con variaciones de escritura ('MORTGAGE STATEMENT' y 'Mortgage
Statements' son la misma carpeta).

El destino DEPENDE DE LA PLANTILLA. 'Discovery' va a '03_Litigation/Discovery' en un
foreclosure y a '07_Discovery' en un FCRA: el mismo nombre, dos sitios distintos. Por
eso cada entrada tiene sus dos columnas.

Tres niveles de certeza, y estan separados a proposito:

    evidente   el mismo nombre o una variante de escritura. No hay nada que decidir.
    propuesta  encaja por significado, pero es una lectura nuestra.
    PREGUNTAR  no hay sitio en la plantilla, o el nombre es ambiguo. No se mueve
               hasta que alguien del despacho lo diga.

Lo que quede sin equivalencia NO se inventa: va a '00_Sin clasificar' dentro del
caso. Un documento mal archivado es peor que uno sin archivar -- quien busca la
mocion en 09_Motions y no la encuentra concluye que no existe.

Uso:
    .venv\Scripts\python.exe equivalencias.py
"""

from __future__ import annotations

import re
import unicodedata

# (destino en plantilla Foreclosure, destino en plantilla FCRA, certeza)
# None = esa plantilla no tiene sitio para esto.
TABLA: dict[str, tuple[str | None, str | None, str]] = {
    # --- las que ya se llaman igual, quitando el prefijo numerico -------------
    "correspondence":              ("04_Correspondence", None, "clear"),
    "general":                     ("01_General", None, "clear"),
    "litigation":                  ("03_Litigation", None, "clear"),
    "loss mitigation":             ("02_Loss Mitigation", None, "clear"),
    "bank statements":             ("02_Loss Mitigation/Bank Statements", None, "clear"),
    "bank statement":              ("02_Loss Mitigation/Bank Statements", None, "clear"),
    "mortgage statements":         ("02_Loss Mitigation/Mortgage Statements", None, "clear"),
    "mortgage statement":          ("02_Loss Mitigation/Mortgage Statements", None, "clear"),
    "pay stubs":                   ("02_Loss Mitigation/Pay Stubs", None, "clear"),
    "paystubs":                    ("02_Loss Mitigation/Pay Stubs", None, "clear"),
    "taxes":                       ("02_Loss Mitigation/Taxes", None, "clear"),
    "utility bills":               ("02_Loss Mitigation/Utility Bills", None, "clear"),
    "orders":                      ("03_Litigation/Orders", None, "clear"),
    "pleadings":                   ("03_Litigation/Pleadings", None, "clear"),

    "documents from client":       (None, "01_Documents from client", "clear"),
    "fcra dispute":                (None, "02_FCRA Dispute", "clear"),
    "credit report":               (None, "02_FCRA Dispute/Credit Report", "clear"),
    "credit reports":              (None, "02_FCRA Dispute/Credit Report", "clear"),
    "dispute letters":             (None, "02_FCRA Dispute/Dispute Letters", "clear"),
    "identifying documents":       (None, "02_FCRA Dispute/Identifying Documents", "clear"),
    "amended complaint":           (None, "03_Complaint/Amended Complaint", "clear"),

    # --- el mismo nombre, distinto sitio segun la plantilla -------------------
    "complaint":                   ("03_Litigation/Pleadings", "03_Complaint", "clear"),
    "answers":                     ("03_Litigation/Pleadings", "05_Answers", "clear"),
    "discovery":                   ("03_Litigation/Discovery", "07_Discovery", "clear"),
    "motions":                     ("03_Litigation/Motions", "09_Motions", "clear"),

    # --- variantes de escritura del mismo concepto ---------------------------
    "tax returns and w2":          ("02_Loss Mitigation/Taxes", None, "clear"),
    "tax returns w2":              ("02_Loss Mitigation/Taxes", None, "clear"),
    "w2 and tax returns":          ("02_Loss Mitigation/Taxes", None, "clear"),
    "income tax and w 2 s":        ("02_Loss Mitigation/Taxes", None, "clear"),

    # Singulares y abreviaturas del mismo nombre. Se separan del bloque de arriba
    # solo para que se vea que salieron de medir la cola, no de adivinar.
    "answer":                      ("03_Litigation/Pleadings", "05_Answers", "clear"),
    "motion":                      ("03_Litigation/Motions", "09_Motions", "clear"),
    "docs from client":            ("01_General", "01_Documents from client", "clear"),
    "documents from the client":   ("01_General", "01_Documents from client", "clear"),
    "loss mit":                    ("02_Loss Mitigation", None, "clear"),
    "stipulation":                 (None, "10_Stipulations", "clear"),
    "notice":                      (None, "11_Notices", "clear"),
    "credit reports and disputes": (None, "02_FCRA Dispute", "clear"),

    # Estas salieron de correr el ruteo sobre un caso de verdad. En un foreclosure
    # del estado no hay carpeta de deposiciones: las deposiciones SON discovery, y
    # dejarlas sin destino mandaba 22 archivos a quedarse quietos por nada.
    "discovery demands":           ("03_Litigation/Discovery", "07_Discovery", "clear"),
    "discovery responses":         ("03_Litigation/Discovery", "07_Discovery", "clear"),
    "depositions":                 ("03_Litigation/Discovery", "08_Depositions", "proposed"),
    "deposition":                  ("03_Litigation/Discovery", "08_Depositions", "proposed"),
    "notices of deposition":       ("03_Litigation/Discovery",
                                    "08_Depositions/Notices of Deposition", "proposed"),

    # Estas salieron de contar los nombres de subcarpeta de los 655 casos FCRA, no
    # de mirar un expediente: son las que mas veces se quedaban sin destino. La
    # plantilla FCRA es mucho mas detallada que la de foreclosure y la tabla se
    # habia escrito mirando sobre todo foreclosures.
    "fcra package":                (None, "02_FCRA Dispute", "proposed"),
    "notice of dispute":           (None, "02_FCRA Dispute/Dispute Letters", "clear"),
    "dispute letter":              (None, "02_FCRA Dispute/Dispute Letters", "clear"),
    "notice of settlement":        ("03_Litigation", "11_Notices/Notice of Settlement", "clear"),
    "notice of appearance":        ("03_Litigation", "11_Notices/Notice of Appearance", "clear"),
    "request for default":         ("03_Litigation/Motions", "09_Motions", "proposed"),
    "id ss statement":             (None, "02_FCRA Dispute/Identifying Documents", "proposed"),
    "rule 26 initial disclosures": ("03_Litigation/Discovery",
                                    "06_Case Management Plan Initial Conference/Rule 26(a)(1)",
                                    "clear"),
    "initial disclosures":         ("03_Litigation/Discovery",
                                    "06_Case Management Plan Initial Conference/Rule 26(a)(1)",
                                    "clear"),
    "subpoenas":                   ("03_Litigation/Discovery", "07_Discovery", "clear"),
    "subpoena":                    ("03_Litigation/Discovery", "07_Discovery", "clear"),
    "expert witness":              ("03_Litigation/Discovery", "07_Discovery", "proposed"),
    "status report":               ("01_General", "13_Status Reports", "clear"),
    # 'Drop letter' es la carta con la que se deja fuera a un demandado, y
    # 'discontinuance' el desistimiento: las dos acaban el pleito para alguien.
    "drop letter":                 ("03_Litigation", "11_Notices/Notice of Dismissal", "proposed"),
    "dismissal":                   ("03_Litigation", "11_Notices/Notice of Dismissal", "proposed"),
    "discontinuance":              ("03_Litigation", "11_Notices/Notice of Dismissal", "proposed"),

    # Y estas de contar los nombres de los casos Foreclosure, igual que el bloque de
    # arriba con los FCRA. Las siete primeras son carpetas de la plantilla FCRA que
    # aparecen dentro de casos que llevan plantilla de foreclosure: no se les puede
    # dejar el destino en blanco solo porque esten en el expediente 'equivocado'.
    "notices":                     ("03_Litigation", "11_Notices", "proposed"),
    "service of process":          ("03_Litigation/Pleadings", "04_Service of Process", "clear"),
    "stipulations":                ("03_Litigation", "10_Stipulations", "clear"),
    "settlement documents":        ("01_General", "12_Settlement Documents", "proposed"),
    "billing and invoices":        ("01_General", "14_Billing and Invoices", "proposed"),
    "case management plan initial conference":
                                   ("03_Litigation",
                                    "06_Case Management Plan Initial Conference", "clear"),
    "status reports":              ("01_General", "13_Status Reports", "clear"),

    "mortgage statments":          ("02_Loss Mitigation/Mortgage Statements", None, "clear"),
    "court documents":             ("03_Litigation", None, "proposed"),
    "retainer":                    ("01_General", "01_Documents from client", "clear"),
    "title":                       ("01_General", None, "proposed"),
    "faxed":                       ("01_General", None, "proposed"),
    "trial":                       ("03_Litigation", None, "proposed"),
    "3rd party bills":             ("02_Loss Mitigation", None, "proposed"),
    "third party bills":           ("02_Loss Mitigation", None, "proposed"),

    # --- encajan por significado, pero es lectura nuestra --------------------
    "court docs":                  ("03_Litigation", None, "proposed"),
    # OSC es Order to Show Cause y TRO Temporary Restraining Order: las dos son
    # peticiones al juez, no escritos de parte, asi que van con las mociones.
    "osc":                         ("03_Litigation/Motions", "09_Motions", "proposed"),
    "osc withdraw":                ("03_Litigation/Motions", "09_Motions", "proposed"),
    "osc to withdraw":             ("03_Litigation/Motions", "09_Motions", "proposed"),
    "tro":                         ("03_Litigation/Motions", "09_Motions", "proposed"),
    "affidavits of service":       ("03_Litigation/Pleadings", "04_Service of Process", "proposed"),
    "affidavit of service":        ("03_Litigation/Pleadings", "04_Service of Process", "proposed"),
    "signed docs":                 ("01_General", "01_Documents from client", "proposed"),
    "liens judgments":             ("01_General", None, "proposed"),
    "liens judgements":            ("01_General", None, "proposed"),
    "fcra":                        (None, "02_FCRA Dispute", "proposed"),
    "notices of dispute":          (None, "02_FCRA Dispute/Dispute Letters", "proposed"),
    "appeal":                      ("03_Litigation", "11_Notices/Notice of Appeal", "proposed"),
    "autho":                       ("01_General", "01_Documents from client", "proposed"),
    "settlement":                  ("01_General", "12_Settlement Documents", "proposed"),

    # --- la plantilla no tiene una carpeta para esto -------------------------
    # La estructura viene dada y NO se le anaden carpetas, asi que estas no crean
    # una hija nueva: su contenido sube a la carpeta padre que si existe. Son
    # documentacion de loss mitigation, que es justo lo que es 02_Loss Mitigation.
    "financial worksheet and hardship letter": ("02_Loss Mitigation", None, "proposed"),
    "hoa insurance utility re taxes":          ("02_Loss Mitigation", None, "proposed"),
    "hoa insurance utility and re taxes":      ("02_Loss Mitigation", None, "proposed"),
    "self employed p l":                       ("02_Loss Mitigation", None, "proposed"),
    "submission":                              ("02_Loss Mitigation", None, "proposed"),
    "submissions":                             ("02_Loss Mitigation", None, "proposed"),
    "submission new":                          ("02_Loss Mitigation", None, "proposed"),
    "submission 2":                            ("02_Loss Mitigation", None, "proposed"),
    "old submission":                          ("02_Loss Mitigation", None, "proposed"),
    "short sale":                              ("02_Loss Mitigation", None, "proposed"),

    # Papeles del caso que no son de ninguna fase concreta. En Foreclosure existe
    # 01_General y es exactamente para esto. En FCRA no hay equivalente -- 01_
    # Documents from client es lo que aporta el cliente, no un cajon general -- asi
    # que ahi van a 00_Sin clasificar y que alguien los coloque.
    "prior case":                              ("01_General", None, "proposed"),
    "prior foreclosure":                       ("01_General", None, "proposed"),
    "old docs":                                ("01_General", None, "proposed"),
    "leases":                                  ("01_General", None, "proposed"),
    "faxes":                                   ("01_General", None, "proposed"),

    # --- aqui si hay que preguntar -------------------------------------------
    # 'SCANNED' no dice de que es: puede ser cualquier fase del caso, y repartirlo
    # a ojo seria inventar. BANKRUPTCY, QWR y Article 15 son OTRAS materias dentro
    # de la carpeta de un foreclosure: puede que sean casos aparte del mismo
    # cliente, que es la discusion de las carpetas que se parten.
    # Ninguna de las dos plantillas tiene un sitio para esto y no es un descuido:
    # son fases y soportes que el esquema no contempla. Se marcan para que aparezcan
    # en la lista de lo que hay que preguntar, en vez de confundirse con la cola de
    # nombres raros que simplemente no reconocemos.
    "recordings":                              (None, None, "ASK"),
    "recording":                               (None, None, "ASK"),
    "arbitration":                             (None, None, "ASK"),

    "scanned":                                 (None, None, "ASK"),
    "bankruptcy":                              (None, None, "ASK"),
    "qwr":                                     (None, None, "ASK"),
    "qwr federal":                             (None, None, "ASK"),
    "article 15":                              (None, None, "ASK"),
    "federal action fee":                      (None, None, "ASK"),
}

SIN_CLASIFICAR = "00_Sin clasificar"

# La cola larga no cabe en una tabla de nombres exactos. Hay carpetas que la firma
# bautiza sobre la marcha -- "Defendant's MSJ (Seq. 9)", "Opp to Compel another
# depo", "Motion to Quash - to efile" -- y son un nombre distinto cada vez aunque
# digan siempre lo mismo. Para esas se busca la palabra clave.
#
# Se consultan DESPUES de la tabla y nunca antes: un nombre exacto siempre gana,
# porque una palabra suelta acierta menos. Y solo se ponen aqui conceptos que se
# reconocen por una sola palabra; lo ambiguo -- "To efile & serve", que es una
# bandeja de salida y puede llevar cualquier cosa -- se queda fuera a proposito.
PATRONES: list[tuple[re.Pattern, str | None, str | None, str]] = [
    (re.compile(r"\bmsj\b|summary judgment"),
     "03_Litigation/Motions", "09_Motions", "proposed"),
    (re.compile(r"\bmotion to\b|\bopp(osition)? to\b|\bcross[- ]?motion\b"),
     "03_Litigation/Motions", "09_Motions", "proposed"),
    (re.compile(r"\bdepo(sition)?s?\b"),
     "03_Litigation/Discovery", "08_Depositions", "proposed"),
    (re.compile(r"\bdiscovery\b|\binterrogator"),
     "03_Litigation/Discovery", "07_Discovery", "proposed"),
    (re.compile(r"\bemails?\b|\bletters?\b|\bcorrespondence\b"),
     "04_Correspondence", None, "proposed"),

    # La cola de los foreclosures. Aqui la firma bautiza con fecha y con el estado
    # del tramite -- 'SUBMISSION 2024', 'OLD SUBMISSIONS', 'BANKRUPTCY NEW' -- asi
    # que el nombre exacto no se repite nunca pero la palabra clave si.
    (re.compile(r"\bsubmissions?\b"), "02_Loss Mitigation", None, "proposed"),
    (re.compile(r"\bappeals?\b|\bappellate\b"),
     "03_Litigation", "11_Notices/Notice of Appeal", "proposed"),
    (re.compile(r"\bmotions?\b|\bosc\b|\btro\b"),
     "03_Litigation/Motions", "09_Motions", "proposed"),
    (re.compile(r"\bnotes\b"), "01_General", "01_Documents from client", "proposed"),
    (re.compile(r"\bmodifications?\b|\bhamp\b|\bhardship\b"),
     "02_Loss Mitigation", None, "proposed"),

    # Estas son OTRAS materias metidas dentro del expediente de un foreclosure, y
    # esa es exactamente la discusion pendiente de si son casos aparte del mismo
    # cliente. Se reconocen para que salgan contadas en la lista de lo que hay que
    # preguntar, no para colocarlas: mientras nadie lo decida, no se mueven.
    (re.compile(r"\bbankruptcy\b|\bbk\b"), None, None, "ASK"),
    (re.compile(r"\bqwr\b"), None, None, "ASK"),
    (re.compile(r"\barticle 15\b"), None, None, "ASK"),
    (re.compile(r"\bfederal action\b"), None, None, "ASK"),
    (re.compile(r"\bscanned\b|\bscans?\b"), None, None, "ASK"),
    (re.compile(r"\bl ?& ?t\b|landlord"), None, None, "ASK"),

    # Al final del todo, y por eso: 'PRIOR BANKRUPTCY' tiene que salir marcada como
    # bankruptcy y no como papeleo viejo. Lo generico solo recoge lo que no reclamo
    # nadie antes.
    (re.compile(r"\bprior\b|\bold file\b|\bold docs?\b"), "01_General", None, "proposed"),
]


def normalizar(nombre) -> str:
    """El nombre sin prefijo numerico, sin acentos y sin puntuacion.

    Asi 'HOA, INSURANCE, UTILITY, RE TAXES' y '02_HOA Insurance Utility RE Taxes'
    son la misma clave.
    """
    limpio = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode()
    limpio = re.sub(r"^\d+[_\-. ]+", "", limpio)
    return re.sub(r"[^a-z0-9]+", " ", limpio.lower()).strip()


# Lo que Windows y SharePoint le pegan al final a una carpeta duplicada. No cambia
# de que es la carpeta: 'W2 and TAX RETURNS - Copy' son declaraciones de impuestos.
SUFIJO_DUPLICADO = re.compile(r"(\s+copy|\s+\d)$")


def destino(nombre, plantilla: str) -> tuple[str, str]:
    """(carpeta de destino, certeza) para una subcarpeta y una plantilla."""
    limpio = normalizar(nombre)
    fila = TABLA.get(limpio)
    if fila is None:
        # 'Notices(1)' es la carpeta 'Notices'. El '(1)' lo pone SharePoint cuando
        # alguien crea una carpeta que ya existia, y en la plantilla de la firma
        # viene ademas con espacios invisibles delante. Son 313 carpetas de casos
        # FCRA quedandose sin destino por un sufijo que no significa nada.
        fila = TABLA.get(SUFIJO_DUPLICADO.sub("", limpio))
    if fila is None:
        fila = next(((f, c, cert) for patron, f, c, cert in PATRONES
                     if patron.search(limpio)), None)
    if fila is None:
        return SIN_CLASIFICAR, "no match"
    para_foreclosure, para_fcra, certeza = fila
    elegido = para_fcra if plantilla == "FCRA" else para_foreclosure
    if elegido is None:
        return SIN_CLASIFICAR, "ASK" if certeza == "ASK" else "no place in this template"
    return elegido, certeza
