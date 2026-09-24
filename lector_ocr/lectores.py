"""Lectores: bytes de un archivo -> texto.

Punto de extension. Para soportar un formato nuevo se escribe una clase con sus
extensiones y se registra; el pipeline no cambia.

Que hay y por que, medido sobre los 315.538 archivos del arbol:

    PDF    274.545 archivos, el 87%. La mitad son escaneos sin capa de texto.
    Word    32.391 en el 94% de las carpetas, y NUNCA se habian leido. Son los
            escritos que redacta la firma (motions, letters, affidavits), asi que
            siempre traen capa de texto y suelen llevar la caratula del caso.
            De las 186 carpetas que dabamos por perdidas para OCR, 123 tienen un
            Word sin abrir.
    Correo     439 .msg. Pocos pero densisimos: un asunto trae indice, cliente y
            contraparte de una sola vez.
    Excel    4.257. Valor bajo para identificar: son planillas financieras del
            paquete de loss mitigation y casi ninguna menciona el caso.

El OCR queda para el final a proposito: es el unico que cuesta tiempo de verdad, y
conviene medir cuanto queda ciego DESPUES de leer Word antes de montarlo.
"""

from __future__ import annotations

import io
import re
import zipfile
from abc import ABC, abstractmethod
from html import unescape
from html.parser import HTMLParser

import pypdf

from . import ocr


class Lector(ABC):
    """Convierte el contenido de un archivo en texto plano."""

    extensiones: tuple[str, ...] = ()

    @abstractmethod
    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        """Devuelve (texto, motivo_de_fallo). Si hay texto, el motivo va vacio."""


class LectorPDF(Lector):
    """PDFs, en tres intentos que van de lo barato a lo caro.

        1. pypdf     milisegundos. Resuelve la mayoria de los PDF del arbol.
        2. PyMuPDF   tambien instantaneo, y abre archivos que pypdf no sabe abrir:
                     fuentes raras, PDF mal construidos, formularios.
        3. OCR       uno a tres segundos por pagina. Solo si los dos anteriores
                     devolvieron vacio, porque entonces no hay capa de texto y el
                     papel es, literalmente, una foto.

    El orden no es preferencia: el tercer paso cuesta mil veces mas que el primero,
    y hacerlo sin haber descartado los otros dos serian horas tiradas.
    """

    extensiones = ("pdf",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        """Como `leer`, y ademas CUAL de los escalones dio el texto.

        Saberlo importa para revisar: un texto que viene de la capa nativa es fiel,
        y uno que viene del OCR puede traer erratas -- 'Marc Mouzon' leido como
        'Mare Mouzon' nos paso. Quien mire el resultado tiene que poder distinguir
        los dos casos sin abrir el PDF.

        LA CONDICION PARA BAJAR AL SIGUIENTE ESCALON NO ES 'vino vacio' SINO 'vino
        algo que no parece texto'. Con la condicion vieja, un escaneo de 23 MB
        cuyo unico texto son los sellos Bates se daba por leido con 38 caracteres,
        y un reporte de credito con fuentes sin mapa de caracteres se daba por
        leido con indices de glifo. El OCR no llegaba a dispararse nunca.
        """
        leidas = paginas
        try:
            lector = pypdf.PdfReader(io.BytesIO(datos))
            hojas = lector.pages if paginas <= 0 else lector.pages[:paginas]
            leidas = len(hojas)
            texto = "\n".join(p.extract_text() or "" for p in hojas)
        except Exception:
            texto = ""  # que no abra con pypdf no quiere decir que no abra con nada

        hace_falta, motivo_ocr = ocr.necesita_ocr(texto, leidas)
        if not hace_falta:
            return texto, "", "pypdf (text layer)"

        del_otro = ocr.texto_sin_ocr(datos, paginas)
        if del_otro.strip() and not ocr.necesita_ocr(del_otro, leidas)[0]:
            return del_otro, "", "PyMuPDF (text layer)"

        # Un portfolio no se rasteriza: sus paginas no tienen nada. El contenido
        # son los adjuntos, y cada uno es un archivo con su propio formato.
        adjuntos = ocr.adjuntos_de_portfolio(datos)
        if adjuntos:
            partes, leidos = [], []
            for nombre, contenido in adjuntos:
                extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
                suyo, _, con_que = leer_con_detalle(extension, contenido, paginas)
                if suyo.strip():
                    partes.append(f"--- {nombre} ---\n{suyo}")
                    leidos.append(con_que)
            if partes:
                usados = ", ".join(sorted({c for c in leidos if c}))
                return ("\n\n".join(partes), "",
                        f"PDF portfolio: {len(partes)} of {len(adjuntos)} attachments"
                        + (f" via {usados}" if usados else ""))
            return "", f"PDF portfolio with {len(adjuntos)} attachments, none readable", ""

        texto_ocr, motivo, con_que = ocr.texto_de_pdf(datos, paginas)
        if texto_ocr.strip():
            # El nombre del motor lo dice ocr.py, no se escribe aqui: con dos
            # motores disponibles, escribirlo a mano seria mentir la mitad de las
            # veces.
            return texto_ocr, "", f"{con_que} (rasterised pages)"

        # Ni la capa de texto servia ni el OCR pudo. Se devuelve por que se
        # intento el OCR, que explica mas que 'unreadable' a secas.
        return "", motivo or motivo_ocr, ""


class LectorImagen(Lector):
    """Fotos y escaneos sueltos. Sin OCR aqui no hay nada que leer.

    Estan en las carpetas de clientes como prueba: fotos del inmueble, capturas de
    pantalla, documentos de identidad fotografiados. Antes ni siquiera aparecian en
    la lista de candidatos, asi que se quedaban fuera sin que nadie se enterara.
    """

    extensiones = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif", "webp")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = ocr.texto_de_imagen(datos)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        texto, motivo, con_que = ocr.texto_de_imagen(datos)
        return texto, motivo, f"{con_que} (image)" if con_que else ""


# Lo que Word guarda en word/media/. El wmf y el emf son vectoriales de Windows y
# Pillow no los abre, asi que no se intentan.
IMAGENES_EN_WORD = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif", "webp"})

# Un Word con cien capturas pegadas existe, y cien OCR son varios minutos por un
# solo archivo. Con las primeras se sabe de que va.
IMAGENES_MAXIMAS = 25


class LectorDOCX(Lector):
    """Word moderno. Es un zip con XML dentro: se lee entero y al instante.

    No existe el concepto de pagina en un .docx, asi que `paginas` se traduce a un
    tope de parrafos. Con los primeros ~40 alcanza: la caratula de un escrito va
    siempre al principio.
    """

    extensiones = ("docx",)
    PARRAFOS_POR_PAGINA = 14

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    @staticmethod
    def _en_orden(documento, tope: int):
        """Parrafos y tablas EN EL ORDEN EN QUE ESTAN en el documento.

        python-docx expone `paragraphs` y `tables` como dos listas separadas, asi
        que recorrer una y luego la otra deja las tablas al final, esten donde
        esten. Y en un escrito judicial la caratula --las partes, el numero de caso
        y el TITULO del documento-- va en una tabla, arriba del todo.

        Eso no perdia texto, lo desordenaba, que aqui es casi peor: la escalera de
        ruteo.py mira el ENCABEZADO del contenido y directorio.py se queda con los
        primeros 2.500 caracteres como caratula. Con el titulo empujado al ultimo
        renglon, 'Notice of Appearance_DZ.docx' se clasificaba como
        'Affidavit of service' -- porque lo primero que aparecia era el certificado
        de servicio del final -- mientras el mismo documento en .pdf salia bien.

        Se recorre el cuerpo del XML hijo a hijo, que es el unico sitio donde el
        orden real esta escrito.
        """
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        vistos = 0
        for hijo in documento.element.body.iterchildren():
            if vistos >= tope:
                return
            if hijo.tag.endswith("}p"):
                parrafo = Paragraph(hijo, documento).text
                if parrafo.strip():
                    vistos += 1
                    yield parrafo
            elif hijo.tag.endswith("}tbl"):
                # En una caratula las celdas van combinadas, y python-docx devuelve
                # la misma celda una vez por cada columna que ocupa. Sin descartar
                # las repetidas, el nombre de las partes sale tres veces.
                #
                # Se guarda el ELEMENTO, no su id(). Con id() esto se tragaba celdas
                # buenas: el objeto es temporal, Python lo libera y le da el mismo
                # numero al siguiente, asi que una celda nueva parecia ya vista. Con
                # el elemento dentro del set la referencia se mantiene viva y la
                # comparacion es de identidad de verdad.
                puestas = set()
                for fila in Table(hijo, documento).rows:
                    for celda in fila.cells:
                        if celda._tc in puestas or not celda.text.strip():
                            continue
                        puestas.add(celda._tc)
                        yield celda.text

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        """(texto, motivo, con que se leyo). Un .docx tiene tres sitios donde mirar.

        Decir cual respondio no es adorno: si el texto salio de OCR de una captura
        pegada dentro del Word, puede traer erratas, y quien lo revise tiene que
        saberlo sin abrir el archivo. Es la misma razon que en el PDF.
        """
        try:
            import docx
        except ImportError:
            return "", "python-docx is missing", ""

        # Si python-docx no puede abrirlo NO se termina aqui. Las otras dos rutas
        # --el XML crudo y las imagenes de dentro-- son un zip y una regex, y no
        # dependen de python-docx para nada, asi que siguen estando disponibles.
        # Cortar la cascada en el primer escalon dejaba archivos sin leer teniendo
        # dos lectores validos detras: 'TU's RFPs to P(8530912.1).docx' salia como
        # 'could not open the Word file (KeyError)' mientras su gemelo en .pdf se
        # leia entero.
        documento, fallo = None, ""
        try:
            documento = docx.Document(io.BytesIO(datos))
        except Exception as error:
            fallo = f"python-docx could not open it ({type(error).__name__})"

        texto = ""
        if documento is not None:
            tope = max(paginas, 1) * self.PARRAFOS_POR_PAGINA
            texto = "\n".join(self._en_orden(documento, tope))
        if texto.strip() and not ocr.necesita_ocr(texto, max(paginas, 1))[0]:
            return texto, "", "python-docx"

        # python-docx solo ve parrafos y tablas. Lo que va en un CUADRO DE TEXTO no
        # lo ve, y ahi es donde algunos escritos meten el cuerpo entero: el borrador
        # del informe pericial de este matter pesa 5,6 MB y daba 430 caracteres, el
        # encabezado y nada mas. Un .docx es un zip con XML dentro, asi que se abre
        # a mano y se saca el texto de todas partes.
        con_que = "python-docx"
        del_xml = self._del_xml(datos)
        if del_xml.strip() and len(del_xml) > len(texto):
            texto, con_que = del_xml, "python-docx (raw XML: text boxes included)"
        if texto.strip() and not ocr.necesita_ocr(texto, max(paginas, 1))[0]:
            return texto, "", con_que

        # Y si sigue sin haber texto, puede que el Word no CONTENGA texto: que sea
        # un sobre con capturas de pantalla pegadas dentro. 'Texts.docx'
        # pesa 3,67 MB y no tiene una letra -- son los mensajes del cliente, en
        # imagenes. El XML no las ve porque no son XML: son PNG dentro del zip.
        de_las_imagenes, cuantas, quien = self._de_las_imagenes(datos, paginas)
        if de_las_imagenes.strip():
            # Quien leyo las imagenes lo dice ocr.py. Estaba escrito a mano como
            # 'Tesseract OCR' y con dos motores eso pasó a ser falso: en la corrida
            # con --ocr paddle, 'Texts.docx' lo leyo Paddle y el informe
            # seguia firmando Tesseract.
            marca = f"{quien} ({cuantas} image(s) inside the Word file)"
            if texto.strip():
                return f"{texto}\n\n{de_las_imagenes}", "", f"{con_que} + {marca}"
            return de_las_imagenes, "", marca

        if texto.strip():
            return texto, "", con_que
        # Si se llego hasta aqui con las tres rutas agotadas, el motivo tiene que
        # decir POR QUE, y si python-docx fue el que no pudo abrirlo, eso se dice:
        # un 'no tiene texto' sobre un archivo que ni se pudo abrir es enganoso.
        if fallo:
            return "", f"{fallo}, and neither the raw XML nor its images had text", ""
        return "", "the Word file has no text and its images have none either", ""

    @staticmethod
    def _de_las_imagenes(datos: bytes, paginas: int) -> tuple[str, int, str]:
        """(texto, cuantas imagenes dieron texto, con que motor se leyeron).

        Va detras de todo lo demas porque cuesta lo que cuesta el OCR, y solo tiene
        sentido cuando el documento ya demostro no tener texto propio.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as paquete:
                medios = [n for n in paquete.namelist()
                          if n.startswith("word/media/")
                          and n.rsplit(".", 1)[-1].lower() in IMAGENES_EN_WORD]
                if not medios:
                    return "", 0, ""
                partes = []
                motores = []
                for nombre in medios[:IMAGENES_MAXIMAS]:
                    # Sin minimo a proposito: la clave de una produccion
                    # documental vive en una imagen dentro de un Word y son once
                    # caracteres. El filtro de ruido esta para las fotos sueltas.
                    suyo, _, con_que = ocr.texto_de_imagen(paquete.read(nombre),
                                                           minimo=0)
                    if suyo.strip():
                        partes.append(suyo)
                        if con_que:
                            motores.append(con_que.split(" (")[0])
        except Exception:  # noqa: BLE001 - si el zip no abre, no hay imagenes que leer
            return "", 0, ""
        return ("\n\n".join(partes), len(partes),
                " + ".join(sorted(set(motores))) or "OCR")

    @staticmethod
    def _del_xml(datos: bytes) -> str:
        """El texto de las partes XML del .docx, cuadros de texto incluidos."""
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as paquete:
                piezas = [n for n in paquete.namelist()
                          if n.startswith("word/") and n.endswith(".xml")
                          and "rels" not in n and "theme" not in n]
                crudo = "\n".join(paquete.read(n).decode("utf-8", "ignore")
                                  for n in piezas[:20])
        except Exception:  # noqa: BLE001 - si no es un zip valido, no hay nada que sacar
            return ""

        # <w:t> lleva el texto; <w:p> y <w:br> son los saltos. El resto es formato.
        crudo = re.sub(r"</w:(p|br|tab)[^>]*>", "\n", crudo)
        crudo = re.sub(r"<[^>]+>", "", crudo)
        crudo = unescape(crudo)
        return re.sub(r"\n{3,}", "\n\n", crudo).strip()


class LectorMSG(Lector):
    """Correo de Outlook. El asunto y los destinatarios valen mas que el cuerpo."""

    extensiones = ("msg",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        try:
            import extract_msg
        except ImportError:
            return "", "extract-msg is missing"

        try:
            mensaje = extract_msg.openMsg(io.BytesIO(datos))
        except Exception as error:
            return "", f"could not open the email ({type(error).__name__})"

        try:
            cabecera = [
                mensaje.subject or "",
                mensaje.sender or "",
                mensaje.to or "",
                mensaje.cc or "",
            ]
            cuerpo = (mensaje.body or "")[: max(paginas, 1) * 3000]
        except Exception as error:
            return "", f"could not read the email ({type(error).__name__})"
        finally:
            try:
                mensaje.close()
            except Exception:
                pass

        texto = "\n".join([c for c in cabecera if c] + [cuerpo])
        if not texto.strip():
            return "", "the email is empty"
        return texto, ""


class LectorXLSX(Lector):
    """Excel moderno. Valor bajo para identificar, pero sale casi gratis.

    Se leen solo las primeras filas de cada hoja: si el nombre del cliente esta,
    esta arriba. Recorrer una planilla entera de transacciones no aporta nada.
    """

    extensiones = ("xlsx",)
    FILAS = 40

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        try:
            import openpyxl
        except ImportError:
            return "", "openpyxl is missing"

        try:
            libro = openpyxl.load_workbook(
                io.BytesIO(datos), read_only=True, data_only=True
            )
        except Exception as error:
            return "", f"could not open the Excel file ({type(error).__name__})"

        partes = []
        try:
            for hoja in libro.worksheets[:3]:
                partes.append(str(hoja.title))
                for fila in hoja.iter_rows(max_row=self.FILAS, values_only=True):
                    celdas = [str(c) for c in fila if c not in (None, "")]
                    if celdas:
                        partes.append(" ".join(celdas))
        except Exception as error:
            return "", f"could not read the Excel file ({type(error).__name__})"
        finally:
            libro.close()

        texto = "\n".join(partes)
        if not texto.strip():
            return "", "the Excel file is empty"
        return texto, ""


class LectorTexto(Lector):
    """Texto plano. No hay nada que extraer: ya es texto."""

    extensiones = ("txt", "md", "log", "csv")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        for codificacion in ("utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                texto = datos.decode(codificacion)
            except (UnicodeDecodeError, UnicodeError):
                continue
            return (texto, "") if texto.strip() else ("", "the text file is empty")
        return "", "could not decode the text file"


class _SinEtiquetas(HTMLParser):
    """Se queda con el texto y tira el marcado. Los reportes de credito que las
    agencias entregan en HTML son tablas enormes: sin quitar las etiquetas, el
    texto sale ahogado en atributos de estilo."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.mudo = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.mudo = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.mudo = False
        elif tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "td", "th"):
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.mudo and data.strip():
            self.partes.append(data.strip())


class LectorHTML(Lector):
    """Paginas guardadas. Los reportes de credito llegan asi bastante a menudo."""

    extensiones = ("html", "htm", "mhtml")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        crudo, _ = LectorTexto().leer(datos, paginas)
        if not crudo:
            return "", "could not decode the HTML file"
        analizador = _SinEtiquetas()
        try:
            analizador.feed(crudo)
        except Exception as error:  # noqa: BLE001
            return "", f"could not parse the HTML ({type(error).__name__})"
        texto = re.sub(r"\n{3,}", "\n\n", " ".join(analizador.partes))
        return (texto, "") if texto.strip() else ("", "the HTML page has no text")


# Un zip de una produccion documental trae cientos de paginas. Los topes no son
# desconfianza: un zip puede venir inflado a proposito, y aunque no lo este, leer
# mil entradas de una carpeta bloquea la corrida entera por un solo archivo.
ENTRADAS_MAXIMAS = 200
DESCOMPRIMIDO_MAXIMO = 500 * 2**20  # 500 MB

CONTRASENAS: list[bytes] = []

# La parte que produce los documentos cifra el zip y deja la clave al lado, en un
# archivo suelto. Es la practica normal en discovery, asi que buscarla ahi no es
# adivinar: es leer lo que el que lo mando dejo puesto para que se leyera.
# '\bpass\b' esta porque el archivo de este matter se llama 'Yamaha production
# pass.docx' -- no 'password'. Buscando solo la palabra completa, el archivo que
# traia la clave no se miraba siquiera.
ARCHIVO_DE_CLAVE = re.compile(
    r"password|passcode|\bpass\b|\bpwd\b|clave|contrase", re.I)

# Como aparece dentro de ese archivo. Las dos primeras formas cubren casi todo;
# la tercera es el caso de un archivo que SOLO tiene la clave y nada mas.
CLAVE_ETIQUETADA = re.compile(
    r"(?:passwords?|passcodes?|pass|pwd|clave|contrase\w*)\s*(?:is|es|:|=)+\s*"
    r"[\"'“]?([^\s\"'”]{4,60})", re.I)


def claves_en_texto(texto: str) -> list[str]:
    """Las contrasenas que parece contener un archivo, de la mas fiable a la menos.

    No se intenta ser listo: se prueban todas contra el zip y la que abra, abre.
    Una clave de mas no cuesta nada; una de menos deja 263 MB sin leer.
    """
    candidatas: list[str] = []
    for m in CLAVE_ETIQUETADA.finditer(texto):
        valor = m.group(1).strip(".,;:")
        if valor and valor.lower() not in ("is", "es", "the") and valor not in candidatas:
            candidatas.append(valor)

    # Un archivo de una sola linea corta es la clave, sin mas. Solo si no se
    # reconocio ya una etiquetada: si la linea dice 'Password: X', la clave es X y
    # no la frase entera.
    if not candidatas:
        lineas = [x.strip() for x in texto.splitlines() if x.strip()]
        if len(lineas) == 1 and 4 <= len(lineas[0]) <= 60:
            candidatas.append(lineas[0])
    return candidatas


def parece_archivo_de_clave(nombre: str) -> bool:
    return bool(ARCHIVO_DE_CLAVE.search(str(nombre)))


class LectorZIP(Lector):
    """Un zip no es un documento, es un sobre.

    Se hace lo mismo que con los PDF Portfolio: sacar lo que hay dentro y mandar
    cada cosa a SU lector. Un PDF de la produccion entra por la cascada de PDF y
    acaba en Tesseract si hace falta; un Word por python-docx. Aqui no se decide
    nada sobre el contenido, solo se abre el sobre.

    Nada se escribe en disco: las entradas se leen a memoria una por una.
    """

    extensiones = ("zip",)

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int, anidado: bool = False,
                     extras: dict | None = None) -> tuple[str, str, str]:
        """El zip entero. En `extras` deja la lista de lo que hay dentro.

        Esa lista no es un lujo. Un zip de 49 MB del que solo se saco un PDF se
        estaba contando como 49 MB leidos, y los otros 11 MB -- diecisiete
        grabaciones de llamadas al cliente -- desaparecian del informe. Un
        contenedor tiene que contar por lo que se le saco, no por lo que pesa.
        """
        try:
            sobre = zipfile.ZipFile(io.BytesIO(datos))
        except Exception as error:  # noqa: BLE001
            return "", f"could not open the zip ({type(error).__name__})", ""

        dentro: list[dict] = []
        with sobre:
            entradas = [e for e in sobre.infolist() if not e.is_dir()]
            if not entradas:
                return "", "the zip is empty", ""
            cifrado = any(e.flag_bits & 0x1 for e in entradas)

            def anotar(entrada, leida: bool, motivo: str, con_que: str = "") -> None:
                dentro.append({"name": entrada.filename,
                               "size_mb": round(entrada.file_size / 2**20, 2),
                               "was_read": leida,
                               "read_with": con_que or None,
                               "not_read_because": motivo or None})

            partes, leidos, saltados, gastado = [], [], 0, 0
            for entrada in entradas[:ENTRADAS_MAXIMAS]:
                extension = (entrada.filename.rsplit(".", 1)[-1].lower()
                             if "." in entrada.filename else "")
                if extension == "zip" and anidado:
                    saltados += 1
                    anotar(entrada, False, "nested zip: only one level is opened")
                    continue
                if gastado + entrada.file_size > DESCOMPRIMIDO_MAXIMO:
                    saltados += 1
                    anotar(entrada, False, "over the uncompressed size cap")
                    continue
                try:
                    contenido = self._abrir(sobre, entrada)
                except Exception:  # noqa: BLE001 - entrada rota o clave mala
                    saltados += 1
                    anotar(entrada, False, "wrong password or broken entry")
                    continue
                gastado += len(contenido)

                if extension == "zip":
                    suyo, motivo, con_que = self.leer_detalle(contenido, paginas,
                                                              anidado=True)
                else:
                    suyo, motivo, con_que = leer_con_detalle(extension, contenido, paginas)
                if suyo.strip():
                    partes.append(f"--- {entrada.filename} ---\n{suyo}")
                    leidos.append(con_que)
                    anotar(entrada, True, "", con_que)
                else:
                    saltados += 1
                    anotar(entrada, False, motivo or "no text")

            if len(entradas) > ENTRADAS_MAXIMAS:
                for entrada in entradas[ENTRADAS_MAXIMAS:]:
                    saltados += 1
                    anotar(entrada, False, f"over the cap of {ENTRADAS_MAXIMAS} entries")

        if extras is not None:
            # Se dan los dos totales, no el aprovechado a secas. Dentro se mide sin
            # comprimir y el contenedor pesa comprimido, asi que restar uno del otro
            # da negativos: un zip de 29 MB con 35 MB de PDFs dentro 'aprovechaba'
            # mas de lo que pesaba. Con los dos, quien cuente saca la PROPORCION y
            # la aplica al tamano real del archivo, que es lo unico que suma.
            extras["archive_contents"] = dentro
            extras["mb_inside_read"] = round(
                sum(e["size_mb"] for e in dentro if e["was_read"]), 2)
            extras["mb_inside_total"] = round(sum(e["size_mb"] for e in dentro), 2)

        if not partes:
            if cifrado:
                # Se dice CUANTAS claves se probaron, no solo que no se pudo. Sin
                # ese numero no se distingue 'no habia ninguna clave a mano' de
                # 'habia tres y ninguna era', y son dos problemas distintos: el
                # primero lo resuelve buscar el archivo de la clave y el segundo
                # pedirsela a quien produjo los documentos.
                probadas = (f"{len(CONTRASENAS)} password(s) from the folder were tried"
                            if CONTRASENAS else "no password was found in the folder")
                return "", (f"the zip is password protected ({len(entradas)} entries) "
                            f"and {probadas}: pass one with --password"), ""
            return "", f"zip with {len(entradas)} entries, none readable", ""

        usados = ", ".join(sorted({c for c in leidos if c}))
        detalle = f"ZIP: {len(partes)} of {len(entradas)} entries"
        if saltados:
            detalle += f", {saltados} skipped"
        return "\n\n".join(partes), "", detalle + (f" via {usados}" if usados else "")

    @staticmethod
    def _abrir(sobre: zipfile.ZipFile, entrada: zipfile.ZipInfo) -> bytes:
        """Lee una entrada, probando las contrasenas que nos hayan dado."""
        if not entrada.flag_bits & 0x1:
            return sobre.read(entrada)
        for clave in CONTRASENAS:
            try:
                return sobre.read(entrada, pwd=clave)
            except (RuntimeError, zipfile.BadZipFile):
                continue
        raise RuntimeError("wrong password")


REGISTRO: dict[str, Lector] = {}


def registrar(lector: Lector) -> None:
    for extension in lector.extensiones:
        REGISTRO[extension] = lector


for _lector in (LectorPDF(), LectorDOCX(), LectorMSG(), LectorXLSX(), LectorImagen(),
                LectorTexto(), LectorHTML(), LectorZIP()):
    registrar(_lector)


def lector_para(extension: str) -> Lector | None:
    return REGISTRO.get(extension.lower())


# Con que se lee cada formato. Solo los que tienen UN lector fijo.
#
# Ni el PDF ni las imagenes estan aqui, y por el mismo motivo: su lector depende de
# lo que pasara al leerlos. El PDF puede responder en cualquiera de sus escalones, y
# una imagen la lee Tesseract o PaddleOCR segun el motor elegido y segun quien
# consiga sacar algo. Escribir el nombre aqui seria adivinarlo, y este campo existe
# precisamente para no tener que adivinar.
COMO_SE_LEE = {
    "docx": "python-docx", "msg": "extract-msg", "xlsx": "openpyxl",
    "txt": "plain text", "md": "plain text", "log": "plain text", "csv": "plain text",
    "html": "HTML parser", "htm": "HTML parser", "mhtml": "HTML parser",
}


def leer_con_detalle(extension: str, datos: bytes, paginas: int,
                     extras: dict | None = None) -> tuple[str, str, str]:
    """(texto, motivo_de_fallo, con_que_se_leyo). Un solo sitio que lo sepa.

    Existe para que quien quiera ese detalle no tenga que repetir por su cuenta la
    cascada del PDF: dos copias de esa logica acabarian discrepando, y entonces el
    informe diria que algo se leyo con pypdf cuando lo leyo el OCR.

    `extras` es un diccionario que el lector rellena si tiene algo que contar que no
    cabe en una frase. Hoy lo usa el zip para decir QUE trae dentro y que se leyo de
    cada cosa, porque sin eso un contenedor de 49 MB del que solo se saco un PDF se
    cuenta entero como leido -- que es justo el error que este informe perseguia.
    """
    lector = lector_para(extension)
    if lector is None:
        return "", f"no reader for .{extension or '(no extension)'}", ""
    if hasattr(lector, "leer_detalle"):
        if "extras" in lector.leer_detalle.__code__.co_varnames:
            return lector.leer_detalle(datos, paginas, extras=extras)
        return lector.leer_detalle(datos, paginas)
    texto, motivo = lector.leer(datos, paginas)
    return texto, motivo, (COMO_SE_LEE.get(extension.lower(), "") if texto.strip() else "")


def extensiones_soportadas() -> tuple[str, ...]:
    return tuple(sorted(REGISTRO))
