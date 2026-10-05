"""
Regla de duplicacion basada en el Matter Name.

Que un cliente tenga varios matters del mismo case type NO significa duplicacion:
un cliente puede tener tres foreclosures de tres propiedades distintas. Lo que decide
es que tan parecido es el nombre del matter y que tan especifico es.

    Alfonzo Green (Index# 700403/2023)
    Alfonzo Green - Index# 700403/2023        -> DUPLICADO (mismo index, casi identicos)

    Eli David Cohan - Foreclosure - 1425 Peak Corp - Art 15
    Eli David Cohan - Foreclosure - 1425 Point Breeze Place Far Rockaway, NY 11691
                                              -> CASO DISTINTO (otra propiedad, otro asunto)

Un nombre que solo trae el cliente, o el cliente y el tipo de caso, no identifica nada:

    Deutsche Bank National Trust Company - Foreclosure     -> generico

Si ese generico convive con otro matter del mismo cliente que si esta especificado,
lo mas probable es que sea el duplicado que quedo sin llenar. Si el cliente no tiene
ningun otro matter, no es duplicado: es un registro incompleto que hay que completar.

Veredictos que emite:
    DUPLICADO                 nombre casi identico, mismo index o misma direccion
    DUPLICADO SIN ESPECIFICAR nombre generico junto a un hermano especificado
    CASO DISTINTO             identificadores o direcciones distintas
    INCOMPLETO                nombre generico y sin hermano que lo desambigue
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd

# Que tan parecidos deben ser dos nombres para considerarlos el mismo matter
UMBRAL_DUPLICADO = 0.90
UMBRAL_GENERICOS = 0.85

# Tipos de caso: aparecen en el nombre pero no identifican el caso
TIPOS_CASO = {
    "foreclosure", "identity", "theft", "wrongful", "reporting", "respa", "violation",
    "chapter", "article", "art", "time", "barred", "debt", "appeal", "fcra", "id", "wf",
    "bankruptcy", "trial", "discovery", "settlement", "consult", "dispute",
}

# Palabras de relleno que tampoco identifican nada
RELLENO = {
    "the", "and", "for", "matter", "case", "client", "new", "york", "ny", "nj",
    "street", "avenue", "ave", "road", "blvd", "boulevard", "drive", "lane", "place",
    "court", "apt", "suite", "unit", "floor", "brooklyn", "queens", "bronx", "manhattan",
    "staten", "island", "jamaica", "corona", "flushing", "west", "east", "north", "south",
    "llc", "inc", "corp", "ltd", "co", "index",
}

# Marcadores que hacen que dos matters del mismo inmueble sean asuntos distintos
MARCADORES = {
    "prior": "prior",
    "pror": "prior",
    "previous": "prior",
    "appeal": "appeal",
    "trial": "trial",
    "art": "articulo15",
    "article": "articulo15",
    "bankruptcy": "bancarrota",
    "chapter": "bancarrota",
    "respa": "respa",
    "discovery": "discovery",
    "settlement": "settlement",
    "defend": "defensa",
}

# Numero de indice del estado de Nueva York: secuencia + anio ("703250-2015",
# "706525/18", "503536_2020"). Este es el UNICO extractor del proyecto; sp_indices.py
# y verificar_casos.py lo importan de aqui.
#
# El patron descarta las numeraciones que se le parecen y aparecen a montones en los
# nombres de archivo de SharePoint:
#   [1-9]\d{3,6}  sin cero a la izquierda: descarta exhibits y docs federales
#                 ("0001-001", "nyed-2_2019-cv-04123-00041-002")
#   [-/_]         separador pegado: descarta cuenta + rango de fechas ("7000- 08.20.24")
#   \d{4}|\d{2}   el anio es de 4 o 2 digitos, nunca de 3
#   (?!\.\d)      descarta fechas encadenadas ("...-01.10.25")
#   \d{2}(?!,\d)  un anio de dos cifras no puede seguir con coma y mas cifras: eso
#                 es un rango con separador de miles ("5001-10,000" no es 5001-2010).
#                 La restriccion es solo para el anio corto: en un indice de cuatro
#                 cifras la coma es puntuacion normal ("508907/2015, que grava...").
PATRON_INDICE = re.compile(r"(?<![\w.])([1-9]\d{3,6})[-/_](\d{4}|\d{2}(?!,\d))(?!\d)(?!\.\d)")

# Codigo postal ZIP+4 precedido de la sigla del estado: 'Brooklyn, NY 11228-2016'.
# Tiene exactamente la forma de un indice y hay que sacarlo antes de buscarlos.
PATRON_ZIP4 = re.compile(r"\b[A-Z]{2}\.?\s+\d{5}-\d{4}\b")
PATRON_DIRECCION = re.compile(r"\b(\d{1,5}(?:-\d{1,5})?)\s+(?:[NSEW]\.?\s+)?([A-Za-z]{3,})")


# --------------------------------------------------------------------------- #
def normalizar(texto) -> str:
    if pd.isna(texto):
        return ""
    sin_tildes = (
        unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", sin_tildes.lower())).strip()


def normalizar_indice(secuencia: str, anio: str) -> str | None:
    """Deja el indice como 'secuencia-aaaa'. Devuelve None si en realidad es una fecha.

    Normalizar importa: '706525/18' y '706525-2018' son el MISMO caso, y sin esto
    dos matters del mismo expediente no se reconocerian entre si.
    """
    sec, an = int(secuencia), int(anio)

    # Un indice empieza con un consecutivo, no con un anio: "2024-12" es una fecha
    if 1990 <= sec <= 2035:
        return None

    if len(anio) == 4:
        if not 1990 <= an <= 2035:
            return None
    elif len(anio) == 2:
        an = 2000 + an if an <= 35 else 1900 + an
    else:
        return None  # tres digitos no es un anio: es numeracion de documento

    return f"{sec}-{an}"


def indices(nombre) -> set[str]:
    """Numeros de indice de un texto, ya normalizados a 'secuencia-aaaa'.

    El descarte por contexto va aqui y no en el patron porque necesita mirar lo que
    rodea al numero, y el patron corre tambien sobre nombres de archivo, donde no hay
    contexto que mirar.
    """
    texto = str(nombre or "")
    # Un ZIP+4 tiene la misma forma que un indice: 'Chester, PA 19016-2000'. Lo que
    # los separa es la sigla del estado justo antes.
    texto = PATRON_ZIP4.sub(" ", texto)
    salida = set()
    for secuencia, anio in PATRON_INDICE.findall(texto):
        normalizado = normalizar_indice(secuencia, anio)
        if normalizado:
            salida.add(normalizado)
    return salida


def direcciones(nombre) -> set[str]:
    """Numero + calle, que es lo que distingue una propiedad de otra."""
    texto = re.sub(r"\d+\s*/\s*\d+[A-Za-z]?", " ", str(nombre or ""))
    salida = set()
    for numero, calle in PATRON_DIRECCION.findall(texto):
        calle = calle.lower()
        if calle not in RELLENO or calle in {"street", "avenue"}:
            salida.add(numero.replace("-", "") + calle[:6])
    return salida


def marcadores(nombre) -> set[str]:
    return {MARCADORES[t] for t in normalizar(nombre).split() if t in MARCADORES}


def descriptor(nombre, cliente) -> set[str]:
    """Lo que queda del nombre al quitarle el cliente, el tipo de caso y el relleno.

    El nombre del contacto no siempre coincide con el que aparece en el matter
    ("Sara Boriskin" vs "Sara Zahava Boriskin"), asi que tambien se descarta el
    prefijo del propio nombre: lo que va antes del primer guion.
    """
    prefijo = re.split(r"\s-\s|\(", str(nombre or ""))[0]
    tokens_cliente = set(normalizar(cliente).split()) | set(normalizar(prefijo).split())
    salida = set()
    for token in normalizar(nombre).split():
        if token in tokens_cliente or token in TIPOS_CASO or token in RELLENO:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        salida.add(token)
    return salida


def es_generico(nombre, cliente) -> bool:
    """El nombre solo dice el cliente, o el cliente y el tipo de caso."""
    return not (descriptor(nombre, cliente) or indices(nombre) or direcciones(nombre))


def personas(nombre) -> set[str]:
    """Nombres propios del matter: palabras que no son tipo de caso ni relleno."""
    return {
        t
        for t in normalizar(nombre).split()
        if len(t) >= 3 and not t.isdigit()
        and t not in TIPOS_CASO and t not in RELLENO and t not in MARCADORES
    }


def nombre_en_matter(matter) -> str:
    """El nombre propio que encabeza el titulo de un matter.

    HubSpot bautiza los matters como 'Ravindra Bansi - Foreclosure - 93-01 204th
    Street'. Ese nombre suele ser el cliente de verdad, y no siempre coincide con el
    contacto asociado: en 1.889 de 4.944 matters no comparten ni una palabra (el
    contacto de Ravindra Bansi es 'Shellpoint Servicing', un administrador
    hipotecario). Emparejar carpetas mirando solo el contacto deja fuera esos casos.

    Se recorre el titulo por tramos y se devuelve el primero que parezca un nombre.
    'Foreclosure - Roland Severe' salta el tipo de caso y devuelve 'Roland Severe';
    'Identity Theft' no devuelve nada, porque sus dos palabras son tipo de caso.
    """
    if pd.isna(matter):
        return ""
    for tramo in str(matter).split(" - "):
        tramo = tramo.strip()
        if len(personas(tramo)) >= 2:
            return tramo
        # Empresas bautizadas por su direccion ('2466 West 3rd Street LLC'): todas
        # sus palabras son relleno, asi que la regla de arriba nunca las ve. El
        # sufijo societario es lo que las distingue de un tramo que es solo una
        # direccion ('93-01 204th Street Hollis' no lo lleva).
        palabras = normalizar(tramo).split()
        if len(palabras) >= 3 and palabras[-1] in {"llc", "inc", "corp", "ltd"}:
            return tramo
    return ""


# El nombre nuevo de una carpeta de caso:
#     {APELLIDO}, {NOMBRE} - Closed - {ID_INTERNO} - {INDEX_NUMBER}
#
# La marca de cerrado va DETRAS del nombre del cliente, no delante (cambio pedido
# el 23-sep-2026). Delante, SharePoint ordenaba la lista por estado y no por
# persona: los cerrados se amontonaban todos en la letra C, asi que para buscar a
# alguien habia que saber de antemano si su caso estaba cerrado.
MARCA_CERRADO = "Closed"

# Como se escribia hasta el 23-sep-2026. Se sigue LEYENDO, y no por cortesia: hoy
# hay 1.789 carpetas con la marca delante y las seguira habiendo hasta que termine
# el renombrado. Un lector que solo entendiera el formato nuevo daria esas 1.789
# por ABIERTAS y no se quejaria -- el fallo callado de siempre.
PREFIJO_CERRADO = "Closed - "

# Separadores que sobran al principio o al final del nombre que ya trae la carpeta.
# Existen de verdad: 'Agramonte, Milciades -' y 'DeligianniDimitra-888854-' vienen
# asi de SharePoint, y pegarles ' - 989207 - 0' detras produce un guion doble.
SOBRA_EN_LOS_BORDES = re.compile(r"^[\s\-,.;_&]+|[\s\-,.;_&]+$")


def limpiar_base(nombre) -> str:
    """Deja el nombre de la carpeta listo para pegarle los campos de atras.

    No toca el interior mas alla de los espacios repetidos: 'DeBrosse. James -Appeal-'
    tiene que seguir diciendo 'Appeal'. Y no quita el '(1)' del final, que parece
    basura pero distingue una carpeta de su copia de conflicto: quitarlo fundiria
    dos carpetas distintas en un mismo nombre.
    """
    return SOBRA_EN_LOS_BORDES.sub("", re.sub(r"\s+", " ", str(nombre)))


def componer_nombre(base, id_interno, index_number, seccion) -> str:
    """El nombre nuevo, armado en un solo sitio.

    Estaba repetido en cuatro scripts y por eso el guion doble se colo en tres
    carpetas: se arreglaba en uno y seguia saliendo por los otros.
    """
    marca = f" - {MARCA_CERRADO}" if seccion == "Closed Matters" else ""
    return f"{limpiar_base(base)}{marca} - {id_interno} - {index_number or '0'}"


def partir_nombre(nombre) -> tuple[str, str, str, bool]:
    """Lo contrario de componer_nombre: (base, ID_INTERNO, index, cerrado).

    ENTIENDE LOS DOS FORMATOS, el de la marca delante y el de la marca detras, y
    esa es toda la razon de que exista. Esta descomposicion estaba copiada en
    cuatro scripts, cada uno con su propio `startswith('Closed - ')`; mover la
    marca habria dejado a los cuatro leyendo 'abierto' en carpetas cerradas, sin
    error y sin aviso. Un sitio que sepa leer las dos formas cuesta menos que
    cuatro que sepan leer una.

    Se parte por la DERECHA y solo dos veces: la base puede llevar el separador
    dentro ('104-22NB - Golfinopoulos - 690196 - 500832-2021'), pero el ID y el
    indice son siempre los dos ultimos campos.
    """
    texto = str(nombre).strip()

    # Formato viejo: la marca iba delante. Se retira aqui para que lo de abajo
    # valga igual para los dos.
    cerrado = texto.lower().startswith(PREFIJO_CERRADO.lower())
    if cerrado:
        texto = texto[len(PREFIJO_CERRADO):]

    partes = [p.strip() for p in texto.rsplit(" - ", 2)]
    if len(partes) == 3 and re.fullmatch(r"\d{3,7}", partes[1]):
        base, id_interno, index_number = partes
    else:
        # No tiene la forma '... - {ID} - {index}'. Mejor no adivinar: se deja
        # todo en la base y los dos campos vacios.
        base, id_interno, index_number = texto.strip(), "", ""

    # Formato nuevo: la marca va detras de la base.
    cola = f" - {MARCA_CERRADO}"
    if base.lower().endswith(cola.lower()):
        base, cerrado = base[: -len(cola)].strip(), True

    return base, id_interno, index_number, cerrado


# Cuanto se tienen que parecer dos apellidos para darlos por el mismo. Absorbe las
# erratas de tecleo ('Vaughn' contra 'Vaughan', 'Sarranga' contra 'Saranga') sin
# juntar apellidos distintos.
PARECIDO_APELLIDO = 0.85


def apellidos_de_carpeta(carpeta) -> set[str]:
    """Los tokens del nombre de la carpeta que pueden ser el apellido del cliente.

    Con coma ('Peters, Lennon') el apellido es lo que va delante, y solo eso: si se
    admitiera tambien el nombre de pila, 'Oliveira, Maria' casaria con cualquier
    Maria del expediente. Sin coma no hay forma de saber cual token es cual, asi que
    valen todos.
    """
    base = str(carpeta).split(",")[0] if "," in str(carpeta) else str(carpeta)
    return {t for t in normalizar(re.sub(r"[.\-_&]", " ", base)).split() if len(t) > 2}


def es_el_cliente(persona, carpeta) -> bool:
    """La persona hallada DENTRO de los documentos, ¿es el dueño de la carpeta?

    Hace falta porque un expediente nombra a mucha gente que no es el cliente:
    contrapartes, jueces, co-deudores y los abogados de la propia firma. Sin este
    filtro, la carpeta 'Perniciaro' se resolvia como el caso de Steven Johnson y
    'Posa' heredaba la materia de Patricio Vallejos, que es cliente de otra carpeta.

    El criterio es el apellido, no el nombre de pila, y se compara contra el apellido
    que da el nombre de la carpeta. Un nombre de pila compartido no prueba nada.
    """
    suyos = [t for t in normalizar(persona).split() if len(t) > 2]
    if not suyos:
        return False
    # Una razon social no tiene apellido: 'Nassau Community Holding LLC' termina en
    # 'llc'. Ahi vale cualquier token, que es como se reconocen entre si.
    candidatos = suyos if len(suyos) > 2 else suyos[-1:]
    propios = apellidos_de_carpeta(carpeta)
    return any(
        t == c or SequenceMatcher(None, t, c).ratio() >= PARECIDO_APELLIDO
        for t in candidatos
        for c in propios
    )


def parecido(a, b) -> float:
    """Similitud insensible al orden: 'Foreclosure - X' y 'X - Foreclosure' son lo mismo."""
    directo = SequenceMatcher(None, normalizar(a), normalizar(b)).ratio()
    ordenado = SequenceMatcher(
        None, " ".join(sorted(normalizar(a).split())), " ".join(sorted(normalizar(b).split()))
    ).ratio()
    return max(directo, ordenado)


# --------------------------------------------------------------------------- #
def comparar(a: dict, b: dict) -> tuple[str, str]:
    """Compara dos matters del mismo cliente. Devuelve (veredicto, motivo)."""
    # El numero de indice va PRIMERO: es el identificador que da el estado de Nueva
    # York y pesa mas que cualquier marca escrita en el nombre. Si dos matters comparten
    # indice son el mismo caso, aunque uno diga "Prior Case" (esa nota suele describir
    # la relacion entre expedientes, no que sean distintos).
    comunes = a["indices"] & b["indices"]
    if comunes:
        return "DUPLICADO", f"mismo index number: {', '.join(sorted(comunes))}"
    if a["indices"] and b["indices"]:
        return "CASO DISTINTO", (
            f"index distinto: {'/'.join(sorted(a['indices']))} vs "
            f"{'/'.join(sorted(b['indices']))}"
        )

    # Sin indices que decidan, un asunto distinto (apelacion, accion previa, Art 15)
    # no es un duplicado aunque se trate del mismo inmueble
    if a["marcadores"] != b["marcadores"]:
        distintos = a["marcadores"] ^ b["marcadores"]
        return "CASO DISTINTO", f"asuntos distintos ({', '.join(sorted(distintos))})"

    comunes = a["archivos"] & b["archivos"]
    if comunes:
        return "DUPLICADO", f"mismo file number: {', '.join(sorted(comunes))}"
    if a["archivos"] and b["archivos"]:
        return "CASO DISTINTO", (
            f"file number distinto: {'/'.join(sorted(a['archivos']))} vs "
            f"{'/'.join(sorted(b['archivos']))}"
        )

    # Un mismo contacto puede estar asociado a matters de otras personas: si los
    # nombres hablan de gente distinta, no son el mismo caso
    if a["personas"] and b["personas"] and not (a["personas"] & b["personas"]):
        return "CASO DISTINTO", (
            f"los nombres se refieren a personas distintas "
            f"({' '.join(sorted(a['personas']))} vs {' '.join(sorted(b['personas']))})"
        )

    comunes = a["direcciones"] & b["direcciones"]
    if comunes:
        return "DUPLICADO", "misma direccion en los dos nombres"
    if a["direcciones"] and b["direcciones"]:
        return "CASO DISTINTO", "direcciones distintas: son dos propiedades"

    ratio = parecido(a["nombre"], b["nombre"])
    if ratio >= UMBRAL_DUPLICADO:
        return "DUPLICADO", f"nombres casi identicos ({ratio:.0%} de coincidencia)"

    # Uno de los dos no dice nada: es el que probablemente sobra
    if a["generico"] != b["generico"]:
        return "DUPLICADO SIN ESPECIFICAR", "un nombre no especifica el caso y el otro si"

    if a["generico"] and b["generico"]:
        if ratio >= UMBRAL_GENERICOS:
            return "DUPLICADO", f"dos nombres genericos casi iguales ({ratio:.0%})"
        return "CASO DISTINTO", "genericos pero con nombres diferentes"

    return "CASO DISTINTO", f"nombres diferentes ({ratio:.0%} de coincidencia)"


def resumir(nombre, cliente, indices_hs: set[str], archivos_hs: set[str]) -> dict:
    """Todo lo que se necesita de un matter para compararlo con sus hermanos."""
    return {
        "nombre": nombre,
        "indices": indices(nombre) | set(indices_hs),
        "archivos": set(archivos_hs),
        "direcciones": direcciones(nombre),
        "marcadores": marcadores(nombre),
        "personas": personas(nombre),
        "generico": es_generico(nombre, cliente),
    }


# Prioridad al consolidar: si un matter es duplicado de alguno, eso manda
PRIORIDAD = ["DUPLICADO", "DUPLICADO SIN ESPECIFICAR", "CASO DISTINTO"]


def clasificar_cliente(matters: list[dict]) -> list[tuple[str, str]]:
    """Veredicto y motivo para cada matter de un mismo cliente."""
    if len(matters) == 1:
        unico = matters[0]
        if unico["generico"]:
            return [
                ("INCOMPLETO", "el nombre solo dice el cliente o el tipo de caso: falta el caso")
            ]
        return [("CASO UNICO", "el cliente tiene un solo matter y esta especificado")]

    veredictos: list[list[tuple[str, str]]] = [[] for _ in matters]
    for i in range(len(matters)):
        for j in range(i + 1, len(matters)):
            veredicto, motivo = comparar(matters[i], matters[j])
            if veredicto == "DUPLICADO SIN ESPECIFICAR":
                # Solo el generico carga el veredicto; el especificado no
                generico, otro = (i, j) if matters[i]["generico"] else (j, i)
                veredictos[generico].append(
                    (
                        veredicto,
                        f"no especifica el caso y el cliente si tiene otro matter "
                        f"especificado ({matters[otro]['nombre']})",
                    )
                )
                veredictos[otro].append(("CASO DISTINTO", "esta especificado"))
            else:
                veredictos[i].append((veredicto, f"{motivo} (vs {matters[j]['nombre']})"))
                veredictos[j].append((veredicto, f"{motivo} (vs {matters[i]['nombre']})"))

    salida = []
    for propios, matter in zip(veredictos, matters):
        for nivel in PRIORIDAD:
            elegido = next((v for v in propios if v[0] == nivel), None)
            if elegido:
                salida.append(elegido)
                break
        else:
            salida.append(("CASO DISTINTO", "sin coincidencia con los otros matters"))
    return salida
