"""Constantes y rutas del paquete de extraccion."""

from __future__ import annotations

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RAIZ_PROYECTO = BASE_DIR.parent
RESULTADOS = BASE_DIR / "resultados"
SALIDA_PROYECTO = RAIZ_PROYECTO / "salida"

CACHE_ARBOL = SALIDA_PROYECTO / "cache_sharepoint.json"
ESTADO_VINCULO = SALIDA_PROYECTO / "estado_vinculo.xlsx"

# Documentos que suelen llevar la caratula con el numero de indice y las partes.
# El sondeo mostro que priorizarlos sube mucho el acierto por archivo descargado.
# Las palabras extra salieron de contar el vocabulario real de los nombres de archivo
# en las carpetas que no se pudieron identificar: order, motion, answer y affirmation
# son escritos judiciales, y todo escrito judicial lleva la caratula.
PATRON_CARATULA = re.compile(
    r"retainer|complaint|summons|s&c|\baos\b|notice of|index|affidavit|affirmation"
    r"|petition|stipulation|\bstip\b|\border\b|motion|answer|judgment|decision"
    r"|\bdeed\b|verified",
    re.I,
)

# Lo contrario: nombres que nunca traen caratula. Estados de cuenta, facturas medicas
# y portadas de fax son ruido caro de bajar, asi que van al final de la cola.
PATRON_RUIDO = re.compile(
    r"\bfax\b|chiro|statement|\bstmt\b|invoice|receipt|\bbank\b|\btax\b|paystub|w-?2\b",
    re.I,
)

# El tope existe para no bajar un escaneo de 300 paginas entero por leer la
# caratula. 15 MB era demasiado bajo: dejaba fuera expedientes completos
# escaneados de una sola pasada, que son justo los que mas contexto traen. Con
# PAGINAS limitando lo que se lee, lo unico que cuesta de mas es la descarga.
MAX_MB = 60
PAGINAS = 3  # la caratula esta al principio

# 12 y no 6: la corrida completa mostro que la mitad de las carpetas sin identificar
# se detuvo en el primer o segundo archivo, no por falta de material (mediana de 55
# PDFs elegibles por carpeta) sino por cortar demasiado pronto.
DOCUMENTOS_POR_CARPETA = 12

# Un nombre que aparece en mas carpetas que esto es machote, no un cliente.
MAX_CARPETAS_POR_PERSONA = 3


def drive_id() -> str:
    """El driveId sale de la primera ruta del arbol ya recorrido."""
    if not CACHE_ARBOL.exists():
        raise FileNotFoundError(
            f"Falta {CACHE_ARBOL.name}. Corre antes: python sp_conexion.py --recorrer"
        )
    with CACHE_ARBOL.open(encoding="utf-8") as archivo:
        primero = json.load(archivo)[0]
    return primero["ruta"].split("/")[2]
