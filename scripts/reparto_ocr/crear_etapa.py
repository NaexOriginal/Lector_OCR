r"""Prepara una ETAPA de lectura: que falta de un grupo de expedientes y que lee cada equipo.

Una etapa es "leer primero estos expedientes" (por ejemplo, los de 2022 en adelante) sin
cambiar el reparto de fondo. Este script:

  1. Cruza los expedientes elegidos con el arbol de Matters y con TODOS los diarios que se
     le pasen (lo ya leido), y clasifica cada archivo:
        leido | resuelto sin texto (definitivo) | copia de un archivo ya leido |
        pendiente: OCR | pendiente: nunca leido | pendiente: fallo al leer
  2. Reparte lo pendiente entre los equipos por el trozo de siempre (md5 del contenido,
     modulo 12): lo que un equipo leyo por su cuenta sigue siendo suyo y no se repite, y
     todas las copias de un documento caen en el mismo equipo.
  3. Si un equipo queda mucho mas cargado, le pasa medios trozos (modulo 24) al menos
     cargado hasta igualar.
  4. Escribe una lista por equipo (lista_<etapa>_<equipo>.txt, solo ids), el estado por
     expediente y el detalle de lo pendiente. Comprueba que las listas no se solapan y
     que suman exactamente lo pendiente.

NO LEE NADA DE SHAREPOINT y no hace OCR: solo cruza ficheros.

Uso (desde python_script_hubspot_info, con su .venv):

    python reparto_ocr\crear_etapa.py --etapa 2022 ^
        --expedientes salida\expedientes_2022_en_adelante.csv ^
        --arbol salida\arbol_matters_5oct.jsonl ^
        --diarios C:\1_Documentos_Personales\Respaldo_JSONL_OCR\2026-10-06 ^
        --log C:\...\1_lectura_ocr_gpu\log_textos_matters.parte0.ocr.RAFAEL.txt

  --expedientes   CSV con una columna 'expediente' (los nombres de carpeta de Matters)
  --diarios       carpetas o ficheros de diarios (.jsonl); las carpetas se recorren enteras
  --cola          cola_ocr.txt (por defecto codigo\salida\cola_ocr.txt)
  --log           una bitacora de lectura, para estimar paginas por tamano (opcional)
  --salida        carpeta de salida (por defecto salida\etapa_<etapa>)
  --mover T:DE:A  mover a mano el medio trozo T (modulo 24) del equipo DE al equipo A, en
                  vez de igualar solo. Se puede repetir. Para REPRODUCIR una etapa ya
                  repartida hay que pasar los mismos movimientos que se usaron:
                  la etapa 2022 (6-oct) uso  --mover 23:gpu3:rafael

Despues: cada lista va a la carpeta codigo\salida\ de su equipo, junto con el arbol que se
uso, y cada equipo lanza la lectura completa con --lista (ver ETAPAS.md).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI / "codigo"))
from describir_casos import IMAGENES, NECESITA_OCR, es_definitiva, extension_de, hay_que_releer  # noqa: E402

# Los trozos de siempre (--de 12). Cambiar aqui si cambia el reparto de fondo.
EQUIPOS = {"rafael": (0, 1, 4, 5), "gpu2": (2, 3, 6, 7), "gpu3": (8, 9, 10, 11)}
PAGINAS_POR_HORA = 743          # medido en un equipo con GPU, una ventana
PDF_QUE_NECESITA_OCR = 0.61     # de lo nunca leido, que parte acaba en OCR (medido en Matters)
DESEQUILIBRIO_MAXIMO = 0.10     # se iguala si el mas cargado pasa un 10% al menos cargado


def clave_de(fila) -> str:
    return fila["hash"] if fila.get("hash") else fila["id"]


def md5_mod(clave: str, n: int) -> int:
    return int(hashlib.md5(clave.encode()).hexdigest(), 16) % n


def cubo(mb: float) -> int:
    return 0 if mb < .1 else 1 if mb < .5 else 2 if mb < 1 else 3 if mb < 2 else 4 if mb < 5 else 5


def paginas_por_tamano(log: Path | None) -> dict[int, float]:
    """Paginas medias por tamano, de una bitacora. Sin bitacora, valores del 6-oct."""
    if not log or not log.exists():
        return {0: 1.3, 1: 2.9, 2: 7.5, 3: 14.1, 4: 27.9, 5: 33.8}
    cub = collections.defaultdict(lambda: [0, 0])
    for linea in open(log, encoding="utf-8"):
        m = re.search(r"  LEIDO .*\(([\d.]+) MB, (\d+) pag\.\)", linea)
        if m:
            c = cub[cubo(float(m[1]))]
            c[0] += int(m[2])
            c[1] += 1
    return {k: v[0] / v[1] for k, v in cub.items()}


def leer_diarios(rutas: list[Path]) -> dict[str, dict]:
    ficheros = []
    for r in rutas:
        ficheros += sorted(r.rglob("*.jsonl")) if r.is_dir() else [r]
    fichas = {}
    for f in ficheros:
        for linea in open(f, encoding="utf-8", errors="replace"):
            try:
                x = json.loads(linea)
            except ValueError:
                continue
            if "id" not in x or "was_read" not in x or hay_que_releer(x):
                continue
            previa = fichas.get(x["id"])
            if previa and previa["was_read"] and not x.get("was_read"):
                continue        # una lectura con texto no la pisa una sin texto
            fichas[x["id"]] = {"was_read": bool(x.get("was_read")), "ok": es_definitiva(x),
                               "motivo": str(x.get("not_read_because") or ""), "hash": x.get("hash")}
    print(f"  diarios: {len(ficheros)} ficheros, {len(fichas):,} archivos con ficha valida")
    return fichas


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--etapa", required=True, help="Nombre corto, p. ej. 2022 (va en el nombre de las listas)")
    p.add_argument("--expedientes", required=True, type=Path)
    p.add_argument("--arbol", required=True, type=Path)
    p.add_argument("--diarios", required=True, nargs="+", type=Path)
    p.add_argument("--cola", type=Path, default=AQUI / "codigo" / "salida" / "cola_ocr.txt")
    p.add_argument("--log", type=Path, default=None)
    p.add_argument("--salida", type=Path, default=None)
    p.add_argument("--mover", action="append", default=[], metavar="T:DE:A",
                   help="Mover a mano el medio trozo T (de 24) del equipo DE al A; sin esto se iguala solo")
    args = p.parse_args()
    salida = args.salida or Path("salida") / f"etapa_{args.etapa}"
    salida.mkdir(parents=True, exist_ok=True)

    elegidos = set(pd.read_csv(args.expedientes, encoding="utf-8-sig")["expediente"])
    arbol = [json.loads(l) for l in open(args.arbol, encoding="utf-8")]
    filas_arbol = [r for r in arbol if r["exp"] in elegidos]
    no_estan = elegidos - {r["exp"] for r in filas_arbol}
    print(f"  expedientes elegidos: {len(elegidos):,} | en el arbol: {len(elegidos) - len(no_estan):,}"
          + (f" | NO estan en el arbol: {len(no_estan)} (renombrados o borrados)" if no_estan else ""))

    fichas = leer_diarios(args.diarios)
    leido_por_hash = {}
    for i, f in fichas.items():
        if f["was_read"] and f["hash"]:
            leido_por_hash.setdefault(f["hash"], i)
    cola = ({l.strip() for l in open(args.cola, encoding="utf-8") if l.strip()}
            if args.cola.exists() else set())
    dueno = {t: eq for eq, ts in EQUIPOS.items() for t in ts}

    filas = []
    for r in filas_arbol:
        f = fichas.get(r["id"])
        h = r.get("hash") or None
        es_img = extension_de(r["name"]) in IMAGENES
        if f and f["ok"]:
            estado = "leido" if f["was_read"] else "resuelto sin texto (definitivo)"
        elif h and h in leido_por_hash:
            estado = "copia de un archivo ya leido"
        elif es_img or r["id"] in cola or (f and f["motivo"] == NECESITA_OCR):
            estado = "pendiente: OCR"
        elif f is None:
            estado = "pendiente: nunca leido"
        else:
            estado = "pendiente: fallo al leer"
        k = clave_de(r)
        filas.append({"id": r["id"], "expediente": r["exp"], "archivo": r["name"], "mb": r["size"] / 2 ** 20,
                      "hash": h, "imagen": es_img, "estado": estado,
                      "trozo_24": md5_mod(k, 24), "equipo": dueno[md5_mod(k, 12)]})
    d = pd.DataFrame(filas)
    pend = d[d.estado.str.startswith("pendiente")].copy()

    media = paginas_por_tamano(args.log)

    def horas(x: pd.DataFrame) -> float:
        vistos, h = set(), 0.0
        for r in x.itertuples():
            if r.hash and r.hash in vistos:
                h += 2 / 3600
                continue
            if r.hash:
                vistos.add(r.hash)
            pag = 1 if r.imagen else (r.mb * 6 if r.mb >= 5 else media.get(cubo(r.mb), 7))
            if r.estado == "pendiente: OCR":
                h += pag / PAGINAS_POR_HORA + 4 / 3600
            elif str(r.archivo).lower().endswith(".pdf"):
                h += PDF_QUE_NECESITA_OCR * pag / PAGINAS_POR_HORA + 5 / 3600
            else:
                h += 5 / 3600
        return h

    # Igualar: medios trozos (modulo 24) del mas cargado al menos cargado. Con --mover se
    # hace a mano y no se iguala solo: es lo que permite reproducir una etapa ya repartida.
    movidos = []
    for m in args.mover:
        t, de, a = m.split(":")
        if de not in EQUIPOS or a not in EQUIPOS:
            raise SystemExit(f"--mover {m}: los equipos son {', '.join(EQUIPOS)}")
        pend.loc[(pend.equipo == de) & (pend.trozo_24 == int(t)), "equipo"] = a
        movidos.append(f"medio trozo {t} (de 24): {de} -> {a}  (a mano)")
    for _ in range(0 if args.mover else 6):
        carga = {eq: horas(pend[pend.equipo == eq]) for eq in EQUIPOS}
        mas, menos = max(carga, key=carga.get), min(carga, key=carga.get)
        if carga[mas] <= carga[menos] * (1 + DESEQUILIBRIO_MAXIMO):
            break
        candidatos = sorted({t for t in pend[pend.equipo == mas].trozo_24})
        mejor, mejor_dif = None, carga[mas] - carga[menos]
        for t in candidatos:
            h_t = horas(pend[(pend.equipo == mas) & (pend.trozo_24 == t)])
            dif = abs((carga[mas] - h_t) - (carga[menos] + h_t))
            if dif < mejor_dif:
                mejor, mejor_dif = t, dif
        if mejor is None:
            break
        pend.loc[(pend.equipo == mas) & (pend.trozo_24 == mejor), "equipo"] = menos
        movidos.append(f"medio trozo {mejor} (de 24): {mas} -> {menos}")

    # Comprobaciones: sin solapes, todo repartido, cada contenido en un solo equipo.
    assert pend.id.is_unique
    assert pend[pend.hash.notna()].groupby("hash").equipo.nunique().max() <= 1
    d.loc[pend.index, "equipo"] = pend.equipo

    print(f"\n  archivos de la etapa: {len(d):,}")
    print("  " + d.estado.value_counts().to_string().replace("\n", "\n  "))
    for m in movidos:
        print(f"  igualado: {m}")
    print()
    total = 0
    for eq in EQUIPOS:
        x = pend[pend.equipo == eq]
        total += len(x)
        cab = (f"# Etapa {args.etapa}: lo PENDIENTE que le toca a {eq}. {len(x):,} archivos.\n"
               f"# Un id de archivo de SharePoint por linea. Uso: describir_casos.py ... --lista <este fichero>\n")
        (salida / f"lista_{args.etapa}_{eq}.txt").write_text(cab + "\n".join(sorted(x.id)) + "\n", encoding="utf-8")
        h = horas(x)
        print(f"  lista_{args.etapa}_{eq}.txt  {len(x):>6,} archivos | {x.mb.sum() / 1024:5.1f} GB | ~{h:4.0f} h ({h / 24:.1f} dias)")
    assert total == len(pend)
    print(f"  total {total:,} = pendientes {len(pend):,} | sin solapes: comprobado")

    d["pendiente"] = d.estado.str.startswith("pendiente")
    por_exp = d.groupby("expediente").agg(
        archivos=("id", "size"), GB=("mb", lambda s: round(s.sum() / 1024, 2)),
        leidos=("estado", lambda s: int((s == "leido").sum())),
        copias_de_leidos=("estado", lambda s: int((s == "copia de un archivo ya leido").sum())),
        pendientes=("pendiente", "sum")).reset_index()
    por_exp["completo"] = por_exp.pendientes.eq(0).map({True: "si", False: "no"})
    por_exp.to_csv(salida / f"expedientes_{args.etapa}_estado.csv", index=False, encoding="utf-8-sig")
    pend[["id", "expediente", "archivo", "mb", "estado", "equipo"]].to_csv(
        salida / f"archivos_pendientes_{args.etapa}.csv", index=False, encoding="utf-8-sig")
    print(f"\n  expedientes completos: {(por_exp.completo == 'si').sum():,} de {len(por_exp):,}")
    print(f"  en {salida}  (el detalle de pendientes lleva nombres de archivos: no sale del equipo)")


if __name__ == "__main__":
    main()
