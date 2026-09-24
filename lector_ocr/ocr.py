"""OCR: pixeles -> texto, para lo que no trae capa de texto.

El 42% de los PDF del arbol son escaneos y hasta ahora eran un agujero negro. Esto
lo cierra, con tres decisiones tomadas a proposito:

TODO PASA EN LA MAQUINA, Y EN ALGUN CASO ESO ES UNA ORDEN JUDICIAL. Tesseract corre
en local y los documentos no salen del equipo. El OCR de nube -- Azure Document
Intelligence, Google Vision, AWS Textract -- lee bastante mejor las tablas y la
letra manuscrita, y es tentador por eso. Pero hay expedientes cuyo protective order
prohibe expresamente mandar material confidencial a una herramienta de IA abierta,
a un sitio web o a un modelo de lenguaje.

Tesseract local no lo viola. Mandar esos documentos a una API de OCR si. Y no se
puede resolver con una regla general: la clausula es reciente y no todos los casos
la llevan, asi que antes de montar OCR de nube sobre un expediente hay que leer su
protective order. Por eso el valor por defecto es el que no puede equivocarse.

SE OCR-EA POCO Y TARDE. Rasterizar y reconocer cuesta entre uno y tres segundos por
pagina, mil veces mas que leer una capa de texto. Por eso el OCR es el ultimo
recurso: solo entra cuando la capa de texto no dio nada USABLE -- ver
`necesita_ocr`, que no es lo mismo que 'no dio nada'.

SI NO ESTA INSTALADO, NO SE ROMPE NADA. `disponible()` lo comprueba una vez y el
lector devuelve el motivo como cualquier otro fallo de lectura. Un despacho sin
Tesseract sigue corriendo el sistema igual que antes, sin una sola excepcion.

Para instalarlo:
    winget install UB-Mannheim.TesseractOCR
    .venv\\Scripts\\python.exe -m pip install pytesseract pillow pymupdf
"""

from __future__ import annotations

import functools
import io
import os
import re
import shutil

# 300 puntos por pulgada. Estaba en 200 y subirlo es el cambio mas rentable que se
# ha medido en todo el OCR: sobre los seis documentos del lote de control donde conocemos la
# respuesta a ojo (probar_ocr.py), los campos leidos bien pasaron de 3 de 5 a 5 de 5.
# Lo que recupera son justo los fallos que no se ven -- la fecha '1/11/2025' que era
# '11/11/2025' y un 'Total Assets: $3,000' que a 200 no aparecia -- y cuesta un 30%
# mas de tiempo, no el doble: 9 segundos contra 12 en la prueba.
#
# Por debajo de 150 Tesseract empieza a confundir letras en los escaneos de fax del
# despacho; por encima de 300 si tarda el doble y no acierta mas.
DPI = 300

# Idiomas. El 'rus' esta porque una parte de la clientela del despacho es
# rusoparlante y sus documentos vienen en cirilico; si el paquete no esta instalado
# se cae a 'eng' solo, sin fallar.
IDIOMAS = "eng"
IDIOMAS_EXTRA = "eng+rus"

RUTAS_HABITUALES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@functools.cache
def _pymupdf():
    """El modulo de PyMuPDF, o None si no esta instalado.

    Se importa como `pymupdf` y no como `fitz`: `fitz` es el nombre historico, sigue
    funcionando y avisa por consola de que va a desaparecer. Ese aviso sale una vez
    por corrida en medio del informe y no aporta nada.
    """
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz

        return fitz
    except ImportError:
        return None


@functools.cache
def _binario() -> str:
    """Donde esta tesseract.exe, o cadena vacia si no esta.

    Se busca en el PATH y en los dos sitios donde lo deja el instalador de Windows:
    el instalador no toca el PATH si no se marca la casilla, y ese descuido dejaria
    el OCR apagado sin que nadie entienda por que.
    """
    encontrado = shutil.which("tesseract")
    if encontrado:
        return encontrado
    return next((r for r in RUTAS_HABITUALES if os.path.exists(r)), "")


@functools.cache
def disponible() -> tuple[bool, str]:
    """(se puede usar, motivo si no). Se comprueba una vez por proceso."""
    if not _binario():
        return False, "Tesseract is missing (winget install UB-Mannheim.TesseractOCR)"
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "pytesseract is missing (pip install pytesseract pillow)"
    if _pymupdf() is None:
        return False, "pymupdf is missing (pip install pymupdf)"
    return True, ""


@functools.cache
def _idiomas() -> str:
    """Usa el ruso si el paquete esta; si no, ingles a secas."""
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = _binario()
        instalados = set(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - si no se puede preguntar, ingles y listo
        return IDIOMAS
    return IDIOMAS_EXTRA if "rus" in instalados else IDIOMAS


# --------------------------------------------------------------------------- #
# Cuando hace falta el OCR
#
# El criterio de antes era 'la capa de texto vino vacia'. Es el criterio
# equivocado y el analisis del matter de control lo midio: 45 de 51 archivos
# ilegibles NO venian vacios, venian con basura, asi que el OCR no llegaba a
# dispararse y el archivo quedaba marcado como leido. 276 MB de 885 se dieron por
# procesados sin haber extraido nada.
#
# Los tres casos que producen esa basura, y como se reconocen:

# El PDF es un contenedor de Acrobat y el contenido real va en los adjuntos: la
# unica pagina que tiene es la de cortesia.
PORTFOLIO = re.compile(r"open this PDF portfolio", re.I)

# Un escaneo cuyo unico texto real es el sello Bates: 'PLAINTIFF001 PLAINTIFF002'
# en 23 MB de imagenes. Por debajo de esto no hay documento, hay sellos.
CARACTERES_POR_PAGINA = 100

# Fuentes embebidas sin tabla ToUnicode: pypdf saca el indice del glifo en vez de
# la letra, y sale '/0/1/2/3/1/4/3/2/5/6/i255'.
#
# LA PROPORCION SE MIDE SOBRE LO QUE NO ES ESPACIO, y esa precision importa. Un
# reporte de credito de las tres agencias es una tabla: cifras, fechas, importes y
# un 39% de saltos de linea. Contando los espacios daba 0,43 y caia por debajo del
# umbral, asi que el sistema mandaba a OCR un documento cuya capa de texto estaba
# perfecta -- mas lento y encima peor, porque el OCR de una tabla es mucho peor que
# su texto nativo. Sin contar espacios da 0,71 y pasa.
#
# Medido sobre los 287 archivos del matter: la basura de glifos queda en 0,32 y el
# minimo de todo lo que sirve es 0,71. El umbral va en medio y lejos de los dos.
PROPORCION_DE_LETRAS = 0.45

PALABRA = re.compile(r"[A-Za-z]{2,}")


def _proporcion_de_letras(texto: str) -> float:
    sin_espacios = sum(1 for c in texto if not c.isspace())
    return sum(c.isalpha() for c in texto) / max(sin_espacios, 1)


def proporcion_de_palabras(texto: str) -> float:
    """Que parte de los tokens alfabeticos son palabras de cuatro letras o mas.

    Es la senal que de verdad separa un documento de la basura, y hace falta porque
    la proporcion de letras sola no sirve para los documentos de CIFRAS. Medido:

        indices de glifo      letras 0,03    palabras largas 0,00
        ruido de una foto     letras 0,38    palabras largas 0,00
        estado de P&L         letras 0,40    palabras largas 0,88
        reporte de credito    letras 0,43    palabras largas 1,00
        escrito judicial      letras 1,00    palabras largas 0,82

    Por letras, el P&L y el reporte de credito caian del lado de la basura. Por
    palabras, los cinco quedan donde tienen que quedar.
    """
    tokens = PALABRA.findall(texto)
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if len(t) >= 4) / len(tokens)


def necesita_ocr(texto: str, paginas_leidas: int) -> tuple[bool, str]:
    """(hay que ocr-ear, por que). Tres comprobaciones sobre el texto ya extraido.

    Cuestan milisegundos porque corren sobre una cadena que ya esta en memoria, y
    lo que se decide con ellas es si vale la pena pagar los segundos del OCR.
    """
    if not texto or not texto.strip():
        return True, "the text layer came back empty"
    if PORTFOLIO.search(texto):
        return True, "it is a PDF portfolio: the content is in the attachments"
    if len(texto) / max(paginas_leidas, 1) < CARACTERES_POR_PAGINA:
        return True, (f"only {len(texto)} characters in {paginas_leidas} page(s): "
                      "the page is an image and the text is just a stamp")
    # Las dos condiciones, no una. Un P&L o un reporte de credito son tablas de
    # cifras y bajan de la proporcion de letras siendo texto impecable; lo que no
    # tienen NUNCA es cero palabras. Los indices de glifo no tienen ni lo uno ni lo
    # otro, asi que exigir ambas deja fuera la basura sin llevarse las tablas.
    if (_proporcion_de_letras(texto) < PROPORCION_DE_LETRAS
            and proporcion_de_palabras(texto) < PALABRAS_LARGAS):
        return True, ("embedded fonts with no character map: the text layer gives "
                      "glyph indexes, not letters")
    return False, ""


# --------------------------------------------------------------------------- #
# Preparar la imagen antes de reconocerla
#
# Aqui esta la mayor parte de lo que se gana, y no en la configuracion de
# Tesseract. Medido sobre una foto de una etiqueta de envio: la imagen tal cual
# daba 0 caracteres y ampliada cuatro veces dio 210.

# Por debajo de esto Tesseract empieza a perder letras: quiere unos 30 pixeles de
# altura de mayuscula, y en una foto de 1280 de ancho el texto pequeno tiene 5.
LADO_MINIMO = 1600
AMPLIACION_MAXIMA = 4

# Al recorte se le pide mas, porque llega ya recortado de una foto y lo que queda
# es justo la parte pequena que no se leyo.
LADO_MINIMO_RECORTE = 2600


def _agrandar(imagen, lado_minimo: int = LADO_MINIMO):
    """Amplia la imagen si es pequena. No inventa informacion: la hace legible.

    Ampliar tiene un techo y conviene saberlo: si el texto original tiene cuatro
    pixeles de alto, la informacion no esta en la imagen y ningun aumento la va a
    traer. Lo que si arregla es el caso contrario -- texto nitido pero pequeno --
    que es el habitual en capturas de pantalla y fotos de documentos.
    """
    lado = max(imagen.size)
    if lado >= lado_minimo:
        return imagen
    escala = min(AMPLIACION_MAXIMA, max(2, round(lado_minimo / max(lado, 1))))
    from PIL import Image

    return imagen.resize((imagen.width * escala, imagen.height * escala), Image.LANCZOS)


def _enderezar(imagen):
    """Rota la imagen si esta de lado. Tesseract no lo hace por su cuenta.

    El police report del matter de control salio como '1 jo 4 eBeq : 400-S2662eyOSzOz
    podey s,wno1,' -- el texto leido de lado, letra por letra. La deteccion de
    orientacion es una llamada aparte y falla cuando hay poco texto en la pagina,
    asi que su fallo no puede tumbar la lectura: si no sabe, se deja como estaba.
    """
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = _binario()
        osd = pytesseract.image_to_osd(imagen, output_type=pytesseract.Output.DICT)
        giro = int(osd.get("rotate", 0)) % 360
    except Exception:  # noqa: BLE001 - sin texto suficiente no hay orientacion que medir
        return imagen
    return imagen.rotate(-giro, expand=True) if giro else imagen


# Como reparte Tesseract la imagen antes de leerla. El 3 es el suyo por defecto y
# analiza la pagina para decidir que zonas son texto; el 6 no analiza nada y trata
# todo como un unico bloque.
#
# SE USA EL 6, y no por gusto. Medido sobre los seis documentos del lote de control
# donde conocemos la respuesta a ojo, el 6 leyo 11 de 11 campos y el 3 solo 9. Los dos
# que recupera son las puntuaciones FICO de sendos reportes de credito, que NINGUNA
# otra configuracion habia encontrado en seis rondas de pruebas: ni a 200, 300, 400 o
# 600 DPI, ni con los modos de texto disperso.
#
# El motivo es el contrario del que parecia. El FICO no es texto corrido: es un
# numero grande dentro de un medidor de colores, y el analisis de layout del 3 lo
# clasificaba como imagen y ni lo miraba. El 6, al no analizar, lo lee. En un caso
# FCRA la puntuacion suele ser el dano reclamado, asi que no es un campo menor.
#
# El 6 iguala o mejora al 3 en los once campos, y tarda un segundo menos.
PAGINA_COMPLETA = 3
UN_SOLO_BLOQUE = 6

# LO QUE HAY QUE VIGILAR: el 6 asume una sola columna. En los documentos medidos
# --reportes, facturas, extractos-- eso es lo que hay y por eso gana. En un escrito
# judicial a dos columnas podria alterar el ORDEN de lectura, y de eso dependen el
# encabezado que mira ruteo.py y los primeros 2.500 caracteres que directorio.py usa
# como caratula. La prueba de arriba comprueba que los valores ESTEN, no en que
# orden, asi que no puede ver ese fallo.
POR_DEFECTO = UN_SOLO_BLOQUE


# --------------------------------------------------------------------------- #
# Que motor lee las imagenes
#
# Son dos problemas distintos y cada motor gana en uno. Sobre los seis documentos
# del lote de control donde conocemos la respuesta a ojo, Tesseract a 300 DPI y psm 6 lee 11 de
# 11 campos. Sobre la foto de una etiqueta de envio, Tesseract devuelve veinte
# caracteres de basura y PaddleOCR saca el numero de guia entero.
#
#   'tesseract'  solo Tesseract. Lo que hacia el sistema hasta ahora
#   'paddle'     solo PaddleOCR
#   'auto'       Tesseract, y PaddleOCR SOLO donde Tesseract no saco nada legible
#
# 'auto' no puede salir peor que 'tesseract': solo entra a rescatar donde ya se
# habia fallado. Por eso es el que conviene por defecto, y por eso Paddle apenas se
# paga -- sus modelos tardan entre cinco y quince segundos en cargar, pero eso solo
# ocurre si algun archivo lo necesita.
MOTORES = ("tesseract", "paddle", "auto")
MOTOR = "auto"

# Cuando se considera que Tesseract no saco nada y hay que llamar al rescate. El
# umbral de parece_ruido() no basta aqui: exige 60 caracteres para opinar, y la
# etiqueta de envio devolvia veinte.
MINIMO_ACEPTABLE = 25


def elegir(motor: str) -> None:
    """Fija el motor para el resto del proceso. Lanza si el nombre no existe."""
    global MOTOR
    if motor not in MOTORES:
        raise ValueError(f"motor '{motor}' desconocido; hay {', '.join(MOTORES)}")
    MOTOR = motor


def _flojo(texto: str) -> bool:
    """Si lo que devolvio Tesseract no sirve y merece una segunda opinion."""
    limpio = (texto or "").strip()
    return len(limpio) < MINIMO_ACEPTABLE or parece_ruido(limpio)


def leer_imagen(imagen, enderezar: bool = True, agrandar: bool = False,
                lado_minimo: int = LADO_MINIMO,
                psm: int = POR_DEFECTO) -> tuple[str, str]:
    """(texto, con que motor se leyo). El unico sitio donde se decide quien lee.

    Devuelve el nombre del motor porque 'read_with' tiene que seguir diciendo la
    verdad: un texto de Paddle y uno de Tesseract fallan de formas distintas, y
    quien revise el resultado necesita saber cual le toco sin abrir el archivo.

    Enderezar y ampliar se hacen AQUI, antes de repartir, para que los dos motores
    reciban exactamente la misma imagen. Si cada uno preparara la suya, comparar los
    resultados no mediria los motores sino los preparativos.
    """
    from . import paddle_ocr

    if enderezar:
        imagen = _enderezar(imagen)
    if agrandar:
        imagen = _agrandar(imagen, lado_minimo)

    if MOTOR == "paddle":
        texto, detalle = paddle_ocr.leer(imagen)
        return texto, _marca_paddle(detalle)

    texto = _de_imagen(imagen, enderezar=False, agrandar=False, psm=psm)
    if MOTOR == "tesseract" or not _flojo(texto):
        return texto, "Tesseract OCR"

    # auto: Tesseract no saco nada legible, se prueba con el otro.
    rescatado, detalle = paddle_ocr.leer(imagen)
    if _flojo(rescatado):
        # Ninguno pudo. Se queda lo de Tesseract, que es lo que se venia haciendo,
        # y el motivo lo dira quien llama. Si Paddle ni siquiera esta instalado,
        # eso viene en detalle['error'] y no se convierte en un fallo silencioso.
        return texto, "Tesseract OCR"
    return rescatado, _marca_paddle(detalle) + " (rescued after Tesseract)"


def _motor_disponible() -> tuple[bool, str]:
    """Si el motor elegido se puede usar. En 'auto' basta con Tesseract."""
    from . import paddle_ocr

    if MOTOR == "paddle":
        return paddle_ocr.disponible()
    return disponible()


def _marca_paddle(detalle: dict) -> str:
    """Como se llama el lector en el informe. Vacio si en realidad no leyo nada.

    Poner 'PaddleOCR' sobre un texto vacio porque el motor ni siquiera estaba
    instalado seria firmar un trabajo que nadie hizo.
    """
    if detalle.get("error") or not detalle.get("fragmentos"):
        return ""
    return f"PaddleOCR (mean confidence {detalle['confianza_media']:.2f})"


def _de_imagen(imagen, enderezar: bool = True, agrandar: bool = False,
               lado_minimo: int = LADO_MINIMO, psm: int = POR_DEFECTO) -> str:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = _binario()
    if enderezar:
        imagen = _enderezar(imagen)
    if agrandar:
        imagen = _agrandar(imagen, lado_minimo)
    return pytesseract.image_to_string(imagen, lang=_idiomas(), config=f"--psm {psm}")


# --------------------------------------------------------------------------- #
# Cuando lo que devolvio el OCR no es texto
#
# Esto NO estaba y hacia falta. Al recortar y ampliar fotos para leer etiquetas, el
# OCR empezo a devolver ruido de las fotos que no tienen texto ninguno: cuatro
# fotos de una persona en un bote dieron entre 122 y 1.043 caracteres de
# '� � os � pan 4 _ .* . 2 we'. Antes esas fotos salian como 'no se pudo leer',
# que era la respuesta correcta; con el recorte pasaron a salir como leidas con
# basura dentro, que es peor: un fallo que se ve es mejor que uno que se esconde.
#
# La senal que los separa se midio sobre esta misma carpeta: la proporcion de
# palabras de cuatro letras o mas. El texto de verdad va de 0,56 a 0,69 y el ruido
# de 0,00 a 0,04. El umbral se pone en medio y lejos de los dos.
PALABRAS_LARGAS = 0.25

# Por debajo de esto no hay con que juzgar, y declararlo ruido cuesta caro: la
# contrasena de una produccion documental viene como imagen dentro de un Word y
# sale del OCR como '%98Bf3@d2xQ' -- once caracteres, ni una palabra de cuatro
# letras. La version anterior de esta funcion la tiraba por ruido y dejaba 147 MB
# de produccion sin abrir teniendo la clave delante.
#
# El ruido de verdad es largo: las cuatro fotos sin texto de este matter dieron
# entre 122 y 1.043 caracteres. El corte va entre las dos cosas.
MINIMO_PARA_JUZGAR = 60


def parece_ruido(texto: str) -> bool:
    """Lo que salio del OCR, es texto o son manchas interpretadas como letras?

    Se aplica SOLO a la salida del OCR y no a una capa de texto: una capa de texto
    mala hay que reemplazarla leyendo de otra forma, pero si el OCR ya devolvio
    ruido no queda nada que intentar y lo unico util es decirlo.

    Ante la duda NO se declara ruido. Un puñado de caracteres basura molesta; una
    clave o un numero de cuenta tirados por sospechosos se pierden sin que nadie se
    entere, que es el fallo que este proyecto lleva toda la semana persiguiendo.
    """
    limpio = texto.strip()
    if len(limpio) < MINIMO_PARA_JUZGAR:
        return False
    tokens = PALABRA.findall(limpio)
    if not tokens:
        return True
    largas = sum(1 for t in tokens if len(t) >= 4) / len(tokens)
    # Con muy pocas palabras la proporcion no significa nada: 'EXHIBIT A' es una
    # pagina separadora legitima y tiene un solo token.
    if len(tokens) < 5:
        return largas == 0
    return largas < PALABRAS_LARGAS


def _region_de_texto(imagen):
    """El recuadro claro mas grande de la imagen, o None si no hay uno claro.

    Es para una foto, no para un escaneo: una etiqueta de envio sobre una caja, un
    documento sobre una mesa, un cartel en una pared. Tesseract asume que le dan
    una PAGINA, y ante una escena reparte mal la imagen y no lee nada. Recortando
    la parte clara -- que en una foto de un documento es el papel -- vuelve a
    tener delante algo que se parece a una pagina.

    Es una heuristica y conviene decirlo: acierta cuando lo que interesa es lo mas
    claro del encuadre, que es el caso corriente porque el papel es blanco. Ante
    una foto sin nada claro devuelve None y no se recorta nada.
    """
    try:
        import numpy as np
        from PIL import ImageOps
    except ImportError:
        return None

    gris = np.asarray(ImageOps.grayscale(imagen))
    claros = gris > 185
    if claros.sum() < gris.size * 0.02:  # no hay nada claro: no es una foto de papel
        return None
    filas, columnas = np.nonzero(claros)
    recuadro = (int(columnas.min()), int(filas.min()),
                int(columnas.max()), int(filas.max()))
    ancho, alto = recuadro[2] - recuadro[0], recuadro[3] - recuadro[1]
    if ancho < 40 or alto < 40:
        return None
    return imagen.crop(recuadro)


def texto_de_imagen(datos: bytes, minimo: int = MINIMO_ACEPTABLE) -> tuple[str, str, str]:
    """Un archivo de imagen entero (png, jpg, tiff...) -> (texto, motivo, con que).

    `minimo` es cuantos caracteres tiene que devolver el OCR para que cuente como
    texto. Hace falta porque parece_ruido() necesita 60 caracteres para opinar y
    Paddle devuelve basura MUY corta: las fotos de una persona en un bote salieron
    con 1, 6 y 10 caracteres y confianzas de 0,57 a 0,94. Antes se reportaban
    correctamente como ilegibles; con Paddle pasaron a contar como leidas.

    Se puede bajar a cero, y hay un caso donde hay que hacerlo: la contrasena de una
    produccion documental viene como imagen dentro de un Word y son once caracteres
    ('%98Bf3@d2xQ'). Un minimo ciego la mataria y dejaria 147 MB sin abrir.
    """
    puede, motivo = _motor_disponible()
    if not puede:
        return "", motivo, ""
    con_que = ""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(datos)) as abierta:
            # Una imagen suelta viene como la subio alguien: puede ser una captura
            # de pantalla pequena o una foto de lado. Una pagina de PDF no, porque
            # la rasterizamos nosotros a 200 DPI.
            imagen = abierta.convert("RGB")
            texto, con_que = leer_imagen(imagen, enderezar=True, agrandar=True)

            # Si no salio nada, puede que no sea un documento escaneado sino una
            # FOTO de un documento. Se prueba una vez mas recortando el papel.
            # Solo despues de fallar: es una llamada mas al OCR y no se paga si la
            # primera funciono.
            if not texto.strip():
                recorte = _region_de_texto(imagen)
                if recorte is not None:
                    # Mas aumento y otro reparto que en la primera pasada: ya no
                    # es una pagina, es un bloque de texto recortado, y suele
                    # venir de una foto donde la letra es diminuta. Medido sobre
                    # una etiqueta de envio: 0 caracteres con los valores de la
                    # primera pasada, 136 con estos.
                    texto, con_que = leer_imagen(recorte, enderezar=False,
                                                 agrandar=True,
                                                 lado_minimo=LADO_MINIMO_RECORTE,
                                                 psm=UN_SOLO_BLOQUE)
    except Exception as error:  # noqa: BLE001
        return "", f"OCR could not read the image ({type(error).__name__})", ""
    if not texto.strip():
        return "", "OCR found no text in the image", ""
    if len(texto.strip()) < minimo:
        return "", (f"OCR returned only {len(texto.strip())} characters: "
                    "too little to be text"), ""
    if parece_ruido(texto):
        return "", "OCR returned noise, not words: the image has no readable text", ""
    return texto, "", con_que


def texto_de_pdf(datos: bytes, paginas: int) -> tuple[str, str, str]:
    """Un PDF escaneado -> (texto, motivo, con que se leyo).

    Se usa PyMuPDF y no pdf2image a proposito: pdf2image necesita que ademas este
    instalado Poppler por fuera, que en Windows es otra descarga suelta que alguien
    tiene que recordar. PyMuPDF es una sola rueda de pip y no depende de nada mas.
    """
    puede, motivo = _motor_disponible()
    if not puede:
        return "", motivo, ""
    try:
        from PIL import Image

        pymupdf = _pymupdf()
        partes, motores = [], []
        with pymupdf.open(stream=datos, filetype="pdf") as documento:
            tope = len(documento) if paginas <= 0 else max(paginas, 1)
            for pagina in documento[:tope]:
                pixeles = pagina.get_pixmap(dpi=DPI)
                with Image.open(io.BytesIO(pixeles.tobytes("png"))) as imagen:
                    # Ya viene a 300 DPI, asi que no hay que ampliarla; lo que si
                    # puede venir es de lado, y eso el OCR no lo arregla solo.
                    trozo, con_que = leer_imagen(imagen, enderezar=True,
                                                 agrandar=False)
                    partes.append(trozo)
                    if trozo.strip():
                        motores.append(con_que)
    except Exception as error:  # noqa: BLE001
        return "", f"OCR could not read the PDF ({type(error).__name__})", ""

    texto = "\n".join(partes)
    if not texto.strip():
        return "", "OCR got no text: unreadable", ""
    if parece_ruido(texto):
        return "", "OCR returned noise, not words: the pages have no readable text", ""
    # En 'auto' unas paginas las puede leer uno y otras el otro, y eso hay que
    # decirlo: no es lo mismo un documento leido entero por Tesseract que uno donde
    # la mitad la rescato Paddle.
    return texto, "", " + ".join(sorted(set(motores))) or "Tesseract OCR"


def adjuntos_de_portfolio(datos: bytes) -> list[tuple[str, bytes]]:
    """Los archivos que un PDF Portfolio lleva dentro, como (nombre, bytes).

    Un portfolio de Acrobat no es un documento: es un sobre. La unica pagina que
    tiene dice 'open this PDF portfolio in Acrobat X or later' y el contenido de
    verdad viaja como adjuntos embebidos. Leer la pagina y darse por satisfecho
    es lo que dejo fuera los exhibits C a J del Motion to Compel -- 41 MB de
    correos del meet and confer reducidos a 117 caracteres de cortesia.

    Devuelve lista vacia si no es un portfolio, si no hay adjuntos o si falta
    pikepdf. Ninguno de los tres es un error que deba tumbar la lectura.
    """
    try:
        import pikepdf
    except ImportError:
        return []
    try:
        with pikepdf.open(io.BytesIO(datos)) as pdf:
            return [(str(nombre), bytes(adjunto.get_file().read_bytes()))
                    for nombre, adjunto in pdf.attachments.items()]
    except Exception:  # noqa: BLE001 - un PDF roto no tiene adjuntos que sacar
        return []


def texto_sin_ocr(datos: bytes, paginas: int) -> str:
    """Segundo intento de leer la capa de texto, con PyMuPDF en vez de pypdf.

    No es lo mismo leer un PDF con una libreria o con otra. pypdf se rinde con
    fuentes raras y con PDF mal construidos que PyMuPDF si abre, y ese rescate es
    gratis: sale antes de plantearse el OCR, que es mil veces mas caro.
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        return ""
    try:
        with pymupdf.open(stream=datos, filetype="pdf") as documento:
            return "\n".join(p.get_text() for p in documento[: max(paginas, 1)])
    except Exception:  # noqa: BLE001 - si no abre, ya lo dira el OCR
        return ""
