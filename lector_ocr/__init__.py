"""De un archivo a su texto, y de su texto a la carpeta que le toca.

    from lector_ocr import leer_con_detalle, donde_archivar

    texto, motivo, con_que = leer_con_detalle("pdf", datos, paginas=2)
    destino = donde_archivar("Notice of Motion.pdf", datos, "Foreclosure")

Dos mitades que se usan juntas o por separado:

    LEER        la cascada pypdf -> PyMuPDF -> portfolio -> OCR, con dos motores
    ARCHIVAR    de que tipo es el documento y adonde va en cada plantilla
    EXPEDIENTE  a que caso pertenece, con las reglas de reglas_ruteo.toml
    RECEPCION   la clave del documento (tribunal, caso, numero) y 'gana la primera copia'
    CLAVES_DB   esas claves en PostgreSQL, con la llave como clave primaria
    MOVIMIENTOS lo que se mueve o renombra en SharePoint, para poder deshacerlo
    CALIDAD     cada pagina con texto utilizable, un reintento y ocr_warning si no
    LIMPIEZA    reglas 1 a 4 sobre el texto leido, antes de extraer nada
    EXTRACCION  caratula (demandantes y demandados), quien lo manda y las fechas
    UBICAR      la carpeta del matter con las reglas del Docs Agent, y el renombrado
    SUBCARPETA  la seccion del matter por tipo, con las reglas de Mario (plantillas A/B/C)
    NOMBRE      '{fecha} Doc {n} {tipo}, {remitente}', con el nombre original al lado
    JSONL       el Claude-{ID}.jsonl del matter: linea por id de SharePoint, incremental
    ANALYST     el mensaje al Analyst con el formato del Issue #1 (paso 9)

`con_que` y `se_decidio` no son decorativos: sin ellos no se sabe a quien atribuir
un acierto ni un error, y un digito mal leido por OCR se confunde con uno mal
tecleado.
"""

from .archivar import Destino, donde_archivar
from .expediente import (Identificacion, identificar, indices_del_documento, indices_en,
                         plantilla_de)
from .ficha import cabecera, enmascarar, ficha_de, fichas_de
from .lectores import (COMO_SE_LEE, extensiones_soportadas, leer_con_detalle,
                       lector_para, registrar)
from .extraccion import Extraccion, extraer
from .limpieza import limpiar_documento, limpiar_ficha
from .recepcion import Clave, RegistroDeClaves, clave_del_documento
from .ubicar import Ubicacion, ubicar
from . import (analyst, calidad, clasificacion, claves_db, equivalencias, expediente, extraccion, ficha,
               jsonl_matter, limpieza, movimientos_db, nombre, ocr, paddle_ocr, plantillas,
               recepcion, reglas, subcarpeta)

__all__ = ["COMO_SE_LEE", "analyst", "Clave", "Destino", "Extraccion", "Identificacion", "RegistroDeClaves",
           "cabecera", "calidad", "clasificacion", "clave_del_documento", "donde_archivar",
           "enmascarar", "equivalencias", "expediente", "extensiones_soportadas", "extraccion",
           "extraer", "ficha", "ficha_de",
           "fichas_de", "identificar", "indices_del_documento", "indices_en",
           "leer_con_detalle", "lector_para", "limpiar_documento", "limpiar_ficha", "limpieza",
           "ocr", "paddle_ocr", "plantilla_de", "plantillas", "recepcion", "reglas",
           "claves_db", "jsonl_matter", "movimientos_db", "nombre", "registrar", "subcarpeta",
           "Ubicacion", "ubicar"]
