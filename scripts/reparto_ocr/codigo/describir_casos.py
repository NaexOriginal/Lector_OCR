r"""Un JSON por caso, en la raiz de su carpeta, con todos sus archivos y su texto.

    Matters/Angert, Helen - 601234 - 508065-2026/
      Claude-601234.json          <- esto
      01_General/
      02_Loss Mitigation/

DOS FASES SEPARADAS A PROPOSITO. Leer 108.562 archivos son entre dieciseis y
veinticuatro horas, y el cuello es la descarga de 160 GB, no el OCR. Si la lectura y
la subida fueran lo mismo, un fallo en la ultima hora tiraria todo el trabajo:

    --extraer   baja y lee, y va anotando cada archivo en un diario jsonl. Se puede
                cortar y seguir: al volver, salta lo que ya leyo
    --subir     arma un JSON por caso con lo que hay en el diario y lo sube. No
                vuelve a leer nada

EL TEXTO VA EN CLARO. Decision de RevOps del 24-sep-2026, tomada sabiendo lo que
implica: el JSON contiene el contenido integro de reportes de credito, extractos
bancarios y documentos de identidad, en un fichero que abre cualquiera con acceso a
la carpeta y que el buscador de SharePoint indexa.

Por eso los SSN SIEMPRE SE CUENTAN aunque no se enmascaren, y el recuento sale en
pantalla y en el propio JSON. Con --enmascarar-ssn se sustituyen por XXX-XX-nnnn.
Que la decision sea de quien la toma no quita que el dato tenga que estar a la vista.

LAS RUTAS SON LAS DEL DESTINO. Se lee del sitio viejo, donde los archivos estan
todavia, pero cada ficha dice en que subcarpeta del sitio NUEVO va a quedar. Asi el
JSON sirve igual si se genera antes o despues de la mudanza.

Uso:
    .venv\Scripts\python.exe describir_casos.py --extraer --limite 200
    .venv\Scripts\python.exe describir_casos.py --extraer
    .venv\Scripts\python.exe describir_casos.py --subir --simular
    .venv\Scripts\python.exe describir_casos.py --subir
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from clasificacion import tipo_de_texto
from copiar_a_matters import CARPETA_DESTINO, destino as destino_nuevo
from extraccion.config import drive_id
from extractor_completo.lectores import leer_con_detalle, topar_ocr
from extractor_completo.ocr import (NECESITA_OCR, avisar_paginas, diferir, empezar_archivo,
                                    se_difirio)
from sp_conexion import Graph
from sp_indices import cargar_arbol
from regla_nombres import partir_nombre
from ver_matters import SALIDA_DIR, tabla, titulo
from verificar_indices import bajar

PLAN = SALIDA_DIR / "plan_fase2.json"
DIARIO = SALIDA_DIR / "textos_extraidos.jsonl"

# Cada maquina escribe SU diario. Un fichero compartido en red se corrompe con
# cuatro procesos anadiendo lineas a la vez, y aqui no hace falta: cada una lee un
# trozo distinto, asi que al final se juntan sin solaparse.
DIARIOS_PARTE = "textos_extraidos.parte*.jsonl"

# LA PASADA COMPLETA DESDE MATTERS VA EN SUS PROPIOS DIARIOS. Lo leido hasta el
# 28-sep se leyo con cuatro paginas y un tope de 25 MB: esta incompleto y NO cuenta
# como hecho. Si compartieran diario, una ficha vieja de cuatro paginas bastaria
# para saltarse el archivo, y el JSONL saldria mezclando documentos completos con
# otros a medias sin forma de distinguirlos.
DIARIO_MATTERS = SALIDA_DIR / "textos_matters.jsonl"

# LAS DOS PASADAS (29-sep). En CPU el OCR va a ~8 archivos por hora; en GPU, a
# ~4 por minuto. Asi que se reparte por TIPO DE TRABAJO, no solo por trozos:
#
#   --sin-ocr   equipos SIN GPU: todo menos las imagenes, con el OCR apagado. Lo que
#               resulte necesitar OCR (un PDF escaneado, un Word con capturas) se
#               anota como NECESITA_OCR y se sigue.
#   --solo-ocr  equipos CON GPU: las imagenes (se sabe por la extension) y la COLA,
#               que es la lista de lo que la otra pasada dejo como NECESITA_OCR.
#
# Cada grupo se reparte entre sus procesos con su propio --de. La cola la genera
# reparto_ocr/cola_ocr.py juntando los diarios de todos los equipos.
IMAGENES = {"jpg", "jpeg", "png", "tif", "tiff", "bmp", "gif", "webp", "heic", "heif",
            "jfif", "jpe"}
COLA_OCR = SALIDA_DIR / "cola_ocr.txt"


def extension_de(nombre) -> str:
    nombre = str(nombre)
    return nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""


def leer_cola(ruta=COLA_OCR) -> set:
    """Los ids que esperan OCR, segun la ultima cola generada. Vacia si no hay."""
    if not ruta.exists():
        return set()
    return {l.strip() for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()}
# VERSION DE LOS LECTORES. La 1 recortaba aun con --paginas 0 (Word a 14
# parrafos, correos a 3.000 caracteres, Excel a 3x40, zips a 200 entradas y el
# rescate de PyMuPDF a 1 pagina). Lo leido con una version anterior se da por
# NO leido en la pasada de Matters y se vuelve a leer solo.
# La 3: el OCR pasa a ser SOLO Paddle. Lo que la 2 leyera con Tesseract se relee.
# La 4 (29-sep): lectores nuevos (.doc, .xls, rtf, eml, odt, pptx, heic, 7z,
# winmail.dat y los disfrazados por contenido). NO se relee todo: de la 3 solo lo
# que salio 'no reader for', que es justo lo que ahora tiene lector. Lo demas
# leido en la 3 ya estaba bien. Ver hay_que_releer().
VERSION_LECTURA = 4
VERSION_MINIMA = 3        # por debajo de esta, se relee todo

# Los motivos que NO se reintentan: no van a cambiar en la proxima tanda. Estaba
# escrito tres veces, una por funcion, y cada copia podia quedarse atras.
# 'not a document': los '._' del Mac y el codigo de las paginas web guardadas
# ('.js.download'); lo devuelve extractor_completo.lectores con esa frase.
PERMANENTES = ("larger than", "no reader for", "not a document")

# UNA IMAGEN SIN TEXTO ES DEFINITIVA SI EL MOTOR FUNCIONO. Las fotos sin letras
# (logos, paisajes, 3 caracteres sueltos) salian "sin texto" y, como ese motivo no
# era permanente, cada arranque de la tanda con GPU las volvia a pasar por Paddle
# ANTES que los escaneos: 74 imagenes en cada arranque, dos veces el 1-oct, y en las
# tres GPU. Si el motor estaba bien (ocr_ok) y no encontro texto, no lo va a
# encontrar manana. Si fue un error del motor (el oneDNN del 29-sep, el import
# roto), ocr_ok no esta y se sigue reintentando. Las fichas viejas no lo llevan:
# se reintentan una ultima vez y desde ahi quedan cerradas.
SIN_TEXTO_DE_VERDAD = ("OCR found no text in the image", "OCR returned only",
                       "OCR returned noise")


def es_definitiva(ficha: dict) -> bool:
    """Si una ficha esta resuelta y no hay que volver a leer el archivo."""
    if ficha.get("was_read"):
        return True
    motivo = str(ficha.get("not_read_because") or "")
    if any(m in motivo for m in PERMANENTES):
        return True
    return bool(ficha.get("ocr_ok")) and motivo.startswith(SIN_TEXTO_DE_VERDAD)


def hay_que_releer(ficha: dict) -> bool:
    """Si una ficha de la pasada de Matters se leyo con lectores que ya no valen."""
    version = ficha.get("reader_version", 1)
    if version < VERSION_MINIMA:
        return True
    motivo = str(ficha.get("not_read_because") or "")
    return version < VERSION_LECTURA and "no reader for" in motivo
DIARIOS_MATTERS_PARTE = "textos_matters.parte*.jsonl"
# Los diarios de una lectura por lista (--lista lista_X.txt -> textos_matters.lista_X.jsonl).
# Van aparte de los de los trozos para que cada etapa tenga el suyo y no se mezclen.
DIARIOS_MATTERS_LISTA = "textos_matters.lista_*.jsonl"


def diarios_de(origen: str) -> list:
    """Los diarios de una pasada. Nunca los de la otra."""
    if origen == "matters":
        patron, principal = DIARIOS_MATTERS_PARTE, DIARIO_MATTERS
        de_listas = sorted(SALIDA_DIR.glob(DIARIOS_MATTERS_LISTA))
    else:
        patron, principal = DIARIOS_PARTE, DIARIO
        de_listas = []
    return (sorted(SALIDA_DIR.glob(patron)) + de_listas
            + ([principal] if principal.exists() else []))


def indice_de(origen: str):
    """Los ids ya leidos que se reparten con el paquete. Uno por pasada."""
    return SALIDA_DIR / ("ya_leidos_matters.txt" if origen == "matters"
                         else "ya_leidos.txt")

SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Cuantas paginas de un PDF se leen POR DEFECTO. Mas de esto multiplica el tiempo
# sin anadir mucho CUANDO SOLO SE QUIERE IDENTIFICAR el documento: lo que dice que
# es esta al principio.
#
# Con --paginas 0 se leen todas. Es otra cosa distinta: ya no se describe el
# documento, se transcribe entero. Cuesta lo que cuesta y a veces hace falta.
PAGINAS = 4

# Por encima de esto no se baja POR DEFECTO. Un escaneo de 400 paginas cuesta
# minutos y aporta lo mismo que uno de 20 para saber que es.
#
# Con --max-mb 0 no hay tope. Son 1.297 archivos que suman 79 GB -- el mayor es de
# 872 MB -- asi que el tope no estaba puesto por capricho: quitarlo duplica con
# creces el tiempo de descarga.
MAX_MB = 25

# Subir por trozos a partir de 4 MB: es el limite del PUT simple de Graph.
TROZO = 4 * 1024 * 1024

# La bitacora de la lectura (ver extraer): cada cuanto dice 'VIVO', y cada cuantas
# paginas apunta por donde va un escaneo largo.
LATIDO = 300
PAGINAS_EN_BITACORA = 25

# En la pasada con GPU, los escaneos de mas de esto se dejan para el final. Ver el
# orden en extraer().
PESADOS_AL_FINAL = 5


def _duracion(segundos: float) -> str:
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos} s"
    if segundos < 3600:
        return f"{segundos // 60} min {segundos % 60:02d} s"
    return f"{segundos // 3600} h {segundos % 3600 // 60:02d} min"


def clave_de_contenido(caso, nombre, size_mb) -> tuple:
    """Lo que identifica un documento sin depender de su id.

    Lo leido desde el sitio de origen tiene el id de alli, que no es el de Matters.
    Expediente + nombre + tamano en MB (a dos decimales, que es como se guardo)
    es lo que tienen en comun las dos copias.
    """
    return (str(caso or "").strip(), str(nombre or "").strip(),
            round(float(size_mb or 0), 2))


def arbol_de_matters() -> pd.DataFrame:
    """El arbol de produccion, con las columnas que espera el resto de extraer().

    Sale de arbol_matters.py. Si no esta, se para: leer sin el seria leer nada, y
    con un arbol de otro sitio, leer lo que no es.
    """
    ruta = SALIDA_DIR / "arbol_matters.jsonl"
    if not ruta.exists():
        raise SystemExit(f"Falta {ruta}.\n  Corre antes: python arbol_matters.py")
    filas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    act = pd.DataFrame(filas)
    act["Carpeta"] = act["exp"]
    act["completa"] = "/Matters/" + act["exp"] + "/" + act["sub"].fillna("")
    return act


def estado_de_lectura(origen: str = "matters", pasada: str = "") -> dict:
    """Que hay que leer y que esta hecho. UN solo sitio que lo sepa.

    Lo usan comprobar.py, asignar.py y empaquetar.py para contar. Antes cada uno
    tenia su copia de esta cuenta y el 28-sep ya divergieron: el comprobador no
    leia ya_leidos.txt y prometia 270.710 pendientes donde habia 255.981.

    extraer() hace la misma cuenta por dentro, con mas cosas (el orden por caso, el
    texto para copiar entre duplicados). Si cambia el criterio de 'hecho' aqui,
    tiene que cambiar alli tambien.
    """
    if origen == "matters":
        act = arbol_de_matters()
    else:
        arbol = cargar_arbol()
        act = arbol[arbol["Ubicacion"].isin(("Active Matters", "Closed Matters"))
                    & ~arbol["es_carpeta"]]
        donde = donde_acabo_cada_caso()
        rep = reparto_por_prefijo()
        con_rep = {r[0].split("/")[3] for r in rep if len(r[0].split("/")) > 3}
        act = act[act["Carpeta"].isin(set(donde) | con_rep)]

    hechos, contenido, para_gpu = set(), set(), set()
    indice = indice_de(origen)
    if indice.exists():
        hechos |= {l.strip() for l in indice.read_text(encoding="utf-8").splitlines()
                   if l.strip()}
    for fichero in diarios_de(origen):
        for linea in fichero.read_text(encoding="utf-8", errors="replace").splitlines():
            if not linea.strip():
                continue
            try:
                f = json.loads(linea)
            except ValueError:
                continue
            motivo = str(f.get("not_read_because") or "")
            if origen == "matters" and hay_que_releer(f):
                continue
            if es_definitiva(f):
                hechos.add(f.get("id"))
                if f.get("was_read"):
                    contenido.add(clave_de_contenido(
                        f.get("caso"), f.get("file_name"), f.get("size_mb")))
            elif motivo == NECESITA_OCR:
                para_gpu.add(f.get("id"))

    # LAS DOS PASADAS, contadas igual que las hace extraer(): 'sin-ocr' no cuenta
    # imagenes ni lo apartado para la GPU; 'solo-ocr' solo cuenta imagenes y cola.
    apartado = (para_gpu | leer_cola(COLA_OCR)) - hechos
    if pasada == "sin-ocr":
        act = act[~act["name"].map(extension_de).isin(IMAGENES)]
        hechos = hechos | apartado
    elif pasada == "solo-ocr":
        act = act[act["name"].map(extension_de).isin(IMAGENES) | act["id"].isin(apartado)]

    def hecho(fila) -> bool:
        # SOLO POR ID DE ESTA PASADA. Reconocer lo leido en el origen por nombre y
        # tamano ahorraba quince mil archivos, pero aquellos se leyeron a medias:
        # darlos por hechos era justo lo que no hay que hacer.
        return fila.id in hechos

    pendientes = [r for r in act.itertuples() if not hecho(r)]
    return {"act": act, "pendientes": pendientes, "hechos": hechos,
            "ya_leidos_ids": [r.id for r in act.itertuples() if hecho(r)]}


def me_toca(identificador: str, mias: set[int], de: int) -> bool:
    """Si este archivo le toca a esta maquina. Sin hablar con las demas.

    EL REPARTO ES POR EL ID DEL ARCHIVO, no por su posicion en la lista. La lista
    depende del arbol y de lo que cada maquina lleve leido, asi que 'los primeros
    64.000' significaria una cosa distinta en cada equipo y acabarian pisandose.
    El id no cambia nunca, asi que el mismo archivo le toca siempre al mismo.

    md5 y no hash(): el hash de Python lleva una semilla aleatoria por proceso
    desde la 3.3, o sea que el reparto saldria distinto en cada arranque y dos
    maquinas leerian lo mismo mientras otro trozo no lo lee nadie.

    Se admiten VARIAS partes por maquina para poder repartir desigual: los equipos
    con GPU van entre cinco y diez veces mas rapido que los de solo CPU, asi que
    darles el mismo trozo a todos deja a los rapidos parados esperando.
    """
    return int(hashlib.md5(str(identificador).encode()).hexdigest(), 16) % de in mias


def clave_de_reparto(fila) -> str:
    """Por que se reparte: el CONTENIDO si se conoce, si no el id.

    En Matters hay 99.436 archivos que son copia exacta de otro (la regla de
    duplicados del jefe). Repartiendo por id, cada copia cae en un equipo distinto
    y cada uno le hace el OCR entero. Repartiendo por quickXorHash todas las copias
    caen en el mismo trozo: se lee una y las demas copian su texto (ver por_hash).
    """
    h = getattr(fila, "hash", None)
    return h if isinstance(h, str) and h else fila.id


def donde_acabo_cada_caso() -> dict[str, str]:
    """carpeta del ORIGEN -> expediente del sitio nuevo. Las dos secciones.

    SUSTITUYE AL FILTRO POR EL PLAN DE FASE 2, que era de Active y solo de los
    casos que ese plan tocaba. Con el, describir_casos veia 855 casos de los 2.873
    del origen: los 1.804 de Closed y 214 de Active quedaban fuera, y de ahi salen
    las 2.127 carpetas sin JSONL que reporta RevOps. No era que fallara la lectura;
    es que esos casos nunca se miraron.

    Un cliente partido tiene una carpeta en origen y varios expedientes en destino,
    asi que no se puede decir a cual va SU CARPETA -- eso se decide archivo a
    archivo y lo sabe reparto.jsonl. Aqui se deja fuera y lo resuelve
    `donde_acabo_este_archivo`.
    """
    mapa = json.loads((SALIDA_DIR / "mapa_nombres.json").read_text(encoding="utf-8"))
    cuantos: dict[str, int] = defaultdict(int)
    for m in mapa["matters"]:
        if m.get("nombre_origen") and m.get("nombre_actual"):
            cuantos[m["nombre_origen"]] += 1
    return {m["nombre_origen"]: m["nombre_actual"] for m in mapa["matters"]
            if m.get("nombre_origen") and m.get("nombre_actual")
            and cuantos[m["nombre_origen"]] == 1}


def reparto_por_prefijo() -> list[tuple[str, str]]:
    """[(carpeta de origen, carpeta de destino)] de lo que repartio repartir.py.

    Es la unica fuente que sabe a que expediente fue cada subcarpeta de un cliente
    partido, porque esa decision se tomo leyendo los documentos. Ordenado de ruta
    mas larga a mas corta para que gane la coincidencia mas especifica.
    """
    salida = []
    diario = SALIDA_DIR / "reparto.jsonl"
    if diario.exists():
        for linea in diario.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            r = json.loads(linea)
            if r.get("estado") == "ok" and r.get("origen") and r.get("destino"):
                salida.append((str(r["origen"]), str(r["destino"])))
    return sorted(set(salida), key=lambda x: -len(x[0]))


def donde_acabo_este_archivo(ruta: str, caso_origen: str, por_caso: dict,
                             reparto: list) -> tuple[str, str]:
    """(expediente, subcarpeta) donde queda este archivo en el sitio nuevo.

    Se pregunta primero al reparto porque es el que decidio caso por caso; el mapa
    por carpeta solo vale cuando el cliente tiene un unico expediente.
    """
    for origen, destino in reparto:
        if ruta.startswith(origen + "/"):
            # destino es 'Matter/03_Litigation/Subcarpeta'
            tramos = destino.split("/", 1)
            resto = ruta[len(origen):].lstrip("/").rsplit("/", 1)[0]
            sub = tramos[1] if len(tramos) > 1 else ""
            return tramos[0], "/".join(x for x in (sub, resto) if x)

    expediente = por_caso.get(caso_origen, "")
    if not expediente:
        return "", ""
    # Sin reparto, la copia fue integra: la estructura del destino es la del origen.
    marca = f"/{caso_origen}/"
    dentro = ruta.split(marca, 1)[1] if marca in ruta else ""
    return expediente, dentro.rsplit("/", 1)[0] if "/" in dentro else ""


def rutas_de_destino(plan: dict) -> tuple[dict[str, str], list[tuple[str, str]], dict[str, str]]:
    """(archivo->destino, [(carpeta_origen, carpeta_destino)], caso_origen->caso_destino)."""
    archivos = {o["origen"]: o["destino"] for o in plan["operaciones"]
                if o["tipo"] == "archivo"}
    carpetas = sorted(((o["origen"], o["destino"]) for o in plan["operaciones"]
                       if o["tipo"] == "carpeta"), key=lambda x: -len(x[0]))
    casos = {o["origen"].rsplit("/", 1)[0]: o["carpeta_destino"]
             for o in plan["operaciones"]}
    return archivos, carpetas, casos


def donde_quedara(ruta: str, archivos: dict, carpetas: list) -> str:
    """La ruta que tendra ese archivo en el sitio nuevo, o '' si el plan no lo mueve."""
    if ruta in archivos:
        return archivos[ruta]
    for origen, destino in carpetas:
        if ruta.startswith(f"{origen}/"):
            return destino + ruta[len(origen):]
    return ""


def extraer(args) -> None:
    # El diario lleva el numero del PRIMER trozo del proceso: con --partes 2,5,8
    # se llama parte2, que es suficiente para que dos procesos no escriban el mismo
    # fichero y para saber de quien es al juntarlos.
    mis_trozos = ([int(x) for x in str(args.partes).split(",")] if args.partes
                  else ([args.parte] if args.parte is not None else []))
    base = "textos_matters" if args.origen == "matters" else "textos_extraidos"
    # Con las dos pasadas, el trozo 0 de la CPU y el trozo 0 de la GPU son trozos
    # DISTINTOS (cada grupo tiene su --de): el nombre lleva el grupo para que no se
    # confundan al juntar los diarios en el equipo de Rafa.
    grupo = ".sinocr" if args.sin_ocr else (".ocr" if args.solo_ocr else "")
    diario_mio = (SALIDA_DIR / f"{base}.parte{mis_trozos[0]}{grupo}.jsonl" if mis_trozos
                  else (DIARIO_MATTERS if args.origen == "matters" else DIARIO))
    # CON UNA LISTA lista_X.txt, EL DIARIO ES textos_matters.lista_X.jsonl (6-oct): cada
    # etapa escribe en el suyo y no se mezcla con lo leido antes. Para que la lectura lo
    # vuelva a encontrar al relanzar, la lista tiene que llamarse lista_<algo>.txt.
    if args.lista and args.origen == "matters":
        etapa = Path(args.lista).stem
        if not etapa.startswith("lista_"):
            raise SystemExit(f"La lista tiene que llamarse lista_<algo>.txt (es {Path(args.lista).name}): "
                             "de su nombre sale el del diario de esta etapa.")
        diario_mio = SALIDA_DIR / f"{base}.{etapa}{grupo}.jsonl"
    if args.sin_ocr and args.solo_ocr:
        raise SystemExit("--sin-ocr y --solo-ocr son las dos pasadas: elige una.")
    if args.sin_ocr:
        diferir(True)
    desde_matters = args.origen == "matters"
    if desde_matters:
        # DESDE PRODUCCION. Ver arbol_matters.py: los equipos prestados no son
        # miembros del sitio de origen y reciben 403; de Matters si lo son. Y aqui
        # el expediente ES la carpeta del archivo, asi que no hace falta el plan de
        # fase 2, ni el mapa de nombres, ni el reparto: nada que cruzar.
        act = arbol_de_matters()
        archivos, carpetas, casos_origen, reparto, del_plan = {}, [], {}, [], {}
    else:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        archivos, carpetas, _ = rutas_de_destino(plan)
        casos_origen = donde_acabo_cada_caso()
        reparto = reparto_por_prefijo()
        del_plan = {o["origen"].split("/")[3]: o["carpeta_destino"]
                    for o in plan["operaciones"]}

        # LAS DOS SECCIONES. Antes decia Ubicacion == "Active Matters" y por eso
        # quedaban fuera 1.804 casos de Closed: ver donde_acabo_cada_caso.
        arbol = cargar_arbol()
        act = arbol[arbol["Ubicacion"].isin(("Active Matters", "Closed Matters"))
                    & ~arbol["es_carpeta"]].copy()
        act["completa"] = (act["ruta"].astype(str).str.split("root:", n=1).str[-1]
                           + "/" + act["name"].astype(str))
        # Se describe lo que tiene un sitio adonde ir: por su expediente, o porque
        # el reparto le encontro uno. Lo demas se quedaria con caso vacio y el
        # JSONL no sabria en que carpeta ponerse.
        con_reparto = {r[0].split("/")[3] for r in reparto if len(r[0].split("/")) > 3}
        act = act[act["Carpeta"].isin(set(casos_origen) | con_reparto | set(del_plan))]

    # SOLO LOS ARCHIVOS DE UNA LISTA (6-oct). Para leer primero un grupo de casos -- los
    # de 2022 en adelante -- sin cambiar el reparto: cada equipo sigue con sus trozos y
    # de ellos lee solo lo que esta en la lista. Va por ID DE ARCHIVO y no por nombre de
    # expediente porque los nombres cambian (RevOps renombra) y el id no. Al quitar
    # --lista, la siguiente corrida sigue con el resto sin repetir nada.
    if args.lista:
        ruta_lista = Path(args.lista)
        if not ruta_lista.exists():
            raise SystemExit(f"No existe la lista {ruta_lista}: copiala antes de lanzar.")
        en_lista = {l.strip() for l in ruta_lista.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")}
        antes = len(act)
        act = act[act["id"].isin(en_lista)]
        print(f"  --lista {ruta_lista.name}: {len(act):,} de {antes:,} archivos del arbol estan en "
              f"la lista ({len(en_lista):,} ids)")

    # UN FALLO TRANSITORIO NO ES 'HECHO'. Aqui se daba por leido cualquier archivo
    # que tuviera una linea en el diario, y con eso los 56.900 que fallaron con un
    # HTTP 401 -- el token caducado -- quedaban marcados para siempre: al relanzar
    # se saltaban y nadie volveria a mirarlos. El peor desenlace posible, porque el
    # informe final diria que el trabajo esta completo.
    #
    # Solo cuenta como hecho lo que se leyo, o lo que fallo por un motivo que no va
    # a cambiar: un archivo de 40 MB seguira siendo de 40 MB manana.
    hechos = set()
    transitorios = 0
    # Lo ya leido visto de otras dos formas, para no repetir OCR:
    #   por_hash       mismo contenido, aunque este en otro expediente
    #   por_contenido  mismo expediente, nombre y tamano: lo que se leyo desde el
    #                  sitio de ORIGEN tiene otro id, pero es el mismo documento
    por_hash: dict[str, dict] = {}
    por_contenido: set[tuple] = set()
    # Lo que la pasada sin OCR dejo para la GPU, visto en los diarios de ESTE equipo.
    para_gpu: set = set()
    hash_para_gpu: set = set()
    # SE LEEN TODOS LOS DIARIOS, no solo el propio: si una maquina se queda a medias
    # y su trozo se reparte entre las otras, lo que ya leyo no se vuelve a leer.
    # LO YA LEIDO EN OTRO EQUIPO, si viene. Es solo una lista de identificadores:
    # el diario entero pesa 109 MB y lleva el texto de reportes de credito y SSN en
    # claro, asi que no se reparte. Sin esto, cada equipo reharia su parte de lo que
    # ya estaba hecho -- 15.076 archivos.
    indice = indice_de(args.origen)
    if indice.exists():
        previos = {l.strip() for l in indice.read_text(encoding="utf-8").splitlines()
                   if l.strip()}
        hechos |= previos
        if previos:
            print(f"  {len(previos):,} ya leidos en otro equipo: se saltan")

    for fichero in diarios_de(args.origen):
        for linea in fichero.read_text(encoding="utf-8", errors="replace").splitlines():
            if not linea.strip():
                continue
            try:
                ficha = json.loads(linea)
            except ValueError:
                continue     # linea corrupta: se ignora y el archivo se vuelve a leer
            motivo = str(ficha.get("not_read_because") or "")
            if args.origen == "matters" and hay_que_releer(ficha):
                transitorios += 1     # leido con lectores viejos: se relee
                continue
            if es_definitiva(ficha):
                hechos.add(ficha["id"])
                if ficha.get("was_read"):
                    if ficha.get("hash"):
                        por_hash.setdefault(ficha["hash"], ficha)
                    por_contenido.add(clave_de_contenido(
                        ficha.get("caso"), ficha.get("file_name"), ficha.get("size_mb")))
            elif motivo == NECESITA_OCR:
                para_gpu.add(ficha["id"])
                if ficha.get("hash"):
                    hash_para_gpu.add(ficha["hash"])
            else:
                transitorios += 1

    # UNOS POCOS POR CASO, no todos. Leer los 108.562 son veinte horas; leer tres de
    # cada caso son veinte minutos y deja el JSONL puesto en las 855 carpetas. La
    # estructura queda montada hoy y el contenido se rellena despues subiendo
    # --por-caso, sin releer nada de lo que ya este en el diario.
    #
    # Se eligen los que mejor identifican el expediente: Word primero -- lo redacta
    # la firma y trae capa de texto siempre -- y dentro de cada formato los mas
    # pequenos, que cuestan menos y dicen lo mismo.
    if args.por_caso:
        orden = {"docx": 0, "pdf": 1, "doc": 2, "msg": 3}
        act = act.assign(
            _ext=act["name"].astype(str).str.rsplit(".", n=1).str[-1].str.lower())
        act = act.assign(_orden=act["_ext"].map(orden).fillna(9))
        # Los que ya estan leidos van primero: no cuestan nada y cuentan para el cupo.
        act = act.assign(_hecho=(~act["id"].isin(hechos)).astype(int))
        act = (act.sort_values(["_hecho", "_orden", "size"])
               .groupby("Carpeta", group_keys=False).head(args.por_caso))

    para_gpu -= hechos                       # lo que otra lectura ya resolvio
    cola = leer_cola(COLA_OCR) - hechos
    if args.sin_ocr:
        # Sin imagenes (siempre OCR), y sin lo que ya se sabe que necesita OCR: lo
        # que este equipo ya dejo para la GPU y lo que dice la cola de todos.
        act = act[~act["name"].map(extension_de).isin(IMAGENES)]
        hechos = hechos | para_gpu | cola
        print(f"  pasada SIN OCR: sin imagenes; {len(para_gpu | cola):,} ya estan "
              "apartados para la GPU")
    elif args.solo_ocr:
        # Las imagenes, que se sabe por la extension, y la cola.
        es_imagen = act["name"].map(extension_de).isin(IMAGENES)
        act = act[es_imagen | act["id"].isin(cola | para_gpu)]
        print(f"  pasada SOLO OCR: {int(es_imagen.sum()):,} imagenes + "
              f"{len(cola | para_gpu):,} de la cola"
              + ("" if COLA_OCR.exists() else "  (sin cola_ocr.txt: solo imagenes)"))
    pendientes = [f for f in act.itertuples() if f.id not in hechos]
    # LO LEIDO DESDE EL ORIGEN SE VUELVE A LEER. Se leyo con cuatro paginas y un
    # tope de 25 MB, asi que esta incompleto; como va en otros diarios (ver
    # DIARIO_MATTERS) ni siquiera aparece aqui.
    if mis_trozos:
        mias = set(mis_trozos)
        antes = len(pendientes)
        pendientes = [f for f in pendientes
                      if me_toca(clave_de_reparto(f), mias, args.de)]
        print(f"  trozos {sorted(mias)} de {args.de}: me tocan {len(pendientes):,} "
              f"de {antes:,}")
    # LAS COPIAS AL FINAL. Con varios hilos, dos copias del mismo documento puestas
    # juntas entrarian a la vez y las dos harian el OCR; al final de la cola, el
    # original ya esta leido y la copia solo copia el texto.
    vistas_h: set[str] = set()
    primeras, copias = [], []
    for f in pendientes:
        h = getattr(f, "hash", None)
        (copias if h and (h in vistas_h or h in por_hash) else primeras).append(f)
        if h:
            vistas_h.add(h)
    # EN LA PASADA CON GPU, LAS IMAGENES PRIMERO. Son pocas (unos miles) y rapidas,
    # y asi se terminan antes de entrar en los 130.000 escaneos de la cola, en vez de
    # quedar mezcladas entre ellos durante dias. El orden es estable: dentro de cada
    # grupo se mantiene el de siempre.
    #
    # Y SUS COPIAS JUSTO DETRAS, no al final de todo: una imagen repetida en otro
    # expediente solo copia el texto de su original, pero puesta detras de los
    # 130.000 escaneos se quedaba pendiente durante dias -- y con --limite para
    # "solo las imagenes" no llegaba nunca. Orden: imagenes, sus copias, escaneos,
    # sus copias. Las copias siguen yendo despues de su original.
    #
    # LOS ESCANEOS PESADOS AL FINAL (2-oct). De lo que le quedaba a un equipo, el 5,2%
    # de los archivos pasaba de 5 MB y sumaba el 61% del peso -- y el peso va con las
    # paginas, que es lo que cuesta. Dejarlos para el final NO acorta la tanda: el
    # trabajo es el mismo. Lo que cambia es que el 95% de los archivos sale en
    # ~40% del tiempo, y que los escaneos de cientos de paginas, que son los que
    # disparan la RAM, no aparecen hasta el final. Una copia pesa lo mismo que su
    # original, asi que sigue yendo detras de el, en el mismo grupo.
    if args.solo_ocr:
        es_img = lambda f: extension_de(f.name) in IMAGENES
        umbral = args.pesados_al_final * 2 ** 20
        pesa = lambda f: bool(umbral) and not es_img(f) and f.size > umbral
        ligero = lambda f: not es_img(f) and not pesa(f)
        pendientes = ([f for f in primeras if es_img(f)] + [f for f in copias if es_img(f)]
                      + [f for f in primeras if ligero(f)] + [f for f in copias if ligero(f)]
                      + [f for f in primeras if pesa(f)] + [f for f in copias if pesa(f)])
        n_img = sum(1 for f in pendientes if es_img(f))
        print(f"  las {n_img:,} imagenes van primero: para hacer SOLO las imagenes, "
              f"anade --limite {n_img}")
        if umbral:
            n_pesados = sum(1 for f in pendientes if pesa(f))
            print(f"  {n_pesados:,} escaneos de mas de {args.pesados_al_final} MB van al "
                  "final (--pesados-al-final 0 para no separarlos)")
    else:
        pendientes = primeras + copias
    if copias:
        print(f"  {len(copias):,} son copias de otro: van al final y no se releen")
    saltados = len(act) - len(pendientes)
    if args.limite:
        pendientes = pendientes[: args.limite]

    titulo(f"EXTRAER TEXTO ({len(pendientes):,} archivos)")
    print(f"  casos: {act['Carpeta'].nunique():,}   ya leidos: {saltados:,}")
    if transitorios:
        print(f"  {transitorios:,} fallaron por algo pasajero (token, red): se reintentan")
    print(f"  tamano maximo: {f'{args.max_mb} MB' if args.max_mb else 'SIN TOPE'}"
          f"   paginas por archivo: {args.paginas or 'TODAS'}\n")

    # 0 = sin tope, en los dos. El nombre del parametro dice el limite, no el modo,
    # para que leer la orden en el historial diga que se pidio.
    tope_mb = args.max_mb
    paginas = args.paginas
    # El OCR con su propio tope. None = el mismo que el general, que es como estaba.
    topar_ocr(args.paginas_ocr)

    # LA BITACORA (2-oct): log_<diario>.txt, al lado del diario. Para saber sin
    # preguntar a nadie si la lectura sigue viva. El diario solo escribe al TERMINAR
    # cada archivo, y un escaneo de 300 paginas son veinte minutos en silencio: desde
    # fuera eso no se distingue de un proceso colgado. La bitacora apunta:
    #
    #   ARRANQUE / FIN      con que se lanzo, cuanto habia pendiente, como acabo
    #   EMPIEZA OCR         cada escaneo al empezar, con sus paginas
    #   pagina N de M       cada PAGINAS_EN_BITACORA paginas de un escaneo largo
    #   LEIDO / NO LEIDO    cada archivo al terminar, con cuanto tardo
    #   VIVO                cada LATIDO segundos, tambien en pantalla, aunque no
    #                       termine nada: en que archivo y pagina esta, y el ritmo
    #
    # NO LLEVA TEXTO DE LOS DOCUMENTOS, solo nombres y cifras. Va dentro de la
    # lectura y no en un script aparte para que nadie tenga que acordarse de lanzarlo.
    bitacora = diario_mio.with_name(f"log_{diario_mio.stem}.txt")
    candado_bitacora = threading.Lock()
    en_curso: dict[int, dict] = {}          # hilo -> el archivo que esta leyendo
    terminados_a_las: deque = deque()       # para el ritmo de la ultima hora
    ultimo_registro: dict[int, dict] = {}   # hilo -> la ultima ficha que anoto
    cuenta_total = [0]                      # archivos terminados en esta sesion

    def apuntar(texto: str, en_pantalla: bool = False) -> None:
        linea = f"{datetime.now():%d/%m %H:%M:%S}  {texto}"
        try:
            with candado_bitacora, bitacora.open("a", encoding="utf-8") as fh:
                fh.write(linea + "\n")
        except OSError:
            pass                            # sin bitacora se sigue leyendo igual
        if en_pantalla:
            print(f"\n  {linea}", flush=True)

    def al_avanzar(pagina: int, total: int) -> None:
        yo = en_curso.get(threading.get_ident())
        if yo is None:
            return
        yo["pagina"], yo["paginas"] = pagina, total
        if pagina == 0:
            apuntar(f"EMPIEZA OCR  {yo['nombre'][:70]}  ({yo['mb']:.1f} MB, {total} pag.)")
        elif pagina % PAGINAS_EN_BITACORA == 0 and pagina < total:
            apuntar(f"   pagina {pagina} de {total}  ({yo['nombre'][:50]})")

    avisar_paginas(al_avanzar)
    pasada = "solo OCR" if args.solo_ocr else ("sin OCR" if args.sin_ocr else "completa")
    apuntar(f"ARRANQUE  pasada {pasada} | trozos {mis_trozos or 'todos'} de {args.de} | "
            f"hilos {args.hilos} | {len(pendientes):,} archivos pendientes"
            + (f" | solo la lista {Path(args.lista).name}" if args.lista else ""), en_pantalla=True)

    # PADDLE, UNA VEZ Y ANTES DE LOS HILOS. Si lo importan los 8 hilos a la vez, uno
    # lo coge a medio cargar y a partir de ahi falla todo el proceso: la noche del
    # 29-sep un equipo con GPU sano dejo 3.034 imagenes en 'could not be loaded'. Ver
    # paddle_ocr.calentar. En la pasada sin OCR no se carga: no se usa.
    if not args.sin_ocr and pendientes:
        from extractor_completo import paddle_ocr
        listo, por_que = paddle_ocr.calentar()
        if listo:
            print("  PaddleOCR cargado antes de empezar", flush=True)
            apuntar("PaddleOCR cargado")
        elif args.solo_ocr:
            apuntar(f"PADDLEOCR NO ARRANCA: {por_que}")
            raise SystemExit(f"\n  PaddleOCR NO ARRANCA: {por_que}\n  En --solo-ocr no hay nada "
                             "que hacer sin el. Corre comprobar.py y pasale la salida a Rafa.")
        else:
            print(f"  OJO: PaddleOCR no arranca ({por_que}): los escaneos quedaran sin "
                  "leer y se reintentaran en la proxima corrida.", flush=True)

    g = Graph()
    drive = destino_nuevo(g)[0] if desde_matters else drive_id()
    candado = threading.Lock()
    cuenta, arranque = Counter(), time.time()

    def anotar(registro: dict) -> None:
        # ensure_ascii=False escribe el texto tal cual, y el OCR puede devolver un
        # subrogado suelto que luego no se puede volver a leer: 20 lineas de la
        # corrida del 24-sep quedaron corruptas asi. Se limpian al escribir, que es
        # donde se sabe que paso; al leer solo se veria una linea rota sin contexto.
        linea = json.dumps(registro, ensure_ascii=False)
        linea = linea.encode("utf-8", "replace").decode("utf-8")
        with candado, diario_mio.open("a", encoding="utf-8") as diario:
            diario.write(linea + "\n")
        ultimo_registro[threading.get_ident()] = registro

    def uno(f) -> None:
        """_uno con su entrada en la bitacora. Lo que pase dentro no cambia."""
        yo = threading.get_ident()
        en_curso[yo] = {"nombre": str(f.name), "mb": f.size / 2 ** 20,
                        "desde": time.time(), "pagina": 0, "paginas": 0}
        ultimo_registro.pop(yo, None)
        try:
            _uno(f)
        finally:
            estado = en_curso.pop(yo, {})
            r = ultimo_registro.pop(yo, None)
            tardo = _duracion(time.time() - estado.get("desde", time.time()))
            nombre, mb = estado.get("nombre", "?")[:70], estado.get("mb", 0)
            if r is None:
                apuntar(f"CORTADO  {nombre}: no llego a anotarse (se relee la proxima vez)")
            elif r.get("copied_from"):
                apuntar(f"COPIA    {nombre}: texto copiado de otro identico ya leido")
            elif r.get("was_read"):
                pags = f", {estado['paginas']} pag." if estado.get("paginas") else ""
                apuntar(f"LEIDO    {nombre}  ({mb:.1f} MB{pags}): "
                        f"{r.get('extracted_text_length') or 0:,} caracteres en {tardo}")
            else:
                apuntar(f"NO LEIDO {nombre}: {str(r.get('not_read_because'))[:90]} ({tardo})")
            with candado_bitacora:
                terminados_a_las.append(time.time())
                cuenta_total[0] += 1

    def _uno(f) -> None:
        if desde_matters:
            caso = f.Carpeta
            carpeta_dentro = str(f.sub).rsplit("/", 1)[0] if "/" in str(f.sub) else ""
            sub_calculada, destino = carpeta_dentro, ""
        else:
            caso, sub_calculada = donde_acabo_este_archivo(
                f.completa, f.Carpeta, casos_origen, reparto)
            if not caso:
                caso, sub_calculada = del_plan.get(f.Carpeta, ""), sub_calculada
            destino = donde_quedara(f.completa, archivos, carpetas)
        extension = str(f.name).rsplit(".", 1)[-1].lower() if "." in str(f.name) else ""
        ficha = {
            "id": f.id, "caso": caso,
            "file_name": str(f.name),
            # El plan de fase 2 manda cuando cubre el archivo, porque ahi la
            # subcarpeta la decidio la tabla de equivalencias. Cuando no lo cubre
            # -- todo Closed -- se usa la ruta que tiene dentro de su caso, que es
            # la que conserva la copia integra. '(folder root)' solo si no hay ni
            # una cosa ni la otra.
            "subfolder": (destino.split(f"/{caso}/", 1)[-1].rsplit("/", 1)[0]
                          if destino and f"/{caso}/" in destino
                          else (sub_calculada or "(folder root)")),
            "size_mb": round(f.size / 2 ** 20, 2),
            "origen": f.completa,
            "se_migra": bool(destino) or desde_matters,
            # El hash va en la ficha para que un archivo repetido en otro expediente
            # -- los duplicados por la orden del despacho -- no se vuelva a leer.
            "hash": getattr(f, "hash", None),
            "reader_version": VERSION_LECTURA,
        }

        # MISMO CONTENIDO YA LEIDO: se copia el texto, no se baja ni se hace OCR.
        # Los duplicados de la regla del despacho estan en varios expedientes y son
        # el mismo documento byte a byte; leerlos N veces serian N pasadas de OCR.
        previa = por_hash.get(ficha["hash"]) if ficha["hash"] else None
        if previa:
            anotar({**ficha,
                    **{k: previa.get(k) for k in (
                        "was_read", "read_with", "not_read_because", "document_type",
                        "contains_ssn", "pages_read", "extracted_text_length",
                        "extracted_text")},
                    "copied_from": previa.get("id")})
            with candado:
                cuenta["copiado (mismo contenido)"] += 1
            return
        # UNA COPIA DE ALGO YA APARTADO PARA LA GPU: ni se baja. Es el mismo
        # documento byte a byte; la GPU lo leera una vez y las copias tomaran su texto.
        if args.sin_ocr and ficha["hash"] and ficha["hash"] in hash_para_gpu:
            anotar({**ficha, "was_read": False, "read_with": None,
                    "not_read_because": NECESITA_OCR})
            with candado:
                cuenta["para la GPU (copia)"] += 1
            return
        if tope_mb and f.size > tope_mb * 2 ** 20:
            anotar({**ficha, "was_read": False, "read_with": None,
                    "not_read_because": f"larger than {tope_mb} MB"})
            with candado:
                cuenta["saltado"] += 1
            return
        try:
            datos, motivo = bajar(g, drive, f.id)
            if not datos:
                anotar({**ficha, "was_read": False, "read_with": None,
                        "not_read_because": motivo[:80]})
                with candado:
                    cuenta["sin bajar"] += 1
                return
            empezar_archivo()
            texto, fallo, con_que = leer_con_detalle(extension, datos, paginas)
        except Exception as error:  # noqa: BLE001
            anotar({**ficha, "was_read": False, "read_with": None,
                    "not_read_because": f"{type(error).__name__}: {error}"[:80]})
            with candado:
                cuenta["error"] = cuenta["error"] + 1
            return

        # ALGO DE ESTE ARCHIVO PIDIO OCR y la pasada es sin OCR: el archivo ENTERO
        # va a la GPU, aunque parte se haya leido (un ZIP con un escaneo dentro, un
        # Word con una captura). Asi su texto sale de UNA lectura, no de dos.
        if args.sin_ocr and se_difirio():
            anotar({**ficha, "was_read": False, "read_with": None,
                    "not_read_because": NECESITA_OCR})
            with candado:
                cuenta["para la GPU (necesita OCR)"] += 1
                if ficha["hash"]:
                    hash_para_gpu.add(ficha["hash"])
            return

        tipo, _, _ = tipo_de_texto(texto) if texto.strip() else (None, "", "")
        lleva_ssn = bool(SSN.search(texto))
        if lleva_ssn and args.enmascarar_ssn:
            texto = SSN.sub(lambda m: f"XXX-XX-{m.group(0)[-4:]}", texto)
        anotar({**ficha,
                "was_read": bool(texto.strip()),
                "read_with": con_que or None,
                "not_read_because": fallo or None,
                "document_type": tipo.nombre if tipo else None,
                "contains_ssn": lleva_ssn,
                # Cuantas paginas se leyeron DE ESTE archivo. Va en la ficha y no
                # solo en la cabecera porque un mismo expediente puede tener
                # documentos leidos en corridas distintas con topes distintos, y
                # quien lo lea tiene que poder distinguir 'no dice nada mas' de
                # 'no se leyo mas'.
                "pages_read": paginas or "all",
                # El motor de OCR estaba bien en esta lectura: un "sin texto" con
                # esto es definitivo (ver es_definitiva). Sin OCR, o si el motor
                # fallo, no se pone y el archivo se sigue reintentando.
                "ocr_ok": (not args.sin_ocr) and not str(fallo or "").startswith(
                    ("PaddleOCR failed", "PaddleOCR could not", "paddleocr could not",
                     "paddlepaddle could not", "paddleocr is not", "paddleocr is installed")),
                "extracted_text_length": len(texto),
                "extracted_text": texto or None})
        with candado:
            cuenta["leido" if texto.strip() else "sin texto"] += 1
            if lleva_ssn:
                cuenta["con SSN"] += 1
            if ficha["hash"] and texto.strip():
                por_hash.setdefault(ficha["hash"], {
                    **ficha, "was_read": True, "read_with": con_que or None,
                    "not_read_because": fallo or None,
                    "document_type": tipo.nombre if tipo else None,
                    "contains_ssn": lleva_ssn, "pages_read": paginas or "all",
                    "extracted_text_length": len(texto),
                    "extracted_text": texto or None})
        time.sleep(0.1)

    # SE PUEDE PARAR Y SEGUIR, y para una tanda de 256.000 archivos y catorce horas
    # eso no es un extra: es la unica forma de hacerla. Cada archivo se anota en el
    # diario en cuanto se lee -- se abre y se cierra por linea, asi que esta en
    # disco, no en un buffer -- y al volver a arrancar los que ya estan se saltan.
    #
    # Ctrl+C no rompe nada: lo peor que pasa es que los archivos que estuvieran a
    # medio leer en ese instante se vuelvan a leer la proxima vez.
    parar = {"ahora": False}

    def cortar(*_):
        if parar["ahora"]:          # segundo Ctrl+C: salir sin esperar a los hilos
            raise KeyboardInterrupt
        parar["ahora"] = True
        print("\n  parando... se termina lo que hay en vuelo y se guarda. "
              "Ctrl+C otra vez para salir ya.", flush=True)

    anterior = signal.signal(signal.SIGINT, cortar)
    hechos_ahora = 0

    # EL LATIDO: cada LATIDO segundos, aunque no termine ningun archivo. Es lo que
    # distingue 'esta con un escaneo largo' de 'se colgo': dice en que archivo y en que
    # pagina esta, y desde cuando.
    fin_del_latido = threading.Event()

    def latir() -> None:
        while not fin_del_latido.wait(LATIDO):
            ahora = time.time()
            with candado_bitacora:
                while terminados_a_las and ahora - terminados_a_las[0] > 3600:
                    terminados_a_las.popleft()
                ultima_hora = len(terminados_a_las)
            trabajando = []
            for yo in list(en_curso.values()):
                pagina = (f", pagina {yo['pagina']} de {yo['paginas']}"
                          if yo.get("paginas") else "")
                trabajando.append(f"{yo['nombre'][:45]} (hace "
                                  f"{_duracion(ahora - yo['desde'])}{pagina})")
            apuntar(f"VIVO  {cuenta_total[0]:,} de {len(pendientes):,} en esta sesion | "
                    f"{ultima_hora} en la ultima hora | "
                    + ("; ".join(trabajando) if trabajando else "entre archivos"),
                    en_pantalla=True)

    threading.Thread(target=latir, daemon=True).start()

    # LA SUBIDA CADA HORA (6-oct): lo NUEVO del diario, como un trozo con su hora, y la
    # bitacora entera, a Documentos/JSONL/<diario>/ del sitio Matters. Asi nadie tiene que
    # ir pasando los diarios a mano, y el avance se ve desde SharePoint.
    #
    # No se resube el diario entero: SharePoint guardaria una version de cientos de MB
    # cada hora. Se sube desde donde se quedo la ultima vez (subida_<diario>.json, al lado
    # del diario) y solo hasta el ultimo salto de linea, para no partir una ficha. Juntando
    # los trozos en orden sale el diario completo.
    #
    # Si una subida falla, se apunta en la bitacora y la proxima lleva tambien lo que
    # quedo: la lectura no se para nunca por esto.
    subir_activo = desde_matters and not args.sin_subir
    estado_subida = diario_mio.with_name(f"subida_{diario_mio.stem}.json")
    carpeta_sp = f"{SUBIDA_CARPETA}/{diario_mio.stem.replace('textos_matters.', '')}"
    candado_subida = threading.Lock()

    def subir_ahora(motivo: str) -> None:
        if not subir_activo:
            return
        with candado_subida:
            try:
                est = (json.loads(estado_subida.read_text(encoding="utf-8"))
                       if estado_subida.exists() else {"offset": 0, "trozos": 0})
                tam = diario_mio.stat().st_size if diario_mio.exists() else 0
                if est["offset"] > tam:
                    # El diario es otro (se borro y empezo de cero): se sube desde el inicio.
                    # El contador de trozos NO vuelve a cero: los nombres no se repiten.
                    apuntar("SUBIDA  el diario es mas corto que lo ya subido: se sube desde el inicio")
                    est["offset"] = 0
                    estado_subida.write_text(json.dumps(est), encoding="utf-8")
                nuevo = b""
                if tam > est["offset"]:
                    with open(diario_mio, "rb") as fh:
                        fh.seek(est["offset"])
                        nuevo = fh.read(tam - est["offset"])
                    nuevo = nuevo[:nuevo.rfind(b"\n") + 1]
                if nuevo:
                    # Numero correlativo + hora: dos trozos nunca se llaman igual (subir con el
                    # mismo nombre REEMPLAZA en SharePoint) y ordenados por nombre quedan en orden.
                    nombre = (f"{diario_mio.stem}.{est['trozos'] + 1:05d}."
                              f"{datetime.now():%Y-%m-%d_%H%M%S}.jsonl")
                    subir_a_sharepoint(g, drive, f"{carpeta_sp}/{nombre}", nuevo)
                    est["offset"] += len(nuevo)
                    est["trozos"] += 1
                    estado_subida.write_text(json.dumps(est), encoding="utf-8")
                    lineas = nuevo.count(b"\n")
                    apuntar(f"SUBIDA  {nombre}: {lineas:,} fichas, {len(nuevo) / 2 ** 20:.1f} MB "
                            f"-> {carpeta_sp} ({motivo})")
                if bitacora.exists():
                    subir_a_sharepoint(g, drive, f"{carpeta_sp}/{bitacora.name}", bitacora.read_bytes())
            except Exception as error:  # noqa: BLE001 - la subida nunca para la lectura
                apuntar(f"SUBIDA FALLIDA ({motivo}): {type(error).__name__}: {str(error)[:150]} "
                        "| se reintenta en la proxima", en_pantalla=True)

    fin_de_subida = threading.Event()

    def subir_cada_hora() -> None:
        while not fin_de_subida.wait(SUBIDA_CADA_MIN * 60):
            subir_ahora("cada hora")

    if subir_activo:
        subir_ahora("al arrancar")           # lo que quedara de la corrida anterior
        threading.Thread(target=subir_cada_hora, daemon=True).start()
    try:
        with ThreadPoolExecutor(max_workers=args.hilos) as piscina:
            for n, _ in enumerate(piscina.map(uno, pendientes), 1):
                hechos_ahora = n
                if parar["ahora"]:
                    break
                if args.minutos and (time.time() - arranque) / 60 >= args.minutos:
                    print(f"\n  se cumplieron los {args.minutos} minutos: se para "
                          "aqui y se sigue en la proxima corrida.", flush=True)
                    break
                if n % 25 == 0:
                    ritmo = n / max(1e-9, (time.time() - arranque) / 60)
                    print(f"  [{n:>6}/{len(pendientes):,}]  {ritmo:,.0f}/min"
                          f"   faltan {(len(pendientes)-n)/max(ritmo,1e-9)/60:,.1f} h ",
                          end="\r", flush=True)
    except KeyboardInterrupt:
        print("\n  cortado a la fuerza.", flush=True)
    finally:
        signal.signal(signal.SIGINT, anterior)
        fin_del_latido.set()
        avisar_paginas(None)
        como = ("PARADO con Ctrl+C" if parar["ahora"]
                else "TERMINADO" if hechos_ahora >= len(pendientes) else "PARADO")
        apuntar(f"FIN  {como}: {hechos_ahora:,} de {len(pendientes):,} en "
                f"{_duracion(time.time() - arranque)} | "
                + ", ".join(f"{k} {v:,}" for k, v in cuenta.most_common()), en_pantalla=True)
        fin_de_subida.set()
        subir_ahora("al terminar")
    print()
    if hechos_ahora < len(pendientes):
        print(f"  quedan {len(pendientes) - hechos_ahora:,} para la proxima: "
              "vuelve a lanzar el mismo comando y sigue donde lo dejo.\n")

    titulo("RESULTADO")
    print(tabla(pd.DataFrame([{"Resultado": k, "Archivos": v}
                              for k, v in cuenta.most_common()])))
    if cuenta["con SSN"]:
        estado = ("enmascarados como XXX-XX-nnnn" if args.enmascarar_ssn
                  else "EN CLARO en el JSON")
        print(f"\n  {cuenta['con SSN']:,} archivos contienen numeros con forma de "
              f"SSN: van {estado}.")
    print(f"\nDiario: {diario_mio}")


def subir(args) -> None:
    desde_matters = args.origen == "matters"
    diarios = diarios_de(args.origen)
    if not diarios:
        raise SystemExit(f"No hay diarios de la pasada '{args.origen}'. "
                         "Corre antes: describir_casos.py --extraer")
    if len(diarios) > 1:
        print(f"  juntando {len(diarios)} diarios: "
              + ", ".join(d.name for d in diarios))

    # Las carpetas que se renombraron DESPUES de extraer. El diario guarda el nombre
    # que tenian entonces, y subir a ese nombre buscaria una carpeta que ya no
    # existe. 140 casos cambiaron de ID al corregir los duplicados.
    renombradas: dict[str, str] = {}
    corregidos = SALIDA_DIR / "ids_corregidos.xlsx"
    if corregidos.exists():
        tabla_ = pd.read_excel(corregidos, sheet_name="Renombrar")
        renombradas = {str(a): str(b) for a, b in
                       zip(tabla_["Se llama ahora"], tabla_["Debe llamarse"])
                       if pd.notna(a) and pd.notna(b)}

    # UNA LINEA ROTA NO PUEDE TIRAR LA SUBIDA ENTERA. El diario tiene 20 lineas de
    # antes de sanear la escritura, con surrogates sueltos que json no sabe leer.
    # Con un json.loads pelado, subir 816 casos moria en la primera. Se saltan, pero
    # se CUENTAN y se dicen: una linea ilegible es un archivo que nadie va a
    # describir, y eso tiene que verse.
    # id del archivo -> expediente, con el mapa de AHORA. Para rescatar las fichas
    # que se leyeron antes de que su caso se supiera.
    # Desde Matters la ficha ya trae su expediente -- es la carpeta del archivo --,
    # asi que no hay nada que rescatar ni hace falta el arbol del origen, que ademas
    # no viaja en el paquete.
    de_su_id = {}
    if not desde_matters:
        from sp_indices import cargar_arbol
        _arbol = cargar_arbol()
        _donde = donde_acabo_cada_caso()
        de_su_id = {r.id: _donde[r.Carpeta] for r in _arbol.itertuples()
                    if not r.es_carpeta and r.Carpeta in _donde}

    por_caso: dict[str, list[dict]] = defaultdict(list)
    ultima: dict = {}
    ilegibles = sin_caso = con_caso = 0
    for linea in (l for d in diarios
                  for l in d.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not linea.strip():
            continue
        try:
            ficha = json.loads(linea)
        except ValueError:
            ilegibles += 1
            continue
        # EL CASO SE VUELVE A RESOLVER AQUI si la ficha vino sin el. Al extraer se
        # guarda el expediente que se conocia ENTONCES, y un archivo leido antes de
        # que su caso tuviera ID quedaba con caso vacio: ni se subia, ni se volvia a
        # leer -- contaba para la cuota de tres y la daba por cumplida. 51 fichas de
        # 29 casos estaban asi, entre ellas las tres de 'Vela, Joseph'.
        #
        # El expediente no depende de cuando se leyo: depende de en que carpeta esta
        # el archivo, y eso se sabe ahora mejor que entonces.
        # Lo leido con lectores viejos no cuenta ni se sube: estaba recortado, y
        # contarlo daria el expediente por completo antes de releerlo.
        if args.origen == "matters" and hay_que_releer(ficha):
            continue
        caso = ficha.get("caso") or de_su_id.get(ficha.get("id"), "")
        if caso:
            # POR ID Y GANA LA ULTIMA. El diario es un registro de intentos, no una
            # lista de archivos: un fichero que fallo por red y se releyo despues
            # tiene DOS lineas, y sin esto el JSONL lo mostraria dos veces -- una
            # diciendo que no se pudo leer y otra con su texto.
            #
            # Gana la ultima porque el diario se escribe en orden: la lectura mas
            # reciente es la que mas sabe. Importa ahora mas que nunca, con 57.000
            # fallos transitorios pendientes de reintento y una tanda que lee el
            # documento entero en vez de cuatro paginas.
            con_caso += 1
            clave = ficha.get("id") or id(ficha)
            # CON DOS PASADAS, LA ULTIMA NO SIEMPRE ES LA QUE MAS SABE. El mismo
            # archivo tiene 'pendiente de OCR' en el diario de un equipo sin GPU y su
            # texto en el de uno con GPU, y el orden entre diarios es el de sus
            # nombres, no el del tiempo. Una lectura con texto no la pisa nunca una
            # ficha sin leer.
            previa = ultima.get(clave)
            if not (previa and previa[1].get("was_read") and not ficha.get("was_read")):
                ultima[clave] = (renombradas.get(caso, caso), {**ficha, "caso": caso})
        else:
            sin_caso += 1
    repetidas = con_caso - len(ultima)
    for caso, ficha in ultima.values():
        por_caso[caso].append(ficha)
    if repetidas:
        print(f"  {repetidas:,} lecturas repetidas del mismo archivo: se queda la "
              "ultima, que es la que mas sabe")
    if sin_caso:
        print(f"  {sin_caso:,} fichas siguen sin expediente y no se suben")
    if ilegibles:
        print(f"  OJO: {ilegibles:,} lineas del diario no se pudieron leer y se "
              "saltan. Esos archivos se quedan sin describir; para recuperarlos hay "
              "que volver a extraerlos.")
    if renombradas:
        print(f"  {len(renombradas):,} casos se renombraron despues de extraer: "
              "se usa el nombre de ahora")

    titulo(f"SUBIR UN JSONL POR CASO ({len(por_caso):,} casos)")
    con_ssn = sum(1 for fichas in por_caso.values()
                  for f in fichas if f.get("contains_ssn"))
    print(f"  archivos descritos: {sum(len(v) for v in por_caso.values()):,}")
    print(f"  con SSN dentro    : {con_ssn:,}")

    if desde_matters:
        # EL TOTAL ES EXACTO. Cada archivo de Matters esta en un solo expediente,
        # asi que el numero de archivos por carpeta sale contando, sin el problema
        # de los clientes partidos que habia leyendo del origen.
        totales = arbol_de_matters().groupby("exp").size().to_dict()

        # SOLO LOS EXPEDIENTES TERMINADOS. Subir uno a medias pisaria el JSONL que
        # ya tiene -- de cuatro paginas, pero de todos sus documentos -- con uno que
        # solo describe los que lleve esta pasada: se perderia informacion en vez de
        # ganarla. Un expediente esta terminado cuando todos sus archivos tienen una
        # ficha que o se leyo, o fallo por algo que no va a cambiar.
        def resuelta(f) -> bool:
            return es_definitiva(f)

        terminados = {c for c, fichas in por_caso.items()
                      if sum(1 for f in fichas if resuelta(f)) >= totales.get(c, 10 ** 9)}
        a_medias = len(por_caso) - len(terminados)
        if a_medias and not args.incluir_parciales:
            print(f"  {a_medias:,} expedientes aun a medias: se dejan con el JSONL que "
                  "tienen (usa --incluir-parciales para subirlos igual)")
            for c in list(por_caso):
                if c not in terminados:
                    por_caso.pop(c)
        print(f"  {len(terminados):,} expedientes leidos por completo")
    else:
        totales = archivos_por_caso()

    g = Graph()
    destino_drive, _ = destino_nuevo(g)

    # SE COMPRUEBA CONTRA EL DESTINO ANTES DE ESCRIBIR NADA. Ver como_se_llaman_ahora.
    reales, por_clave = como_se_llaman_ahora(g)
    renombrados, perdidos = {}, []
    for caso in list(por_caso):
        if caso in reales:
            continue
        base, _, indice, cerrado = partir_nombre(caso)
        candidatos = por_clave.get(
            (base.strip().lower(), str(indice or "").strip(), cerrado), [])
        if len(candidatos) == 1:
            renombrados[caso] = candidatos[0]
        else:
            perdidos.append((caso, len(candidatos)))
    for viejo, nuevo in renombrados.items():
        por_caso[nuevo].extend(por_caso.pop(viejo))
    if renombrados:
        print(f"  {len(renombrados):,} casos se llaman distinto en el destino "
              "(RevOps reasigno IDs): se usa el nombre de ahora")
    if perdidos:
        print(f"  OJO: {len(perdidos):,} casos NO se pueden situar en el destino y "
              "NO se suben:")
        for caso, cuantos in perdidos[:10]:
            print(f"     {caso[:58]:<60} {'ambiguo: ' + str(cuantos) if cuantos else 'no existe'}")
        for caso, _ in perdidos:
            por_caso.pop(caso, None)

    if args.simular:
        for caso, fichas in list(por_caso.items())[:5]:
            cuerpo = armar(caso, fichas, totales.get(caso))
            print(f"  {caso[:46]:<48} {len(fichas):>5} archivos  "
                  f"{len(cuerpo.encode('utf-8'))/2**20:.2f} MB")
        print("\n(simulacion: no se subio nada)")
        return

    # LO QUE NO CAMBIO NO SE VUELVE A SUBIR. Cada corrida reescribia los 2.537
    # JSONL enteros aunque solo hubieran cambiado veinte: media hora de subida y
    # 2.537 versiones nuevas en SharePoint por nada. Se guarda cuantas fichas tenia
    # cada expediente la ultima vez y se salta el que siga igual.
    #
    # El contador de fichas es la senal correcta y no la fecha: el JSONL lleva un
    # 'generated' con la hora, asi que comparar el contenido daria distinto siempre.
    # Una huella por pasada: comparar fichas de la pasada completa con las de la de
    # cuatro paginas daria 'distinto' siempre, por la razon equivocada.
    HUELLA = SALIDA_DIR / ("jsonl_subidos_matters.json" if desde_matters
                           else "jsonl_subidos.json")
    previo = json.loads(HUELLA.read_text(encoding="utf-8")) if HUELLA.exists() else {}
    iguales = [c for c, f in por_caso.items() if previo.get(c) == len(f)]
    for c in iguales:
        por_caso.pop(c)
    if iguales:
        print(f"  {len(iguales):,} expedientes no han cambiado desde la ultima "
              "subida: se saltan")

    hechos, fallos = 0, []
    for n, (caso, fichas) in enumerate(sorted(por_caso.items()), 1):
        cuerpo = armar(caso, fichas, totales.get(caso))
        crudo = cuerpo.encode("utf-8")
        nombre = f"Claude-{caso.rsplit(' - ', 2)[-2] if ' - ' in caso else caso}.jsonl"
        ruta = quote(f"{CARPETA_DESTINO}/{caso}/{nombre}", safe="/")
        try:
            if len(crudo) < TROZO:
                r = g.sesion.put(
                    f"https://graph.microsoft.com/v1.0/drives/{destino_drive}"
                    f"/root:/{ruta}:/content", data=crudo, timeout=120)
                if r.status_code >= 300:
                    raise OSError(f"{r.status_code}: {r.text[:100]}")
            else:
                subir_por_trozos(g, destino_drive, ruta, crudo)
            hechos += 1
        except Exception as error:  # noqa: BLE001
            fallos.append((caso, f"{type(error).__name__}: {error}"[:110]))
        print(f"  [{n:>5}/{len(por_caso):,}] subidos {hechos:,}  fallos {len(fallos)} ",
              end="\r", flush=True)
    print()
    titulo("RESULTADO")
    print(f"  subidos: {hechos:,}\n  fallos:  {len(fallos):,}")
    for caso, motivo in fallos[:10]:
        print(f"    {caso[:44]:<46} {motivo}")


def archivos_por_caso() -> dict[str, int]:
    """Cuantos archivos tiene cada caso EN TOTAL, con el nombre DEL DESTINO.

    Hace falta para que la cabecera pueda decir 3 DE CUANTOS. Sin ese numero,
    'files_described: 3' no distingue un caso de tres documentos de uno de
    ochocientos del que se leyeron tres, y el pase que venga despues no sabe que le
    falta -- que es justo lo que hace inservible una cobertura parcial.

    HAY QUE TRADUCIR EL NOMBRE. El arbol cuenta por carpeta del ORIGEN
    ('Khan, Nasrin') y el diario guarda el caso por su nombre del DESTINO
    ('Khan, Nasrin - 600828 - 35209-2018'). Sin el mapa no casa ni uno: se
    comprobo, 0 de 816.

    Un cliente partido tiene una carpeta en origen y varias en destino, asi que su
    total se reparte entre ellas y no se puede repartir sin saber que archivo va a
    cual. En esos casos se devuelve None, que la cabecera muestra tal cual: es
    preferible no decir nada a decir un numero inventado.
    """
    from sp_indices import cargar_arbol

    arbol = cargar_arbol()
    por_origen = arbol[~arbol["es_carpeta"]].groupby("Carpeta").size().to_dict()

    mapa = json.loads((SALIDA_DIR / "mapa_nombres.json").read_text(encoding="utf-8"))
    cuantos_destinos: dict[str, int] = defaultdict(int)
    for m in mapa["matters"]:
        if m.get("nombre_origen") and m.get("nombre_actual"):
            cuantos_destinos[m["nombre_origen"]] += 1

    salida: dict[str, int] = {}
    for m in mapa["matters"]:
        origen, actual = m.get("nombre_origen"), m.get("nombre_actual")
        if not origen or not actual or origen not in por_origen:
            continue
        if cuantos_destinos[origen] > 1:
            continue          # cliente partido: el total no es de este caso solo
        salida[actual] = por_origen[origen]
    return salida


def como_se_llaman_ahora(g) -> dict[str, str]:
    """{lo que dice el diario: como se llama HOY en el destino}. Se PREGUNTA al sitio.

    POR QUE NO BASTA CON ids_corregidos.xlsx. RevOps sigue tocando /teams/Matters:
    en su informe del 28-sep constan 57 carpetas renombradas por colisiones de ID,
    41 borradas y 2 fusionadas. El diario guarda el nombre que tenian al extraer, y
    169 de los 2.414 casos ya no se llaman asi.

    Y SUBIR A UN NOMBRE QUE NO EXISTE NO FALLA: Graph crea la ruta al hacer PUT, asi
    que saldrian 169 carpetas fantasma con un JSONL suelto dentro y nadie se
    enteraria. Por eso esto se resuelve preguntando al destino, no con una tabla.

    Se empareja por cliente + indice + marca 'Closed', que es lo que no cambia; el
    ID si. La marca hace falta: hay clientes con dos expedientes del mismo indice,
    uno abierto y otro cerrado ('Paterson, David - 900006 - 601537-2025' y
    'Paterson, David - Closed - 900275 - 601537-2025'), y sin ella los doce casos
    asi salian ambiguos y se quedaban sin subir.

    Si aun asi hay mas de un candidato no se elige: el caso se salta y se dice.
    """
    reales, url = set(), (f"/drives/{destino_nuevo(g)[0]}/root:/{CARPETA_DESTINO}"
                          ":/children?$select=name&$top=999")
    while url:
        r = g.get(url)
        reales |= {x["name"] for x in r.get("value", [])}
        url = r.get("@odata.nextLink")

    def clave(nombre: str) -> tuple[str, str, bool]:
        base, _, indice, cerrado = partir_nombre(nombre)
        return base.strip().lower(), str(indice or "").strip(), cerrado

    por_clave: dict[tuple[str, str], list[str]] = defaultdict(list)
    for nombre in reales:
        por_clave[clave(nombre)].append(nombre)
    return reales, por_clave


def armar(caso: str, fichas: list[dict], total_del_caso: int | None = None,
          carpeta_id: str | None = None) -> str:
    """El JSONL de un caso: una linea por archivo, precedida de una de resumen.

    JSONL y no JSON por dos razones practicas. Se puede AÑADIR: cuando se lean mas
    archivos de este caso, se pegan lineas al final sin rehacer el fichero ni
    reparsearlo. Y se puede leer a trozos: un caso de 4.044 archivos no obliga a
    cargar veinte megas en memoria para mirar uno.

    La primera linea lleva el resumen y se reconoce por tener 'matter'; las demas
    son documentos y llevan 'file_name'.

    EL ID DE SHAREPOINT VA PRIMERO en cada linea (6-oct): es lo que no cambia aunque el
    archivo se renombre o se mueva, y lo que hay que tener a mano para ir a buscarlo.
    Antes se quitaba al armar y solo quedaba, en las copias, el 'copied_from'.
    `carpeta_id` es el de la carpeta del expediente, si quien llama lo conoce.
    """
    limpias = [{"sharepoint_id": f.get("id"),
                **{k: v for k, v in f.items() if k not in ("id", "caso")}} for f in fichas]
    limpias.sort(key=lambda f: (f.get("subfolder") or "", f.get("file_name") or ""))
    cabecera = {
        **({"sharepoint_folder_id": carpeta_id} if carpeta_id else {}),
        "matter": caso,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "files_described": len(limpias),
        # CUANTOS TIENE EL CASO EN TOTAL. Sin esto, 'files_described: 3' no dice si
        # el caso esta completo o si faltan 797. Es el dato que permite volver mas
        # tarde y saber por donde seguir.
        "files_in_matter": total_del_caso,
        "coverage": (None if not total_del_caso
                     else round(len(limpias) / total_del_caso, 3)),
        "files_read": sum(1 for f in limpias if f.get("was_read")),
        "files_with_ssn": sum(1 for f in limpias if f.get("contains_ssn")),
        # Lo que se leyo de verdad, sacado de las fichas: si la tanda mezclo topes
        # distintos salen los dos y no se finge un numero unico.
        "pages_read_per_file": sorted(
            {f.get("pages_read", PAGINAS) for f in limpias}, key=str) or [PAGINAS],
        "partial": total_del_caso is None or len(limpias) < total_del_caso,
        "note": ("One JSON object per line. First line is this summary; the rest are "
                 "documents. Compare files_described with files_in_matter to know "
                 "what is still pending; more lines can be appended later. Full extracted text, "
                 "unmasked - contains document contents verbatim, including any "
                 "personal data they carry."),
    }
    return "\n".join(json.dumps(o, ensure_ascii=False)
                     for o in [cabecera, *limpias]) + "\n"


# LA SUBIDA DE LOS DIARIOS A SHAREPOINT (6-oct): a Documentos/JSONL/ del sitio Matters, una
# carpeta con permisos propios (solo RevOps). NO va dentro de Matters/: ahi cada carpeta de
# primer nivel es un expediente para el arbol, y sus .jsonl se leerian como documentos.
SUBIDA_CARPETA = "JSONL"
SUBIDA_CADA_MIN = 60
# Graph exige que cada fragmento de una sesion de subida sea multiplo de 320 KiB.
FRAGMENTO_SESION = 320 * 1024 * 30


def subir_a_sharepoint(g: Graph, drive: str, ruta_rel: str, crudo: bytes) -> None:
    """Sube (o reemplaza) un fichero en el drive. Lanza OSError si no puede.

    NO USA g.get NI g.post A PROPOSITO: ante un 403 o un 404 terminan el proceso con
    sys.exit, que es lo correcto en un recorrido pero no aqui -- una subida que falla no
    puede parar la lectura. Renueva el token si caduco (las corridas son de 12 horas y el
    token dura una) y espera si SharePoint frena.
    """
    import requests

    base = f"https://graph.microsoft.com/v1.0/drives/{drive}/root:/{quote(ruta_rel, safe='/')}"

    def pedir(metodo: str, url: str, **kw):
        r = None
        for intento in range(5):
            r = g._pedir(metodo, url, **kw)
            if r.status_code == 401:
                g.sesion.headers.update({"Authorization": f"Bearer {g._token()}"})
                continue
            if r.status_code in (429, 503, 504):
                time.sleep(min(120, int(r.headers.get("Retry-After", 2 ** intento))))
                continue
            return r
        return r

    if len(crudo) < TROZO:
        r = pedir("put", f"{base}:/content", data=crudo)
        if r.status_code >= 300:
            raise OSError(f"{r.status_code}: {r.text[:150]}")
        return
    r = pedir("post", f"{base}:/createUploadSession",
              json={"item": {"@microsoft.graph.conflictBehavior": "replace"}})
    if r.status_code >= 300:
        raise OSError(f"no se pudo abrir la subida: {r.status_code}: {r.text[:150]}")
    url, total = r.json()["uploadUrl"], len(crudo)
    for inicio in range(0, total, FRAGMENTO_SESION):
        trozo = crudo[inicio:inicio + FRAGMENTO_SESION]
        # La URL de la sesion ya lleva la autorizacion: no se manda la cabecera.
        r = requests.put(url, data=trozo, timeout=300, headers={
            "Content-Length": str(len(trozo)),
            "Content-Range": f"bytes {inicio}-{inicio + len(trozo) - 1}/{total}"})
        if r.status_code >= 300:
            raise OSError(f"fragmento {inicio}: {r.status_code}: {r.text[:120]}")


def subir_por_trozos(g: Graph, drive: str, ruta: str, crudo: bytes) -> None:
    """Graph no admite PUT directo por encima de 4 MB: hay que abrir una sesion."""
    sesion = g.post(f"/drives/{drive}/root:/{ruta}:/createUploadSession",
                    {"item": {"@microsoft.graph.conflictBehavior": "replace"}})
    url, total = sesion["uploadUrl"], len(crudo)
    for inicio in range(0, total, TROZO):
        trozo = crudo[inicio:inicio + TROZO]
        fin = inicio + len(trozo) - 1
        r = g.sesion.put(url, data=trozo, timeout=300, headers={
            "Content-Length": str(len(trozo)),
            "Content-Range": f"bytes {inicio}-{fin}/{total}"})
        if r.status_code >= 300:
            raise OSError(f"trozo {inicio}: {r.status_code} {r.text[:80]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--extraer", action="store_true", help="Lee y llena el diario")
    p.add_argument("--subir", action="store_true", help="Arma el JSONL y lo sube")
    p.add_argument("--por-caso", type=int, default=3,
                   help="Cuantos archivos se leen por caso (0 = todos)")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--hilos", type=int, default=8)
    p.add_argument("--simular", action="store_true")
    p.add_argument("--paginas", type=int, default=PAGINAS,
                   help=f"Paginas por archivo; 0 = TODAS (por defecto {PAGINAS})")
    p.add_argument("--incluir-parciales", action="store_true",
                   help="Al subir desde Matters, subir tambien los expedientes que "
                        "aun no estan leidos del todo")
    # MATTERS POR DEFECTO. Con 'origen' por defecto, bastaba con que un equipo
    # prestado se dejara el --origen matters para que todo diera 403 (28-sep, trozo
    # 2: 174 de 174). El sitio de origen ahora solo lo lee Rafa, y lo pide a mano.
    p.add_argument("--origen", choices=["origen", "matters"], default="matters",
                   help="De donde se leen los documentos. 'matters' (defecto) = el "
                        "sitio de produccion, el que pueden leer los equipos "
                        "prestados. 'origen' = teams/RevOps-2.Projects")
    p.add_argument("--parte", type=int, default=None,
                   help="El trozo que le toca a este proceso (0..de-1). Cada uno "
                        "escribe su propio diario y al subir se juntan solos")
    p.add_argument("--partes", default=None,
                   help="Varios trozos para el mismo proceso: '0,3,6'. Sustituye a "
                        "--parte; no hacen falta los dos")
    p.add_argument("--de", type=int, default=4, help="En cuantos trozos se parte")
    p.add_argument("--paginas-ocr", type=int, default=None,
                   help="Tope de paginas SOLO para el OCR. Con --paginas 0 deja el "
                        "78%% de los archivos completos y acota el 22%% que hay que "
                        "rasterizar, que es lo unico caro")
    p.add_argument("--max-mb", type=int, default=MAX_MB,
                   help=f"Tamano maximo en MB; 0 = sin tope (por defecto {MAX_MB})")
    p.add_argument("--minutos", type=int, default=0,
                   help="Parar limpiamente pasados N minutos y seguir en la "
                        "siguiente corrida (0 = sin limite)")
    p.add_argument("--sin-ocr", action="store_true",
                   help="Pasada de los equipos SIN GPU: todo menos imagenes, sin OCR; "
                        "lo que lo necesite queda para la GPU")
    p.add_argument("--solo-ocr", action="store_true",
                   help="Pasada de los equipos CON GPU: imagenes y la cola "
                        "(salida/cola_ocr.txt)")
    p.add_argument("--sin-subir", action="store_true",
                   help="No subir el diario ni la bitacora a Documentos/JSONL de SharePoint "
                        f"(por defecto se suben cada {SUBIDA_CADA_MIN} minutos)")
    p.add_argument("--lista", default=None,
                   help="Fichero con ids de archivo (uno por linea): solo se leen esos. "
                        "Ej.: codigo\\salida\\ids_2022_en_adelante.txt")
    p.add_argument("--pesados-al-final", type=float, default=PESADOS_AL_FINAL,
                   help=f"En --solo-ocr, los escaneos de mas de N MB se leen al final "
                        f"(por defecto {PESADOS_AL_FINAL:g}; 0 = sin separar)")
    p.add_argument("--enmascarar-ssn", action="store_true",
                   help="Sustituye los SSN por XXX-XX-nnnn (por defecto van en claro)")
    args = p.parse_args()

    if args.extraer:
        extraer(args)
    elif args.subir:
        subir(args)
    else:
        p.error("elige --extraer o --subir")


if __name__ == "__main__":
    main()
