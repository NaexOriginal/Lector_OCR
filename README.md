# lector_ocr

De un archivo a su texto, y de su texto a **la carpeta que le toca**. Con OCR solo
cuando hace falta, y diciendo siempre quién lo leyó y con qué se decidió.

```python
from lector_ocr import leer_con_detalle, donde_archivar

texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)

destino = donde_archivar("Notice of Motion.pdf", datos, "Foreclosure")
destino.carpeta      # '03_Litigation/Motions'
destino.se_decidio   # 'el nombre'
```

```
python leer.py documento.pdf
python leer.py escaneo.pdf --motor paddle --plantilla FCRA
python leer.py --diagnostico
```

## Archivar: de qué tipo es y adónde va

Una segunda escalera, encima de la lectura, y con la misma regla: lo barato primero
y sin adivinar cuando no hay señal.

| Peldaño | Coste | Resuelve |
|---|---|---|
| El **nombre** del archivo | cero, no se abre | ~36% de los casos reales |
| El **contenido** (título, luego cuerpo) | una lectura | el resto |
| Nada casa | — | devuelve vacío |

El tercer peldaño importa tanto como los otros dos. Un archivo sin clasificar, en una
carpeta a la vista, vale más que uno archivado a ojo donde no toca: lo primero se
arregla mirándolo, lo segundo no se descubre hasta que alguien no encuentra sus
papeles.

Cada tipo declara su destino **en las dos plantillas**, así que el mismo documento
sabe ir a `03_Litigation/Motions` o a `09_Motions` según el expediente:

```
python leer.py SCAN_0042.png                    ->  03_Litigation/Motions
python leer.py SCAN_0042.png --plantilla FCRA   ->  09_Motions
```

**La tabla se comprueba a sí misma.** `clasificacion.revisar()` verifica que todos
los destinos que declara existan de verdad en su plantilla. Un nombre mal escrito ahí
no falla haciendo ruido: crearía una carpeta nueva en producción.

## Posición contra contenido

Esto es para archivos **sueltos**, los que no tienen carpeta que los explique. Si un
documento ya está guardado en `LITIGATION`, esa carpeta lleva dentro la intención de
quien lo archivó y vale más que cualquier lectura — se midió: reclasificar por
contenido lo que ya se sabía por posición cambió **56 de 314** documentos, varios a
peor (un `Motion to Compel` pasó a `Letter`).

Para ese caso está `equivalencias.py`, que traduce nombre de carpeta a nombre de
carpeta sin abrir un solo archivo.

## Por qué está hecho así

**La cascada, de lo barato a lo caro.** Un PDF se intenta con `pypdf`; si no da
nada usable, con PyMuPDF; si tampoco, se mira si es un *PDF portfolio* (un sobre con
los documentos de verdad adjuntos, que se abre con pikepdf); y solo entonces se
rasteriza y se hace OCR. Reconocer una página cuesta entre uno y tres segundos, mil
veces más que leer una capa de texto.

**`necesita_ocr` no pregunta «vino vacío», pregunta «vino plausible».** Un escaneo
puede devolver media página de basura tipográfica y no estar vacío. El criterio es
la proporción de palabras reconocibles, no la longitud.

**Todo pasa en la máquina.** Los OCR de nube leen mejor las tablas y la letra
manuscrita, pero hay expedientes cuyo protective order prohíbe mandar material
confidencial a una herramienta de IA abierta o a un modelo de lenguaje. El valor por
defecto es el que no puede equivocarse.

**Si un motor no está instalado, no se rompe nada.** `disponible()` lo comprueba y
la lectura devuelve el motivo como cualquier otro fallo. Y si el motor falla al leer,
el motivo es **el error del motor**, no «sin texto»: un fallo de Paddle que se
escondía así dejó una tanda entera de escaneos sin leer sin que nadie lo notara.

## Lo que se midió, y con qué resultado

Los umbrales no son de catálogo. Cada uno se midió contra documentos reales cuyo
contenido se había verificado a ojo, y la medición está escrita en el comentario que
acompaña a la constante.

| Decisión | Antes | Después | Medido sobre |
|---|---|---|---|
| `DPI = 300` (era 200) | 3 de 5 campos | **5 de 5** | lote de control |
| `psm 6` en vez de psm 3 | 9 de 11 campos | **11 de 11** | 6 documentos conocidos |
| PaddleOCR en GPU | 3,83 s/imagen | **0,22 s/imagen** | la misma imagen, 17× |

`psm 6` supone **una sola columna**. Es lo correcto para cartas, escritos y
formularios; para un periódico a tres columnas sería peor.

## Los dos motores, y por qué manda Paddle

Ganan en problemas distintos:

- **Tesseract** lee mejor el documento de oficina: 11 de 11 campos conocidos.
- **PaddleOCR** rescata lo que Tesseract deja en basura — la foto de una etiqueta de
  envío, donde Tesseract devuelve veinte caracteres inservibles y Paddle saca el
  número de guía entero.

**El valor por defecto es `MOTOR = "paddle"`.** Antes era `"auto"` (manda
Tesseract y Paddle rescata), y en la práctica fallaba por donde menos se veía:
Tesseract es un programa aparte, no un paquete de pip, y en los equipos donde no estaba
instalado **cada escaneo y cada imagen salía `Tesseract is missing`**. Paddle se
instala con pip, corre igual en GPU que en CPU y lee con confianza por fragmento.
Tesseract sigue disponible con `ocr.elegir("tesseract" | "auto")`.

Con Paddle, **la página la endereza el propio Paddle** (`use_doc_orientation_classify`):
el enderezado de antes lo hacía Tesseract y ya no se le llama.

**En CPU, sin oneDNN.** Con `paddlepaddle` 3.3.1 la aceleración oneDNN (MKLDNN) de CPU
revienta en cada predicción (`NotImplementedError: ConvertPirAttribute2RuntimeAttribute
not support`). `paddle_ocr` la apaga en CPU (`enable_mkldnn=False`); en GPU no aplica.

Las dos rutas reciben **la misma imagen** ya enderezada y escalada, para que la
comparación mida el motor y no el preprocesado.

## Un solo modelo, aunque llamen ocho a la vez

El modelo de PaddleOCR se construye **una vez** y se comparte, con doble
comprobación bajo candado. `functools.lru_cache` no basta: protege el diccionario de
la caché, pero no impide que varios hilos ejecuten la función a la vez cuando todos
fallan la caché al arrancar. Ocho hilos entraban, ocho construían un `PaddleOCR`, uno
ganaba la caché y los otros siete quedaban tirados **con su memoria de GPU ya
reservada**.

En un proceso por lotes eso es lento. En un servidor con peticiones concurrentes es
peor: un modelo por petición agota la VRAM y el proceso muere sin decir por qué.

La **inferencia también se serializa**, y no cuesta nada: reconocer una imagen son
0,22 s en GPU frente al segundo y medio que tarda bajarse el archivo. El paralelismo
que importa es el de la entrada/salida, no el del OCR. Paddle tampoco garantiza que
un predictor se pueda usar desde varios hilos.

Para escalar en servidor, el mismo principio: **un proceso, un modelo**, y varios
procesos detrás de una cola si hace falta más — no varios modelos dentro del mismo
proceso.

## `con_que` no es decorativo

Cada lectura devuelve qué la produjo: `pypdf`, `PyMuPDF`, `python-docx`,
`extract-msg`, `Tesseract`, `PaddleOCR`. Sin esa columna no se puede juzgar un
resultado. Si sale un `1` donde debería poner `7`, importa muchísimo si lo leyó
`python-docx` — imposible, ese texto es el que escribió el autor — o Tesseract, donde
es probable y se arregla subiendo DPI.

Ese fue el hallazgo que cerró una cacería larga: un pase que «corregía» dígitos con
OCR estaba tomándolos de la caja de al lado y convertía `2025` en `12025`. La
salvaguarda que lo permitía decía *«conserva los dígitos y añade uno»*, que describe
robar un dígito a la perfección. El módulo se borró.

## La ficha: una línea de JSONL por archivo

```python
from lector_ocr import ficha_de, cabecera
import json

registro = ficha_de("Notice of Motion.pdf", datos, "Foreclosure")
json.dumps(registro, ensure_ascii=False)   # una línea del fichero
```

```json
{"file_name": "SCAN_001.png", "subfolder": "(folder root)", "size_mb": 0.0,
 "was_read": true, "read_with": "PaddleOCR (mean confidence 0.97) (image)", "not_read_because": null,
 "document_type": "Notice of motion", "goes_to": "03_Litigation/Motions",
 "decided_by": "el contenido (titulo)", "decided_because": "NOTICE OF MOTION",
 "contains_ssn": true, "extracted_text_length": 42, "extracted_text": "..."}
```

**JSONL y no JSON**, y no es cosmético. Un expediente crece: hoy se describen tres
archivos y mañana treinta. Con JSONL se **añaden líneas al final** sin releer ni
reescribir lo que ya había, y se recorre a trozos — un caso de cuatro mil archivos no
obliga a cargar veinte megas en memoria para mirar uno.

La convención: la **primera línea** es un resumen y se reconoce porque lleva `matter`;
las demás son archivos y llevan `file_name`. El resumen incluye `partial`, y eso
importa — un fichero incompleto que no se declare incompleto se lee como completo, y
entonces la ausencia de un documento parece una afirmación de que no existe.

`decided_by` dice **cuánto fiarse**: `el nombre` y `el contenido (titulo)` son
fiables; `el contenido (mencion)` casó con algo suelto en el cuerpo y acierta bastante
menos. `decided_because` trae la línea exacta, que suele bastar para juzgar la
propuesta sin abrir el documento.

## Datos personales

`contains_ssn` va en **cada** ficha, se enmascare o no. Con `enmascarar_ssn=True` los
números quedan `XXX-XX-6789`, conservando los cuatro últimos dígitos, que son los que
sirven para cotejar.

El valor por defecto es **no enmascarar**, porque quién decide eso es la
organización. Lo que no se negocia es que el número esté a la vista: una decisión
tomada sin saber cuántos documentos llevan datos personales no es una decisión.

## Para montarlo en un servidor

**Un proceso, un modelo.** Ver la sección de PaddleOCR más abajo. Si hace falta más
capacidad, varios procesos detrás de una cola — nunca varios modelos dentro del mismo
proceso.

**La lectura es completa por defecto.** `ficha_de` lee todas las páginas y no tiene
tope de tamaño: la ficha es el texto del documento, no una muestra, y con un tope los
escaneos largos —justo los que solo tienen texto si se les hace OCR— quedaban a medias.
Si un servidor necesita que un archivo enorme no bloquee a los demás, el tope se pasa:
`ficha_de(..., paginas=4, max_mb=25)`.

**Cada archivo se lee una vez.** `ficha_de` clasifica con el texto que ya leyó (su
principio, unas dos páginas), en vez de volver a leer el archivo. Antes lo leía dos
veces, y un escaneo pasaba dos veces por el OCR. Comprobado sobre 119 documentos: la
clasificación sale igual en 118; el que cambia es un `.doc`, formato que antes no
tenía lector.

**Imágenes descomprimidas enormes.** Pillow avisa por encima de ~89 megapíxeles y
falla por encima de ~179. Con entrada de terceros eso es una vía de denegación de
servicio, así que conviene fijar `Image.MAX_IMAGE_PIXELS` a un valor propio en vez de
dejar el de la librería.

**Nombres con blancos invisibles.** Si el servidor escribe archivos o carpetas con el
nombre que trae el documento, recórtale los bordes incluyendo el espacio duro
(` `): se escribe igual que un espacio normal, no se ve, y SharePoint rechaza el
nombre con un 400 seco. Nos costó el 20% de una migración.

## Los módulos

| | |
|---|---|
| `ocr.py` · `paddle_ocr.py` | píxeles a texto, dos motores |
| `lectores.py` | la cascada por formato, y el olfateo por contenido |
| `legado.py` | `.doc` y `.xls` de Office 97-2003 |
| `otros_formatos.py` | rtf, xml, eml, mht, xlsm/xlsb/ods, dotx/docm, odt, pptx |
| `disfrazados.py` | lo que no dice lo que es, heic/jfif, 7z y winmail.dat |
| `clasificacion.py` | texto a tipo de documento, y su carpeta en cada plantilla |
| `equivalencias.py` | nombre de carpeta a nombre de carpeta |
| `plantillas.py` | las dos estructuras. Se sustituyen para adaptarlo a otra casa |
| `archivar.py` | une las dos mitades |
| `ficha.py` | la línea de JSONL de un archivo, y la cabecera del expediente |

## Qué lee

`pdf` · `docx` · `doc` · `rtf` · `odt` · `msg` · `eml` · `xlsx` · `xls` · `xlsm` ·
`xlsb` · `ods` · `pptx` · `html` · `mht` · `xml` · `txt` · `csv` · `zip` · `7z` ·
`winmail.dat` · imágenes (`jpg`, `png`, `tif`, `heic`, `jfif`…). 45 extensiones.

**Todo con pip, nada de Office**, así que corre igual en un servidor Linux. El `.doc`
lo lee un lector propio sobre `olefile`, que reconstruye el texto desde la tabla de
piezas de Word 97-2003. Si LibreOffice está instalado, se usa de respaldo para lo que
ese lector no entiende (Word 6/95).

**Lo que no dice lo que es se lee por lo que es.** Antes de elegir lector se miran
los primeros bytes: un `.xls` que es HTML de un portal, un `.doc` que es RTF, un PDF
llamado `.download`, un Word sin extensión o llamado `.pdf`. Las respuestas «es
texto» solo se aceptan si la extensión no significa nada (`.download`, `.dat`, sin
extensión), para que un `.css` o un `.svg` no pasen por documentos.

**Lo que no es un documento se dice.** Los `._` que deja un Mac al copiar
(*AppleDouble*) y el código de las páginas web guardadas (`jquery.min.js.download`)
devuelven un motivo con `not a document`, para que quien reintente sepa que no hay
nada que reintentar.

**La lectura completa (`paginas=0`) no recorta nada:** todos los párrafos de un Word,
todas las hojas y filas de un Excel, el correo entero, todas las entradas de un ZIP y
comprimidos anidados hasta cinco niveles.

El lector de ZIP abre archivos protegidos si la contraseña aparece en un documento
hermano (`Password: X`), que es como llegan de algunos proveedores.

El de `.docx` recorre el cuerpo **en orden de documento**, no párrafos y luego
tablas: la carátula de un escrito vive en una tabla y se iba al final del texto,
detrás del cuerpo, arruinando cualquier regla que mirase la cabecera.

## Instalación

```
pip install -r requirements.txt
```

Tesseract ya no hace falta: solo si se elige a mano (`winget install
UB-Mannheim.TesseractOCR`).

`paddlepaddle` no sale de PyPI y hay que elegir CPU o GPU; las instrucciones están al
final de `requirements.txt`. Para comprobar qué se está usando de verdad:

```
python leer.py --diagnostico
```
