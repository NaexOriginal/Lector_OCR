r"""Archivos que no dicen lo que son, y tres formatos que faltaban (heic, jfif, 7z).

LOS DISFRAZADOS. En Matters (29-sep) hay unos 305 archivos con una extension que no
dice nada: 218 '.download' (descargas del navegador), 38 '.dat', 49 con extensiones
que son en realidad parte del nombre ('Carta 1.5.2019' -> '.2019') y los que no
tienen extension. Muchos son PDF o Word completos. Hasta ahora salian 'no reader
for .download' y, como ese motivo es permanente, no se leian nunca.

Aqui se mira el CONTENIDO (los primeros bytes, y para Office y ZIP lo que llevan
dentro) y se decide que es de verdad. Lo usa leer_con_detalle en dos casos:

    1. la extension no tiene lector, o es de las que no significan nada
    2. el lector de su extension fallo y el contenido es de otro tipo
       (un '.pdf' que en realidad es un Word)

LO QUE NO SE ACEPTA. Un .css o un .svg SON texto, y un olfateo ingenuo los daria
por leidos. Por eso las respuestas 'es texto / HTML / XML' solo se aceptan cuando la
extension no es una extension de verdad (.download, .dat, sin extension, '2019');
las firmas binarias (PDF, Office, imagenes, ZIP, 7z) valen siempre, porque un
acceso directo .lnk nunca va a empezar por '%PDF'.

winmail.dat: los adjuntos que Outlook empaqueta en TNEF. Se abren con tnefparse
y cada adjunto se lee con su lector.
"""

from __future__ import annotations

import io
import re
import zipfile

from .lectores import Lector, LectorImagen

# Extensiones que no dicen nada del contenido: con estas se acepta cualquier olfateo.
SIN_SIGNIFICADO = {"", "download", "crdownload", "part", "dat", "tmp", "bin", "file", "unknown"}

# Lo que se reconoce solo por ser texto: no basta con una extension de verdad.
TEXTUALES = {"txt", "html", "xml"}


def parece_extension(ext: str) -> bool:
    """Si 'ext' es una extension de verdad (css, lnk, svg) o parte del nombre ('2019',
    'final version'). Las de verdad son cortas y con alguna letra."""
    return bool(re.fullmatch(r"[a-z][a-z0-9]{0,5}", ext or ""))


def _dentro_de_ole(datos: bytes) -> str | None:
    try:
        import olefile

        with olefile.OleFileIO(io.BytesIO(datos)) as ole:
            nombres = {"/".join(n) for n in ole.listdir()}
    except Exception:  # noqa: BLE001
        return None
    if "WordDocument" in nombres:
        return "doc"
    if "Workbook" in nombres or "Book" in nombres:
        return "xls"
    if any(n.startswith("__substg1.0_") for n in nombres):
        return "msg"
    return None


def _dentro_de_zip(datos: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            nombres = set(z.namelist())
            tipo = z.read("mimetype").decode("ascii", "ignore") if "mimetype" in nombres else ""
    except Exception:  # noqa: BLE001
        return "zip"
    if "word/document.xml" in nombres:
        return "docx"
    if "xl/workbook.xml" in nombres or "xl/workbook.bin" in nombres:
        return "xlsb" if "xl/workbook.bin" in nombres else "xlsx"
    if "ppt/presentation.xml" in nombres:
        return "pptx"
    if "opendocument.text" in tipo:
        return "odt"
    if "opendocument.spreadsheet" in tipo:
        return "ods"
    return "zip"


def que_es_de_verdad(datos: bytes) -> str | None:
    """La extension que le corresponde por su contenido, o None si no se sabe."""
    if len(datos) < 4:
        return None
    cabeza = datos[:16]
    # LOS CONTENEDORES ANTES QUE EL PDF. '%PDF-' se busca en los primeros 1.024 bytes
    # (algunos PDF traen basura delante), y un ZIP guardado SIN comprimir que empieza
    # por un PDF lo lleva ahi mismo: se leia como un unico PDF y lo demas que traia
    # dentro no se leia. Visto el 29-sep probando la pasada sin OCR.
    contenedor = cabeza.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
                                    b"7z\xbc\xaf\x27\x1c", b"\x78\x9f\x3e\x22"))
    if not contenedor and b"%PDF-" in datos[:1024]:
        return "pdf"
    if cabeza.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _dentro_de_ole(datos)
    if cabeza.startswith(b"PK\x03\x04"):
        return _dentro_de_zip(datos)
    if cabeza.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if cabeza.startswith(b"\x78\x9f\x3e\x22"):
        return "tnef"
    if cabeza.startswith(b"\x89PNG"):
        return "png"
    if cabeza.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if cabeza.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if cabeza.startswith((b"II*\x00", b"MM\x00*")):
        return "tif"
    if cabeza.startswith(b"BM") and len(datos) > 26:
        return "bmp"
    if cabeza[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "webp"
    if datos[4:8] == b"ftyp" and datos[8:12] in (b"heic", b"heix", b"hevc", b"heim",
                                                  b"heis", b"mif1", b"msf1"):
        return "heic"

    texto = datos[:4000].lstrip(b"\xef\xbb\xbf \t\r\n")
    minus = texto[:1500].lower()
    if texto.startswith(b"{\\rtf"):
        return "rtf"
    # Un correo o una pagina guardada (MIME): cabeceras de correo al principio.
    if re.match(rb"(?:(?:received|return-path|from|to|subject|date|mime-version|"
                rb"message-id|x-[\w-]+|content-type):[^\n]*\r?\n)+", minus) and (
            b"mime-version:" in minus or b"received:" in minus or b"subject:" in minus):
        return "eml"
    if minus.startswith((b"<!doctype html", b"<html")) or b"<html" in minus[:600]:
        return "html"
    if minus.startswith(b"<?xml"):
        return "xml"
    muestra = datos[:4000]
    if b"\x00" not in muestra:
        imprimibles = sum(1 for b in muestra if b in b"\t\r\n" or b >= 32)
        if imprimibles / len(muestra) > 0.97:
            return "txt"
    return None


# LOS RESTOS DE 'GUARDAR PAGINA COMO'. Al guardar una pagina web, el navegador
# baja tambien su codigo: 'jquery.min.js.download', 'analytics.js.download'. En
# Matters (29-sep) son 213 de los 218 '.download' (y 2 '.pl.download'). Son
# texto, asi que el olfateo los aceptaba, y metian en el JSONL del expediente
# millones de caracteres de JavaScript: uno solo, 5,2 millones.
#
# Se reconocen por el contenido, no por el nombre (a leer_con_detalle solo le llega
# 'download'): palabras de programa (function, var, =>, this., $( ...) y una
# densidad de llaves, parentesis y punto y coma que la prosa no tiene. Un escrito
# judicial puede decir 'return' o tener un parentesis; no tiene cinco de estas
# senales Y un 3% de simbolos de codigo, ni treinta senales (ver parece_codigo).
#
# El motivo lleva 'not a document' a proposito: es PERMANENTE, como los '._' del
# Mac (describir_casos.PERMANENTES tiene que incluir esa frase).
NO_ES_DOCUMENTO_WEB = "web page asset (JavaScript/CSS code), not a document"
_SENALES_DE_CODIGO = re.compile(
    r"\bfunction\b|=>|\bvar\s|\bconst\s|\blet\s|\bthis\.|\bwindow\.|\bdocument\.|"
    r"\$\(|\bprototype\b|\bmodule\.exports\b|\brequire\(|\bundefined\b|===|!==|"
    r"\buse\s+strict\b|\bmy\s+[$@%]\w|\bsub\s+\w+\s*\{|!important|@media\b|"
    r"[.#]?[\w-]+\s*\{[^{}]{0,200}:[^{}]{0,200};")


def parece_codigo(datos: bytes) -> bool:
    """Si el texto es codigo de una pagina web (JS, CSS, Perl) y no un documento."""
    texto = datos[:40000].decode("utf-8", "ignore")
    if not texto.strip():
        return False
    senales = len(_SENALES_DE_CODIGO.findall(texto))
    simbolos = sum(texto.count(c) for c in "{};()=") / len(texto)
    # Dos vias. Con pocas senales hace falta ademas densidad de simbolos (la prosa
    # ronda el 1%). Con muchas basta con ellas: un .js muy comentado baja la
    # densidad al 3% (richfaces-event.js: 84 senales, 3,2%), y ningun escrito tiene
    # treinta 'function', 'var' o '$(' en sus primeras paginas.
    return (senales >= 5 and simbolos > 0.03) or senales >= 30


def olfatear(extension: str, datos: bytes) -> str | None:
    """La extension con la que leerlo, si el contenido lo justifica. None si no."""
    real = que_es_de_verdad(datos)
    if not real or real == extension:
        return None
    if real in TEXTUALES and extension not in SIN_SIGNIFICADO and parece_extension(extension):
        return None       # un .css o un .svg: es texto, pero no es un documento
    return real


# ------------------------------------------------------------ imagenes

class LectorImagenExtra(LectorImagen):
    """jfif / jpe (son JPG) y heic / heif (fotos de iPhone). El OCR es el mismo;
    para HEIC hace falta pillow-heif, que ensena a Pillow a abrirlas."""

    extensiones = ("jfif", "jpe", "heic", "heif")

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        if datos[4:8] == b"ftyp":
            try:
                import pillow_heif

                pillow_heif.register_heif_opener()
            except ImportError:
                return "", "pillow-heif is missing (pip install pillow-heif)", ""
        return super().leer_detalle(datos, paginas)


# ------------------------------------------------------------------ 7z

class Lector7Z(Lector):
    """Comprimido 7-Zip, con py7zr. Como el ZIP: cada archivo de dentro con su
    lector, todos, y comprimidos dentro de comprimidos hasta cinco niveles."""

    extensiones = ("7z",)
    NIVELES = 5

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int, nivel: int = 0) -> tuple[str, str, str]:
        from .lectores import leer_con_detalle

        try:
            import py7zr
            from py7zr.io import BytesIOFactory
        except ImportError:
            return "", "py7zr is missing (pip install py7zr)", ""
        try:
            with py7zr.SevenZipFile(io.BytesIO(datos), mode="r") as archivo:
                if archivo.needs_password():
                    return "", "the 7z archive is password protected", ""
                entradas = [e for e in archivo.list() if not e.is_directory]
                # OJO: BytesIOFactory CORTA SIN AVISAR lo que pase de 'limit'. Se
                # pone el tamano real del mayor archivo de dentro, y un poco mas.
                limite = max((e.uncompressed or 0 for e in entradas), default=0) + 1024
                fabrica = BytesIOFactory(limite)
                archivo.extractall(factory=fabrica)
        except py7zr.exceptions.PasswordRequired:
            return "", "the 7z archive is password protected", ""
        except Exception as error:  # noqa: BLE001
            return "", f"could not open the 7z archive ({type(error).__name__})", ""

        partes, leidos, fallidos = [], set(), 0
        for nombre, producto in sorted(fabrica.products.items()):
            producto.seek(0)
            contenido = producto.read()
            ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre.rsplit("/", 1)[-1] else ""
            if ext == "7z":
                if nivel + 1 >= self.NIVELES:
                    fallidos += 1
                    continue
                suyo, _, con_que = self.leer_detalle(contenido, paginas, nivel + 1)
            else:
                suyo, _, con_que = leer_con_detalle(ext, contenido, paginas)
            if suyo.strip():
                partes.append(f"--- {nombre} ---\n{suyo}")
                if con_que:
                    leidos.add(con_que.split(" (")[0].split(":")[0])
            else:
                fallidos += 1
        if not partes:
            return "", f"7z with {len(entradas)} files, none readable", ""
        via = f" via {', '.join(sorted(leidos))}" if leidos else ""
        return ("\n\n".join(partes), "",
                f"7z: {len(partes)} of {len(entradas)} files{via}")


# ---------------------------------------------------------- winmail.dat

class LectorTNEF(Lector):
    """winmail.dat: Outlook empaqueta ahi el cuerpo y los adjuntos cuando manda a
    alguien que no usa Outlook. El documento de verdad son los adjuntos."""

    extensiones = ("tnef",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        from .lectores import LectorHTML, leer_con_detalle

        try:
            from tnefparse import TNEF
        except ImportError:
            return "", "tnefparse is missing (pip install tnefparse)", ""
        try:
            paquete = TNEF(datos, do_checksum=False)
        except Exception as error:  # noqa: BLE001
            return "", f"could not open the winmail.dat ({type(error).__name__})", ""

        partes = []
        cuerpo = paquete.body or ""
        if isinstance(cuerpo, bytes):
            cuerpo = cuerpo.decode("cp1252", "replace")
        if not cuerpo.strip() and paquete.htmlbody:
            html = paquete.htmlbody
            cuerpo, _ = LectorHTML().leer(html if isinstance(html, bytes) else html.encode(), 0)
        if cuerpo.strip():
            partes.append(cuerpo.strip())
        leidos = set()
        for adjunto in paquete.attachments:
            try:
                nombre = adjunto.long_filename() or adjunto.name or "attachment"
            except Exception:  # noqa: BLE001
                nombre = getattr(adjunto, "name", "attachment") or "attachment"
            if isinstance(nombre, bytes):
                nombre = nombre.decode("cp1252", "replace")
            ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
            suyo, motivo, con_que = leer_con_detalle(ext, adjunto.data or b"", paginas)
            if suyo.strip():
                partes.append(f"--- {nombre} ---\n{suyo}")
                if con_que:
                    leidos.add(con_que.split(" (")[0].split(":")[0])
            else:
                partes.append(f"--- {nombre} --- (not read: {motivo})")
        texto = "\n\n".join(partes).strip()
        if not texto:
            return "", "the winmail.dat has no body and no attachments", ""
        via = f" via {', '.join(sorted(leidos))}" if leidos else ""
        return texto, "", f"winmail.dat (TNEF): {len(paquete.attachments)} attachment(s){via}"
