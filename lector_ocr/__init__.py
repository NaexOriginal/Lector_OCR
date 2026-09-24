"""De un archivo a su texto, y de su texto a la carpeta que le toca.

    from lector_ocr import leer_con_detalle, donde_archivar

    texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)
    destino = donde_archivar("Notice of Motion.pdf", datos, "Foreclosure")

Dos mitades que se usan juntas o por separado:

    LEER        la cascada pypdf -> PyMuPDF -> portfolio -> OCR, con dos motores
    ARCHIVAR    de que tipo es el documento y adonde va en cada plantilla

`con_que` y `se_decidio` no son decorativos: sin ellos no se sabe a quien atribuir
un acierto ni un error, y un digito mal leido por OCR se confunde con uno mal
tecleado.
"""

from .archivar import Destino, donde_archivar
from .lectores import (COMO_SE_LEE, extensiones_soportadas, leer_con_detalle,
                       lector_para, registrar)
from . import clasificacion, equivalencias, ocr, paddle_ocr, plantillas

__all__ = ["COMO_SE_LEE", "Destino", "clasificacion", "donde_archivar",
           "equivalencias", "extensiones_soportadas", "leer_con_detalle",
           "lector_para", "ocr", "paddle_ocr", "plantillas", "registrar"]
