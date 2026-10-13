"""De un archivo a su texto, y de su texto a la carpeta que le toca.

    from lector_ocr import leer_con_detalle, donde_archivar

    texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)
    destino = donde_archivar("Notice of Motion.pdf", datos, "Foreclosure")

Dos mitades que se usan juntas o por separado:

    LEER        la cascada pypdf -> PyMuPDF -> portfolio -> OCR, con dos motores
    ARCHIVAR    de que tipo es el documento y adonde va en cada plantilla
    EXPEDIENTE  a que caso pertenece: asunto, nombre del archivo, indice y apellido

`con_que` y `se_decidio` no son decorativos: sin ellos no se sabe a quien atribuir
un acierto ni un error, y un digito mal leido por OCR se confunde con uno mal
tecleado.
"""

from .archivar import Destino, donde_archivar
from .expediente import Identificacion, identificar, indices_del_documento, indices_en
from .ficha import cabecera, enmascarar, ficha_de, fichas_de
from .lectores import (COMO_SE_LEE, extensiones_soportadas, leer_con_detalle,
                       lector_para, registrar)
from . import clasificacion, equivalencias, expediente, ficha, ocr, paddle_ocr, plantillas

__all__ = ["COMO_SE_LEE", "Destino", "Identificacion", "cabecera", "clasificacion",
           "donde_archivar", "enmascarar", "equivalencias", "expediente",
           "extensiones_soportadas", "ficha", "ficha_de", "fichas_de", "identificar",
           "indices_del_documento", "indices_en", "leer_con_detalle", "lector_para", "ocr",
           "paddle_ocr", "plantillas", "registrar"]
