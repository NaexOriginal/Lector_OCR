r"""Lee un archivo del disco y dice que texto tiene y QUIEN lo leyo.

Sirve para dos cosas: comprobar que la instalacion esta completa -- Tesseract,
PaddleOCR, la GPU -- y mirar por que un documento concreto se lee mal, que es la
pregunta que siempre acaba apareciendo.

SIEMPRE DICE CON QUE LO LEYO. Un texto sin esa columna no se puede juzgar: si sale
'1' donde deberia poner '7', importa muchisimo si lo leyo python-docx (imposible,
el texto es el que escribio el autor) o Tesseract (probable, y hay que subir DPI).

Uso:
    python leer.py documento.pdf
    python leer.py escaneo.pdf --motor paddle --paginas 4
    python leer.py --diagnostico
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lector_ocr import (donde_archivar, extensiones_soportadas,
                        leer_con_detalle, ocr, paddle_ocr)


def diagnostico() -> None:
    """Que motores hay instalados y sobre que se van a ejecutar."""
    print("MOTORES\n")
    hay, detalle = ocr.disponible()
    print(f"  Tesseract   {'si' if hay else 'NO'}   {detalle}")
    hay_p, detalle_p = paddle_ocr.disponible()
    print(f"  PaddleOCR   {'si' if hay_p else 'NO'}   {detalle_p}")
    if hay_p:
        donde, porque = paddle_ocr.dispositivo()
        print(f"  dispositivo {donde.upper()}   {porque}")
    print(f"\n  motor por defecto: {ocr.MOTOR}")
    print(f"  DPI: {ocr.DPI}   psm: {ocr.POR_DEFECTO}")
    print(f"\n  extensiones que se saben leer: {', '.join(extensiones_soportadas())}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("archivo", nargs="?", help="El archivo a leer")
    p.add_argument("--paginas", type=int, default=2,
                   help="Paginas del PDF que se leen (0 = todas)")
    p.add_argument("--motor", choices=ocr.MOTORES, help="Que motor usa el OCR")
    p.add_argument("--caracteres", type=int, default=1500,
                   help="Cuanto texto se imprime")
    p.add_argument("--plantilla", choices=["Foreclosure", "FCRA"], default="Foreclosure",
                   help="Con cual de las dos estructuras se decide la carpeta")
    p.add_argument("--diagnostico", action="store_true",
                   help="Solo dice que esta instalado")
    args = p.parse_args()

    if args.diagnostico or not args.archivo:
        diagnostico()
        return

    if args.motor:
        ocr.elegir(args.motor)

    ruta = Path(args.archivo)
    if not ruta.exists():
        raise SystemExit(f"No existe: {ruta}")

    extension = ruta.suffix.lstrip(".").lower()
    texto, motivo, con_que = leer_con_detalle(extension, ruta.read_bytes(), args.paginas)

    destino = donde_archivar(ruta.name, ruta.read_bytes(), args.plantilla)

    print(f"\n  archivo    {ruta.name}")
    print(f"  leido con  {con_que or '(nada lo pudo leer)'}")
    print(f"  caracteres {len(texto):,}")
    print(f"  tipo       {destino.tipo or '(no se reconocio)'}"
          + (f"   [{destino.certeza}]" if destino.certeza else ""))
    print(f"  va a       {destino.carpeta or '(sin clasificar)'}"
          + (f"   <- {destino.se_decidio}" if destino.se_decidio else ""))
    if motivo:
        print(f"  aviso      {motivo}")
    print("\n" + "-" * 70)
    print(texto[: args.caracteres] if texto.strip() else "(sin texto)")
    if len(texto) > args.caracteres:
        print(f"\n... y {len(texto) - args.caracteres:,} caracteres mas")


if __name__ == "__main__":
    main()
