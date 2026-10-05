"""De un documento suelto a la carpeta del cliente: quien es, sin saberlo de antemano.

Todo lo anterior daba por sabido de quien era la carpeta y solo preguntaba QUE hay
dentro. Un archivo que llega a la bandeja no trae eso: no hay carpeta que lo
respalde, asi que la identidad tiene que salir del propio papel.

Dos senales lo consiguen, y no valen lo mismo:

    EL NUMERO DE INDICE   identifica el caso, no a la persona. Es el unico
                          identificador duro que comparten el juzgado, HubSpot y
                          nosotros. Si aparece y casa con una carpeta, se acabo.
    EL APELLIDO DEL CLIENTE   mas debil, porque un expediente nombra a mucha gente:
                          contraparte, juez, co-deudores y los abogados de la propia
                          firma.

El apellido se busca AL REVES de lo que parece natural. No se pregunta 'que personas
menciona el documento y cual de ellas es cliente', porque eso depende de que el
cliente este en el catalogo de HubSpot, y no siempre esta: el primer documento real
que entro a la bandeja era de Helen Angert, que tiene carpeta y no tiene ficha, asi
que la senal no llegaba a dispararse nunca. Se pregunta al reves: 'el apellido que da
nombre a ESTA carpeta, aparece dentro del documento?'. Eso no depende de HubSpot, y
ademas convierte la segunda senal en una CONFIRMACION de la primera, que es para lo
que sirve de verdad.

Las dos juntas y de acuerdo es lo que se puede archivar sin que nadie mire. Una
sola, o dos que se contradicen, se informa y decide una persona: meter un escrito en
el expediente equivocado es peor que dejarlo en la bandeja.

UN DOCUMENTO TRAE VARIOS INDICES. La caratula lleva el del caso, pero el cuerpo cita
el de la accion previa, el del caso relacionado y a veces el de un machote. Por eso
NO se toma 'el indice' sino todos, y se prefiere el que aparece en la cabecera: ahi
esta la caratula.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from crear_subcarpetas import CONTENEDOR, NOMBRES_CONTENEDOR, RAIZ_PERMITIDA
from regla_nombres import (apellidos_de_carpeta, es_el_cliente,
                           indices as indices_de, normalizar, partir_nombre)
from sp_indices import cargar_arbol

# Un caso cuyo campo de indice es esto es un caso al que todavia no le conocemos el
# numero. No es un indice con el que se pueda casar nada.
SIN_INDICE = {"", "0", "-", "s/n"}

# Cuanto texto es 'la caratula'. Mas largo que la cabecera que usa clasificacion.py
# porque una demanda federal lista a todos los demandados antes de empezar, y ahi
# puede haber quince lineas de partes.
CARATULA = 2500


@dataclass(frozen=True)
class Caso:
    """Una carpeta de caso del contenedor de destino, descompuesta."""

    carpeta: str
    base: str            # 'Angert, Helen'
    id_interno: str      # '690080'
    index_number: str    # '508065-2026', ya normalizado
    plantilla: str       # Foreclosure / FCRA
    cerrado: bool

    @property
    def ruta(self) -> str:
        return f"/{RAIZ_PERMITIDA}/{CONTENEDOR}/{self.carpeta}"


def descomponer(nombre: str) -> tuple[str, str, str, bool]:
    """'Angert, Helen - Closed - 690080 - 508065-2026' -> partes, con el indice ya
    normalizado.

    El reparto en campos lo hace regla_nombres.partir_nombre, que entiende tanto la
    marca de cerrado delante (formato viejo) como detras (el de ahora). Aqui solo
    se añade lo propio de este modulo: pasar el indice por el normalizador.
    """
    base, id_interno, index_number, cerrado = partir_nombre(nombre)
    normalizado = indices_de(index_number) if index_number else set()
    return base, id_interno, (next(iter(normalizado), "") if normalizado else ""), cerrado


@functools.cache
def casos() -> tuple[Caso, ...]:
    """Los casos del contenedor de destino, leidos del arbol ya recorrido.

    La plantilla se deduce mirando las subcarpetas que el caso TIENE, no una tabla
    aparte: la estructura real manda sobre lo que diga cualquier inventario, y asi
    no hay dos sitios que puedan discrepar.
    """
    arbol = cargar_arbol()
    # Los dos nombres del contenedor, no solo el de ahora: un arbol recorrido antes
    # del renombrado dice 'Matter (New Names)', y comparar solo contra 'Matters'
    # devolveria cero casos sin dar un solo error.
    dentro = arbol["Ubicacion"].isin(NOMBRES_CONTENEDOR)

    hijas: dict[str, set[str]] = {}
    for fila in arbol[dentro & (arbol["Nivel"] == 3)].itertuples():
        hijas.setdefault(fila.Carpeta, set()).add(fila.name)

    salida = []
    for fila in arbol[dentro & (arbol["Nivel"] == 2) & arbol["es_carpeta"]].itertuples():
        base, id_interno, index_number, cerrado = descomponer(fila.name)
        es_fcra = any(h.startswith("02_FCRA") for h in hijas.get(fila.name, ()))
        salida.append(Caso(fila.name, base, id_interno, index_number,
                           "FCRA" if es_fcra else "Foreclosure", cerrado))
    return tuple(salida)


# Palabras que dan nombre a una carpeta sin distinguir a nadie. Un documento
# cualquiera las contiene, asi que como apellido no valen.
NO_DISTINGUEN = frozenset(
    """llc inc corp ltd the and new york city county court group llp pllc company
    associates properties realty holdings trust estate management enterprises
    partners development construction ventures capital consulting services
    incorporated corporation limited avenue street road place house
    petroff amshen
    action federal page plaintiff defendant defendants exhibit index supreme
    district eastern southern western northern civil case matter file""".split()
)
# 'petroff' y 'amshen' estan ahi por la razon mas evidente y por eso mas facil de
# pasar por alto: es el nombre de la propia firma, y aparece en todos y cada uno de
# los escritos que redacta. Hay ademas carpetas que lo llevan en el nombre
# ('Allard v. Petroff'), asi que sin esto cualquier documento del despacho parecia
# tener algo que ver con ellas.


def apellidos_utiles(caso: Caso) -> set[str]:
    """Los tokens del nombre de la carpeta que de verdad identifican a alguien."""
    return {t for t in apellidos_de_carpeta(caso.carpeta)
            if len(t) > 3 and t not in NO_DISTINGUEN}


@functools.cache
def _por_apellido() -> dict[str, tuple[Caso, ...]]:
    """apellido -> casos que lo llevan. Se arma una vez y sirve para toda la corrida."""
    mapa: dict[str, list[Caso]] = {}
    for caso in casos():
        for apellido in apellidos_utiles(caso):
            mapa.setdefault(apellido, []).append(caso)
    return {k: tuple(v) for k, v in mapa.items()}


def casos_en(tokens: set[str]) -> list[Caso]:
    """Los casos cuyas palabras distintivas aparecen TODAS en ese texto.

    Todas y no una cualquiera. 'Anderson.Robert' se escribe sin coma, asi que sus
    palabras distintivas son las dos, y una demanda de Robert Holston contenia
    'robert' y de golpe el caso de Anderson era candidato. Exigiendo tambien
    'anderson' deja de serlo, y lo mismo pasa con 'Weinberger (Federal)' o con
    'Mikelic - Amex Action'. Es la misma regla que ya usa el extractor para las
    razones sociales: una empresa exige todas sus palabras, no una suelta.
    """
    salida = []
    for apellido in tokens & set(_por_apellido()):
        for caso in _por_apellido()[apellido]:
            if apellidos_utiles(caso) <= tokens and caso not in salida:
                salida.append(caso)
    return salida


def nombre_completo_en(caso: Caso, tokens: set[str]) -> bool:
    """El nombre ENTERO de la carpeta -- apellido y nombre de pila -- esta ahi?

    Sirve para desempatar, y hace falta mas de lo que parece: hay un cliente que se
    apellida 'Robert', asi que una demanda de Robert Holston lo hacia candidato a el
    tambien. Pero la caratula dice 'Robert Holston', no 'Ryian Robert': exigiendo
    las dos partes del nombre, 'Holston, Robert' casa y 'Robert, Ryian' no.
    """
    partes = {t for t in normalizar(caso.base).split() if len(t) > 2} - NO_DISTINGUEN
    return bool(partes) and partes <= tokens


def confirma_el_texto(caso: Caso, texto: str) -> bool:
    """El apellido de ESTA carpeta, aparece dentro del documento?

    Por tokens completos y no por subcadena: buscando subcadenas, 'Rao' aparece
    dentro de 'ratio' y 'May' dentro de 'may be'.
    """
    utiles = apellidos_utiles(caso)
    return bool(utiles and utiles & set(normalizar(texto).split()))


@dataclass
class Veredicto:
    """A que caso pertenece un documento, y con que respaldo."""

    caso: Caso | None = None
    razon: str = "nothing in the document ties it to a case"
    por_indice: list[Caso] = None       # type: ignore[assignment]
    por_nombre: list[Caso] = None       # type: ignore[assignment]
    indice_usado: str = ""
    # Un booleano y no una frase: antes esto se deducia buscando un trozo de
    # texto dentro del motivo, y traducir el motivo habria apagado el candado
    # sin que nada fallara. Lo que decide si algo se mueve no puede depender
    # de como este redactado un mensaje.
    confirmado: bool = False

    @property
    def seguro(self) -> bool:
        """Solo cuando las dos senales existen y apuntan al mismo sitio."""
        return self.caso is not None and self.confirmado


def resolver(indices_hallados: list[str], personas_halladas: list[str],
             indices_de_cabecera: set[str] | None = None,
             texto: str = "") -> Veredicto:
    """El caso al que pertenece un documento, a partir de lo que dice por dentro.

    `indices_de_cabecera` son los que salieron del principio del texto. Sirven para
    desempatar cuando el documento cita varios: el de la caratula es el del caso, y
    los del cuerpo son referencias a otros pleitos.

    `texto` es el documento entero, y es lo que permite CONFIRMAR: para cada caso
    candidato se comprueba si su propio apellido aparece dentro.
    """
    por_indice = [c for c in casos()
                  if c.index_number and c.index_number not in SIN_INDICE
                  and c.index_number in set(indices_hallados)]

    # Por apellido, en dos vias que se suman: lo que el extractor reconocio como
    # cliente de HubSpot, y los apellidos de las propias carpetas que aparecen en el
    # texto. La segunda no depende de que el cliente tenga ficha en el CRM.
    #
    # Y se busca SOLO EN LA CARATULA. Buscando en el documento entero, una demanda
    # devolvia 57 casos posibles: nombra a la contraparte, a los buros, al juez, a
    # los abogados de las dos partes y a cada tercero que se cruce. Cincuenta y
    # siete candidatos no son una ambiguedad que alguien pueda resolver, son ruido.
    # En la caratula estan las PARTES, que es justo lo que se busca.
    por_nombre = {c for c in casos()
                  if any(es_el_cliente(p, c.carpeta) for p in personas_halladas)}
    en_caratula: set[str] = set()
    if texto:
        en_caratula = set(normalizar(texto[:CARATULA]).split())
        por_nombre.update(casos_en(en_caratula))
    por_nombre = sorted(por_nombre, key=lambda c: c.carpeta)

    # Si el apellido deja varios candidatos, se prueba con el nombre completo antes
    # de rendirse y llamarlo ambiguo. Solo se estrecha si asi queda UNO: quedarse
    # con dos de cinco no ayuda a nadie a decidir.
    if len(por_nombre) > 1 and en_caratula:
        completos = [c for c in por_nombre if nombre_completo_en(c, en_caratula)]
        if len(completos) == 1:
            por_nombre = completos

    veredicto = Veredicto(por_indice=por_indice, por_nombre=por_nombre)

    # Si varios indices casan, manda el de la caratula.
    if len(por_indice) > 1 and indices_de_cabecera:
        en_caratula = [c for c in por_indice if c.index_number in indices_de_cabecera]
        if len(en_caratula) == 1:
            por_indice = en_caratula

    # Con un candidato por indice, la pregunta ya no es 'de quien es' sino 'cuadra?'.
    # Basta con mirar si el apellido de esa carpeta esta en el documento; no hace
    # falta que el nombre completo haya salido de una lista.
    if len(por_indice) == 1:
        caso = por_indice[0]
        veredicto.caso = caso
        if texto and confirma_el_texto(caso, texto):
            veredicto.razon = "both signals agree: index number and surname"
            veredicto.confirmado = True
        else:
            veredicto.razon = "index number only, the surname is not inside"
    elif len(por_nombre) == 1 and not por_indice:
        veredicto.caso = por_nombre[0]
        veredicto.razon = "surname only, no index number"
    elif por_indice or por_nombre:
        veredicto.razon = (f"AMBIGUOUS: {len(por_indice)} cases by index and "
                           f"{len(por_nombre)} by surname, someone has to decide")

    if veredicto.caso is not None:
        veredicto.indice_usado = veredicto.caso.index_number
    return veredicto
