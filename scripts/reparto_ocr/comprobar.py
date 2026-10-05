r"""Dice si este equipo esta listo para leer su trozo. No lee ningun documento.

PARA QUE. La tanda dura dias. Descubrir a las tres horas que faltaba un fichero de
134 MB, o que Paddle esta instalado pero en la rueda equivocada, cuesta mucho mas
que los veinte segundos que tarda esto.

Comprueba lo que de verdad falla en un equipo nuevo, en el orden en que falla:

    el entorno            que las librerias esten y se puedan importar
    el motor de OCR       que Paddle cargue, que diga por donde va a correr
                          y que LEA una imagen de prueba (--sin-ocr lo salta)
    los ficheros de datos los que no se pueden generar aqui (vienen en el paquete)
    el acceso a SharePoint que el .env sirva de verdad, no que exista

NO LEE DOCUMENTOS. La unica lectura es una imagen inventada; no baja archivos: eso es la tanda, y
aqui solo se comprueba que podria hacerse.

Uso:
    .venv\Scripts\python.exe reparto_ocr\comprobar.py
    .venv\Scripts\python.exe comprobar.py --parte 0 --de 4              (con NVIDIA)
    .venv\Scripts\python.exe comprobar.py --parte 4 --de 20 --sin-ocr   (sin NVIDIA)
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

# LAS DOS DISPOSICIONES. En el equipo de Rafa esto vive dentro del repositorio y el
# codigo esta un nivel mas arriba; en los equipos que reciben la carpeta comprimida,
# el codigo esta en reparto_ocr/codigo/. Mirar solo una hacia que el comprobador
# fallara justo donde mas falta hace: en la maquina del otro.
AQUI = Path(__file__).resolve().parent
RAIZ = AQUI / "codigo" if (AQUI / "codigo" / "describir_casos.py").exists() else AQUI.parent
sys.path.insert(0, str(RAIZ))

BIEN, MAL, OJO = "  [ok]   ", "  [FALTA]", "  [ojo]  "
fallos: list[str] = []


def decir(bien: bool, texto: str, arreglo: str = "") -> bool:
    print(f"{BIEN if bien else MAL} {texto}")
    if not bien:
        fallos.append(arreglo or texto)
    return bien


def entorno() -> None:
    print("\nENTORNO")
    decir(sys.version_info >= (3, 10),
          f"Python {sys.version_info.major}.{sys.version_info.minor}",
          "hace falta Python 3.10 o mas")
    for modulo, arreglo in (("pypdf", "pip install pypdf"),
                            ("fitz", "pip install pymupdf"),
                            ("pikepdf", "pip install pikepdf"),
                            ("docx", "pip install python-docx"),
                            ("pandas", "pip install pandas"),
                            ("requests", "pip install requests"),
                            # Los lectores del 29-sep. Sin ellos no se rompe nada, pero
                            # esos formatos saldrian 'is missing' y se quedarian sin leer.
                            ("python_calamine", "pip install -r requirements-comun.txt"),
                            ("olefile", "pip install -r requirements-comun.txt"),
                            ("pillow_heif", "pip install -r requirements-comun.txt"),
                            ("py7zr", "pip install -r requirements-comun.txt"),
                            ("tnefparse", "pip install -r requirements-comun.txt")):
        try:
            importlib.import_module(modulo)
            decir(True, modulo)
        except ImportError:
            decir(False, modulo, arreglo)


def motor() -> None:
    """Si el OCR va a correr, y por donde. Sin cargar los modelos."""
    print("\nMOTOR DE OCR")
    try:
        from extractor_completo import ocr
    except ImportError as error:
        decir(False, f"no se pudo importar el OCR ({error})",
              "revisa que estas en la carpeta del repositorio")
        return

    try:
        from extractor_completo import paddle_ocr
    except ImportError:
        decir(False, "paddle_ocr no esta", "ver INSTALAR.md")
        return

    puede, por_que = paddle_ocr.disponible()
    if not decir(puede, f"PaddleOCR {'listo' if puede else por_que}", "ver INSTALAR.md"):
        return

    donde, razon = paddle_ocr.dispositivo()
    if donde == "gpu":
        print(f"{BIEN} va por GPU: {razon}")
    else:
        # No es un fallo: la mitad de los equipos van por CPU a proposito. Pero hay
        # que decirlo, porque cambia el ritmo por diez y quien lo lance tiene que
        # saber por que su equipo va lento.
        print(f"{OJO} va por CPU: {razon}")
        print("         es varias veces mas lento. Si ESPERABAS GPU:")
        print("         - solo valen las NVIDIA (una Radeon o una Intel no)")
        print("         - comprueba que instalaste paddlepaddle-gpu, no paddlepaddle")
        import os
        hilos = os.environ.get("OMP_NUM_THREADS")
        if not hilos:
            print(f"{OJO} OMP_NUM_THREADS sin fijar: con varios procesos se estorban")
            print("         set OMP_NUM_THREADS=4")


def lectura_de_prueba() -> None:
    """Que Paddle LEA de verdad, no solo que este instalado.

    POR QUE. El 28-sep este comprobador decia 'PaddleOCR listo' en los equipos sin
    NVIDIA y la tanda luego no leia ni una pagina escaneada: oneDNN reventaba en
    cada prediccion (ver paddle_ocr._construir). Cargar el motor no probaba nada;
    hacerle leer algo si. Se lee una imagen inventada: ningun documento.
    """
    print("\nLECTURA DE PRUEBA (Paddle lee una imagen inventada)")
    try:
        from PIL import Image, ImageDraw, ImageFont

        from extractor_completo import paddle_ocr
    except ImportError as error:
        decir(False, f"no se pudo preparar la prueba ({error})", "ver INSTALAR.md")
        return
    imagen = Image.new("RGB", (1600, 300), "white")
    ImageDraw.Draw(imagen).text((40, 100), "PRUEBA DE LECTURA 12345", fill="black",
                                font=ImageFont.load_default(size=80))
    print("         (la primera vez tarda: carga los modelos)")
    texto, detalle = paddle_ocr.leer(imagen)
    if detalle.get("error"):
        decir(False, f"Paddle fallo: {detalle['error'][:110]}",
              "Paddle esta instalado pero no lee: pasale este error a Rafa")
    elif "12345" in texto:
        decir(True, f"Paddle leyo '{texto.strip()[:40]}' "
                    f"(confianza {detalle.get('confianza_media', 0):.2f})")
    else:
        decir(False, f"Paddle no leyo el texto de prueba (salio: '{texto.strip()[:40]}')",
              "Paddle no lee bien: pasale esta salida a Rafa")


def datos() -> None:
    print("\nFICHEROS DE DATOS")
    # Leyendo desde Matters basta el arbol de produccion: el expediente de cada
    # archivo es la carpeta en la que esta, asi que ya no hacen falta el plan de
    # fase 2, el mapa de nombres ni el reparto.
    from sp_conexion import ARCHIVO_ENV
    for nombre, mb in (("salida/arbol_matters.jsonl", 20),
                       ("salida/ya_leidos_matters.txt", 0)):
        ruta = RAIZ / nombre
        if not ruta.exists():
            decir(False, f"{nombre} no esta", f"copia {nombre} del equipo de Rafa")
            continue
        tam = ruta.stat().st_size / 2 ** 20
        if tam < mb:
            decir(False, f"{nombre} solo pesa {tam:.0f} MB (esperaba >{mb})",
                  f"vuelve a copiar {nombre}: parece incompleto")
        else:
            decir(True, f"{nombre}  {tam:.0f} MB")
    # El .env se busca en varios sitios (ver sp_conexion._buscar_env); se dice
    # cual se va a usar, que es la duda que siempre sale.
    decir(ARCHIVO_ENV.exists(), f".env  ({ARCHIVO_ENV})",
          "falta el .env: dejalo en reparto_ocr o en reparto_ocr/codigo")


def sharepoint() -> None:
    """Que la cuenta pueda leer MATTERS. Existir el .env no basta.

    Es el sitio de produccion, y no el de origen, porque ahi es donde son miembros
    los equipos prestados: contra el de origen recibian 403. Se prueba bajando la
    lista de expedientes, que es lo mismo que va a hacer la tanda; un simple 'la
    raiz responde' pasaria aunque no se pudiera leer ningun documento.
    """
    print("\nACCESO A SHAREPOINT (Matters)")
    try:
        from copiar_a_matters import CARPETA_DESTINO, destino
        from sp_conexion import Graph
        g = Graph()
        drive, _ = destino(g)
        r = g.get(f"/drives/{drive}/root:/{CARPETA_DESTINO}:/children",
                  **{"$top": "5", "$select": "name"})
        vistos = len(r.get("value", []))
        decir(vistos > 0, f"la cuenta ve los expedientes de Matters ({vistos} de muestra)",
              "la cuenta no ve Matters: pide que la anadan como miembro del sitio")
    except SystemExit as error:
        decir(False, f"{str(error)[:110]}", "revisa el .env (ver INSTALAR.md)")
    except Exception as error:  # noqa: BLE001
        decir(False, f"{type(error).__name__}: {str(error)[:90]}",
              "revisa el .env y la conexion")


def mi_trozo(parte: int | None, de: int, pasada: str = "") -> None:
    if parte is None:
        return
    print(f"\nMI TROZO ({parte} de {de})")
    try:
        # LA MISMA CUENTA QUE LA TANDA, sacada del mismo sitio. Antes esto tenia su
        # propia copia y divergio: no leia ya_leidos.txt y prometia 270.710
        # pendientes donde habia 255.981.
        from describir_casos import clave_de_reparto, estado_de_lectura, me_toca

        pend = estado_de_lectura("matters", pasada)["pendientes"]
        mios_filas = [r for r in pend if me_toca(clave_de_reparto(r), {parte}, de)]
        mios = len(mios_filas)
        unicos = len({clave_de_reparto(r) for r in mios_filas})
        print(f"{BIEN} me tocan {mios:,} archivos ({unicos:,} distintos: el resto "
              "son copias y no se releen)")
        print(f"         (de {len(pend):,} que quedan por leer entre todos)")
        print(f"         a 4/min serian {unicos/4/60:.0f} h;  a 1/min, {unicos/60:.0f} h")
    except Exception as error:  # noqa: BLE001
        decir(False, f"no se pudo calcular: {type(error).__name__}: {error}"[:90], "")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--parte", type=int, default=None, help="Tu trozo, para contarlo")
    p.add_argument("--de", type=int, default=8)
    p.add_argument("--sin-ocr", action="store_true",
                   help="No hacer la lectura de prueba (si el equipo ya esta leyendo)")
    args = p.parse_args()

    print(f"\n  COMPROBACION PREVIA   ({RAIZ})")
    entorno()
    motor()
    if not args.sin_ocr:
        lectura_de_prueba()
    datos()
    sharepoint()
    mi_trozo(args.parte, args.de, "sin-ocr" if args.sin_ocr else "")

    print()
    if fallos:
        print(f"  NO ESTA LISTO: {len(fallos)} cosa(s) por resolver")
        for f in fallos:
            print(f"     - {f}")
        print("\n  No lances la tanda hasta que esto salga limpio.")
        sys.exit(1)
    print("  LISTO. Ya puedes lanzar tu parte (ver EJECUTAR.md).")


if __name__ == "__main__":
    main()
