r"""PaddleOCR: el motor que lee lo que Tesseract no.

POR QUE ESTA AQUI. Sobre la foto de una etiqueta de envio que nos mandaron como caso
dificil, los dos motores dieron esto:

    Tesseract psm 6      20 caracteres    '= VU  > ep tileeg,.'
    Tesseract psm 11     95 caracteres    'wet cas RIOR Y MAIL EXPRESS 1A...'
    PaddleOCR            28 fragmentos, con el numero de guia entero:
                         'PRIORITY MAIL EXPRESS 1-DAY'      0.99
                         '4092 7204 1036 9827 8273 92'      0.89
                         'POSTAL USE ONLY'                  1.00

No sustituye a Tesseract: sobre los seis documentos del lote de control donde conocemos la
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
    python -m lector_ocr.paddle_ocr
"""

from __future__ import annotations

import functools
import os

# CUANTOS NUCLEOS PUEDE USAR. Esto hay que fijarlo ANTES de que se importe paddle:
# despues ya no se lo lee. En la maquina de RevOps venia en 1 sobre un procesador de
# 24 nucleos, o sea que el motor corria en un vigesimocuarto de la maquina, y esa era
# la razon de que la carpeta entera tardara tanto -- no la falta de GPU.
#
# No se cogen los 24: el proceso tambien rasteriza PDFs y descarga archivos, y
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


def disponible() -> tuple[bool, str]:
    """(se puede usar, por que no). Nunca lanza.

    SE COMPRUEBAN LOS DOS PAQUETES. 'paddleocr' es solo la envoltura; el motor es
    'paddlepaddle', y son instalaciones separadas -- ademas la de CPU y la de GPU se
    pisan, asi que cambiar de una a otra deja un rato sin ninguna. Mirando solo
    paddleocr, esto contestaba que si con el motor desinstalado, y una corrida de
    349 archivos habria arrancado para caerse en el primero.
    """
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        return False, "paddleocr is not installed (pip install paddleocr)"
    except Exception as error:  # noqa: BLE001
        return False, f"paddleocr could not be loaded ({type(error).__name__})"

    try:
        import paddle  # noqa: F401
    except ImportError:
        return False, ("paddleocr is installed but paddlepaddle is not: "
                       "install paddlepaddle (CPU) or paddlepaddle-gpu")
    except Exception as error:  # noqa: BLE001
        return False, f"paddlepaddle could not be loaded ({type(error).__name__})"
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


@functools.lru_cache(maxsize=1)
def _motor():
    """La instancia, una sola vez.

    Cargar los modelos tarda entre cinco y quince segundos. Crear un PaddleOCR por
    archivo convertiria una carpeta de 349 documentos en una hora de arranques.
    """
    from paddleocr import PaddleOCR

    # Los tres clasificadores auxiliares se apagan a proposito: enderezar la pagina
    # y corregir el alabeo ya los hace nuestro pipeline antes de llegar aqui, y cada
    # uno es otro modelo que cargar.
    donde, _ = dispositivo()
    try:
        return PaddleOCR(lang=IDIOMA,
                         device=donde,
                         use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_textline_orientation=False)
    except TypeError:
        # PaddleOCR 2.x no conoce esos parametros.
        return PaddleOCR(lang=IDIOMA, use_angle_cls=False, show_log=False)


def _como_arreglo(imagen):
    """La imagen PIL como arreglo BGR, que es lo que espera Paddle (usa OpenCV)."""
    import numpy as np

    arreglo = np.array(imagen.convert("RGB"))
    return arreglo[:, :, ::-1]


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
    puede, motivo = disponible()
    if not puede:
        return "", {"error": motivo}

    try:
        motor = _motor()
        arreglo = _como_arreglo(imagen)
        try:
            salida = motor.predict(arreglo)          # 3.x
        except AttributeError:
            salida = motor.ocr(arreglo)              # 2.x
        trozos = _fragmentos(salida)
    except Exception as error:  # noqa: BLE001
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

    if len(sys.argv) < 2:
        print("Uso: python -m lector_ocr.paddle_ocr <imagen>")
        return
    ruta = Path(sys.argv[1])
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
