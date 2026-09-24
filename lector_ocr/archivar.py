"""De un archivo a la carpeta que le toca. Une la lectura con la clasificacion.

    from lector_ocr import donde_archivar

    destino = donde_archivar("Notice of Motion.pdf", datos, "Foreclosure")
    destino.carpeta      # '03_Litigation/Motions'
    destino.se_decidio   # 'el nombre' | 'el contenido (titulo)' | ''
    destino.leido_con    # 'python-docx' | 'Tesseract OCR' | ...

LA ESCALERA, de lo barato a lo caro, y no adivina cuando no sabe:

    1. EL NOMBRE DEL ARCHIVO        'Retainer.pdf' no necesita abrirse. Cuesta cero
                                    y resuelve un tercio de los casos reales
    2. EL CONTENIDO                 se lee -- capa de texto si la hay, OCR si no --
                                    y se busca el tipo primero en el titulo y
                                    despues en el cuerpo
    3. NADA                         se devuelve vacio. Un archivo sin clasificar en
                                    una carpeta a la vista vale mas que uno
                                    archivado a ojo donde no toca

POR QUE LA POSICION GANA AL CONTENIDO CUANDO LA HAY. Esto es para archivos SUELTOS,
los que no tienen carpeta que los explique. Si un documento ya esta guardado en
'LITIGATION', esa carpeta lleva dentro la intencion de quien lo archivo y vale mas
que cualquier lectura: se midio, y reclasificar por contenido lo que ya se sabia por
posicion cambio 56 de 314 documentos, varios a peor. Para eso esta equivalencias.py,
que traduce carpeta a carpeta sin abrir nada.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import clasificacion
from .lectores import leer_con_detalle

# Cuanto se lee de un PDF para decidir. El tipo de documento se declara al
# principio: si no aparece en dos paginas, no aparece.
PAGINAS = 2


@dataclass(frozen=True)
class Destino:
    """Adonde va el archivo, y con que se decidio."""

    carpeta: str          # '03_Litigation/Motions', o '' si no se supo
    tipo: str             # 'Notice of motion'
    certeza: str          # la que declara la tabla para ese tipo
    se_decidio: str       # 'el nombre' | 'el contenido (titulo)' | 'el contenido (mencion)'
    leido_con: str = ""   # que lector produjo el texto, vacio si no hizo falta
    # La linea que lo decidio. Permite juzgar la propuesta sin abrir el documento,
    # que es lo que hace revisable una lista de dos mil.
    evidencia: str = ""

    def __bool__(self) -> bool:
        return bool(self.carpeta)


def donde_archivar(nombre: str, datos: bytes | None = None,
                   plantilla: str = "Foreclosure", paginas: int = PAGINAS) -> Destino:
    """La carpeta que le toca a este archivo dentro de su expediente.

    `datos` puede ir vacio: entonces solo se intenta el peldano del nombre, que no
    necesita descargar nada. Es util para decidir de antemano a cuantos archivos
    hace falta abrirles el fichero.
    """
    tipo, _ = clasificacion.tipo_de_nombre(nombre)
    if tipo is not None and tipo.destino(plantilla):
        return Destino(tipo.destino(plantilla), tipo.nombre, tipo.certeza, "el nombre")

    if not datos:
        return Destino("", "", "", "")

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    texto, _, con_que = leer_con_detalle(extension, datos, paginas)
    # (tipo, EVIDENCIA, donde) -- en ese orden. Tenerlo al reves ponia en
    # `se_decidio` la linea que hizo coincidir, que es un valor distinto por archivo
    # y no sirve para agrupar ni para saber cuanto fiarse.
    tipo, evidencia, donde = clasificacion.tipo_de_texto(texto)
    lector = str(con_que).split(" (")[0]
    if tipo is None or not tipo.destino(plantilla):
        return Destino("", tipo.nombre if tipo else "", "", "", lector, evidencia[:160])
    return Destino(tipo.destino(plantilla), tipo.nombre, tipo.certeza,
                   f"el contenido ({donde})", lector, evidencia[:160])
