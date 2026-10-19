"""Paso 4 del Docs Agent: lo que el documento dice de si mismo, sobre el texto ya limpio.

    from lector_ocr.extraccion import extraer

    datos = extraer(texto, tipo="Order")
    datos.caratula.demandados     # ['AMERICREDIT FINANCIAL SERVICES, INC.', 'TransUnion LLC', ...]
    datos.remitente.sent_by       # 'court' | 'opposing_counsel' | 'our_firm' | 'third_party' | ''
    datos.remitente.filer_name    # 'Hon. Jane Doe, J.S.C.' | 'Locke Lord LLP'
    datos.fechas                  # [Fecha(what='hearing', date='2024-08-12', ...)]

El tipo de documento y el index ya los sacan clasificacion.py y expediente.py; el
numero de documento, recepcion.py (paso 1). Aqui va lo que faltaba: la caratula, quien
lo manda y las fechas.

CADA DATO TRAE SU EVIDENCIA, el trozo de texto de donde salio, como en el resto del
paquete. Y si no hay senal clara se deja vacio: un remitente adivinado es peor que uno
en blanco, porque el Analyst actuaria sobre el.

LA CARATULA SE BUSCA ENTRE ETIQUETAS. 'Plaintiff' (o Petitioner) cierra el bloque de
los demandantes, '-against-' / 'v.' lo separa, y 'Defendant' (o Respondent) cierra el
de los demandados. Asi viene en el estado y en el federal:

    MUHAMMED ALI, Plaintiff,
    -against-
    AMERICREDIT FINANCIAL SERVICES, INC. d/b/a GM FINANCIAL; TransUnion LLC, Defendants.

LOS DEMANDADOS SE PARTEN SOLO POR ';'. Con comas no se puede: 'U.S. BANK TRUST NATIONAL
ASSOCIATION, NOT IN ITS INDIVIDUAL CAPACITY, BUT SOLELY AS TRUSTEE...' es UNA parte con
tres comas. Sin ';' se devuelve el bloque entero como una sola entrada, y el paso 5 lo
usa al reves: busca cada demandado de HubSpot DENTRO del bloque, igual que el apellido
de la carpeta se busca dentro del documento.

EL REMITENTE ES QUIEN FIRMA, NO QUIEN APARECE. Petroff Amshen sale en casi todo: en lo
que redacta y en la lista de notificacion ('To: ... Petroff Amshen LLP, Attorneys for
Defendants') de lo que manda la contraparte. Por eso se mira el bloque de firma, al
final, y se quita antes lo que viene detras de 'To:'.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

# --------------------------------------------------------------------------- #
# Caratula

# Donde se busca. Con 4.000 caracteres se perdia la mitad de las demandas: muchos
# escritos llevan delante un aviso, una portada o la notice of motion, y la caratula
# de la affirmation va despues. Se toma la PRIMERA del documento.
VENTANA_CARATULA = 20000
# Un foreclosure lista a todos los ocupantes ('JOHN DOE #1 through #12, the last
# twelve names being fictitious...'): el bloque de demandados pasa de 1.500 caracteres.
MAXIMO_BLOQUE = 3000

DEMANDANTE = re.compile(
    r"\b(Plaintiffs?|Petitioners?)(?:\s*-\s*(?:Appellants?|Respondents?|Counterclaim[\s-]Defendants?))?\s*[,.:]?",
    re.I)
SEPARADOR = re.compile(r"(?:^|\s)(?:-\s*against\s*-?|against|vs?\.)(?=\s)", re.I | re.M)
DEMANDADO = re.compile(
    r"\b(Defendants?|Respondents?)(?:\s*-\s*(?:Appellees?|Respondents?|Appellants?))?\s*[,.:]?",
    re.I)

# Lo que cierra el bloque de los demandantes por arriba: la cabecera del juzgado, el
# separador de la caratula ('X', '-----X', ':'), el index.
CORTE_DE_CABECERA = re.compile(
    r"(?:COUNTY\s+OF\s+[A-Z]+|DISTRICT\s+OF\s+[A-Z ]+?(?=\s{2}|\n|$)|DEPARTMENT|"
    # El renglon separador tiene que llevar algo ('X', ':', '-'): con [X:\-\s] un
    # renglon VACIO tambien cortaba, y como entre el nombre y 'Plaintiff,' casi siempre
    # hay uno, el bloque de demandantes quedaba vacio y la caratula se perdia.
    r"COURT\s+OF\s+[A-Z ]+?(?=\n)|^[ \t]*[X:\-][X:\- \t]*$|\bX\b(?=\s)|Index\s+No\.?:?[\s\d/-]*"
    # La cabecera de una orden ('PRESENT: HON. X, J.S.C.') y el sello de NYSCEF.
    r"|J\.\s?S\.\s?C\.|RECEIVED\s+NYSCEF:\s*[\d/]+|NYSCEF\s+DOC\.?\s*NO\.?\s*\d+"
    r"|FILED:[^\n]*?(?:CLERK|DEPT\.?)[^\n]*?(?:AM|PM)\b)",
    re.I | re.M)
# Una cita de jurisprudencia ('Galper v. JP Morgan Chase Bank, N.A., 802 F.3d 437')
# tambien lleva 'v.': si el bloque trae la cita, no es la caratula sino el cuerpo.
CITA = re.compile(r"\b\d+\s+(?:F\.\s?(?:\d?d|Supp\.?(?:\s?\d?d)?)|A\.D\.\s?\d?d|N\.Y\.\s?\d?d|"
                  r"Misc\.\s?\d?d|S\.\s?Ct\.|U\.S\.|N\.Y\.S\.\s?\d?d)\s+\d+")
# Un bloque de partes de verdad: corto y con alguna palabra.
MAXIMO_DEMANDANTES = 600
MINIMO_PARTE = 4
# Formularios de notificacion: 'Plaintiff(s): X  Defendant(s): Y', sin '-against-'.
FORMULARIO = re.compile(
    r"Plaintiff\(s\)\s*:\s*(?P<p>.{4,400}?)\s*Defendant\(s\)\s*:\s*(?P<d>.{4,600}?)"
    r"(?=,?\s*(?:[A-Z][a-z]+\s+[A-Z][a-z]+,\s+the\s+undersigned|being\s+duly|the\s+undersigned|\n|$))",
    re.S)
FINCA = re.compile(r"\b(?:Block|Lot)\s*:\s*\d+\b", re.I)
# Lo que no es una parte aunque este dentro del bloque.
RUIDO_DE_PARTE = re.compile(
    r"\s*(?:(?:\d{1,2}:)?\d{2}-(?:cv|bk|mc|cr|ap)-\d+(?:-[A-Z]+)*(?:\s*\([A-Z]+\))*"
    r"|Index\s+No\.?:?\s*[\d/-]*|\bX\b|[:\-]{2,}|\s:\s)\s*",
    re.I)


@dataclass
class Caratula:
    demandantes: list[str] = field(default_factory=list)
    demandados: list[str] = field(default_factory=list)
    demandantes_texto: str = ""
    demandados_texto: str = ""
    evidencia: str = ""

    @property
    def hay(self) -> bool:
        return bool(self.demandados_texto)


def _limpiar_bloque(bloque: str) -> str:
    bloque = FINCA.sub(" ", RUIDO_DE_PARTE.sub(" ", bloque))
    bloque = re.sub(r"[\u200e\u200f]", "", bloque)          # marcas de direccion de Word
    return re.sub(r"\s+", " ", bloque).strip(" ,;:.-")


def _partes(bloque: str) -> list[str]:
    if ";" in bloque:
        return [p for p in (_limpiar_bloque(x) for x in bloque.split(";")) if p]
    return [bloque] if bloque else []


def caratula(texto: str) -> Caratula:
    """Demandantes y demandados de la caratula. Vacia si no se reconoce."""
    zona = texto[:VENTANA_CARATULA]
    for sep in SEPARADOR.finditer(zona):
        antes = zona[:sep.start()]
        etiqueta_d = None
        for m in DEMANDANTE.finditer(antes):
            etiqueta_d = m
        # La etiqueta del demandante tiene que estar justo antes del separador.
        if not etiqueta_d or sep.start() - etiqueta_d.end() > 80:
            continue
        despues = zona[sep.end(): sep.end() + MAXIMO_BLOQUE]
        etiqueta_x = DEMANDADO.search(despues)
        if not etiqueta_x:
            continue
        demandados = _limpiar_bloque(despues[:etiqueta_x.start()])

        # Los demandantes: desde el ultimo corte de cabecera hasta la etiqueta.
        previo = antes[max(0, etiqueta_d.start() - MAXIMO_BLOQUE): etiqueta_d.start()]
        cortes = list(CORTE_DE_CABECERA.finditer(previo))
        if cortes:
            previo = previo[cortes[-1].end():]
        demandantes = _limpiar_bloque(previo)
        if (len(demandados) < MINIMO_PARTE or len(demandantes) < MINIMO_PARTE
                or len(demandantes) > MAXIMO_DEMANDANTES or CITA.search(demandados)):
            continue
        inicio = sep.start() - len(previo) - (etiqueta_d.end() - etiqueta_d.start())
        evidencia = re.sub(r"\s+", " ", zona[max(0, inicio): sep.end() + etiqueta_x.end()])
        return Caratula(_partes(demandantes), _partes(demandados), demandantes, demandados,
                        evidencia.strip()[:300])
    if formulario := FORMULARIO.search(zona):
        demandantes, demandados = (_limpiar_bloque(formulario.group(g)) for g in ("p", "d"))
        if len(demandantes) >= MINIMO_PARTE and len(demandados) >= MINIMO_PARTE:
            return Caratula(_partes(demandantes), _partes(demandados), demandantes, demandados,
                            re.sub(r"\s+", " ", formulario.group(0))[:300])
    return Caratula()


# --------------------------------------------------------------------------- #
# Remitente

COLA = 3000   # el bloque de firma va al final
NUESTRA_FIRMA = re.compile(r"petroff\s*(?:&|and)?\s*amshen|petroffamshen\.com|1795\s+Coney\s+Island", re.I)
# Un abogado que firma solo, sin sufijo de despacho: 'Jane Roe, Esq. Attorney for Plaintiff'.
ABOGADO_SOLO = re.compile(
    r"\b(?:Attorneys?|Counsel)\s+for\s+(?:the\s+)?(Plaintiffs?|Defendants?|Petitioners?|Respondents?)\b")

# Lo que viene detras de 'To:' es la lista de notificacion: ahi esta la OTRA parte.
LISTA_DE_NOTIFICACION = re.compile(r"(?:^|\s)(?:TO|Cc|SERVICE\s+LIST)\s*:", re.M)

# La corte firma asi.
FIRMA_DE_LA_CORTE = re.compile(
    r"\bSO[\s-]ORDERED\b|^\s*ENTER\s*:|\bJ\.\s?S\.\s?C\.|\bU\.\s?S\.\s?[DM]\.\s?J\.|"
    r"\bHON(?:\.|ORABLE)\s+[A-Z]|\bJustice\s+of\s+the\s+Supreme\s+Court\b|\bCLERK\s+OF\s+(?:THE\s+)?COURT\b",
    re.I | re.M)
JUEZ = re.compile(
    r"\b(HON(?:\.|ORABLE)\s+[A-Z][A-Za-z.'\- ]{2,40}?)(?=\s*,|\s+J\.|\s+U\.S\.|\s*$|\n)|"
    r"\b([A-Z][A-Za-z.'\- ]{2,40}?),?\s+(?:J\.\s?S\.\s?C\.|U\.\s?S\.\s?[DM]\.\s?J\.)",
    re.M)
# Los tipos de clasificacion.py que emite la corte. La Order to show cause NO: la firma
# el juez, pero la presenta una parte, y quien la manda es esa parte.
TIPOS_DE_LA_CORTE = {"Court order", "Judgment of foreclosure and sale",
                     "Notice of availability (efiling)"}

# La firma de un abogado: el despacho delante de 'Attorneys for' / 'Counsel for'. El
# nombre no lleva comas salvo justo antes del sufijo ('WEINREB LAW FIRM, PLLC'): asi no
# arrastra 'Syosset, New York Respectfully Submitted,' que va delante en la firma.
DESPACHO = re.compile(
    r"([A-Z][A-Za-z&.'\- ]{2,60}?(?:,\s*)?\b(?:LLP|L\.L\.P\.|LLC|PLLC|P\.L\.L\.C\.|P\.C\.|PC|LAW\s+(?:FIRM|GROUP|OFFICES?)[A-Za-z .]*))"
    r"[\s,]+(?:\S+\s+){0,12}?(?:Attorneys?|Counsel)\s+for\s+(?:the\s+)?"
    r"(Plaintiffs?|Defendants?|Petitioners?|Respondents?|Appellants?|Appellees?|[A-Z][\w.,&' -]{2,60})",
    re.S)
# La persona que firma: 'Nombre, Esq.' manda sobre '/s/ Nombre', que a veces va pegado
# al nombre escrito ('/s/ Serge F. Petroff Serge F. Petroff, Esq.').
FIRMANTE_ESQ = re.compile(r"\b([A-Z][A-Za-z.'\- ]{2,60}?),?\s+Esq\.?", re.M)
FIRMANTE_S = re.compile(r"/s/\s*([A-Z][A-Za-z.'\- ]{2,40}?)(?=_|\s{2}|,|$|\n)", re.M)
SALUDO_DE_FIRMA = re.compile(r"^.*\b(?:Submitted|etc\.?|Truly|Sincerely|Dated|By)\b[:,.]?\s*", re.I)


def _persona(cola: str) -> str:
    m = FIRMANTE_ESQ.search(cola) or FIRMANTE_S.search(cola)
    if not m:
        return ""
    partes = re.sub(r"[_\s]+", " ", m.group(1)).strip().split()
    # La regla 3 pega 'Attorneys for Plaintiff' al nombre que va debajo: lo que hay
    # hasta la ultima palabra de rol no es el nombre.
    roles = [i for i, p in enumerate(partes) if ROL.fullmatch(p.strip(",.:"))]
    if roles:
        partes = partes[roles[-1] + 1:]
    mitad = len(partes) // 2
    if len(partes) % 2 == 0 and partes[:mitad] == partes[mitad:]:
        partes = partes[:mitad]       # el nombre repetido: '/s/ X Y' + 'X Y, Esq.'
    return " ".join(partes[-4:])


ROL = re.compile(r"(?:Plaintiffs?|Defendants?|Petitioners?|Respondents?|Appellants?|Appellees?|"
                 r"for|By|Attorneys?|Counsel|Submitted|Esq)", re.I)


def _despacho(crudo: str) -> str:
    return SALUDO_DE_FIRMA.sub("", re.sub(r"\s+", " ", crudo)).strip(" ,")


@dataclass
class Remitente:
    sent_by: str = ""        # 'court' | 'opposing_counsel' | 'our_firm' | 'third_party' | ''
    filer_name: str = ""     # el juez, el despacho o la persona que firma
    firma: str = ""          # el despacho que firma, si lo hay
    representa_a: str = ""   # 'Plaintiff', 'Defendants'... lo que dice tras 'Attorneys for'
    firmantes: list[str] = field(default_factory=list)  # todos los despachos que firman
    evidencia: str = ""


def _cola_sin_notificacion(texto: str) -> str:
    cola = texto[-COLA:]
    # La lista de notificacion va al final y despues de la firma. Se corta en la
    # PRIMERA 'To:' que aparezca detras de la mitad de la cola, para no cortar un
    # 'TO THE ABOVE-NAMED DEFENDANTS' de la cabecera de una citacion corta.
    for m in LISTA_DE_NOTIFICACION.finditer(cola):
        if m.start() > len(cola) // 4:
            return cola[:m.start()]
    return cola


# Un correo reenviado no lleva bloque de despacho: quien lo manda es su 'From:'.
DE_CORREO = re.compile(r"^\s*From\s*:\s*(.{3,120}?)\s*$", re.M | re.I)
DOMINIO = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
CORREO_GENERICO = re.compile(r"gmail|yahoo|hotmail|outlook|aol|icloud|msn", re.I)
ANTES_DE_LA_FIRMA = 250


def _en_notificacion(texto: str, m: re.Match) -> bool:
    """Si ese 'DESPACHO, Attorneys for...' esta en una lista de notificacion: detras
    de un 'TO:' o 'Cc:' cercano. Ahi aparece la OTRA parte, no quien firma."""
    antes = texto[max(0, m.start() - ANTES_DE_LA_FIRMA): m.start()]
    return bool(LISTA_DE_NOTIFICACION.search(antes))


def _de_correo(texto: str) -> Remitente | None:
    m = DE_CORREO.search(texto[:800])
    if not m:
        return None
    de = m.group(1).strip()
    dominio = DOMINIO.search(de)
    nombre = re.sub(r"[\[<(].*$", "", de).strip(" '\"")
    if NUESTRA_FIRMA.search(de):
        quien = "our_firm"
    elif re.search(r"\bEsq\b|law|legal|llp|pllc", de, re.I) or (
            dominio and not CORREO_GENERICO.search(dominio.group(1))):
        # Un dominio propio (no gmail) en un correo de un expediente es casi siempre
        # un despacho o un servicer; 'Esq.' o 'law' lo confirman como abogado.
        quien = ("opposing_counsel" if re.search(r"\bEsq\b|law|legal|llp|pllc", de, re.I)
                 else "third_party")
    else:
        quien = "third_party"
    return Remitente(quien, nombre, dominio.group(1) if dominio else "", "", [], _trozo(texto, m))


def remitente(texto: str, tipo: str = "") -> Remitente:
    """Quien manda el documento, por su firma."""
    if not texto.strip():
        return Remitente()
    cola = _cola_sin_notificacion(texto)

    corte = FIRMA_DE_LA_CORTE.search(cola)
    if tipo in TIPOS_DE_LA_CORTE or (corte and not DESPACHO.search(cola)):
        juez = JUEZ.search(cola)
        nombre = next((g for g in juez.groups() if g), "") if juez else ""
        evidencia = (juez or corte)
        return Remitente("court", re.sub(r"\s+", " ", nombre).strip(), "", "", [],
                         _trozo(cola, evidencia) if evidencia else f"document type: {tipo}")

    correo = _de_correo(texto)
    if correo:
        return correo

    # La firma puede no estar al final: un escrito con anexos los lleva detras. Se toma
    # el PRIMER bloque de firma del documento que no este en una lista de notificacion.
    despachos = [m for m in DESPACHO.finditer(texto) if not _en_notificacion(texto, m)]
    if despachos:
        firmantes = []
        for d in despachos:
            nombre = _despacho(d.group(1))
            if nombre not in firmantes:
                firmantes.append(nombre)
        principal = despachos[0]
        firma = _despacho(principal.group(1))
        quien = "our_firm" if NUESTRA_FIRMA.search(firma) else "opposing_counsel"
        # La persona que firma, cerca de ESE bloque y no en cualquier parte.
        alrededor = texto[max(0, principal.start() - 300): principal.end() + 400]
        return Remitente(quien, _persona(alrededor) or firma, firma,
                         principal.group(2).strip(" ,."), firmantes, _trozo(texto, principal))

    # Un abogado sin despacho con sufijo. Es nuestro si nuestra firma o nuestra
    # direccion estan junto a el; si no, de la contraparte. Sin una persona que firme
    # cerca no se afirma nada: 'attorneys for the Plaintiff' sale tambien en el texto
    # de cualquier citacion.
    for m in ABOGADO_SOLO.finditer(texto):
        if _en_notificacion(texto, m):
            continue
        alrededor = texto[max(0, m.start() - 400): m.end() + 400]
        persona = _persona(alrededor)
        if persona:
            quien = "our_firm" if NUESTRA_FIRMA.search(alrededor) else "opposing_counsel"
            return Remitente(quien, persona, "Petroff Amshen LLP" if quien == "our_firm" else "",
                             m.group(1), [], _trozo(texto, m))

    # Sin despacho ni corte: una carta de un servicer, un reporte, un tercero. Si la
    # firma es nuestra (un correo, una carta del despacho), se dice.
    if NUESTRA_FIRMA.search(cola):
        return Remitente("our_firm", "", "Petroff Amshen LLP", "", [],
                         _trozo(cola, NUESTRA_FIRMA.search(cola)))
    return Remitente("third_party", "", "", "", [], "")


def _trozo(texto: str, m: re.Match, margen: int = 60) -> str:
    return re.sub(r"\s+", " ", texto[max(0, m.start() - margen): m.end() + margen]).strip()[:200]


# --------------------------------------------------------------------------- #
# Fechas

MESES = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
MESES.update({m[:3]: i for m, i in list(MESES.items())})
MESES["sept"] = 9

_MES = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
FECHA = (rf"(?:{_MES}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
         rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?{_MES},?\s+\d{{4}}"
         r"|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})(?!\d)"
         r"|\d{4}-\d{2}-\d{2})")

# (que es, patron). El primer grupo con nombre 'f' es la fecha. El orden importa
# solo para el desempate: una misma fecha con dos etiquetas se queda con la primera.
ETIQUETAS = [
    ("filing_date", rf"FILED:\s*[A-Z][A-Z .'-]*?(?:CLERK|DEPT\.?)\s+(?P<f>{FECHA})"),
    ("filing_date", rf"RECEIVED\s+NYSCEF:\s*(?P<f>{FECHA})"),
    ("filing_date", rf"\bDocument\s+\d+(?:-\d+)?\s+Filed\s+(?P<f>{FECHA})"),
    ("hearing", rf"RETURN\s+DATE(?:\s+AND\s+TIME)?\s*:?\s*(?:[A-Z][a-z]+day,?\s+)?(?P<f>{FECHA})"),
    ("hearing", rf"\breturnable\b[^.]{{0,80}}?(?P<f>{FECHA})"),
    ("hearing", rf"\badjourned\b[^.]{{0,60}}?\bto\s+(?:[A-Z][a-z]+day,?\s+)?(?P<f>{FECHA})"),
    ("hearing", rf"\b(?:hearing|conference|oral\s+argument|appear(?:ance)?|trial|inquest|"
                rf"auction|foreclosure\s+sale|calendar\s+call)\b[^.]{{0,80}}?\bon\s+"
                rf"(?:[A-Z][a-z]+day,?\s+)?(?P<f>{FECHA})"),
    ("deadline", rf"\b(?:on\s+or\s+before|no\s+later\s+than|not\s+later\s+than|due\s+(?:on|by)?|"
                 rf"(?:filed|served|submitted|completed)\s+by)\s+(?:[A-Z][a-z]+day,?\s+)?(?P<f>{FECHA})"),
    ("document_date", rf"\bDated\s*:?[^\n]{{0,60}}?(?P<f>{FECHA})"),
]
ETIQUETAS_COMPILADAS = [(que, re.compile(p, re.I)) for que, p in ETIQUETAS]
MAXIMO_FECHAS = 20


@dataclass
class Fecha:
    what: str        # 'filing_date' | 'hearing' | 'deadline' | 'document_date'
    date: str        # ISO, '2024-08-12'
    evidencia: str = ""


def a_iso(crudo: str) -> str:
    """'August 12, 2024' / '8/12/24' / '12th day of August, 2024' -> '2024-08-12'. '' si no vale."""
    s = crudo.strip().lower().replace(",", " ")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\bday\s+of\b", " ", s)
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
            a, mes, d = map(int, m.groups())
        elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s):
            mes, d, a = map(int, m.groups())
            a = a + 2000 if a < 100 and a <= 35 else (a + 1900 if a < 100 else a)
        else:
            partes = s.replace(".", " ").split()
            if partes[0].isdigit():
                d, mes, a = int(partes[0]), MESES[partes[1][:4] if partes[1][:4] == "sept" else partes[1][:3]], int(partes[-1])
            else:
                mes, d, a = MESES[partes[0][:4] if partes[0][:4] == "sept" else partes[0][:3]], int(partes[1]), int(partes[-1])
        return date(a, mes, d).isoformat()
    except (ValueError, KeyError, IndexError):
        return ""


def fechas(texto: str) -> list[Fecha]:
    """Las fechas con etiqueta. Una fecha sola no dice para que es: no se adivina."""
    salida: list[Fecha] = []
    vistas: set[tuple[str, str]] = set()
    for que, patron in ETIQUETAS_COMPILADAS:
        for m in patron.finditer(texto):
            iso = a_iso(m.group("f"))
            if not iso or (que, iso) in vistas:
                continue
            vistas.add((que, iso))
            salida.append(Fecha(que, iso, _trozo(texto, m, 40)))
            if len(salida) >= MAXIMO_FECHAS:
                return salida
    return salida


# --------------------------------------------------------------------------- #
# Todo junto


@dataclass
class Extraccion:
    caratula: Caratula
    remitente: Remitente
    fechas: list[Fecha]

    def campos(self) -> dict:
        """Los campos de la ficha (y del mensaje al Analyst), en ingles como el resto."""
        c, r = self.caratula, self.remitente
        return {
            "caption": ({"plaintiffs": c.demandantes, "defendants": c.demandados,
                         "evidence": c.evidencia} if c.hay else None),
            "sent_by": r.sent_by or None,
            "filer_name": r.filer_name or None,
            "filer_firm": r.firma or None,
            "filer_represents": r.representa_a or None,
            "sent_by_evidence": r.evidencia or None,
            "dates_found": [asdict(f) for f in self.fechas] or None,
        }


def extraer(texto: str, tipo: str = "") -> Extraccion:
    """Caratula, remitente y fechas de un documento ya limpio. `tipo` es el de
    clasificacion (nombre del Tipo): una orden la manda la corte aunque no se vea firma."""
    texto = texto or ""
    return Extraccion(caratula(texto), remitente(texto, tipo), fechas(texto))
