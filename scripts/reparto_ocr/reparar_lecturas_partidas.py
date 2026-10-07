r"""Quita de los diarios las lecturas que quedaron A MEDIAS porque la GPU se rompio.

Que paso (7-oct, servidor de Azure con A100): con varios procesos de Paddle en la misma
GPU, a veces uno da 'CUDA error(700/716)' a mitad de un archivo. Las paginas de antes se
leian y las de despues fallaban una a una, y el archivo quedaba LEIDO con parte del texto
('Notice of motion for SJ', 277 paginas: 16.427 caracteres). Como estaba LEIDO, no se
volvia a leer nunca. describir_casos ya lo evita (desde el commit que trae este fichero);
esto limpia lo que se guardo antes.

Como los encuentra, en las bitacoras log_textos_matters.<lista>.txt (un proceso, un hilo):
  - antes del arreglo: en cada sesion, el archivo LEIDO justo antes del primer 'CUDA error';
  - despues: el archivo LEIDO justo antes de 'FIN  REINICIO POR GPU ROTA'.

Que hace con ellos:
  1. borra sus lineas LEIDAS (y las copias de ellas) de los diarios textos_matters.lista_*:
     se vuelven a leer en la proxima corrida. Cada diario tocado queda respaldado en
     <diario>.antes_reparacion;
  2. quita sus casos del registro de casos subidos, para que el armado los vuelva a subir
     cuando esten completos de verdad (el Claude-{ID}.jsonl de SharePoint se sobrescribe).

CON LA LECTURA PARADA. Desde la carpeta que contiene codigo/:
    python reparar_lecturas_partidas.py --equipo gpu3            # solo dice que haria
    python reparar_lecturas_partidas.py --equipo gpu3 --aplicar
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

SALIDA = Path("codigo") / "salida"
LINEA = re.compile(r"^\d\d/\d\d \d\d:\d\d:\d\d  (LEIDO    |NO LEIDO |COPIA    |FIN  |ARRANQUE)(.*)$")


def partidas_en(bitacora: Path) -> set[str]:
    """Los nombres (cortados a 70, como en la bitacora) de los archivos leidos a medias."""
    nombres: set[str] = set()
    ultimo = None          # (tipo, nombre) del ultimo archivo terminado en la sesion
    hubo_cuda = False      # ya hubo un 'CUDA error' en esta sesion
    for linea in bitacora.read_text(encoding="utf-8", errors="replace").split("\n"):
        m = LINEA.match(linea)
        if not m:
            continue
        tipo, resto = m.group(1).strip(), m.group(2)
        if tipo == "ARRANQUE":
            ultimo, hubo_cuda = None, False
        elif tipo == "FIN":
            if "REINICIO POR GPU ROTA" in resto and ultimo and ultimo[0] == "LEIDO":
                nombres.add(ultimo[1])
            ultimo, hubo_cuda = None, False
        elif tipo == "LEIDO":
            ultimo = ("LEIDO", resto.split("  (", 1)[0].strip())
        elif tipo == "COPIA":
            ultimo = ("COPIA", resto.split(": texto copiado", 1)[0].strip())
        else:  # NO LEIDO
            if "CUDA error" in resto and not hubo_cuda:
                hubo_cuda = True
                if ultimo and ultimo[0] == "LEIDO":
                    nombres.add(ultimo[1])
            ultimo = ("NO LEIDO", "")
    return nombres


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--equipo", required=True, help="el final del nombre de las listas: gpu3")
    p.add_argument("--etapa", default="2022")
    p.add_argument("--aplicar", action="store_true", help="sin esto, solo dice que haria")
    args = p.parse_args()

    diarios = sorted(SALIDA.glob(f"textos_matters.lista_*_{args.equipo}.jsonl"))
    if not diarios:
        raise SystemExit(f"No hay diarios textos_matters.lista_*_{args.equipo}.jsonl en {SALIDA}")

    # 1. los nombres, por bitacora, y sus ids en el diario de esa misma lista
    partidas: set[str] = set()
    for diario in diarios:
        bitacora = SALIDA / f"log_{diario.stem}.txt"
        if not bitacora.exists():
            continue
        nombres = partidas_en(bitacora)
        if not nombres:
            continue
        for linea in diario.read_text(encoding="utf-8", errors="replace").split("\n"):
            if not linea.strip():
                continue
            try:
                x = json.loads(linea)
            except ValueError:
                continue
            if x.get("was_read") and not x.get("copied_from") and str(x.get("file_name", ""))[:70] in nombres:
                partidas.add(x["id"])
        print(f"  {bitacora.name}: {len(nombres)} archivos leidos a medias")
    print(f"\n  En total: {len(partidas)} archivos (ids) a releer")
    if not partidas:
        return

    # 2. las lineas que sobran en cada diario
    casos: set[str] = set()
    for diario in diarios:
        lineas = diario.read_text(encoding="utf-8", errors="replace").split("\n")
        quedan, quitadas = [], 0
        for linea in lineas:
            x = None
            if linea.strip():
                try:
                    x = json.loads(linea)
                except ValueError:
                    pass
            if x and x.get("was_read") and (x.get("id") in partidas or x.get("copied_from") in partidas):
                quitadas += 1
                casos.add(x.get("caso"))
                continue
            quedan.append(linea)
        if quitadas:
            print(f"  {diario.name}: se quitan {quitadas} lineas")
            if args.aplicar:
                shutil.copy2(diario, diario.with_name(diario.name + ".antes_reparacion"))
                diario.write_text("\n".join(quedan), encoding="utf-8")

    # 3. sus casos, fuera del registro de subidos
    registro = SALIDA / f"etapa_{args.etapa}" / f"casos_subidos_{args.etapa}_{args.equipo}.json"
    if registro.exists():
        hechos = json.loads(registro.read_text(encoding="utf-8"))
        fuera = sorted(e for e in hechos if e in casos)
        print(f"\n  Casos ya subidos que se volveran a subir al completarse: {len(fuera)}")
        for e in fuera:
            print(f"    {e}")
        if args.aplicar and fuera:
            shutil.copy2(registro, registro.with_name(registro.name + ".antes_reparacion"))
            registro.write_text(json.dumps({e: v for e, v in hechos.items() if e not in fuera},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  HECHO." if args.aplicar else "\n  No se ha tocado nada: anade --aplicar para hacerlo.")


if __name__ == "__main__":
    main()
