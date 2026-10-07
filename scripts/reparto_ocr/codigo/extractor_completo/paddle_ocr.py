r"""PaddleOCR: el motor que lee lo que Tesseract no.

POR QUE ESTA AQUI. Sobre la foto de una etiqueta de envio que nos mandaron como caso
dificil, los dos motores dieron esto:

    Tesseract psm 6      20 caracteres    '= VU  > ep tileeg,.'
    Tesseract psm 11     95 caracteres    'wet cas RIOR Y MAIL EXPRESS 1A...'
    PaddleOCR            28 fragmentos, con el numero de guia entero:
                         'PRIORITY MAIL EXPRESS 1-DAY'      0.99
                         '4092 7204 1036 9827 8273 92'      0.89
                         'POSTAL USE ONLY'                  1.00

No sustituye a Tesseract: sobre los seis documentos de Jimenez donde conocemos la
respuesta a ojo, Tesseract a 300 DPI y psm 6 lee 11 de 11 campos. Son dos problemas
distintos -- el escaneo de un reporte de credito y la foto de una caja -- y cada
motor gana en uno.

LO QUE APORTA ADEMAS DEL TEXTO: una CONFIANZA POR FRAGMENTO. Tesseract, llamado como
lo llamamos, devuelve una cadena y nada mas; todo este proyecto ha peleado con
errores que no se ven precisamente por eso. Aqui un '0.27 t' se descarta y un '0.89'
sobre un numero de guia se puede marcar para revisar.

Y SE ORDENAN LOS FRAGMENTOS DE ARRIBA ABAJO. Paddle los devuelve en el orden en que
los detecto, que no es el de lectura. Importa mas de lo que parece: el clasificador
decide por los primeros 300 caracteres, asi que un titulo que llegue al final del
texto no lo ve nadie. Es el mismo fallo que tuvimos con las tablas de Word.

Instalacion (en el .venv de este proyecto, no en otro):
    .venv\Scripts\python.exe -m pip install paddlepaddle paddleocr

Uso:
    .venv\Scripts\python.exe -m extractor_completo.paddle_ocr
"""

from __future__ import annotations

import functools
import os
import threading

# CUANTOS NUCLEOS PUEDE USAR. Esto hay que fijarlo ANTES de que se importe paddle:
# despues ya no se lo lee. En la maquina de RevOps venia en 1 sobre un procesador de
# 24 nucleos, o sea que el motor corria en un vigesimocuarto de la maquina, y esa era
# la razon de que la carpeta entera tardara tanto -- no la falta de GPU.
#
# No se cogen los 24: el proceso tambien rasteriza PDFs y habla con SharePoint, y
# dejar la maquina sin aire no acelera nada. Se puede fijar por fuera si hace falta.
NUCLEOS = os.environ.get("OCR_NUCLEOS") or str(max(1, (os.cpu_count() or 4) * 2 // 3))
for _variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[_variable] = NUCLEOS

# EL RUIDO DE PADDLE, CALLADO. Sus capas en C++ escriben a stderr por su cuenta y se
# comen la linea de progreso: llegamos a ver
# 'ReduceMeanCheckIfOneDNNSupportfcking Results TU.pdfdf Compel D'. Eso no es
# cosmetico nada mas -- un mensaje de verdad se pierde ahi dentro.
#
# Tambien hay que fijarlo antes del import, y solo si nadie lo puso ya: quien este
# depurando Paddle tiene que poder subir el nivel sin editar esto.
os.environ.setdefault("GLOG_minloglevel", "2")       # solo errores
os.environ.setdefault("GLOG_logtostderr", "0")
os.environ.setdefault("FLAGS_call_stack_level", "0")

# Por debajo de esto el fragmento es ruido de deteccion, no texto. Medido sobre la
# etiqueta de envio: lo legible venia entre 0,63 y 1,00, y la basura ('t', 'Taes ew')
# entre 0,27 y 0,48. El umbral se pone en medio y lejos de los dos.
CONFIANZA_MINIMA = 0.55

# Idiomas. El modelo 'en' tambien lee cifras y signos, que es lo que mas nos importa.
IDIOMA = "en"


# EL IMPORT, DE UNO EN UNO. Paddle es un paquete enorme, y si varios hilos lo
# importan a la vez uno lo coge a medio cargar ('partially initialized module':
# AttributeError). Peor: un import que falla a la mitad deja modulos rotos en
# sys.modules, y a partir de ahi CADA import del proceso falla (RuntimeError). La
# noche del 29-sep, en --solo-ocr los 8 hilos pidieron Paddle en el mismo instante
# y un equipo con GPU sano devolvio 'could not be loaded' en 3.034 de 3.034
# imagenes. Por eso: candado, y el resultado bueno se recuerda.
_candado_import = threading.Lock()
_ya_disponible = False
# EL FALLO TAMBIEN SE RECUERDA (6-oct). Cada intento de importar Paddle anade sus carpetas
# de CUDA al PATH del proceso. Sin recordar el fallo, se reintentaba en CADA archivo: en la
# GPU 2, tras ~450 intentos el PATH paso el limite de Windows y desde ahi todo PDF fallaba
# con 'WinError 206: The filename or extension is too long'. Un import que falla no se
# arregla solo a mitad de corrida: se dice una vez y se sigue sin Paddle.
_fallo_import: str | None = None
# LA GPU ROTA NO SE ARREGLA SOLA (7-oct). En el servidor de Azure (A100), con varios
# procesos de Paddle en la misma GPU, de vez en cuando uno da 'CUDA error(700), an
# illegal memory access' o 'CUDA error(716), misaligned address'. Desde ese momento
# ESE proceso ya no puede usar la GPU: cada archivo siguiente fallaba en un segundo y
# quedaba como no leido. En una noche fueron 25.000 fallos y 827 lecturas. Se recuerda
# el primer error; describir_casos lo ve, termina lo que tiene y sale, y el bucle que
# lo lanza lo vuelve a arrancar con la GPU limpia (Paddle carga en ~5 s).
_gpu_rota: str | None = None


def gpu_rota() -> str | None:
    """El primer error de CUDA de este proceso, o None si la GPU sigue bien."""
    return _gpu_rota


def disponible() -> tuple[bool, str]:
    """(se puede usar, por que no). Nunca lanza.

    SE COMPRUEBAN LOS DOS PAQUETES. 'paddleocr' es solo la envoltura; el motor es
    'paddlepaddle', y son instalaciones separadas -- ademas la de CPU y la de GPU se
    pisan, asi que cambiar de una a otra deja un rato sin ninguna. Mirando solo
    paddleocr, esto contestaba que si con el motor desinstalado, y una corrida de
    349 archivos habria arrancado para caerse en el primero.
    """
    global _ya_disponible, _fallo_import
    if _ya_disponible:
        return True, ""
    if _fallo_import:
        return False, _fallo_import
    with _candado_import:
        if _ya_disponible:
            return True, ""
        if _fallo_import:
            return False, _fallo_import
        puede, motivo = _importar()
        _ya_disponible = puede
        if not puede:
            _fallo_import = motivo
        return puede, motivo


def calentar() -> tuple[bool, str]:
    """Importa Paddle y construye el modelo UNA vez, antes de lanzar los hilos.

    Lo llama la tanda desde el hilo principal. Devuelve (listo, por que no): si no
    esta listo, la tanda no debe arrancar -- mejor pararse al empezar que pasar la
    noche anotando el mismo error en cada archivo.
    """
    puede, motivo = disponible()
    if not puede:
        return False, motivo
    try:
        _motor()
    except Exception as error:  # noqa: BLE001
        return False, f"PaddleOCR could not start ({type(error).__name__}: {error})"[:200]
    return True, ""


def _importar() -> tuple[bool, str]:
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        return False, "paddleocr is not installed (pip install paddleocr)"
    except Exception as error:  # noqa: BLE001
        return False, f"paddleocr could not be loaded ({type(error).__name__}: {error})"[:240]

    try:
        import paddle  # noqa: F401
    except ImportError:
        return False, ("paddleocr is installed but paddlepaddle is not: "
                       "install paddlepaddle (CPU) or paddlepaddle-gpu")
    except Exception as error:  # noqa: BLE001
        return False, f"paddlepaddle could not be loaded ({type(error).__name__}: {error})"[:240]
    return True, ""


def dispositivo() -> tuple[str, str]:
    """('cpu' o 'gpu', por que). Se detecta, no se pide.

    Un '--device gpu' que acepta la orden y luego corre en CPU en silencio seria
    peor que no tenerlo: pediriamos GPU, veriamos que sigue lento y buscariamos el
    problema donde no esta. Asi que se mira si paddle puede de verdad.

    Para que pueda hay que cambiar de paquete, no de parametro: 'paddlepaddle' y
    'paddlepaddle-gpu' son ruedas distintas y la de CPU no lleva CUDA dentro.
    """
    forzado = os.environ.get("OCR_DISPOSITIVO", "").strip().lower()
    try:
        import paddle
    except ImportError:
        return "cpu", "paddle no esta instalado"

    if not paddle.is_compiled_with_cuda():
        if forzado == "gpu":
            return "cpu", ("se pidio gpu pero este paddle es la rueda de CPU: "
                           "hay que instalar paddlepaddle-gpu")
        return "cpu", "esta instalada la rueda de CPU (paddlepaddle, sin CUDA)"
    try:
        cuantas = paddle.device.cuda.device_count()
    except Exception:  # noqa: BLE001
        cuantas = 0
    if not cuantas:
        return "cpu", "paddle lleva CUDA pero no ve ninguna GPU"
    if forzado == "cpu":
        return "cpu", "forzado por OCR_DISPOSITIVO=cpu"
    return "gpu", f"{cuantas} GPU(s) visibles"


# UNA SOLA INSTANCIA, Y DE VERDAD. Aqui habia un @functools.lru_cache, que protege
# el diccionario de la cache pero NO impide que varios hilos ejecuten la funcion a la
# vez cuando todos fallan la cache al arrancar: los ocho entraban, los ocho
# construian un PaddleOCR, uno ganaba la cache y los otros siete quedaban tirados
# habiendo reservado ya su memoria en la GPU. En la corrida del 24-sep-2026 se veian
# seis 'Creating model' por cada tipo de modelo.
#
# En un servidor esto es peor que lento: un modelo por peticion concurrente agota la
# VRAM y el proceso muere sin decir por que.
_instancia = None
_candado_motor = threading.Lock()

# La inferencia tambien se serializa. Paddle no garantiza que un predictor se pueda
# usar desde varios hilos, y aqui no cuesta nada: reconocer una imagen son 0,22 s en
# GPU y bajarse el archivo segundo y medio. El paralelismo que importa es el de la
# descarga, no el del OCR.
_candado_inferencia = threading.Lock()


def _motor():
    """La instancia compartida. Se construye una vez, pase lo que pase.

    Doble comprobacion: la primera sin candado para que el caso normal -- ya
    construida -- no pague sincronizacion, y la segunda dentro para que dos hilos
    que lleguen juntos no la construyan dos veces.

    Cargar los modelos tarda entre cinco y quince segundos. Crear un PaddleOCR por
    archivo convertiria una carpeta de 349 documentos en una hora de arranques.
    """
    global _instancia
    if _instancia is not None:
        return _instancia
    with _candado_motor:
        if _instancia is None:
            _instancia = _construir()
        return _instancia


def _construir():
    from paddleocr import PaddleOCR

    # ENDEREZAR LA PAGINA LO HACE PADDLE. Antes lo hacia Tesseract (OSD) en
    # ocr._enderezar, pero con Paddle como motor unico Tesseract ya no se llama, y
    # sin esto una pagina escaneada de lado se leeria letra por letra al reves.
    # El alabeo y la orientacion por linea siguen apagados: otro modelo cada uno.
    donde, _ = dispositivo()
    # EN CPU, SIN oneDNN (MKLDNN). Con paddlepaddle 3.3.1 la aceleracion oneDNN de
    # CPU revienta en CADA prediccion: 'NotImplementedError: ConvertPirAttribute2
    # RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]'.
    # Confirmado el 29-sep en un equipo prestado (sin NVIDIA): con oneDNN, error;
    # sin el, 'PRUEBA DE LECTURA 12345' exacto. En el trozo 1 eso dejo sin leer las
    # 5.202 paginas escaneadas. En GPU no aplica: ahi no se usa oneDNN.
    extra = {"enable_mkldnn": False} if donde == "cpu" else {}
    # AJUSTES PARA EL SERVIDOR (7-oct), por variable de entorno; sin ellas, lo de siempre.
    #   OCR_GIRO=0         no endereza la pagina antes de leerla (PP-LCNet_x1_0_doc_ori)
    #   OCR_LADO_MAX=2048  el lado mayor de la pagina que entra al detector de texto
    #                      (por defecto Paddle la deja hasta 4.000 px)
    # Con los dos, un solo proceso en la A100 dejo de dar 'CUDA error'. Una pagina
    # escaneada de lado se lee peor sin el giro; 2.048 px son ~190 dpi en carta.
    giro = os.environ.get("OCR_GIRO", "1").strip() != "0"
    lado = int(os.environ.get("OCR_LADO_MAX", "0").strip() or 0)
    if lado:
        extra.update(text_det_limit_type="max", text_det_limit_side_len=lado)
    try:
        return PaddleOCR(lang=IDIOMA,
                         device=donde,
                         use_doc_orientation_classify=giro,
                         use_doc_unwarping=False,
                         use_textline_orientation=False,
                         **extra)
    except TypeError:
        # PaddleOCR 2.x no conoce esos parametros.
        return PaddleOCR(lang=IDIOMA, use_angle_cls=False, show_log=False)


def _como_arreglo(imagen):
    """La imagen PIL como arreglo BGR, que es lo que espera Paddle (usa OpenCV)."""
    import numpy as np

    arreglo = np.array(imagen.convert("RGB"))
    # CONTIGUO, NO UNA VISTA (7-oct). arreglo[:, :, ::-1] es una vista con el paso
    # negativo, y Paddle recorta cada linea de texto con cv2.warpPerspective sobre la
    # pagina entera: OpenCV no acepta esa vista y copia la pagina (24 MB a 300 DPI) EN
    # CADA LINEA. Medido en el servidor: el 90% del tiempo de OCR; 2,93 s por pagina
    # antes y 0,32 s despues, con el mismo texto.
    return np.ascontiguousarray(arreglo[:, :, ::-1])


def _fragmentos(salida) -> list[tuple[float, float, str, float]]:
    """(arriba, izquierda, texto, confianza) de cada trozo, sea cual sea la version.

    La 3.x devuelve un objeto con 'rec_texts' / 'rec_scores' / 'rec_polys'; la 2.x
    una lista de [caja, (texto, confianza)]. Se aceptan las dos porque no controlamos
    que version quede instalada, y fallar por eso seria un fallo evitable.
    """
    trozos: list[tuple[float, float, str, float]] = []
    if not salida:
        return trozos

    for pagina in salida:
        datos = getattr(pagina, "json", None) or pagina
        if isinstance(datos, dict) and "res" in datos:
            datos = datos["res"]

        if isinstance(datos, dict) and "rec_texts" in datos:      # 3.x
            textos = datos.get("rec_texts") or []
            puntajes = datos.get("rec_scores") or []
            cajas = datos.get("rec_polys") or datos.get("rec_boxes") or []
            for i, texto in enumerate(textos):
                puntaje = float(puntajes[i]) if i < len(puntajes) else 0.0
                arriba, izquierda = _esquina(cajas[i] if i < len(cajas) else None)
                trozos.append((arriba, izquierda, str(texto), puntaje))
            continue

        for linea in datos or []:                                  # 2.x
            try:
                caja, (texto, puntaje) = linea[0], linea[1]
            except (TypeError, ValueError, IndexError):
                continue
            arriba, izquierda = _esquina(caja)
            trozos.append((arriba, izquierda, str(texto), float(puntaje)))
    return trozos


def _esquina(caja) -> tuple[float, float]:
    """(y, x) de la esquina superior izquierda, para poder ordenar de arriba abajo."""
    if caja is None:
        return 0.0, 0.0
    try:
        puntos = [(float(p[1]), float(p[0])) for p in caja]
        return min(p[0] for p in puntos), min(p[1] for p in puntos)
    except (TypeError, ValueError, IndexError):
        try:  # una caja plana [x1, y1, x2, y2]
            return float(caja[1]), float(caja[0])
        except (TypeError, ValueError, IndexError):
            return 0.0, 0.0


def leer(imagen, confianza_minima: float = CONFIANZA_MINIMA) -> tuple[str, dict]:
    """(texto, detalle). El detalle lleva la confianza, que es lo que Tesseract no da.

    detalle = {fragmentos, descartados, confianza_media, confianza_minima_vista}
    """
    global _gpu_rota
    puede, motivo = disponible()
    if not puede:
        return "", {"error": motivo}
    if _gpu_rota:
        # No se toca mas la GPU: ya no responde y cada intento solo anade otro error.
        return "", {"error": f"PaddleOCR failed (GPU rota en este proceso: {_gpu_rota})"[:160]}

    try:
        motor = _motor()
        # El arreglo se prepara FUERA del candado: es trabajo de CPU puro y no toca
        # el predictor, asi que no hay razon para que un hilo espere por el.
        arreglo = _como_arreglo(imagen)
        with _candado_inferencia:
            try:
                salida = motor.predict(arreglo)      # 3.x
            except AttributeError:
                salida = motor.ocr(arreglo)          # 2.x
        trozos = _fragmentos(salida)
    except Exception as error:  # noqa: BLE001
        if "CUDA error" in str(error) and _gpu_rota is None:
            _gpu_rota = str(error).strip()[:100]
        return "", {"error": f"PaddleOCR failed ({type(error).__name__}: {error})"[:160]}

    # Se agrupan por renglon antes de ordenar: dos fragmentos de la misma linea
    # tienen 'arriba' parecido pero no identico, y ordenar solo por y los baraja.
    trozos.sort(key=lambda t: (round(t[0] / 12), t[1]))
    buenos = [t for t in trozos if t[3] >= confianza_minima]
    descartados = len(trozos) - len(buenos)

    if not buenos:
        return "", {"fragmentos": 0, "descartados": descartados,
                    "confianza_media": 0.0}

    texto = "\n".join(t[2] for t in buenos)
    puntajes = [t[3] for t in buenos]
    return texto, {
        "fragmentos": len(buenos),
        "descartados": descartados,
        "confianza_media": round(sum(puntajes) / len(puntajes), 3),
        "confianza_minima_vista": round(min(puntajes), 3),
    }


def main() -> None:
    import io
    import sys
    from pathlib import Path

    puede, motivo = disponible()
    print(f"PaddleOCR disponible: {puede}   {motivo}")
    if not puede:
        return

    ruta = Path(sys.argv[1] if len(sys.argv) > 1
                else "extractor_completo/mejoras/envios-de-caja-a-colombia-1.webp")
    if not ruta.exists():
        print(f"No existe {ruta}")
        return

    import time

    from PIL import Image

    donde, motivo = dispositivo()
    with Image.open(io.BytesIO(ruta.read_bytes())) as abierta:
        imagen = abierta.convert("RGB")
        leer(imagen)                      # calentar: la carga de modelos no cuenta
        arranque = time.time()
        for _ in range(3):
            texto, detalle = leer(imagen)
        segundos = (time.time() - arranque) / 3

    print(f"\n{ruta.name}")
    print(f"  {donde.upper()} con {NUCLEOS} hilo(s)   ({motivo})")
    print(f"  {segundos:.2f} s por imagen   (la referencia en CPU son 3,83 s)")
    print(f"  {detalle}\n")
    print(texto)


if __name__ == "__main__":
    main()
