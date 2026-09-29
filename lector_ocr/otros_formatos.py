r"""Los formatos poco comunes que tienen arreglo facil. En Matters son ~253 (29-sep).

    rtf              127   el lector RTF de legado.py
    xml               98   XML Spreadsheet 2003 como tabla; cualquier otro, su texto
    eml                8   correo: cabeceras, cuerpo Y adjuntos (cada uno con su lector)
    mht / mhtml        8   pagina guardada: se desempaqueta el MIME y se lee el HTML
    xlsm / xlsb / ods  5   python-calamine
    dotx / docm / dotm 2   el lector de Word, corrigiendo el tipo interno
    odt                3   zip + content.xml, Python puro
    pptx               2   zip + XML de cada diapositiva y sus notas, Python puro

Nada usa Office ni programas externos: todo corre en Linux.
"""

from __future__ import annotations

import email
import email.policy
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .legado import libro_con_calamine, texto_de_rtf, texto_de_xml_excel
from .lectores import Lector, LectorDOCX, LectorHTML

# Un correo puede traer otro correo adjunto, y ese otro. Dos niveles bastan para
# un reenvio de un reenvio; mas alla es casi seguro un bucle o un archivo roto.
NIVELES_DE_ADJUNTOS = 3


def _limpiar(texto: str) -> str:
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


class LectorRTF(Lector):
    extensiones = ("rtf",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        if not datos.lstrip()[:5] == b"{\\rtf":
            # Un .rtf que no lo es: suele ser un .doc o un .docx renombrado.
            from .legado import LectorDOC
            t, m, c = LectorDOC().leer_detalle(datos, paginas)
            return t, m, f"{c} (named .rtf)" if c else ""
        texto = texto_de_rtf(datos)
        return (texto, "", "RTF parser") if texto else ("", "the RTF file has no text", "")


class LectorXML(Lector):
    """Un .xml puede ser cualquier cosa. Si es una hoja de Excel 2003 se lee como
    tabla; si es otro XML (un Word 2003 XML, una exportacion de un sistema) se
    saca todo su texto, en orden, un elemento por linea."""

    extensiones = ("xml",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        if b"urn:schemas-microsoft-com:office:spreadsheet" in datos[:4000].lower():
            texto = texto_de_xml_excel(datos)
            if texto:
                return texto, "", "XML Spreadsheet 2003"
        try:
            raiz = ET.fromstring(datos)
        except ET.ParseError:
            # XML mal formado: se quitan las etiquetas a lo bruto, que algo dice.
            crudo = datos.decode("utf-8", "ignore")
            texto = _limpiar(re.sub(r"<[^>]+>", "\n", crudo))
            return (texto, "", "XML (malformed, tags stripped)") if texto else (
                "", "malformed XML with no text", "")
        trozos = [t.strip() for t in raiz.itertext() if t and t.strip()]
        texto = "\n".join(trozos)
        return (texto, "", "XML text") if texto else ("", "the XML file has no text", "")


class LectorEML(Lector):
    """Correo en formato estandar (.eml). A diferencia del .msg de Outlook, se lee
    con la libreria estandar de Python.

    LOS ADJUNTOS SE LEEN. En un despacho el correo muchas veces es solo el sobre:
    'adjunto la orden del juez'. La orden es el adjunto, y sin leerlo el correo
    contaria como leido con dos lineas de cortesia.
    """

    extensiones = ("eml",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int, nivel: int = 0) -> tuple[str, str, str]:
        from .lectores import leer_con_detalle

        try:
            mensaje = email.message_from_bytes(datos, policy=email.policy.default)
        except Exception as error:  # noqa: BLE001
            return "", f"could not parse the email ({type(error).__name__})", ""

        cabecera = [f"{campo}: {mensaje[campo]}" for campo in
                    ("Subject", "From", "To", "Cc", "Date") if mensaje[campo]]
        cuerpo, adjuntos, lectores = "", [], set()
        try:
            parte = mensaje.get_body(preferencelist=("plain", "html"))
        except Exception:  # noqa: BLE001
            parte = None
        if parte is not None:
            contenido = parte.get_content()
            if parte.get_content_subtype() == "html":
                contenido, _ = LectorHTML().leer(contenido.encode("utf-8"), 0)
            cuerpo = contenido or ""

        for adjunto in mensaje.iter_attachments():
            nombre = adjunto.get_filename() or "attachment"
            try:
                crudo = adjunto.get_payload(decode=True) or b""
            except Exception:  # noqa: BLE001
                continue
            if adjunto.get_content_type() == "message/rfc822":
                interno = adjunto.get_payload()
                crudo = (interno[0].as_bytes() if isinstance(interno, list) and interno
                         else crudo)
                extension = "eml"
            else:
                extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
            if not crudo:
                continue
            if extension == "eml" and nivel + 1 >= NIVELES_DE_ADJUNTOS:
                adjuntos.append(f"--- {nombre} --- (not read: email nested too deep)")
                continue
            if extension == "eml":
                suyo, motivo, con_que = self.leer_detalle(crudo, paginas, nivel + 1)
            else:
                suyo, motivo, con_que = leer_con_detalle(extension, crudo, paginas)
            if suyo.strip():
                adjuntos.append(f"--- {nombre} ---\n{suyo}")
                if con_que:
                    lectores.add(con_que.split(" (")[0])
            else:
                adjuntos.append(f"--- {nombre} --- (not read: {motivo})")

        texto = _limpiar("\n".join(cabecera) + "\n\n" + cuerpo +
                         ("\n\n" + "\n\n".join(adjuntos) if adjuntos else ""))
        if not texto.strip():
            return "", "the email is empty", ""
        con = "email parser"
        if adjuntos:
            con += f" + {len(adjuntos)} attachment(s)"
            if lectores:
                con += f" via {', '.join(sorted(lectores))}"
        return texto, "", con


class LectorMHT(Lector):
    """Pagina web guardada en un solo archivo (.mht / .mhtml). Es un MIME, como un
    correo: el HTML va dentro, casi siempre en quoted-printable. Pasarlo tal cual
    al lector HTML dejaba el texto lleno de '=3D' y '=\\n'."""

    extensiones = ("mht", "mhtml")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        try:
            mensaje = email.message_from_bytes(datos, policy=email.policy.default)
            partes = [p for p in mensaje.walk() if p.get_content_type() == "text/html"]
        except Exception:  # noqa: BLE001
            partes = []
        if not partes:
            # No es MIME: una pagina HTML normal con otra extension.
            t, m = LectorHTML().leer(datos, paginas)
            return t, m, "HTML parser" if t else ""
        textos = []
        for parte in partes:
            html = parte.get_content()
            t, _ = LectorHTML().leer(html.encode("utf-8") if isinstance(html, str) else html, 0)
            if t.strip():
                textos.append(t)
        texto = _limpiar("\n\n".join(textos))
        return (texto, "", "MHTML (MIME + HTML parser)") if texto else (
            "", "the web archive has no text", "")


class LectorHojaCalamine(Lector):
    """Excel con macros (.xlsm), binario (.xlsb) y OpenDocument (.ods). Las macros
    no se ejecutan: calamine solo lee los valores."""

    extensiones = ("xlsm", "xlsb", "ods")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        try:
            texto = libro_con_calamine(datos)
        except ImportError:
            return "", "python-calamine is missing (pip install python-calamine)", ""
        except Exception as error:  # noqa: BLE001
            mensaje = str(error).lower()
            if "password" in mensaje or "encrypt" in mensaje:
                return "", "the spreadsheet is password protected", ""
            return "", f"could not open the spreadsheet ({type(error).__name__})", ""
        return (texto, "", "python-calamine") if texto.strip() else (
            "", "the spreadsheet is empty", "")


# Los tipos internos de las variantes de Word. python-docx solo acepta el de un
# documento normal y rechaza la plantilla (.dotx) o la version con macros (.docm)
# aunque por dentro sean identicos. Se cambia el tipo y se lee igual.
_TIPOS_WORD = (
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    b"application/vnd.ms-word.document.macroEnabled.main+xml",
    b"application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
)
_TIPO_DOCX = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def _como_docx(datos: bytes) -> bytes:
    entrada = zipfile.ZipFile(io.BytesIO(datos))
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as nuevo:
        for item in entrada.infolist():
            contenido = entrada.read(item.filename)
            if item.filename == "[Content_Types].xml":
                for tipo in _TIPOS_WORD:
                    contenido = contenido.replace(tipo, _TIPO_DOCX)
            nuevo.writestr(item, contenido)
    return salida.getvalue()


class LectorVarianteWord(Lector):
    extensiones = ("dotx", "docm", "dotm")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        try:
            datos = _como_docx(datos)
        except zipfile.BadZipFile:
            return "", "not a Word file (not a zip)", ""
        return LectorDOCX().leer_detalle(datos, paginas)


_ODF = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


class LectorODT(Lector):
    """OpenDocument de texto (LibreOffice). Zip con content.xml: cada text:p y
    text:h es un parrafo, y las tablas van con sus celdas en orden."""

    extensiones = ("odt", "ott")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                raiz = ET.fromstring(z.read("content.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
            return "", f"could not open the OpenDocument file ({type(error).__name__})", ""
        parrafos = ["".join(p.itertext()).strip()
                    for p in raiz.iter() if p.tag in (f"{_ODF}p", f"{_ODF}h")]
        texto = _limpiar("\n".join(p for p in parrafos if p))
        return (texto, "", "OpenDocument (content.xml)") if texto else (
            "", "the OpenDocument file has no text", "")


_DRAWML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class LectorPPTX(Lector):
    """PowerPoint moderno. Zip con un XML por diapositiva: cada a:p es un parrafo.
    Se leen las diapositivas EN SU ORDEN (slide2 antes que slide10) y las notas del
    orador, que es donde a veces esta lo que se dijo."""

    extensiones = ("pptx", "pptm", "ppsx")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    @staticmethod
    def _parrafos(xml: bytes) -> list[str]:
        raiz = ET.fromstring(xml)
        return [t for t in ("".join(p.itertext()).strip()
                            for p in raiz.iter(f"{_DRAWML}p")) if t]

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        try:
            z = zipfile.ZipFile(io.BytesIO(datos))
        except zipfile.BadZipFile:
            return "", "not a PowerPoint file (not a zip)", ""

        def numero(nombre: str) -> int:
            m = re.search(r"(\d+)\.xml$", nombre)
            return int(m.group(1)) if m else 0

        with z:
            nombres = z.namelist()
            diapositivas = sorted((n for n in nombres
                                   if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=numero)
            notas = {numero(n): n for n in nombres
                     if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)}
            partes = []
            for n in diapositivas:
                try:
                    texto = "\n".join(self._parrafos(z.read(n)))
                except ET.ParseError:
                    continue
                bloque = f"--- slide {numero(n)} ---\n{texto}" if texto else ""
                if numero(n) in notas:
                    try:
                        nota = "\n".join(self._parrafos(z.read(notas[numero(n)])))
                    except ET.ParseError:
                        nota = ""
                    if nota:
                        bloque += f"\n[notes]\n{nota}"
                if bloque:
                    partes.append(bloque)
        texto = _limpiar("\n\n".join(partes))
        return (texto, "", f"PowerPoint XML ({len(diapositivas)} slides)") if texto else (
            "", "the presentation has no text", "")
