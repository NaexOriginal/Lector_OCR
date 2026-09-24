# lector_ocr

De un archivo a su texto, con OCR **solo cuando hace falta** y diciendo siempre
quién lo leyó.

```python
from lector_ocr import leer_con_detalle

texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)
```

```
python leer.py documento.pdf
python leer.py escaneo.pdf --motor paddle --paginas 4
python leer.py --diagnostico
```

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

**Si Tesseract no está instalado, no se rompe nada.** `disponible()` lo comprueba y
la lectura devuelve el motivo como cualquier otro fallo.

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

## Los dos motores, y por qué están los dos

Ganan en problemas distintos:

- **Tesseract** lee mejor el documento de oficina: 11 de 11 campos conocidos.
- **PaddleOCR** rescata lo que Tesseract deja en basura — la foto de una etiqueta de
  envío, donde Tesseract devuelve veinte caracteres inservibles y Paddle saca el
  número de guía entero.

Por eso el valor por defecto es `MOTOR = "auto"`: manda Tesseract, y Paddle entra
solo cuando lo que vuelve es demasiado corto o parece ruido. Se cambia con
`ocr.elegir("tesseract" | "paddle" | "auto")`.

Las dos rutas reciben **la misma imagen** ya enderezada y escalada, para que la
comparación mida el motor y no el preprocesado.

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

## Qué lee

`pdf` · `docx` · `msg` · `xlsx` · `html` · `txt` · `zip` · imágenes

El lector de ZIP abre archivos protegidos si la contraseña aparece en un documento
hermano (`Password: X`), que es como llegan de algunos proveedores.

El de `.docx` recorre el cuerpo **en orden de documento**, no párrafos y luego
tablas: la carátula de un escrito vive en una tabla y se iba al final del texto,
detrás del cuerpo, arruinando cualquier regla que mirase la cabecera.

## Instalación

```
pip install -r requirements.txt
winget install UB-Mannheim.TesseractOCR
```

`paddlepaddle` no sale de PyPI y hay que elegir CPU o GPU; las instrucciones están al
final de `requirements.txt`. Para comprobar qué se está usando de verdad:

```
python leer.py --diagnostico
```
