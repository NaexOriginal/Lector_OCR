"""Paso 2 del Docs Agent: que CADA pagina tenga texto utilizable, o que se sepa cual no.

    from lector_ocr.calidad import leer_pdf_por_paginas

    lectura = leer_pdf_por_paginas(datos)
    lectura.texto            # el documento, pagina tras pagina
    lectura.ocr_warning      # True si alguna pagina quedo sin texto fiable
    lectura.fallidas         # [3, 7]: las que fallaron dos veces
    lectura.paginas[2]       # Pagina(numero=3, origen='ocr', estado='fallida', ...)

POR QUE POR PAGINA. Antes se juzgaba el documento entero, y eso escondia dos fallos:
    - un PDF mixto -- un escrito con texto y los anexos escaneados al final -- pasaba
      el filtro por la capa de texto de las primeras paginas y los anexos no se leian
      nunca, sin aviso;
    - un escaneo de veinte paginas con una ilegible salia como leido: la pagina mala
      se perdia dentro del texto de las otras diecinueve.
La spec lo pide asi: comprobar que el resultado tenga texto utilizable en cada pagina.

LA ESCALERA DE CADA PAGINA, de lo barato a lo caro, como en el resto del paquete:
    1. Su capa de texto (pypdf y, si no sirve, PyMuPDF). Si es plausible, ya esta.
    2. OCR de la pagina. Si devuelve texto que no es ruido, ya esta.
    3. Si la imagen no tiene tinta, es una pagina EN BLANCO: no es un fallo.
    4. Un reintento, distinto del primero (ver DPI_REINTENTO).
    5. Si tampoco: la pagina queda 'fallida' y el documento lleva ocr_warning, para
       que nadie se fie de una capa de texto vacia.

UNA PAGINA FALLIDA CONSERVA SU CAPA DE TEXTO. En un escaneo de NYSCEF esa capa es el
sello, y el sello lleva el INDEX NO. que empareja el documento con su caso. Lo que
no se guarda es el ruido del OCR: basura con aspecto de texto es peor que un hueco.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field

from . import ocr

# EL REINTENTO TIENE QUE CAMBIAR ALGO. Paddle es determinista: la misma imagen da el
# mismo texto, asi que repetir la lectura tal cual solo duplicaria el tiempo. Se
# rasteriza a mas resolucion, que es lo que rescata la letra pequena (ver ocr.DPI: de
# 200 a 300 paso de 3 de 5 a 5 de 5 campos). SIN MEDIR a 400 todavia.
DPI_REINTENTO = 400

# Estados de una pagina.
LEIDA = "leida"              # a la primera, por la capa o por el OCR
REINTENTADA = "reintentada"  # fallo la primera vez y la segunda salio bien
EN_BLANCO = "en_blanco"      # sin tinta: no hay nada que leer y no es un fallo
FALLIDA = "fallida"          # dos intentos sin texto utilizable -> ocr_warning
SIN_OCR = "sin_ocr"          # necesitaba OCR y no se hizo (motor ausente o tope)

# Las que NO dejan el documento con aviso.
SIN_AVISO = frozenset({LEIDA, REINTENTADA, EN_BLANCO})


@dataclass
class Pagina:
    numero: int                      # desde 1, como la ve una persona
    texto: str = ""
    origen: str = ""                 # 'capa' | 'ocr' | ''
    estado: str = ""
    motivo: str = ""                 # por que necesito OCR, o por que fallo
    confianza: float | None = None   # la media de Paddle; None con Tesseract o sin OCR
    intentos: int = 0                # pasadas de OCR: 0, 1 o 2
    motor: str = ""

    def resumen(self) -> dict:
        """La pagina sin su texto, para la ficha: el texto ya va en el documento."""
        datos = asdict(self)
        datos.pop("texto")
        datos["caracteres"] = len(self.texto)
        return datos


@dataclass
class LecturaPDF:
    paginas: list[Pagina] = field(default_factory=list)
    motivo: str = ""                 # si no salio nada, por que

    @property
    def texto(self) -> str:
        return "\n".join(p.texto for p in self.paginas)

    @property
    def fallidas(self) -> list[int]:
        return [p.numero for p in self.paginas if p.estado == FALLIDA]

    @property
    def ocr_warning(self) -> bool:
        """Alguna pagina sin texto en el que se pueda confiar."""
        return any(p.estado not in SIN_AVISO for p in self.paginas)

    @property
    def con_que(self) -> str:
        """El 'read_with' del documento: que capas y que motor, y en cuantas paginas."""
        del_ocr = [p for p in self.paginas if p.origen == "ocr" and p.texto.strip()]
        de_capa = [p for p in self.paginas if p.origen == "capa" and p.texto.strip()]
        if not del_ocr:
            return "pypdf (text layer)" if de_capa else ""
        motores = sorted({re.sub(r"\s*\(.*$", "", p.motor) for p in del_ocr if p.motor})
        motor = " + ".join(motores) or ("PaddleOCR" if ocr.MOTOR == "paddle" else "Tesseract OCR")
        confianzas = [p.confianza for p in del_ocr if p.confianza is not None]
        if confianzas:
            motor += f" (mean confidence {sum(confianzas) / len(confianzas):.2f})"
        if not de_capa:
            return f"{motor} (rasterised pages)"
        return f"text layer + {motor} on {len(del_ocr)} of {len(self.paginas)} pages"

    def resumen(self) -> dict:
        """Lo que va a la ficha y al mensaje del Analyst."""
        cuenta: dict[str, int] = {}
        for p in self.paginas:
            cuenta[p.estado] = cuenta.get(p.estado, 0) + 1
        return {
            "ocr_warning": self.ocr_warning,
            "pages_total": len(self.paginas),
            "pages_by_status": cuenta,
            "pages_failed": self.fallidas,
            "pages_without_ocr": [p.numero for p in self.paginas if p.estado == SIN_OCR],
        }


def _legible(texto: str) -> bool:
    """Lo que devolvio el OCR, sirve? Vacio o ruido no.

    No se exige un minimo de caracteres: 'EXHIBIT A' es una pagina separadora de
    nueve, y la clave de una produccion son once. parece_ruido ya no juzga lo corto.
    """
    return bool(texto.strip()) and not ocr.parece_ruido(texto)


def _capas(datos: bytes, total: int) -> list[str]:
    """La capa de texto de cada pagina con pypdf. '' donde no abre o no hay."""
    if b"%PDF" not in datos[:1024]:
        return [""] * total   # PyMuPDF abre imagenes como documento; pypdf solo ensucia el log
    try:
        import pypdf

        lector = pypdf.PdfReader(io.BytesIO(datos))
        return [(lector.pages[i].extract_text() or "") for i in range(total)]
    except Exception:  # noqa: BLE001 - PyMuPDF lo intenta despues, pagina a pagina
        return [""] * total


def _ocr(pagina, dpi: int) -> tuple[str, str, float | None, bool]:
    """(texto, motor, confianza, en blanco) de una pagina rasterizada a `dpi`."""
    with ocr.rasterizar(pagina, dpi) as imagen:
        texto, motor, confianza = ocr.leer_imagen_detalle(imagen, enderezar=True,
                                                          agrandar=False)
        blanca = not _legible(texto) and ocr.pagina_en_blanco(imagen)
    return texto, motor, confianza, blanca


def leer_pagina(pagina, numero: int, capa: str, hacer_ocr: bool = True,
                motivo_sin_ocr: str = "") -> Pagina:
    """Una pagina por la escalera completa. `pagina` es la de PyMuPDF."""
    resultado = Pagina(numero=numero)

    falta, motivo = ocr.necesita_ocr(capa, 1)
    if falta:
        # El segundo lector de capa es gratis: PyMuPDF abre lo que pypdf no.
        otra = pagina.get_text() or ""
        if otra.strip() and not ocr.necesita_ocr(otra, 1)[0]:
            capa, falta = otra, False
    if not falta:
        resultado.texto, resultado.origen, resultado.estado = capa, "capa", LEIDA
        return resultado

    resultado.motivo = motivo
    if not hacer_ocr:
        resultado.texto, resultado.origen = capa, "capa" if capa.strip() else ""
        resultado.estado, resultado.motivo = SIN_OCR, motivo_sin_ocr or motivo
        return resultado

    for intento, dpi in enumerate((ocr.DPI, DPI_REINTENTO), start=1):
        texto, motor, confianza, blanca = _ocr(pagina, dpi)
        resultado.intentos = intento
        if _legible(texto):
            resultado.texto, resultado.origen = texto, "ocr"
            resultado.motor, resultado.confianza = motor, confianza
            resultado.estado = LEIDA if intento == 1 else REINTENTADA
            return resultado
        if blanca:
            # Sin tinta no hay nada que reintentar. Se queda la capa (el sello, si
            # lo hay) y no cuenta como fallo.
            resultado.texto, resultado.origen = capa, "capa" if capa.strip() else ""
            resultado.estado, resultado.motivo = EN_BLANCO, "blank page"
            return resultado

    # Dos intentos sin texto. Se conserva la capa (el sello lleva el INDEX NO.) y se
    # dice por que: el error del motor si lo hubo, si no que salio vacio o ruido.
    resultado.texto, resultado.origen = capa, "capa" if capa.strip() else ""
    resultado.estado = FALLIDA
    resultado.motivo = ocr._sin_texto(
        "OCR returned noise, not words" if texto.strip() else "OCR found no text")
    return resultado


def leer_pdf_por_paginas(datos: bytes, paginas: int = 0,
                         tope_ocr: int | None = None) -> LecturaPDF:
    """Todas las paginas (o las `paginas` primeras), cada una por su escalera.

    `tope_ocr`: a partir de cuantas paginas ya no se hace OCR (None = las mismas que
    `paginas`; 0 = todas). Las que se quedan fuera por el tope salen como 'sin_ocr' y
    tambien dejan aviso: el texto de esas paginas no esta.
    """
    pymupdf = ocr._pymupdf()
    if pymupdf is None:
        return LecturaPDF(motivo="PyMuPDF is not installed")
    ocr._empezar()
    puede, motivo_motor = ocr.motor_disponible()
    tope = paginas if tope_ocr is None else tope_ocr

    lectura = LecturaPDF()
    try:
        with pymupdf.open(stream=datos, filetype="pdf") as documento:
            total = len(documento) if paginas <= 0 else min(paginas, len(documento))
            capas = _capas(datos, total)
            for i in range(total):
                dentro_del_tope = tope <= 0 or i < tope
                lectura.paginas.append(leer_pagina(
                    documento[i], i + 1, capas[i],
                    hacer_ocr=puede and dentro_del_tope,
                    motivo_sin_ocr=(motivo_motor if not puede
                                    else f"beyond the OCR cap of {tope} pages")))
    except Exception as error:  # noqa: BLE001
        lectura.motivo = f"OCR could not read the PDF ({type(error).__name__})"
        return lectura

    if not lectura.texto.strip():
        estados = {p.estado for p in lectura.paginas}
        if not puede:
            lectura.motivo = motivo_motor
        elif estados == {EN_BLANCO}:
            lectura.motivo = "every page is blank"
        else:
            lectura.motivo = ocr._sin_texto("OCR found no text on the pages")
    return lectura
