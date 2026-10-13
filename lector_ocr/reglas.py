"""Las reglas para decidir a que carpeta va un documento, leidas de un .toml.

    from lector_ocr import reglas

    juego = reglas.cargar("reglas_ruteo.toml")       # o reglas.por_defecto()
    caso = reglas.aplicar(juego, texto, carpetas, asunto=asunto, nombre_archivo=nombre)

POR QUE UN .toml. Las reglas de ruteo cambian mas que el codigo: hoy son tres
(Index, nombre del cliente, el caso mas reciente) y manana habra mas. Con el .toml
se reordenan, se apagan o se ajustan sin tocar ni desplegar codigo nuevo. Lo que
el .toml no puede hacer es inventar un TIPO de regla: eso se programa una vez aqui
(en TIPOS) y desde ahi se usa como los demas.

SE VALIDA AL CARGAR, NO AL USAR. Un tipo, una fuente o un criterio mal escrito en el
.toml tumba la carga con un error que dice cual y cuales valen. Si se dejara pasar,
la regla no encontraria nunca nada y los documentos acabarian todos en sin
clasificar sin un solo error: el fallo callado de siempre.

LA PRIMERA REGLA QUE ENCUENTRA CARPETA GANA. Si una regla encuentra varias y su
desempate no deja una, se apunta la duda y se sigue con la siguiente regla, que
puede resolverla (un Index compartido por dos clientes se resuelve por el nombre).
Si ninguna decide, no se elige: el documento va a sin clasificar.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .expediente import (CARATULA, NO_DISTINGUEN, Caso, Identificacion, apellido_dentro,
                         catalogo, indices_del_documento, indices_en, normalizar,
                         plantilla_de)

POR_DEFECTO = Path(__file__).with_name("reglas_ruteo.toml")

# Como se llama cada fuente en el informe (Identificacion.se_decidio).
FUENTES = {
    "asunto": "el asunto",
    "nombre_archivo": "el nombre del archivo",
    "documento": "el documento",
    "caratula": "la caratula",
}


class ReglasInvalidas(ValueError):
    """El .toml tiene algo que no se sabe aplicar."""


@dataclass(frozen=True)
class Regla:
    nombre: str
    tipo: str
    fuentes: tuple[str, ...]
    desempate: tuple[str, ...] = ()
    largo_minimo: int = 4
    ignorar: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Juego:
    reglas: tuple[Regla, ...]
    caratula: int = CARATULA
    origen: str = ""


@dataclass
class _Contexto:
    """Lo que las reglas miran de un documento, preparado una vez."""

    texto: str
    asunto: str
    nombre_archivo: str
    caratula: int
    por_indice: dict[str, list[Caso]]
    casos: list[Caso]
    _palabras: dict[str, set[str]] = field(default_factory=dict)

    def crudo(self, fuente: str) -> str:
        return {"asunto": self.asunto, "nombre_archivo": self.nombre_archivo,
                "documento": self.texto, "caratula": self.texto[: self.caratula]}[fuente]

    def palabras(self, fuente: str) -> set[str]:
        if fuente not in self._palabras:
            self._palabras[fuente] = set(normalizar(self.crudo(fuente)).split())
        return self._palabras[fuente]

    def indices(self, fuente: str) -> list[str]:
        if fuente == "documento":
            return indices_del_documento(self.texto, self.caratula)
        return indices_en(self.crudo(fuente))


# --------------------------------------------------------------------------- #
# Tipos de regla. Cada uno recibe la regla y el contexto y devuelve
# (fuente que acerto, carpetas candidatas). Para anadir un tipo: una funcion aqui y
# su nombre en TIPOS. Nada mas.

def _por_indice(regla: Regla, ctx: _Contexto) -> tuple[str, list[Caso]]:
    """El primer Index de las fuentes (en su orden) que tiene carpeta."""
    for fuente in regla.fuentes:
        for indice in ctx.indices(fuente):
            if indice in ctx.por_indice:
                return fuente, list(ctx.por_indice[indice])
    return "", []


def _por_cliente(regla: Regla, ctx: _Contexto) -> tuple[str, list[Caso]]:
    """Las carpetas cuyas palabras distintivas aparecen TODAS en las fuentes.

    Todas y no una: 'Anderson.Robert' tiene dos, y una demanda de Robert Holston no
    puede hacer candidato a Anderson. Para proponer un caso solo por el nombre se
    exigen apellidos de al menos `largo_minimo` letras: 'Lee' o 'Rao' aparecen en
    cualquier caratula.
    """
    palabras: set[str] = set()
    acertadas = []
    for fuente in regla.fuentes:
        palabras |= ctx.palabras(fuente)
    ignorar = NO_DISTINGUEN | regla.ignorar
    hallados = []
    for caso in ctx.casos:
        propios = caso.apellidos - ignorar
        if propios and all(len(a) >= regla.largo_minimo for a in propios) and propios <= palabras:
            hallados.append(caso)
    if not hallados:
        return "", []
    # Que fuente lo dijo: la primera que contiene los apellidos del primer hallado.
    for fuente in regla.fuentes:
        if (hallados[0].apellidos - ignorar) <= ctx.palabras(fuente):
            acertadas.append(fuente)
    return (acertadas[0] if acertadas else regla.fuentes[0]), hallados


TIPOS: dict[str, Callable[[Regla, _Contexto], tuple[str, list[Caso]]]] = {
    "indice": _por_indice,
    "cliente": _por_cliente,
}

# --------------------------------------------------------------------------- #
# Criterios de desempate. Cada uno recibe las candidatas y devuelve las que se
# quedan. Si ninguna lo cumple, devuelve las mismas: un criterio que no aplica no
# descarta. `None` es un freno: no se puede elegir entre estas.

def _activa(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> list[Caso] | None:
    return [c for c in candidatas if not c.cerrado] or candidatas


def _nombre_completo(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> list[Caso] | None:
    palabras: set[str] = set()
    for fuente in regla.fuentes:
        palabras |= ctx.palabras(fuente)
    return [c for c in candidatas if c.nombre_completo and c.nombre_completo <= palabras] or candidatas


def _un_solo_cliente(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> list[Caso] | None:
    """Freno: carpetas de clientes distintos no se desempatan por fecha ni por Index.
    Elegir seria adivinar de quien es el documento."""
    return candidatas if len({c.nombre_completo for c in candidatas}) == 1 else None


def _sin_indice(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> list[Caso] | None:
    return [c for c in candidatas if not c.tiene_indice] or candidatas


def _mas_reciente(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> list[Caso] | None:
    """La creada mas recientemente. Sin fecha en alguna no se puede comparar: entonces
    no descarta nada (y si no queda una, nadie elige)."""
    if not all(c.creada for c in candidatas):
        return candidatas
    ultima = max(c.creada for c in candidatas)
    return [c for c in candidatas if c.creada == ultima]


CRITERIOS: dict[str, Callable[[list[Caso], Regla, _Contexto], list[Caso] | None]] = {
    "activa": _activa,
    "nombre_completo": _nombre_completo,
    "un_solo_cliente": _un_solo_cliente,
    "sin_indice": _sin_indice,
    "mas_reciente": _mas_reciente,
}


def _desempatar(candidatas: list[Caso], regla: Regla, ctx: _Contexto) -> tuple[Caso | None, str]:
    """(la elegida o None, que criterio la dejo sola o por que no se pudo)."""
    if len(candidatas) == 1:
        return candidatas[0], ""
    for criterio in regla.desempate:
        quedan = CRITERIOS[criterio](candidatas, regla, ctx)
        if quedan is None:
            return None, f"{len(candidatas)} folders of different clients"
        candidatas = quedan
        if len(candidatas) == 1:
            return candidatas[0], criterio
    return None, f"{len(candidatas)} folders and the tie-break rules leave more than one"


# --------------------------------------------------------------------------- #
def aplicar(juego: Juego, texto: str, carpetas: Sequence[Any], *, asunto: str = "",
            nombre_archivo: str = "") -> Identificacion:
    """Prueba las reglas en orden y devuelve el caso de la primera que decide."""
    por_indice, casos = catalogo(carpetas)
    ctx = _Contexto(str(texto or ""), str(asunto or ""), str(nombre_archivo or ""),
                    juego.caratula, por_indice, casos)
    salida = Identificacion(indices_en_documento=ctx.indices("documento")[:10])
    dudas: list[str] = []

    for regla in juego.reglas:
        fuente, candidatas = TIPOS[regla.tipo](regla, ctx)
        if not candidatas:
            continue
        elegido, por_que = _desempatar(candidatas, regla, ctx)
        if elegido is None:
            dudas.append(f"{regla.nombre}: {por_que}")
            if not salida.candidatos:
                salida.candidatos = [c.carpeta for c in candidatas][:10]
            continue
        salida.carpeta, salida.carpeta_id = elegido.carpeta, elegido.id
        salida.id_interno, salida.indice = elegido.id_interno, elegido.indice
        salida.plantilla = plantilla_de(elegido)
        salida.regla, salida.se_decidio = regla.nombre, FUENTES.get(fuente, fuente)
        salida.candidatos = [c.carpeta for c in candidatas][:10] if len(candidatas) > 1 else []
        # Confirmado: el Index de la carpeta casa Y su apellido esta en el documento.
        salida.confirmado = bool(elegido.indice and regla.tipo == "indice"
                                 and apellido_dentro(elegido, ctx.palabras("documento")))
        partes = [f"rule '{regla.nombre}' via {FUENTES.get(fuente, fuente)}"]
        if len(candidatas) > 1:
            partes.append(f"{len(candidatas)} candidates, tie broken by '{por_que}'")
        if dudas:
            partes.append("earlier: " + "; ".join(dudas))
        salida.razon = "; ".join(partes)
        return salida

    if dudas:
        salida.razon = "AMBIGUOUS, nobody chose: " + "; ".join(dudas)
    return salida


# --------------------------------------------------------------------------- #
def cargar(origen: str | Path | dict) -> Juego:
    """Lee y valida un juego de reglas: ruta a un .toml, su texto, o el dict ya leido."""
    if isinstance(origen, dict):
        datos, nombre = origen, "(dict)"
    elif isinstance(origen, Path) or "\n" not in str(origen):
        # Una sola linea es una ruta; el texto de un .toml siempre tiene varias.
        ruta = Path(origen)
        if not ruta.exists():
            raise ReglasInvalidas(f"no existe el archivo de reglas: {ruta}")
        try:
            datos, nombre = tomllib.loads(ruta.read_text(encoding="utf-8")), str(ruta)
        except tomllib.TOMLDecodeError as error:
            raise ReglasInvalidas(f"{ruta}: no es un TOML valido ({error})") from error
    else:
        try:
            datos, nombre = tomllib.loads(str(origen)), "(texto)"
        except tomllib.TOMLDecodeError as error:
            raise ReglasInvalidas(f"el texto de reglas no es un TOML valido ({error})") from error

    general = datos.get("general", {})
    crudas = datos.get("regla", [])
    if not crudas:
        raise ReglasInvalidas(f"{nombre}: no hay ninguna [[regla]]")
    reglas = []
    for i, r in enumerate(crudas, 1):
        donde = f"{nombre}, regla {i} ({r.get('nombre', 'sin nombre')})"
        tipo = r.get("tipo")
        if tipo not in TIPOS:
            raise ReglasInvalidas(f"{donde}: tipo '{tipo}' desconocido; valen {sorted(TIPOS)}")
        fuentes = tuple(r.get("fuentes", ()))
        malas = [f for f in fuentes if f not in FUENTES]
        if not fuentes or malas:
            raise ReglasInvalidas(f"{donde}: fuentes {malas or '(ninguna)'} no validas; valen {sorted(FUENTES)}")
        desempate = tuple(r.get("desempate", ()))
        malos = [c for c in desempate if c not in CRITERIOS]
        if malos:
            raise ReglasInvalidas(f"{donde}: criterios {malos} desconocidos; valen {sorted(CRITERIOS)}")
        reglas.append(Regla(
            nombre=str(r.get("nombre") or f"regla {i}"), tipo=tipo, fuentes=fuentes,
            desempate=desempate, largo_minimo=int(r.get("largo_minimo", 4)),
            ignorar=frozenset(normalizar(" ".join(r.get("ignorar", []))).split())))
    return Juego(tuple(reglas), int(general.get("caratula", CARATULA)), nombre)


_por_defecto: Juego | None = None


def por_defecto() -> Juego:
    """Las reglas de la firma, las de lector_ocr/reglas_ruteo.toml. Se leen una vez."""
    global _por_defecto
    if _por_defecto is None:
        _por_defecto = cargar(POR_DEFECTO)
    return _por_defecto
