"""Paso 3 del Docs Agent: el texto leido, limpio y comparable ANTES de extraer nada.

    from lector_ocr.limpieza import limpiar_documento

    limpio = limpiar_documento(texto, read_with=con_que, control_por_pagina=True)
    limpio.texto       # lo que leen la extraccion y el JSONL
    limpio.marca       # {'generated', 'rules': [1, 2, 3, 4], 'pii_masked': False}

Son las reglas 1 a 4 de Petroff-Litigation-Assistant (app/limpieza), tomadas de la
copia de docket (apps/ocr-service/limpieza_ocr), que trae dos arreglos de la regla 4
que el repo original no tiene:

    - no duplica lo que YA estaba normalizado: 'Qualified Written Request (QWR)' salia
      'Qualified Written Request (QWR) (Qualified Written Request (QWR))'  (615f079)
    - no mete espanol en el texto del documento: 'under Reg X', no 'bajo Reg X' (3d8a67e)

    1  descarte   texto basura (< 25 caracteres, > 40% de simbolos, confianza < 45%)
    2  artefactos caracteres asiaticos, de control, Unicode roto, lineas de relleno
    3  parrafos   palabras cortadas con guion y saltos de linea suaves
    4  terminos   citas legales y agencias de credito con una sola forma

LA REGLA 5 (ENMASCARAR SSN, CUENTAS, TELEFONOS) NO SE APLICA, a proposito: el JSONL
del caso guarda el texto entero, igual que los que ya estan en Matters. Los SSN se
siguen contando en la ficha (contains_ssn).

LA REGLA 1 NO VACIA LO QUE YA PASO EL CONTROL POR PAGINA. En un PDF o una imagen, el
paso 2 (calidad.py) ya separo pagina a pagina el texto bueno del ruido; lo que llega
aqui son paginas legibles. Vaciarlas por cortas borraria una portada 'EXHIBIT A' o la
contrasena de una produccion, que son once caracteres. En esos documentos la regla 1
solo deja la marca (`motivo`, con `vaciado=False`). En el resto de formatos, sin
control propio, actua como en docket y vacia el texto.

patrones y terminos son copia de app/limpieza: si cambian alla, se copian aqui otra vez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# --------------------------------------------------------------------------- #
# Patrones (app/limpieza/patrones.py)

CARACTERES_ASIATICOS = re.compile(r"[一-鿿]+")
CARACTERES_CONTROL_Y_UNICODE_ROTO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f�]")
LINEA_DE_RELLENO = re.compile(r"^[#*_\-=\s]{3,}$", re.MULTILINE)
PALABRA_CORTADA_CON_GUION = re.compile(r"(\b[a-zA-Z]+)-\s*\n\s*([a-zA-Z]+\b)")
# UNICA DIFERENCIA CON app/limpieza: el saludo de una carta y las cabeceras de un
# correo tambien van en su propio renglon. La clasificacion los reconoce al principio
# de linea ('^dear', '^from:'), y la regla 3 los pegaba al renglon de antes -- la
# direccion, la fecha -- con lo que cartas y correos dejaban de reconocerse.
# Medido sobre 1.437 textos de los diarios, contra el tipo que delata el nombre del
# archivo (699 con tipo): crudo 36,9%, limpio 38,9%, limpio con esto 39,6%.
LINEA_ESTRUCTURAL = re.compile(
    r"^(\d+\.|\([a-zA-Z0-9]+\)|Section\b|Article\b|ORDERED\b|WHEREAS\b|EXHIBIT\b|•|\*|-|\||#"
    r"|Dear\b|From\s*:|To\s*:|Subject\s*:|Sent\s*:|Re\s*:|Date\s*:|Cc\s*:)\s*",
    re.IGNORECASE,
)
SIMBOLO_NO_ALFANUMERICO = re.compile(r"[^a-zA-Z0-9\s]")
SALTOS_DE_LINEA_EXCESIVOS = re.compile(r"\n{3,}")
SEPARADOR_DE_PARRAFO = "\n\n"
CONFIANZA_EN_DESCRIPCION_OCR = re.compile(r"confidence\s+([\d.]+)")

# --------------------------------------------------------------------------- #
# Terminos (app/limpieza/terminos_legales.py, con 'under' en vez de 'bajo')

_TERMINOS_FORECLOSURE_NY = [
    (r"\bRPAPL\s*§?\s*1304\b|\b90[\s-]day\s+pre[\s-]foreclosure\s+notice\b",
     "RPAPL § 1304 (90-Day Pre-Foreclosure Notice)"),
    (r"\bCPLR\s*§?\s*3408\b|\bmandatory\s+settlement\s+conference\b",
     "CPLR § 3408 (Mandatory Settlement Conference)"),
    (r"\bFAPA\b|\bForeclosure\s+Abuse\s+Prevention\s+Act\b",
     "FAPA (Foreclosure Abuse Prevention Act, CPLR 203/213)"),
    (r"\ballong[es]?\b", "Allonge"),
    (r"\bpromiss?ory\s+note\b|\bmortgage\s+note\b", "Promissory Note"),
    (r"\bmortgagee\b", "Mortgagee"),
    (r"\bmortgagor\b", "Mortgagor"),
    (r"\bnotice\s+of\s+pendency\b|\blis\s+pendens\b", "Notice of Pendency (Lis Pendens)"),
]

_TERMINOS_FCRA = [
    (r"\btrans[\s-]?union\b", "TransUnion"),
    (r"\bexperian\b", "Experian"),
    (r"\bequifax\b", "Equifax"),
    (r"\binnovis\b", "Innovis"),
    (r"\bconsumer\s+reporting\s+agenc(?:y|ies)\b|\bCRA\b", "Consumer Reporting Agency (CRA)"),
    (r"\bfurnisher\s+of\s+information\b|\bfurnisher\b", "Furnisher of Information"),
    (r"\bmetro\s*2\b", "Metro 2"),
]

_TERMINOS_RESPA_TILA_FDCPA = [
    (r"\bqualified\s+written\s+request\b|\bQWR\b", "Qualified Written Request (QWR)"),
    (r"\bnotice\s+of\s+error\b|\bNOE\b", "Notice of Error (NOE)"),
    (r"\brequest\s+for\s+information\b|\bRFI\b",
     "Request for Information (RFI) under Reg X (12 CFR § 1024)"),
    (r"\bdebt\s+validation\s+(?:letter|notice)\b", "Debt Validation Letter under FDCPA § 1692g"),
]

TERMINOS_NORMALIZADOS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(patron, re.IGNORECASE), forma)
    for patron, forma in _TERMINOS_FORECLOSURE_NY + _TERMINOS_FCRA + _TERMINOS_RESPA_TILA_FDCPA
]

# --------------------------------------------------------------------------- #
# Reglas

MINIMO_CARACTERES = 25
CONFIANZA_MINIMA = 45.0
PROPORCION_MAXIMA_SIMBOLOS = 0.40
FINALES_DE_ORACION = (".", ":", ";", "!", "?")
PORCENTAJE = 100.0
REGLAS = [1, 2, 3, 4]


def confianza_de(read_with: str) -> float | None:
    """La de 'PaddleOCR (mean confidence 0.99)' en escala 0-100; None si no la dice."""
    coincidencia = CONFIANZA_EN_DESCRIPCION_OCR.search(str(read_with or ""))
    if not coincidencia:
        return None
    valor = float(coincidencia.group(1))
    return valor * PORCENTAJE if valor <= 1 else valor


def motivo_de_descarte(texto: str, confianza: float | None = None) -> str | None:
    """Regla 1: por que el texto es basura, o None si sirve."""
    total = len(texto.strip())
    if not total:
        return "Texto vacio"
    if total < MINIMO_CARACTERES:
        return f"total_caracteres < {MINIMO_CARACTERES} ({total} chars)"
    if confianza and confianza < CONFIANZA_MINIMA:
        return f"confianza_promedio < {CONFIANZA_MINIMA} ({confianza:.1f}%)"
    simbolos = len(SIMBOLO_NO_ALFANUMERICO.findall(texto)) / total
    if simbolos > PROPORCION_MAXIMA_SIMBOLOS:
        return (f"Mas del {PROPORCION_MAXIMA_SIMBOLOS * PORCENTAJE:.0f}% de simbolos no "
                f"alfanumericos ({simbolos * PORCENTAJE:.1f}%)")
    return None


def _unificar_parrafos(texto: str) -> str:
    """Regla 3b: une lineas sueltas en parrafos; las estructurales y vacias van solas."""
    lineas: list[str] = []
    parrafo = ""
    for linea in (renglon.strip() for renglon in texto.split("\n")):
        if not linea or LINEA_ESTRUCTURAL.match(linea):
            if parrafo:
                lineas.append(parrafo)
                parrafo = ""
            lineas.append(linea)
        elif parrafo and not parrafo.endswith(FINALES_DE_ORACION):
            parrafo += " " + linea
        else:
            if parrafo:
                lineas.append(parrafo)
            parrafo = linea
    if parrafo:
        lineas.append(parrafo)
    return "\n".join(lineas)


def _normalizar_termino(texto: str, patron: re.Pattern, forma: str) -> str:
    """Regla 4 para un termino, sin tocar lo que YA esta escrito en su forma normalizada.

    Las formas llevan la sigla dentro ('Qualified Written Request (QWR)') y el patron
    busca las dos cosas, el nombre largo y la sigla. Sin esto, un documento que ya decia
    'Qualified Written Request (QWR)' salia 'Qualified Written Request (QWR) (Qualified
    Written Request (QWR))': el nombre largo se cambiaba por la forma entera y la sigla
    que lo seguia, otra vez.

    LO YA NORMALIZADO SE PROTEGE Y SE DEJA EN SU FORMA EXACTA. En docket se respetaba
    tal cual lo que casaba con la forma sin distinguir mayusculas, y en los terminos
    cuya forma es la misma palabra ('Promissory Note', 'Experian', 'Mortgagee')
    cualquier 'promissory note' o 'EXPERIAN' contaba como ya normalizado y se quedaba
    como estaba: la regla 4 dejaba de unificar justo esos. Ahora cada aparicion de la
    forma, con las mayusculas que traiga, se cambia por un marcador, se normaliza el
    resto y el marcador vuelve como la forma exacta. El marcador lleva \\x00, que la
    regla 2 ya quito del texto, asi que no puede confundirse con nada del documento.
    """
    protegidas = 0

    def proteger(_m: re.Match) -> str:
        nonlocal protegidas
        protegidas += 1
        return MARCADOR

    texto = re.sub(re.escape(forma), proteger, texto, flags=re.IGNORECASE)
    texto = patron.sub(forma, texto)
    return texto.replace(MARCADOR, forma) if protegidas else texto


MARCADOR = "\x00termino\x00"


def limpiar_texto(texto: str) -> str:
    """Reglas 2 a 4, y los saltos de linea repetidos compactados."""
    texto = CARACTERES_ASIATICOS.sub("", texto)
    texto = CARACTERES_CONTROL_Y_UNICODE_ROTO.sub(" ", texto)
    texto = LINEA_DE_RELLENO.sub("", texto)
    texto = PALABRA_CORTADA_CON_GUION.sub(r"\1\2", texto)
    texto = _unificar_parrafos(texto)
    for patron, forma in TERMINOS_NORMALIZADOS:
        texto = _normalizar_termino(texto, patron, forma)
    return SALTOS_DE_LINEA_EXCESIVOS.sub(SEPARADOR_DE_PARRAFO, texto).strip()


# --------------------------------------------------------------------------- #
# Un documento


@dataclass
class Limpieza:
    texto: str                       # el texto limpio (vacio si la regla 1 lo vacio)
    motivo: str = ""                 # por que la regla 1 lo considera basura, si lo hace
    vaciado: bool = False            # si la regla 1 lo vacio o solo lo marco
    marca: dict = field(default_factory=dict)

    def campos(self) -> dict:
        """Los campos de la ficha. Con el mismo nombre que docket cuando se vacia."""
        campos: dict = {"cleaning": self.marca} if self.marca else {}
        if self.motivo:
            clave = "cleaning_discarded_because" if self.vaciado else "cleaning_flagged_because"
            campos[clave] = self.motivo
        return campos


def limpiar_documento(texto: str, read_with: str = "", *,
                      control_por_pagina: bool = False) -> Limpieza:
    """Las reglas 1 a 4 sobre el texto de un documento.

    `control_por_pagina`: el lector ya separo pagina a pagina lo legible del ruido
    (PDF e imagen, ver calidad.py). Entonces la regla 1 marca y no vacia.
    """
    if not texto or not texto.strip():
        return Limpieza(texto=texto or "")
    marca = {"generated": datetime.now().isoformat(timespec="seconds"),
             "rules": REGLAS, "pii_masked": False}
    motivo = motivo_de_descarte(texto, confianza_de(read_with))
    if motivo and not control_por_pagina:
        return Limpieza(texto="", motivo=motivo, vaciado=True, marca=marca)
    return Limpieza(texto=limpiar_texto(texto), motivo=motivo or "", marca=marca)


def limpiar_ficha(ficha: dict) -> dict:
    """Una ficha ya hecha, limpia: para los JSONL que se generaron antes de este paso.

    Las fichas nuevas ya salen limpias de ficha_de. Aqui no hay control por pagina que
    consultar, salvo que la ficha diga que lo tuvo (`pages`).
    """
    texto = ficha.get("extracted_text") or ""
    if not ficha.get("was_read") or not texto or ficha.get("cleaning"):
        return ficha
    limpio = limpiar_documento(texto, ficha.get("read_with") or "",
                               control_por_pagina=bool(ficha.get("pages")))
    return {**ficha, "extracted_text": limpio.texto or "",
            "extracted_text_length": len(limpio.texto), **limpio.campos()}
