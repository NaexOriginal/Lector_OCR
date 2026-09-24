"""De un archivo a su texto, con OCR solo cuando hace falta.

    from lector_ocr import leer_con_detalle

    texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)

`con_que` dice QUE lo leyo -- pypdf, PyMuPDF, python-docx, Tesseract o PaddleOCR --
y esa columna no es decorativa: sin ella no se sabe a quien atribuir un acierto ni
un error, y un digito mal leido por OCR se confunde con un dato mal tecleado.
"""

from .lectores import (COMO_SE_LEE, extensiones_soportadas, leer_con_detalle,
                       lector_para, registrar)
from . import ocr, paddle_ocr

__all__ = ["COMO_SE_LEE", "extensiones_soportadas", "leer_con_detalle",
           "lector_para", "registrar", "ocr", "paddle_ocr"]
