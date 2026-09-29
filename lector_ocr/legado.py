r"""Los formatos viejos de Office: .xls (Excel 97-2003) y .doc (Word 97-2003).

POR QUE HACEN FALTA. En Matters hay unos 5.000 entre los dos y hasta ahora salian
'no reader for .xls / .doc'. Ese motivo es PERMANENTE (no se reintenta), asi que
sin estos lectores esos archivos no se leian nunca.

TIENEN QUE FUNCIONAR EN UN SERVIDOR LINUX. Nada de Word, Excel ni COM:

    .xls   python-calamine   (Rust, ruedas para Linux / Windows / Mac)
    .doc   olefile + un lector propio del formato binario de Word, en Python puro
           y, SI ESTA INSTALADO, LibreOffice sin pantalla como respaldo

MUCHOS NO SON LO QUE DICE SU EXTENSION. Un portal exporta 'reporte.xls' y es una
tabla HTML; un escrito guardado como 'carta.doc' es un RTF; alguien renombra un
.docx a .doc. Por eso lo primero es mirar los primeros bytes y mandarlo al lector
que corresponde. Leerlos todos como binario de Office los daba por ilegibles.
"""

from __future__ import annotations

import io
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from .lectores import Lector, LectorDOCX, LectorHTML, LectorTexto, LectorXLSX

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # contenedor OLE2: .doc, .xls, .msg...
ZIP = b"PK\x03\x04"                          # .docx / .xlsx


def que_es(datos: bytes) -> str:
    """Lo que el archivo ES, por sus primeros bytes, diga lo que diga su nombre."""
    cabeza = datos[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if datos.startswith(OLE):
        return "ole"
    if datos.startswith(ZIP):
        return "zip"
    if cabeza.startswith(b"{\\rtf"):
        return "rtf"
    minus = cabeza[:600].lower()
    if b"urn:schemas-microsoft-com:office:spreadsheet" in datos[:4000].lower():
        return "xml_excel"
    if minus.startswith((b"<!doctype html", b"<html", b"<table", b"<meta")) or b"<html" in minus:
        return "html"
    if datos.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "texto"
    # Texto plano disfrazado (CSV o tabulado exportado como .xls): casi todo
    # imprimible y sin bytes nulos.
    muestra = datos[:4000]
    if muestra and b"\x00" not in muestra:
        imprimibles = sum(1 for b in muestra if b in b"\t\r\n" or 32 <= b < 127 or b >= 128)
        if imprimibles / len(muestra) > 0.97:
            return "texto"
    return "desconocido"


# ----------------------------------------------------------------------- RTF

_RTF_DESTINOS_FUERA = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "headerl", "headerr", "footerl", "footerr", "listtable",
    "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "themedata",
    "colorschememapping", "latentstyles", "datastore", "filetbl", "revtbl",
}
_RTF_TOKEN = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|([^\\{}\r\n]+)", re.I)


def texto_de_rtf(datos: bytes) -> str:
    """El texto de un RTF, sin librerias. Solo lo necesario para leerlo."""
    crudo = datos.decode("latin-1", "ignore")
    pila, fuera, saltar_uc = [], False, 0
    salida: list[str] = []
    uc = 1
    for m in _RTF_TOKEN.finditer(crudo):
        palabra, num, hexa, simbolo, llave, texto = m.groups()
        if llave == "{":
            pila.append((fuera, uc))
            continue
        if llave == "}":
            fuera, uc = pila.pop() if pila else (False, 1)
            saltar_uc = 0            # el salto pendiente no cruza el fin de grupo
            continue
        if palabra:
            p = palabra.lower()
            if p in _RTF_DESTINOS_FUERA:
                fuera = True
            elif p in ("par", "line", "row", "sect", "page"):
                if not fuera:
                    salida.append("\n")
            elif p in ("tab", "cell"):
                if not fuera:
                    salida.append("\t")
            elif p == "uc" and num:
                uc = int(num)
            elif p == "u" and num:
                if not fuera:
                    valor = int(num)
                    salida.append(chr(valor + 65536 if valor < 0 else valor))
                saltar_uc = uc
            continue
        if simbolo:
            if simbolo == "*":
                fuera = True          # destino opcional que no se entiende: fuera
            elif simbolo in "\\{}" and not fuera:
                salida.append(simbolo)
            elif simbolo == "~" and not fuera:
                salida.append(" ")
            continue
        # El salto de \uN (los caracteres de reserva) se consume SIEMPRE, tambien
        # dentro de lo que se descarta: si no, un \u de la tabla de fuentes se
        # comia la primera letra del documento ('SUPREME' salia 'UPREME').
        if hexa:
            if saltar_uc:
                saltar_uc -= 1
            elif not fuera:
                salida.append(bytes([int(hexa, 16)]).decode("cp1252", "replace"))
            continue
        if texto:
            if saltar_uc:
                quitar = min(saltar_uc, len(texto))
                texto, saltar_uc = texto[quitar:], saltar_uc - quitar
            if not fuera:
                salida.append(texto)
    return _limpiar("".join(salida))


def _limpiar(texto: str) -> str:
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


# ------------------------------------------------------------ LibreOffice

def _soffice() -> str:
    for nombre in ("soffice", "libreoffice"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    for ruta in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(ruta).exists():
            return ruta
    return ""


def con_libreoffice(datos: bytes, extension: str, a: str) -> bytes | None:
    """Convierte con LibreOffice sin pantalla, SI esta instalado. None si no.

    Es el respaldo, no la via principal: pesa y tarda, pero lee lo que el lector
    propio no (Word 6/95, archivos a medio romper). En el servidor Linux se
    instala con `apt install libreoffice-core libreoffice-writer libreoffice-calc`.
    """
    soffice = _soffice()
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as carpeta:
        entrada = Path(carpeta) / f"entrada.{extension}"
        entrada.write_bytes(datos)
        # Perfil propio: dos conversiones a la vez con el perfil comun se bloquean.
        perfil = Path(carpeta, "perfil").as_uri()
        try:
            subprocess.run([soffice, f"-env:UserInstallation={perfil}", "--headless",
                            "--convert-to", a, "--outdir", carpeta, str(entrada)],
                           capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        salida = Path(carpeta) / f"entrada.{a.split(':')[0]}"
        return salida.read_bytes() if salida.exists() else None


# ------------------------------------------------------------------ .doc

class LectorDOC(Lector):
    """Word 97-2003 binario. Lector propio: olefile abre el contenedor y el texto
    se reconstruye desde la tabla de piezas, como hace el propio Word.

    El formato (MS-DOC) guarda el texto en trozos ('piezas') que pueden estar en
    cp1252 o en UTF-16, en cualquier orden. La tabla que dice donde esta cada uno
    va en el stream '0Table' o '1Table' (lo dice un bit de la cabecera). Leer el
    stream 'WordDocument' de corrido, que es lo facil, mezcla texto con formato y
    se salta lo que Word guardo al editar.
    """

    extensiones = ("doc", "dot")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        tipo = que_es(datos)
        if tipo == "zip":                       # un .docx renombrado
            t, m, c = LectorDOCX().leer_detalle(datos, paginas)
            return t, m, f"{c} (.docx named .doc)" if c else ""
        if tipo == "rtf":
            texto = texto_de_rtf(datos)
            return (texto, "", "RTF parser (.rtf named .doc)") if texto else (
                "", "RTF file with no text", "")
        if tipo == "html":
            t, m = LectorHTML().leer(datos, paginas)
            return t, m, "HTML parser (.html named .doc)" if t else ""
        if tipo == "texto":
            t, m = LectorTexto().leer(datos, paginas)
            return t, m, "plain text (named .doc)" if t else ""
        if tipo != "ole":
            return self._respaldo(datos, "not a Word 97-2003 file")

        try:
            texto, motivo = self._de_las_piezas(datos)
        except Exception as error:  # noqa: BLE001
            texto, motivo = "", f"could not parse the Word file ({type(error).__name__})"
        if texto.strip():
            return texto, "", "olefile (Word 97-2003)"
        return self._respaldo(datos, motivo or "the Word file has no text")

    def _respaldo(self, datos: bytes, motivo: str) -> tuple[str, str, str]:
        """LibreOffice si esta; si no, el motivo por el que no se pudo."""
        if "password" in motivo:
            return "", motivo, ""         # cifrado: LibreOffice tampoco lo abre
        convertido = con_libreoffice(datos, "doc", "docx")
        if convertido:
            t, m, c = LectorDOCX().leer_detalle(convertido, 0)
            if t.strip():
                return t, "", f"LibreOffice -> {c}"
        return "", motivo, ""

    @staticmethod
    def _de_las_piezas(datos: bytes) -> tuple[str, str]:
        import olefile

        with olefile.OleFileIO(io.BytesIO(datos)) as ole:
            if not ole.exists("WordDocument"):
                return "", "OLE file without a WordDocument stream (not a Word file)"
            wd = ole.openstream("WordDocument").read()
            if len(wd) < 0x1AA:
                return "", "Word file header too short"
            ident, nfib = struct.unpack_from("<HH", wd, 0)
            if ident != 0xA5EC:
                return "", "not a Word document (bad signature)"
            banderas = struct.unpack_from("<H", wd, 0x0A)[0]
            if banderas & 0x0100:
                return "", "the Word file is password protected"
            if nfib < 101:
                # Word 6/95: otra cabecera y sin tabla de piezas en el mismo sitio.
                return "", "Word 6/95 file (older than Word 97)"
            tabla = "1Table" if banderas & 0x0200 else "0Table"
            if not ole.exists(tabla):
                return "", f"missing {tabla} stream"
            tbl = ole.openstream(tabla).read()
            fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
            if not lcb_clx or fc_clx + lcb_clx > len(tbl):
                return "", "no piece table in the Word file"
            clx = tbl[fc_clx:fc_clx + lcb_clx]

        # Clx = [Prc]* Pcdt. Los Prc (0x01) son formato: se saltan.
        i = 0
        while i < len(clx) and clx[i] == 0x01:
            i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
        if i >= len(clx) or clx[i] != 0x02:
            return "", "malformed piece table"
        lcb = struct.unpack_from("<I", clx, i + 1)[0]
        plc = clx[i + 5:i + 5 + lcb]
        n = (lcb - 4) // 12
        cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
        partes = []
        for k in range(n):
            fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
            largo = cps[k + 1] - cps[k]
            if largo <= 0:
                continue
            if fc & 0x40000000:                     # comprimido: 1 byte por letra
                ini = (fc & 0x3FFFFFFF) // 2
                partes.append(wd[ini:ini + largo].decode("cp1252", "replace"))
            else:                                   # UTF-16LE
                partes.append(wd[fc:fc + 2 * largo].decode("utf-16-le", "replace"))
        return _texto_de_word("".join(partes)), ""


_CAMPO = re.compile(r"\x13[^\x13\x14\x15]*\x14?([^\x13\x15]*)\x15")


def _texto_de_word(crudo: str) -> str:
    """Los caracteres de control de Word, a texto normal.

    \\r parrafo, \\x0b salto de linea, \\x0c salto de pagina, \\x07 fin de celda.
    Los CAMPOS (\\x13 codigo \\x14 resultado \\x15) se quedan con el resultado:
    'PAGE' o 'HYPERLINK "http..."' no es texto del documento, el numero y el
    texto del enlace si. Se repite porque los campos se anidan.
    """
    anterior = None
    while anterior != crudo:
        anterior, crudo = crudo, _CAMPO.sub(r"\1", crudo)
    crudo = (crudo.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
             .replace("\x07", "\t").replace("\x1e", "-").replace("\x1f", "")
             .replace("\xa0", " "))
    crudo = re.sub(r"[\x00-\x08\x0e-\x12\x13\x14\x15\x16-\x1d]", "", crudo)
    return _limpiar(crudo)


# ------------------------------------------------------------------ .xls

class LectorXLS(Lector):
    """Excel 97-2003 binario, con python-calamine. TODAS las hojas y filas.

    Se lee entero a proposito, aunque el .xlsx se leia con tope de filas en la
    version vieja: la lectura es completa por orden, y una planilla de pagos o de
    transacciones es justo donde estan las cifras del caso.
    """

    extensiones = ("xls", "xlt")

    def leer(self, datos: bytes, paginas: int) -> tuple[str, str]:
        texto, motivo, _ = self.leer_detalle(datos, paginas)
        return texto, motivo

    def leer_detalle(self, datos: bytes, paginas: int) -> tuple[str, str, str]:
        tipo = que_es(datos)
        if tipo == "zip":                       # un .xlsx renombrado
            t, m = LectorXLSX().leer(datos, paginas)
            return t, m, "openpyxl (.xlsx named .xls)" if t else ""
        if tipo == "html":                      # lo tipico de un portal
            t, m = LectorHTML().leer(datos, paginas)
            return t, m, "HTML parser (.html named .xls)" if t else ""
        if tipo == "xml_excel":
            texto = texto_de_xml_excel(datos)
            return (texto, "", "XML Spreadsheet 2003 (named .xls)") if texto else (
                "", "XML spreadsheet with no data", "")
        if tipo == "texto":
            t, m = LectorTexto().leer(datos, paginas)
            return t, m, "plain text (named .xls)" if t else ""
        if tipo != "ole":
            return "", "not an Excel 97-2003 file", ""

        try:
            texto = libro_con_calamine(datos)
        except ImportError:
            return "", "python-calamine is missing (pip install python-calamine)", ""
        except Exception as error:  # noqa: BLE001
            mensaje = str(error).lower()
            if "password" in mensaje or "encrypt" in mensaje:
                return "", "the Excel file is password protected", ""
            convertido = con_libreoffice(datos, "xls", "xlsx")
            if convertido:
                t, m = LectorXLSX().leer(convertido, 0)
                if t.strip():
                    return t, "", "LibreOffice -> openpyxl"
            return "", f"could not open the Excel file ({type(error).__name__})", ""
        if not texto.strip():
            return "", "the Excel file is empty", ""
        return texto, "", "python-calamine"


def _celda(valor) -> str:
    if valor is None or valor == "":
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def libro_con_calamine(datos: bytes) -> str:
    from python_calamine import CalamineWorkbook

    libro = CalamineWorkbook.from_filelike(io.BytesIO(datos))
    partes = []
    for nombre in libro.sheet_names:
        hoja = libro.get_sheet_by_name(nombre)
        filas = []
        for fila in hoja.to_python(skip_empty_area=True):
            celdas = [_celda(c) for c in fila]
            if any(celdas):
                filas.append("\t".join(celdas).rstrip("\t"))
        if filas:
            partes.append(f"--- {nombre} ---\n" + "\n".join(filas))
    return "\n\n".join(partes)


def texto_de_xml_excel(datos: bytes) -> str:
    """'XML Spreadsheet 2003': lo que exportan muchos sistemas con extension .xls."""
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError:
        return ""
    ns = "{urn:schemas-microsoft-com:office:spreadsheet}"
    partes = []
    for hoja in raiz.iter(f"{ns}Worksheet"):
        filas = []
        for fila in hoja.iter(f"{ns}Row"):
            celdas = ["".join(d.itertext()).strip() for d in fila.iter(f"{ns}Data")]
            if any(celdas):
                filas.append("\t".join(celdas))
        if filas:
            partes.append(f"--- {hoja.get(ns + 'Name', '')} ---\n" + "\n".join(filas))
    return "\n\n".join(partes)
