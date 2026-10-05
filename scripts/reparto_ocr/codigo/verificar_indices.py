r"""El nombre nuevo dice que esta carpeta es el indice X. ¿Lo dicen sus documentos?

POR QUE ASI Y NO AL REVES. La alternativa era volver a deducir el nombre de cada
carpeta leyendo su contenido y comparar los dos nombres. Eso produce montones de
casi-coincidencias que alguien tiene que juzgar una por una. Aqui la pregunta ya
viene con la respuesta esperada, y la respuesta es de tres valores y las tres se
pueden accionar:

    CONFIRMA      el indice declarado aparece en la caratula de sus documentos
    NO COINCIDE   aparecen indices, pero NINGUNO es el declarado  <- lo caro
    SIN SENAL     no aparece ningun indice. No verificable por esta via

'NO COINCIDE' no prueba que el nombre este mal: un escrito puede nombrar el indice
de otro pleito. Prueba que hay que mirarlo, que es justo lo que se quiere antes de
consolidar una migracion de 2.250 carpetas.

LO QUE ESTO NO PUEDE VERIFICAR, Y CONVIENE DECIRLO: el ID_INTERNO. No esta impreso
en ningun documento -- lo emite el despacho con emitir_ids.py. Comprobarlo es una
conciliacion contra HubSpot y el Excel de la firma, no lectura.

DONDE SE LEE. En Active y Closed Matters, no en el contenedor de destino: ese
tiene 63.313 carpetas y 1.037 archivos, o sea el esqueleto de subcarpetas sin los
documentos. Los papeles siguen en su sitio original.

BARATO A PROPOSITO, por tres decisiones:

    Word primero        un .docx de la firma trae capa de texto siempre y la
                        caratula arriba: se lee en milisegundos y sin OCR. Solo se
                        baja a los PDF escaneados si el Word no resolvio
    salida temprana     en cuanto aparece el indice declarado, esa carpeta esta
                        confirmada y se pasa a la siguiente
    solo las que tienen algo que cotejar   1.393 de 3.043; las otras 1.650 dicen
                        '- 0' y no hay contra que compararlas

SE PUEDE CORTAR Y SEGUIR. El progreso se escribe segun avanza, igual que en la
reversion del renombrado: una corrida de una hora que se corta no puede perderlo
todo. Y el JSON guarda el nombre VIEJO y el NUEVO de cada carpeta, que es lo que
salvo la situacion cuando hubo que deshacer los renombrados.

NO ESCRIBE EN SHAREPOINT. Lee y reporta.

Uso:
    .venv\Scripts\python.exe verificar_indices.py --limite 25
    .venv\Scripts\python.exe verificar_indices.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

from extraccion.config import drive_id
from extractor_completo.config import RESULTADOS
from crear_subcarpetas import NOMBRES_CONTENEDOR
from extractor_completo.directorio import CARATULA
from extractor_completo.lectores import leer_con_detalle
from regla_nombres import indices as indices_de, partir_nombre
from sp_conexion import Graph
from sp_indices import cargar_arbol
from ver_matters import SALIDA_DIR, tabla, titulo

MANIFIESTO = SALIDA_DIR / "verificacion_indices.json"

# El reparto del nombre en campos lo hace regla_nombres.partir_nombre, que sabe
# leer la marca de cerrado tanto delante (formato viejo) como detras (el de ahora).
# Aqui habia un regex propio con 'Closed - ' escrito a mano, y al mover la marca
# habria dejado de reconocer 1.789 carpetas -- sin fallar: simplemente saltandoselas.

# Formatos que llevan caratula, en orden de coste. El Word va primero a proposito.
FORMATOS = ("docx", "pdf")

# Un archivo mas grande casi siempre es un escaneo de cientos de paginas, y la
# caratula esta en la primera.
MAX_MB = 15
PAGINAS = 2

# Cuantos archivos se prueban como mucho antes de rendirse con una carpeta. Con la
# salida temprana, la mayoria gasta uno o dos.
MAXIMO_ARCHIVOS = 6

CADA_CUANTO_SE_GUARDA = 20


# El numero de caso FEDERAL, que regla_nombres.indices() no reconoce -- esa funcion
# es para indices estatales de Nueva York. Y hace falta: 90 de las 1.393 carpetas
# llevan un numero federal en el campo del index, casi todas de los casos FCRA, y
# para todas ellas indices_de() devolvia el conjunto vacio. Con el esperado vacio la
# comparacion NO PODIA ACERTAR NUNCA: salian como 'sin senal' o 'NO COINCIDE' se
# leyera lo que se leyera, despues de bajarse seis archivos para nada.
#
# Se normaliza a una sola forma porque en los papeles aparece de varias
# ('1:25-cv-02332', '1-25-cv-2332') y compararlas como texto no casaria.
# El separador entre oficina y año va de las tres formas: el nombre de la carpeta
# escribe '1-25-cv-02332', la caratula de un escrito '1:25-cv-2332' y a veces
# '125-cv-2332'. Si solo se acepta ':' -- como estaba -- el nombre declarado no casa
# consigo mismo y la carpeta se descarta por invalida.
FEDERAL_EN_TEXTO = re.compile(r"\b(\d)[-:]?(\d{2})-cv-0*(\d{1,6})\b", re.I)


# Que se puede volver a hacer sin borrar el manifiesto entero. Cada criterio dice
# QUE filas se tiran para que la siguiente pasada las repita.
#
# Existe porque hasta ahora cada rehecho se hacia con un comando de una linea
# escrito a mano sobre el JSON, y eso es fragil: un filtro mal puesto borra trabajo
# bueno y no hay forma de saberlo despues.
REHACER = {
    "plantilla": ("las que recibieron un numero propuesto para varias carpetas: "
                  "es un documento plantilla, no el caso de nadie"),
    "sin-senal": "las que se leyeron y no dieron ningun numero",
    "sin-archivos": "las que no encontraron archivos que leer",
    "fallidas": "las que fallaron al descargar",
    "no-coincide": "las que dieron un numero distinto al del nombre",
}


def a_rehacer(hechas: dict[str, dict], criterio: str) -> set[str]:
    """Las claves del manifiesto que ese criterio manda repetir."""
    if criterio == "plantilla":
        repetido = {i for i, n in Counter(
            r.get("propuesto") for r in hechas.values() if r.get("propuesto")
        ).items() if n > 1}
        return {k for k, r in hechas.items() if r.get("propuesto") in repetido}
    if criterio == "sin-senal":
        return {k for k, r in hechas.items() if r.get("veredicto") == "sin senal"}
    if criterio == "sin-archivos":
        return {k for k, r in hechas.items()
                if r.get("veredicto") == "sin archivos legibles"}
    if criterio == "fallidas":
        return {k for k, r in hechas.items()
                if "no se pudo descargar" in str(r.get("veredicto"))}
    if criterio == "no-coincide":
        return {k for k, r in hechas.items() if r.get("veredicto") == "NO COINCIDE"}
    return set()


def bajar(g: Graph, drive: str, item_id: str) -> tuple[bytes, str]:
    """(contenido, por que no se pudo). Reintenta cuando SharePoint limita.

    HACE FALTA Y SE PAGO CARO NO TENERLO. La primera version llamaba a
    `g.sesion.get` directamente -- copiado de sondeo_contenido.py -- y eso se salta
    el manejo de 429 que si tiene Graph.get. Sobre 1.650 carpetas SharePoint empieza
    a limitar, y cada respuesta 429 se descartaba en silencio con un `continue`. El
    resultado: 682 carpetas LLENAS de documentos reportadas como 'sin archivos
    legibles', el 41% de la corrida, sin una sola senal de que el problema era la
    red y no las carpetas.

    Ahora se respeta el Retry-After y, si aun asi falla, se DEVUELVE EL MOTIVO para
    que quede escrito en vez de confundirse con 'aqui no habia nada que leer'.
    """
    import time as _t

    for intento in range(5):
        try:
            r = g.sesion.get(f"https://graph.microsoft.com/v1.0/drives/{drive}"
                             f"/items/{item_id}/content", timeout=120)
        except Exception as error:  # noqa: BLE001
            if intento == 4:
                return b"", f"red: {type(error).__name__}"
            _t.sleep(2 ** intento)
            continue
        if r.status_code == 200:
            return r.content, ""
        if r.status_code == 401:
            # EL TOKEN CADUCA CADA HORA y esto corre dieciseis. Sin esto, la corrida
            # del 24-sep-2026 devolvio 56.900 'HTTP 401' seguidos: quince horas
            # bajando nada a 875 por minuto, que es la velocidad de fallar.
            # get(), post() y patch() lo renuevan solos desde siempre; esta llamada
            # es la unica que se saltaba ese camino.
            g.sesion.headers.update({"Authorization": f"Bearer {g._token()}"})
            continue
        if r.status_code in (429, 503, 504):
            _t.sleep(int(r.headers.get("Retry-After", 2 ** intento)))
            continue
        return b"", f"HTTP {r.status_code}"
    return b"", "HTTP 429 tras 5 intentos"


# El indice ELECTRONICO de Nueva York: 'EF004283-2017'. Tampoco lo reconoce
# indices_de(), y es el que usan los casos presentados por NYSCEF, o sea los
# recientes. Lo destapo la revision a mano: 'Pryce, Cassius' tiene EF004283-2017 y
# EF005923-2017, y como no veiamos ninguno de los dos, lo unico legible que quedaba
# en sus documentos era el numero de una plantilla -- y eso fue lo que propusimos.
EF_EN_TEXTO = re.compile(r"\bEF\s?0*(\d{1,7})\s*[-/]\s*(\d{4})\b", re.I)


def identificadores(texto: str) -> set[str]:
    """Los numeros de caso del texto: estatales, electronicos (EF) y federales."""
    federales = {f"{a}-{b}-cv-{c}" for a, b, c in FEDERAL_EN_TEXTO.findall(texto or "")}
    electronicos = {f"EF{a}-{b}" for a, b in EF_EN_TEXTO.findall(texto or "")}
    return indices_de(texto) | federales | electronicos


def nucleo(nombre: str) -> str:
    """El nombre del cliente, sin prefijo de cerrado ni campos añadidos.

    Sirve para emparejar la carpeta del contenedor de destino con la de Active o
    Closed, que despues del revert volvio a llamarse como al principio.
    """
    base, id_interno, index, _ = partir_nombre(nombre)
    if len(id_interno) == 6:
        return base.strip().lower()
    # Un numero de cinco o siete cifras NO es un ID_INTERNO: es parte del nombre
    # que alguien tecleo mal ('Vanessa-88779'). Se vuelve a pegar, porque quitarlo
    # dejaria un nucleo mas corto que podria emparejar con el cliente equivocado.
    cola = f" - {id_interno} - {index}" if id_interno else ""
    return f"{base}{cola}".strip().lower()


def por_verificar(arbol: pd.DataFrame, sin_index: bool = False) -> list[dict]:
    """Las carpetas a mirar.

    Por defecto, las que DECLARAN un indice (se verifica). Con sin_index, las
    que dicen '- 0' y no declaran nada (se descubre).
    """
    arbol = arbol.copy()
    arbol["area"] = arbol["ruta"].astype(str).str.extract(r"zz-pruebas-no-usar/([^/]+)")
    clientes = arbol[arbol["es_carpeta"] & (arbol["Nivel"] == 2)]

    # Donde estan los documentos de verdad, indexado por nucleo del nombre.
    origen: dict[str, dict] = {}
    for _, f in clientes[clientes["area"].isin(("Active Matters", "Closed Matters"))].iterrows():
        origen.setdefault(nucleo(f["name"]), {"name": f["name"], "area": f["area"]})

    tareas = []
    for _, f in clientes[clientes["area"].isin(NOMBRES_CONTENEDOR)].iterrows():
        base, id_interno, index, _ = partir_nombre(f["name"])
        # SEIS cifras, no las tres a siete que admite partir_nombre. Un ID de cinco
        # o de siete es uno mal tecleado ('Vanessa-88779'), y esos se corrigen a
        # mano, no se verifican: colarlos aqui moveria los totales del informe que
        # ya se esta revisando (1.393 con indice, 1.650 sin el).
        if len(id_interno) != 6 or not index:
            continue
        vacio = index in ("0", "")
        if vacio != sin_index:
            continue
        fuente = origen.get(nucleo(f["name"]))
        tareas.append({
            "carpeta_nueva": str(f["name"]),
            "carpeta_vieja": fuente["name"] if fuente else None,
            "area": fuente["area"] if fuente else None,
            "id_interno": id_interno,
            "index_declarado": index,
            "base": base,
        })
    return tareas


MANIFIESTO_DESCUBRIR = SALIDA_DIR / "descubrimiento_indices.json"

# Cuantos documentos DISTINTOS tienen que traer el mismo numero para darlo por
# corroborado. Dos basta y ahorra mucho: un indice que sale en dos escritos de la
# misma carpeta no es una mencion de pasada.
CORROBORACION = 2


def descubrir(g: Graph, drive: str, arbol: pd.DataFrame, tarea: dict,
              cuantos: int) -> dict:
    """Que numero de caso dicen los documentos de una carpeta que no lo declara.

    Es lo contrario de `verificar`: alli sabiamos la respuesta y comprobabamos;
    aqui no hay nada escrito en el nombre --dice '- 0'-- y se pregunta a los
    papeles. Por eso NO PROPONE Y YA: cuenta en CUANTOS documentos distintos sale
    cada numero, porque uno que aparece en dos escritos de la carpeta vale mucho
    mas que uno que sale en uno solo, que puede ser la mencion de otro pleito.
    """
    salida = {**tarea, "veredicto": "sin carpeta de origen", "propuesto": "",
              "en_documentos": 0, "otros_vistos": "", "archivos_leidos": 0,
              "donde": "", "leido_con": ""}
    if not tarea["carpeta_vieja"]:
        return salida

    elegidos = candidatos(arbol, tarea["carpeta_vieja"], cuantos)
    veces: Counter = Counter()
    primera_vez: dict[str, tuple[str, str]] = {}
    for _, archivo in elegidos.iterrows():
        datos, fallo = bajar(g, drive, archivo["id"])
        if fallo:
            salida["descargas_fallidas"] = salida.get("descargas_fallidas", 0) + 1
            salida["ultimo_fallo"] = fallo
            continue
        texto, _, con_que = leer_con_detalle(archivo["ext"], datos, PAGINAS)
        salida["archivos_leidos"] += 1
        if not texto.strip():
            continue
        for i in identificadores(texto[:CARATULA]):
            veces[i] += 1
            primera_vez.setdefault(i, (str(archivo["name"])[:50],
                                       str(con_que).split(" (")[0]))
        if veces and veces.most_common(1)[0][1] >= CORROBORACION:
            break  # ya hay uno corroborado: no hace falta seguir bajando archivos

    if not veces:
        if salida["archivos_leidos"]:
            salida["veredicto"] = "sin senal"
        elif salida.get("descargas_fallidas"):
            salida["veredicto"] = f"no se pudo descargar ({salida['ultimo_fallo']})"
        else:
            salida["veredicto"] = "sin archivos legibles"
        return salida

    mejor, cuantas = veces.most_common(1)[0]
    salida.update(
        propuesto=mejor, en_documentos=cuantas,
        otros_vistos=", ".join(i for i, _ in veces.most_common()[1:5]),
        donde=primera_vez[mejor][0], leido_con=primera_vez[mejor][1],
        veredicto=("propone (corroborado)" if cuantas >= CORROBORACION
                   else "propone (un solo documento)"))
    return salida


def candidatos(arbol: pd.DataFrame, carpeta: str, cuantos: int) -> pd.DataFrame:
    """Los archivos de la carpeta, Word primero y de menor a mayor tamaño."""
    dentro = arbol[(arbol["Carpeta"] == carpeta) & (~arbol["es_carpeta"])
                   & (arbol["size"] < MAX_MB * 2 ** 20)].copy()
    if dentro.empty:
        return dentro
    dentro["ext"] = dentro["name"].astype(str).str.rsplit(".", n=1).str[-1].str.lower()
    dentro = dentro[dentro["ext"].isin(FORMATOS)]
    if dentro.empty:
        return dentro
    dentro["orden"] = dentro["ext"].map({e: i for i, e in enumerate(FORMATOS)})
    return dentro.sort_values(["orden", "size"]).head(cuantos)


def verificar(g: Graph, drive: str, arbol: pd.DataFrame, tarea: dict,
              cuantos: int) -> dict:
    """Mira los documentos de una carpeta hasta confirmar o agotarlos."""
    esperado = identificadores(tarea["index_declarado"])
    salida = {**tarea, "veredicto": "sin carpeta de origen", "indices_hallados": "",
              "archivos_leidos": 0, "donde": "", "leido_con": ""}
    if not esperado:
        # Si el propio nombre no da un numero de caso reconocible, la comparacion
        # no puede acertar. Se dice y no se descarga nada: leer seis archivos para
        # una pregunta sin respuesta posible es tiempo tirado, y el veredicto
        # resultante ('sin senal') seria ademas enganoso.
        salida["veredicto"] = "el index del nombre no es un numero de caso valido"
        return salida
    if not tarea["carpeta_vieja"]:
        return salida

    elegidos = candidatos(arbol, tarea["carpeta_vieja"], cuantos)
    hallados: set[str] = set()
    # De DONDE salio cada indice, tambien cuando NO coincide. Guardarlo solo en las
    # confirmaciones dejaba las discrepancias imposibles de revisar: 'Soubbotine'
    # decia haber encontrado un indice tras leer tres archivos y no habia forma de
    # saber en cual, asi que nadie podia comprobarlo sin repetir el trabajo.
    de_donde: dict[str, str] = {}
    for _, archivo in elegidos.iterrows():
        datos, fallo = bajar(g, drive, archivo["id"])
        if fallo:
            salida["descargas_fallidas"] = salida.get("descargas_fallidas", 0) + 1
            salida["ultimo_fallo"] = fallo
            continue
        texto, _, con_que = leer_con_detalle(archivo["ext"], datos, PAGINAS)
        salida["archivos_leidos"] += 1
        if not texto.strip():
            continue
        de_este = identificadores(texto[:CARATULA])
        for i in de_este:
            de_donde.setdefault(i, f"{str(archivo['name'])[:44]} [{str(con_que).split(' (')[0]}]")
        hallados |= de_este
        if esperado & de_este:
            # SALIDA TEMPRANA: ya esta confirmada, no hay que leer el resto.
            salida.update(veredicto="confirma", indices_hallados=tarea["index_declarado"],
                          donde=str(archivo["name"])[:50],
                          leido_con=str(con_que).split(" (")[0])
            return salida

    salida["indices_hallados"] = ", ".join(sorted(hallados)[:4])
    salida["donde"] = " | ".join(f"{i}: {de_donde[i]}" for i in sorted(hallados)[:3])
    if hallados:
        salida["veredicto"] = "NO COINCIDE"
    elif salida["archivos_leidos"]:
        salida["veredicto"] = "sin senal"
    elif salida.get("descargas_fallidas"):
        salida["veredicto"] = f"no se pudo descargar ({salida['ultimo_fallo']})"
    else:
        salida["veredicto"] = "sin archivos legibles"
    return salida


# Un indice que aparece en MAS carpetas distintas que esto no es el caso de nadie:
# es un documento plantilla copiado por todas partes con un indice de ejemplo
# dentro. Medido sobre las 170 discrepancias de la primera corrida: '15109-2013'
# salia en 24 carpetas sin relacion entre si, y el siguiente mas repetido no pasaba
# de cinco. El umbral va en medio.
UMBRAL_PLANTILLA = 5

# De mas urgente a menos. Manda el orden del Excel: quien lo abra tiene que ver
# primero lo que hay que arreglar, no lo que hay que explicar.
CATEGORIAS = (
    "casi-coincidencia: un digito",
    "año imposible en el nombre",
    "queda por explicar",
    "solo aparece un indice de plantilla",
    "cliente partido en varios matters",
)

ANIO = re.compile(r"-(\d{4})$")


def _un_digito(a: str, b: str) -> bool:
    """Si los dos numeros se diferencian en UN solo caracter.

    Cuenta las tres formas, y la tercera se aprendio mirando el resultado: uno
    CAMBIADO ('66842' / '68842'), uno de MAS y uno de MENOS. La primera version solo
    miraba cambios --exigia la misma longitud-- y se perdia
    'Dankner: 80930-2023 en el nombre, 809302-2023 en los papeles', que es un digito
    perdido y es exactamente el fallo que perseguimos en todo este proyecto.
    """
    if a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if abs(len(a) - len(b)) != 1:
        return False
    corto, largo = (a, b) if len(a) < len(b) else (b, a)
    # Quitar un caracter del largo tiene que dar el corto.
    return any(largo[:i] + largo[i + 1:] == corto for i in range(len(largo)))


def clasificar(fila: dict, cuantas_nuevas: dict[str, int],
               plantillas: set[str]) -> str:
    """Por que NO COINCIDE esta fila. Las 170 planas no se pueden trabajar.

    El orden de las comprobaciones importa: las dos primeras no dependen de los
    documentos, y una fila que cae en ellas ya esta resuelta sin que nadie abra
    nada.
    """
    declarado = str(fila.get("index_declarado") or "")
    hallados = [x.strip() for x in str(fila.get("indices_hallados") or "").split(",")
                if x.strip()]

    # El año solo se juzga en indices ESTATALES. En un numero federal las cuatro
    # cifras finales son el numero de caso, no el año: '1-24-cv-6067' salia como
    # 'año imposible 6067' cuando el año esta en medio y es 2024.
    anio = None if "-cv-" in declarado.lower() else ANIO.search(declarado)
    if anio and not (1990 <= int(anio.group(1)) <= 2027):
        # No hacen falta los documentos: un indice de 1937 no es un caso vivo.
        return "año imposible en el nombre"
    if any(_un_digito(declarado, h) for h in hallados):
        return "casi-coincidencia: un digito"
    if cuantas_nuevas.get(fila.get("carpeta_vieja"), 0) > 1:
        # Varias carpetas nuevas comparten carpeta de origen, asi que se leen los
        # mismos documentos para todas y como mucho una puede coincidir. Es un
        # artefacto del emparejamiento, no un error del nombre.
        return "cliente partido en varios matters"
    if hallados and all(h in plantillas for h in hallados):
        return "solo aparece un indice de plantilla"
    return "queda por explicar"


def repartir(registros: list[dict]) -> pd.DataFrame:
    """Los NO COINCIDE con su motivo, ordenados por urgencia."""
    malas = [r for r in registros if r.get("veredicto") == "NO COINCIDE"]
    if not malas:
        return pd.DataFrame()

    cuantas_nuevas = Counter(r.get("carpeta_vieja") for r in malas)
    donde_sale = defaultdict(set)
    for r in malas:
        for i in str(r.get("indices_hallados") or "").split(","):
            if i.strip():
                donde_sale[i.strip()].add(r.get("carpeta_vieja"))
    plantillas = {i for i, carpetas in donde_sale.items()
                  if len(carpetas) > UMBRAL_PLANTILLA}

    t = pd.DataFrame(malas)
    t["motivo"] = [clasificar(r, cuantas_nuevas, plantillas) for r in malas]
    t["_orden"] = t["motivo"].map({c: i for i, c in enumerate(CATEGORIAS)}).fillna(99)
    return t.sort_values(["_orden", "carpeta_vieja"]).drop(columns="_orden")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limite", type=int, default=0, help="Solo N carpetas (prueba)")
    p.add_argument("--archivos", type=int, default=MAXIMO_ARCHIVOS,
                   help=f"Archivos por carpeta como mucho. Def: {MAXIMO_ARCHIVOS}")
    p.add_argument("--descubrir", action="store_true",
                   help="Las que dicen '- 0': en vez de comprobar un indice, "
                        "buscar cual es. Propone, no escribe")
    p.add_argument("--rehacer", choices=sorted(REHACER), action="append", default=[],
                   help="Repetir un grupo ya hecho. Se puede poner varias veces. "
                        + "; ".join(f"{k}: {v}" for k, v in REHACER.items()))
    args = p.parse_args()

    arbol = cargar_arbol()
    tareas = por_verificar(arbol, sin_index=args.descubrir)
    manifiesto = MANIFIESTO_DESCUBRIR if args.descubrir else MANIFIESTO
    trabajo = descubrir if args.descubrir else verificar

    # Lo ya hecho no se repite: una corrida de una hora que se corta no puede
    # empezar de cero, y eso ya nos costo dos intentos en la reversion.
    hechas: dict[str, dict] = {}
    if manifiesto.exists():
        hechas = {r["carpeta_nueva"]: r
                  for r in json.loads(manifiesto.read_text(encoding="utf-8"))}
    # Rehacer es TIRAR del manifiesto, y eso se dice en voz alta antes de hacerlo:
    # lo que se borra aqui vuelve a costar descargas y tiempo.
    for criterio in args.rehacer:
        fuera = sorted(a_rehacer(hechas, criterio))
        # CON --limite SOLO SE TIRAN LAS QUE SE VAN A REHACER. Tirar las 640 para
        # probar con veinte dejaria 620 veredictos borrados y dos horas de trabajo
        # por repetir: exactamente lo contrario de lo que quiere quien esta
        # probando algo antes de comprometerse.
        if args.limite:
            fuera = fuera[: args.limite]
        for k in fuera:
            hechas.pop(k, None)
        print(f"  --rehacer {criterio}: se repiten {len(fuera):,} "
              f"({REHACER[criterio]})")

    pendientes = [t for t in tareas if t["carpeta_nueva"] not in hechas]
    if args.limite:
        pendientes = pendientes[: args.limite]

    if args.descubrir:
        titulo(f"DESCUBRIR EL INDICE QUE FALTA  ({len(tareas):,} carpetas dicen '- 0')")
        print("  no hay nada escrito en el nombre: se le pregunta a los documentos")
        print(f"  se para en cuanto un numero sale en {CORROBORACION} documentos distintos")
        print("  NO ESCRIBE NADA: propone, y lo aprueba una persona")
    else:
        titulo(f"VERIFICAR EL INDICE CONTRA LOS DOCUMENTOS  ({len(tareas):,} carpetas)")
        print("  se lee de Active y Closed Matters: ahi estan los documentos")
        print("  Word primero, y se para en cuanto aparece el indice declarado")
    if hechas:
        print(f"  reanudando: {len(hechas):,} ya hechas")
    print(f"  pendientes ahora: {len(pendientes):,}\n")
    if not pendientes:
        print("  No queda ninguna pendiente.")
    else:
        g, drive = Graph(), drive_id()
        arranque = time.time()
        for i, tarea in enumerate(pendientes, 1):
            transcurrido = time.time() - arranque
            faltan = (len(pendientes) - i) / max(i / max(transcurrido, 0.001), 1e-9) / 60
            print(f"  [{i:,}/{len(pendientes):,}] quedan ~{faltan:.0f} min   "
                  f"{tarea['carpeta_nueva'][:42]:<44}", end="\r", flush=True)
            try:
                fila = trabajo(g, drive, arbol, tarea, args.archivos)
            except Exception as error:  # noqa: BLE001
                fila = {**tarea, "veredicto": f"error: {type(error).__name__}",
                        "indices_hallados": "", "archivos_leidos": 0,
                        "donde": "", "leido_con": ""}
            fila["cuando"] = datetime.now().isoformat(timespec="seconds")
            hechas[tarea["carpeta_nueva"]] = fila
            if i % CADA_CUANTO_SE_GUARDA == 0:
                manifiesto.write_text(json.dumps(list(hechas.values()), ensure_ascii=False,
                                                 indent=1), encoding="utf-8")
        print(" " * 100, end="\r")
        manifiesto.write_text(json.dumps(list(hechas.values()), ensure_ascii=False,
                                         indent=1), encoding="utf-8")

    t = pd.DataFrame(list(hechas.values()))
    if args.descubrir:
        titulo("RESULTADO")
        print(tabla(t["veredicto"].value_counts().rename_axis("Veredicto")
                    .reset_index(name="Carpetas")))
        propuestas = t[t["propuesto"].astype(str) != ""].copy()

        # Un indice propuesto que YA esta en el nombre de otra carpeta no se puede
        # dar por bueno sin mirarlo: o los documentos de esta mencionan el pleito de
        # aquella, o una de las dos esta mal asignada.
        ya_usados = set()
        if MANIFIESTO.exists():
            ya_usados = {r["index_declarado"] for r in
                         json.loads(MANIFIESTO.read_text(encoding="utf-8"))}
        propuestas["ya_asignado_a_otra"] = propuestas["propuesto"].isin(ya_usados)

        firmes = propuestas[(propuestas["en_documentos"] >= CORROBORACION)
                            & (~propuestas["ya_asignado_a_otra"])]
        titulo(f"PROPUESTAS FIRMES ({len(firmes)})")
        print(f"  el mismo numero en {CORROBORACION}+ documentos y sin usar en otra carpeta")
        if len(firmes):
            print(tabla(firmes[["area", "carpeta_vieja", "propuesto",
                                "en_documentos", "donde", "leido_con"]].head(25)))
        flojas = propuestas[propuestas["en_documentos"] < CORROBORACION]
        print(f"\n  de un solo documento (mas flojas): {len(flojas)}")
        print(f"  el numero ya esta en otra carpeta : {int(propuestas['ya_asignado_a_otra'].sum())}")
        print("\n  NADA de esto se ha escrito. Es una propuesta para aprobar.")

        destino = RESULTADOS / "descubrimiento_indices.xlsx"
        with pd.ExcelWriter(destino) as escritor:
            t.to_excel(escritor, sheet_name="Todo", index=False)
            (firmes if len(firmes) else pd.DataFrame([{"": "ninguna"}])).to_excel(
                escritor, sheet_name="Propuestas firmes", index=False)
            (flojas if len(flojas) else pd.DataFrame([{"": "ninguna"}])).to_excel(
                escritor, sheet_name="Un solo documento", index=False)
        print(f"\nExcel: {destino}")
        print(f"Manifiesto: {manifiesto}")
        return

    titulo("RESULTADO")
    print(tabla(t["veredicto"].value_counts().rename_axis("Veredicto")
                .reset_index(name="Carpetas")))

    malas = repartir(list(hechas.values()))
    titulo(f"LAS QUE NO COINCIDEN, POR MOTIVO ({len(malas)})")
    if len(malas):
        print(tabla(malas["motivo"].value_counts().rename_axis("Motivo")
                    .reset_index(name="Carpetas")))

        # Lo urgente arriba y con los valores delante: son las que se pueden
        # resolver hoy, y las dos primeras categorias ni siquiera necesitan que
        # nadie abra un documento.
        # Los del index invalido ya no son 'NO COINCIDE' --con un numero que no
        # normaliza no hay contra que comparar-- pero SON errores, y mas claros que
        # ninguno: un indice de 1947 no es un caso vivo. Al hacer el veredicto mas
        # correcto se cayeron del listado urgente, que es lo unico que la gente mira.
        invalidos = t[t["veredicto"].astype(str).str.contains("no es un numero de caso")]
        if len(invalidos):
            titulo(f"EL INDICE DEL NOMBRE NO ES VALIDO ({len(invalidos)})")
            print("  no hace falta abrir ningun documento: el nombre esta mal")
            print(tabla(invalidos[["area", "carpeta_vieja", "carpeta_nueva",
                                   "index_declarado"]]))

        urgentes = malas[malas["motivo"].isin(CATEGORIAS[:2])]
        if len(urgentes):
            titulo(f"RESOLVER PRIMERO ({len(urgentes)})")
            print(tabla(urgentes[["area", "carpeta_vieja", "index_declarado",
                                  "indices_hallados", "motivo"]]))
            print("\n  'un digito' = uno de los dos esta mal, y hay que decidir cual.")
            print("  'año imposible' = el nombre esta mal, sin necesidad de abrir nada.")

        quedan = (malas["motivo"] == "queda por explicar").sum()
        print(f"\n  Para revision humana de verdad: {quedan}. El resto tiene motivo.")
    else:
        print("\n  ninguna: donde los documentos dan un indice, coincide con el nombre")

    destino = RESULTADOS / "verificacion_indices.xlsx"
    with pd.ExcelWriter(destino) as escritor:
        t.to_excel(escritor, sheet_name="Todo", index=False)
        (malas if len(malas) else pd.DataFrame([{"": "ninguna"}])).to_excel(
            escritor, sheet_name="No coinciden", index=False)
        if len(malas):
            columnas = [c for c in ("area", "carpeta_vieja", "carpeta_nueva",
                                    "index_declarado", "indices_hallados", "donde",
                                    "archivos_leidos") if c in malas.columns]
            malas[malas["motivo"] == "queda por explicar"][columnas].to_excel(
                escritor, sheet_name="Para revisar", index=False)
    print(f"\nExcel: {destino}")
    print(f"Manifiesto (nombre viejo y nuevo): {MANIFIESTO}")


if __name__ == "__main__":
    main()
