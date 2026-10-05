r"""La cola de OCR: lo que los equipos SIN GPU dejaron para los equipos CON GPU.

Se corre en el equipo de Rafa, despues de juntar los diarios de todos:

    1. Copia los textos_matters.parte*.jsonl de cada equipo a salida\
    2. .venv\Scripts\python.exe reparto_ocr\cola_ocr.py
    3. Pasa codigo\salida\cola_ocr.txt a los equipos con GPU (a su codigo\salida\)

QUE ENTRA EN LA COLA: cada archivo cuya ultima noticia es que NECESITA OCR y que
nadie ha leido todavia. Eso es:

    - lo que la pasada --sin-ocr aparto ('needs OCR: left for a machine with GPU')
    - lo que un equipo sin GPU intento leer con OCR antes de las dos pasadas y no
      pudo ('OCR got no text', 'PaddleOCR failed': el fallo de oneDNN del 29-sep)

Las IMAGENES no van en la cola: la pasada --solo-ocr las coge sola por la extension.

LA COLA SOLO LLEVA IDENTIFICADORES, nunca texto: los diarios llevan SSN y reportes
de credito en claro, y esto viaja a otros equipos.

Uso:
    .venv\Scripts\python.exe reparto_ocr\cola_ocr.py
    .venv\Scripts\python.exe reparto_ocr\cola_ocr.py --solo-contar
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

# Los fallos que significan "esto necesitaba OCR y en este equipo no salio". Un
# 'OCR got no text' en un equipo CON GPU suele ser una pagina en blanco de verdad;
# en uno sin GPU, antes del arreglo de oneDNN, era Paddle reventando. Se reintentan
# en la GPU: si de verdad no hay texto, alli se vera.
FALLOS_DE_OCR = ("OCR got no text", "OCR found no text", "PaddleOCR failed",
                 "OCR could not read")


def main() -> None:
    from describir_casos import (IMAGENES, NECESITA_OCR, SALIDA_DIR, arbol_de_matters,
                                 diarios_de, es_definitiva, extension_de, hay_que_releer)

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--solo-contar", action="store_true",
                   help="Dice cuantos hay, sin escribir la cola")
    args = p.parse_args()

    diarios = diarios_de("matters")
    if not diarios:
        raise SystemExit(f"No hay diarios en {SALIDA_DIR}: copia alli los "
                         "textos_matters.parte*.jsonl de cada equipo.")

    leidos, necesita, motivos = set(), set(), Counter()
    for fichero in diarios:
        for linea in fichero.read_text(encoding="utf-8", errors="replace").splitlines():
            if not linea.strip():
                continue
            try:
                f = json.loads(linea)
            except ValueError:
                continue
            if hay_que_releer(f):
                continue
            motivo = str(f.get("not_read_because") or "")
            if es_definitiva(f):
                leidos.add(f["id"])
            elif motivo == NECESITA_OCR or motivo.startswith(FALLOS_DE_OCR):
                necesita.add(f["id"])
                motivos["apartado por la pasada sin OCR" if motivo == NECESITA_OCR
                        else "fallo de OCR en un equipo sin GPU"] += 1

    cola = necesita - leidos
    arbol = arbol_de_matters()
    en_arbol = set(arbol["id"])
    fuera = cola - en_arbol                # borrados o movidos desde que se hizo el arbol
    cola &= en_arbol
    imagenes = arbol[arbol["name"].map(extension_de).isin(IMAGENES)]
    imagenes_pend = len(set(imagenes["id"]) - leidos)

    print(f"\n  {len(diarios)} diario(s) leidos de {SALIDA_DIR}")
    for texto, n in motivos.most_common():
        print(f"     {n:>8,}  {texto}")
    print(f"\n  COLA DE OCR: {len(cola):,} archivos"
          + (f"  ({len(fuera):,} ya no estan en el arbol, fuera)" if fuera else ""))
    print(f"  + {imagenes_pend:,} imagenes, que la pasada --solo-ocr coge por la extension")

    if args.solo_contar:
        return
    destinos = [SALIDA_DIR / "cola_ocr.txt", AQUI / "codigo" / "salida" / "cola_ocr.txt"]
    for destino in destinos:
        if destino.parent.exists():
            destino.write_text("\n".join(sorted(cola)), encoding="utf-8")
            print(f"  escrita: {destino}")
    print("\n  Pasa codigo\\salida\\cola_ocr.txt a los equipos con GPU "
          "(a su codigo\\salida\\) y relanzan su linea.")


if __name__ == "__main__":
    main()
