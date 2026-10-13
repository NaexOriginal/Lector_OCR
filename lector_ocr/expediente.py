"""De un documento suelto a la carpeta de su caso: por el asunto, el nombre y lo que dice.

    from lector_ocr.expediente import identificar

    caso = identificar(texto, carpetas, asunto="New filing: nyed-1:25-cv-06123",
                       nombre_archivo="task-379 - 709633-2025 - Doc 19 - ORDER.pdf")
    caso.carpeta       # 'Cato, Lamar - 100002 - 709633-2025'
    caso.se_decidio    # 'el nombre del archivo'
    caso.confirmado    # True: el indice casa Y el apellido esta dentro del documento

`carpetas` son los nombres de las carpetas de caso ('Apellido, Nombre - ID - Index'),
tal y como los da SharePoint. Este modulo no habla con SharePoint: quien llama ya
tiene esa lista y la pasa.

LAS SENALES, DE LA MAS BARATA A LA MAS CARA, como en archivar.py:

    EL ASUNTO DEL CORREO      el aviso de DocketBird trae el numero del caso
    EL NOMBRE DEL ARCHIVO     las radicaciones del Inbox lo llevan ('... - 709633-2025 - ...')
    EL INDICE DENTRO          la caratula y el sello del juzgado (INDEX NO., Case 1:25-cv-...)
    EL APELLIDO DENTRO        el de la carpeta, buscado en la caratula

EL INDICE identifica el caso, no a la persona: es el unico identificador duro que
comparten el juzgado, HubSpot y la firma. EL APELLIDO es mas debil, porque un
expediente nombra a mucha gente -- contraparte, juez, co-deudores, los abogados de la
propia firma --, asi que sirve sobre todo para CONFIRMAR el indice.

El apellido se busca AL REVES de lo que parece natural. No se pregunta 'que personas
menciona el documento y cual es cliente' (eso depende de un catalogo de clientes que
no siempre esta al dia), sino 'el apellido que da nombre a ESTA carpeta, aparece
dentro?'.

Las dos senales juntas y de acuerdo es lo que se puede archivar sin que nadie mire
(`confirmado`). Una sola se propone; dos que se contradicen no se resuelven aqui:
meter un escrito en el expediente equivocado es peor que dejarlo en la bandeja.

UN DOCUMENTO CITA VARIOS INDICES. La caratula lleva el del caso, pero el cuerpo cita
el de la accion previa o el de un caso relacionado. Por eso se toman todos y manda el
de la cabecera.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Sequence

# Cuanto texto es 'la caratula'. Una demanda federal lista a todos los demandados
# antes de empezar, y ahi puede haber quince lineas de partes.
CARATULA = 2500

# --------------------------------------------------------------------------- #
# Indices
#
# ESTATAL (Nueva York): secuencia y anio, '709633-2025', '709633/25', '9339-2014'.
#   [1-9]\d{3,6}  sin cero a la izquierda: descarta exhibits ('0001-001')
#   [-/_]         separador pegado: descarta cuenta + rango de fechas ('7000- 08.20.24')
#   \d{4}|\d{2}   el anio es de 4 o 2 digitos, nunca de 3
#   (?!\.\d)      descarta fechas encadenadas ('...-01.10.25')
#   \d{2}(?!,\d)  '5001-10,000' es un rango con separador de miles, no 5001-2010
PATRON_INDICE = re.compile(r"(?<![\w.])([1-9]\d{3,6})[-/_](\d{4}|\d{2}(?!,\d))(?!\d)(?!\.\d)")
# Un ZIP+4 con la sigla del estado tiene la forma de un indice: 'Brooklyn, NY 11228-2016'.
PATRON_ZIP4 = re.compile(r"\b[A-Z]{2}\.?\s+\d{5}-\d{4}\b")
# El que viene ETIQUETADO es el del caso, no una cita: 'INDEX NO. 850209/2016',
# 'Index No.: 709633-2025', 'Index #709633/25'.
PATRON_INDICE_ETIQUETADO = re.compile(
    r"\bINDEX\s*(?:NO\.?|NUMBER|#)\s*[:.]?\s*([1-9]\d{3,6}\s*[-/]\s*\d{2,4})\b", re.I)
# FEDERAL: distrito, anio, tipo y numero. Se escribe de muchas formas y todas son el
# mismo caso: '1:25-cv-06123' (caratula y sello ECF), 'nyed-1:25-cv-06123' (asunto
# de DocketBird), '1-25-cv-06123' y '1_26-cv-06417' (nombres de carpeta).
PATRON_FEDERAL = re.compile(
    r"(?<![\w])(\d)\s*[:_-]\s*(\d{2})\s*[-_]\s*(cv|bk|mc|cr|ap)\s*[-_]?\s*(\d{3,6})(?!\d)", re.I)


def _normalizar_estatal(secuencia: str, anio: str) -> str | None:
    """'709633/25' -> '709633-2025'. None si en realidad es una fecha."""
    sec, an = int(secuencia), int(anio)
    # Un indice empieza con un consecutivo, no con un anio: '2024-12' es una fecha.
    if 1990 <= sec <= 2035:
        return None
    if len(anio) == 4:
        if not 1990 <= an <= 2035:
            return None
    elif len(anio) == 2:
        an = 2000 + an if an <= 35 else 1900 + an
    else:
        return None
    return f"{sec}-{an}"


def _normalizar_federal(distrito: str, anio: str, tipo: str, numero: str) -> str:
    """Todas las formas a '1-25-cv-06123'. El numero va a cinco cifras: '6417' y
    '06417' son el mismo caso."""
    return f"{int(distrito)}-{anio}-{tipo.lower()}-{int(numero):05d}"


def indices_en(texto: str) -> list[str]:
    """Los indices de un texto, estatales y federales, ya normalizados, en el orden
    en que aparecen. Vale para un asunto, un nombre de archivo o un documento."""
    texto = str(texto or "")
    hallados: list[tuple[int, str]] = []
    for m in PATRON_FEDERAL.finditer(texto):
        hallados.append((m.start(), _normalizar_federal(*m.groups())))
    # Lo federal fuera antes de buscar lo estatal: '25-cv-06123' no tiene la forma de
    # un indice estatal, pero '1-25' de su principio podria acabar pareciendolo.
    resto = PATRON_FEDERAL.sub(lambda m: " " * len(m.group(0)), texto)
    resto = PATRON_ZIP4.sub(lambda m: " " * len(m.group(0)), resto)
    for m in PATRON_INDICE.finditer(resto):
        normalizado = _normalizar_estatal(*m.groups())
        if normalizado:
            hallados.append((m.start(), normalizado))
    salida: list[str] = []
    for _, indice in sorted(hallados):
        if indice not in salida:
            salida.append(indice)
    return salida


def indices_del_documento(texto: str) -> list[str]:
    """Los indices de un documento, el del caso primero.

    Orden: los etiquetados ('INDEX NO.'), luego los de la caratula, luego el resto.
    Un escrito cita otros pleitos en el cuerpo; el suyo esta en la cabecera.
    """
    texto = str(texto or "")
    etiquetados = []
    for m in PATRON_INDICE_ETIQUETADO.finditer(texto):
        for indice in indices_en(m.group(1).replace(" ", "")):
            if indice not in etiquetados:
                etiquetados.append(indice)
    salida = list(etiquetados)
    for indice in indices_en(texto[:CARATULA]) + indices_en(texto):
        if indice not in salida:
            salida.append(indice)
    return salida


# --------------------------------------------------------------------------- #
# Carpetas
#
# El nombre de una carpeta de caso es 'Apellido, Nombre - ID - Index', con la marca
# de cerrado detras del nombre ('... - Closed - 600926 - 9339-2014') o, en el formato
# viejo, delante ('Closed - ...'). Se entienden los dos.
PREFIJO_CERRADO = "closed - "
COLA_CERRADO = " - closed"

# Palabras que dan nombre a una carpeta sin distinguir a nadie: un documento
# cualquiera las contiene. 'petroff' y 'amshen' son la propia firma, que aparece en
# todos los escritos que redacta.
NO_DISTINGUEN = frozenset(
    """llc inc corp ltd the and new york city county court group llp pllc company
    associates properties realty holdings trust estate management enterprises
    partners development construction ventures capital consulting services
    incorporated corporation limited avenue street road place house
    petroff amshen
    action federal page plaintiff defendant defendants exhibit index supreme
    district eastern southern western northern civil case matter file closed""".split()
)


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", sin_tildes.lower())).strip()


@dataclass(frozen=True)
class Caso:
    """Una carpeta de caso, descompuesta."""

    carpeta: str
    base: str            # 'Cato, Lamar'
    id_interno: str      # '100002'
    indice: str          # '709633-2025' o '1-25-cv-06123', ya normalizado; '' si no tiene
    cerrado: bool

    @property
    def apellidos(self) -> frozenset[str]:
        """Los tokens del nombre que de verdad identifican a alguien.

        Con coma ('Peters, Lennon') el apellido es lo que va delante, y solo eso: si
        valiera el nombre de pila, 'Oliveira, Maria' casaria con cualquier Maria.
        Sin coma no se sabe cual es cual y valen todos.
        """
        base = self.base.split(",")[0] if "," in self.base else self.base
        return frozenset(t for t in normalizar(re.sub(r"[.\-_&]", " ", base)).split()
                         if len(t) > 2 and t not in NO_DISTINGUEN)

    @property
    def nombre_completo(self) -> frozenset[str]:
        return frozenset(t for t in normalizar(self.base).split()
                         if len(t) > 2 and t not in NO_DISTINGUEN)


def descomponer(carpeta: str) -> Caso:
    """'Gubicza, Ferenc - Closed - 600926 - 9339-2014' -> Caso.

    Se parte por la DERECHA y solo dos veces: la base puede llevar ' - ' dentro, pero
    el ID y el indice son siempre los dos ultimos campos. Lo que no tiene esa forma
    no se adivina: queda todo en la base, sin ID ni indice.
    """
    texto = str(carpeta).strip()
    cerrado = texto.lower().startswith(PREFIJO_CERRADO)
    if cerrado:
        texto = texto[len(PREFIJO_CERRADO):]
    partes = [p.strip() for p in texto.rsplit(" - ", 2)]
    if len(partes) == 3 and re.fullmatch(r"\d{3,7}", partes[1]):
        base, id_interno, crudo = partes
    else:
        base, id_interno, crudo = texto, "", ""
    if base.lower().endswith(COLA_CERRADO):
        base, cerrado = base[: -len(COLA_CERRADO)].strip(), True
    indice = next(iter(indices_en(crudo)), "") if crudo not in ("", "0") else ""
    return Caso(str(carpeta), base, id_interno, indice, cerrado)


@functools.lru_cache(maxsize=8)
def _catalogo(carpetas: tuple[str, ...]) -> tuple[dict[str, list[Caso]], list[Caso]]:
    """indice -> casos, y la lista de casos. Se arma una vez por lista de carpetas."""
    casos = [descomponer(c) for c in carpetas]
    por_indice: dict[str, list[Caso]] = {}
    for caso in casos:
        if caso.indice:
            por_indice.setdefault(caso.indice, []).append(caso)
    return por_indice, casos


# --------------------------------------------------------------------------- #
@dataclass
class Identificacion:
    """A que caso pertenece un documento, y con que respaldo."""

    carpeta: str | None = None
    id_interno: str = ""
    indice: str = ""
    # Que senal lo decidio: 'el asunto', 'el nombre del archivo', 'el indice del
    # documento', 'el apellido del documento'. Vacio si no se decidio.
    se_decidio: str = ""
    # Un booleano y no una frase: lo que decide si algo se archiva solo no puede
    # depender de como este redactado un mensaje.
    confirmado: bool = False
    razon: str = "nothing in the subject, the file name or the document ties it to a case"
    candidatos: list[str] = field(default_factory=list)
    indices_en_documento: list[str] = field(default_factory=list)


def _apellido_dentro(caso: Caso, palabras: set[str]) -> bool:
    """El apellido de la carpeta, esta entre esas palabras? Por palabra completa: por
    subcadena, 'Rao' aparece dentro de 'ratio'. Admite erratas del OCR o de tecleo
    ('Vaughn' / 'Vaughan')."""
    return any(a in palabras or any(len(p) > 3 and SequenceMatcher(None, a, p).ratio() >= 0.85
                                    for p in palabras if p[:1] == a[:1])
               for a in caso.apellidos)


def _por_apellido(casos: list[Caso], caratula: set[str]) -> list[Caso]:
    """Los casos cuyas palabras distintivas aparecen TODAS en la caratula.

    Todas y no una: 'Anderson.Robert' tiene dos, y una demanda de Robert Holston no
    puede hacer candidato a Anderson. Si quedan varios, desempata el nombre completo
    (apellido y nombre de pila), pero solo si asi queda UNO.
    """
    # Para PROPONER un caso solo por apellido se exigen apellidos de mas de tres
    # letras: 'Lee' o 'Rao' aparecen en cualquier caratula. Para CONFIRMAR un indice
    # (_apellido_dentro) si valen, porque ahi el indice ya eligio el caso.
    hallados = [c for c in casos
                if c.apellidos and all(len(a) > 3 for a in c.apellidos) and c.apellidos <= caratula]
    if len(hallados) > 1:
        completos = [c for c in hallados if c.nombre_completo <= caratula]
        if len(completos) == 1:
            return completos
    return hallados


def identificar(texto: str, carpetas: Sequence[str], *, asunto: str = "",
                nombre_archivo: str = "") -> Identificacion:
    """El caso de un documento, mirando el asunto, el nombre y lo que dice por dentro.

    `texto` es el documento leido (puede venir vacio si no se pudo leer: entonces
    solo cuentan el asunto y el nombre, y nada queda confirmado).
    """
    por_indice, casos = _catalogo(tuple(carpetas))
    palabras = set(normalizar(texto).split())
    caratula = set(normalizar(str(texto or "")[:CARATULA]).split())
    del_documento = indices_del_documento(texto)
    salida = Identificacion(indices_en_documento=del_documento)

    def casan(indices: list[str]) -> list[Caso]:
        vistos: list[Caso] = []
        for indice in indices:
            for caso in por_indice.get(indice, []):
                if caso not in vistos:
                    vistos.append(caso)
        return vistos

    # El indice del documento que cuenta para contradecir es el de la cabecera: los
    # del cuerpo son citas de otros pleitos.
    de_cabecera = casan(indices_del_documento(str(texto or "")[:CARATULA]))
    senales = [("el asunto", casan(indices_en(asunto))),
               ("el nombre del archivo", casan(indices_en(nombre_archivo))),
               ("el indice del documento", de_cabecera or casan(del_documento))]

    for senal, candidatos in senales:
        if not candidatos:
            continue
        if len(candidatos) > 1:
            # El mismo indice en dos carpetas (un duplicado, o el caso abierto y su
            # copia cerrada). Desempata el apellido, y solo si deja uno.
            con_apellido = [c for c in candidatos if _apellido_dentro(c, caratula)]
            if len(con_apellido) != 1:
                salida.candidatos = [c.carpeta for c in candidatos]
                salida.razon = (f"AMBIGUOUS: {len(candidatos)} case folders share the "
                                f"index from {senal}; someone has to decide")
                return salida
            candidatos = con_apellido
        caso = candidatos[0]
        # Si la cabecera del documento apunta a OTRO caso, no se elige: el asunto o el
        # nombre pueden estar mal tecleados, y el papel tambien puede ser de otro.
        otros = [c for c in de_cabecera if c != caso]
        if otros and caso not in de_cabecera:
            salida.candidatos = [caso.carpeta] + [c.carpeta for c in otros]
            salida.razon = (f"CONFLICT: {senal} points to {caso.carpeta} but the "
                            f"document header points to {otros[0].carpeta}")
            return salida
        salida.carpeta, salida.id_interno, salida.indice = caso.carpeta, caso.id_interno, caso.indice
        salida.se_decidio = senal
        if palabras and _apellido_dentro(caso, palabras):
            salida.confirmado = True
            salida.razon = "the index matches and the client's surname is in the document"
        elif not palabras:
            salida.razon = "index only: the document could not be read to confirm it"
        else:
            salida.razon = "index only: the client's surname is not in the document"
        return salida

    # Sin indice que case: el apellido de alguna carpeta en la caratula. Se propone,
    # nunca se confirma -- un apellido solo no basta para archivar sin mirar.
    hallados = _por_apellido(casos, caratula) if caratula else []
    if len(hallados) == 1:
        caso = hallados[0]
        salida.carpeta, salida.id_interno, salida.indice = caso.carpeta, caso.id_interno, caso.indice
        salida.se_decidio = "el apellido del documento"
        salida.razon = "surname only, no index number matched"
    elif hallados:
        salida.candidatos = [c.carpeta for c in hallados][:10]
        salida.razon = (f"AMBIGUOUS: {len(hallados)} case folders by surname and no "
                        "index number matched; someone has to decide")
    return salida
