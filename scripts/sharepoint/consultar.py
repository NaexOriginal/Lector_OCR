r"""Mirar SharePoint desde la consola: listar carpetas, buscar expedientes, sacar el arbol.

SOLO LEE. No crea, no mueve, no borra y no descarga contenido de documentos: lo que
devuelve son nombres, tamanos, fechas e identificadores.

Usa la conexion de sp_conexion.py (al lado), que lee las credenciales del .env o de
las variables de entorno:

    GRAPH_TENANT_ID   el tenant del despacho
    GRAPH_CLIENT_ID   la app de Graph del despacho (cliente publico, sin secret)
    GRAPH_SITIO_MATTERS   opcional; por defecto amshenllp.sharepoint.com:/teams/Matters

La primera vez abre el navegador para iniciar sesion; despues reutiliza el token
(.msal_cache.json, que no se sube al repositorio). La app actua como quien inicia
sesion: solo alcanza lo que esa persona ya puede ver en SharePoint.

Uso (desde la carpeta scripts\sharepoint):
    python consultar.py --quien
    python consultar.py --buscar "Adeyemi"
    python consultar.py --listar "Matters/Adeyemi - Closed - 601243 - 0"
    python consultar.py --listar "Matters/Adeyemi - Closed - 601243 - 0" --recursivo
    python consultar.py --arbol                     (todo Matters, ~35-40 min)
    python consultar.py --arbol --salida otra_ruta.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sp_conexion import SALIDA_DIR, Graph, leer_env  # noqa: E402

SITIO_MATTERS = leer_env("GRAPH_SITIO_MATTERS") or "amshenllp.sharepoint.com:/teams/Matters"
CARPETA_MATTERS = "Matters"
# Los JSONL que generamos nosotros en cada expediente: no son documentos del caso.
PROPIO = re.compile(r"^Claude-[^/]*\.jsonl?$", re.I)


def drive_de(g: Graph, sitio: str) -> str:
    """El drive (biblioteca de documentos) principal de un sitio. Preguntado, no supuesto."""
    s = g.get(f"/sites/{sitio}")
    return g.get(f"/sites/{s['id']}/drives")["value"][0]["id"]


def hijos(g: Graph, drive: str, item_id: str):
    r = g.get(f"/drives/{drive}/items/{item_id}/children",
              **{"$select": "id,name,size,folder,file,lastModifiedDateTime,fileSystemInfo",
                 "$top": "999"})
    while True:
        yield from r.get("value", [])
        siguiente = r.get("@odata.nextLink")
        if not siguiente:
            break
        r = g.get(siguiente)


def quien(g: Graph, drive: str) -> None:
    # /me pide User.Read, que la app no siempre tiene: si falla, se sigue. Va por la
    # sesion directa porque Graph.get termina el proceso ante un error.
    r = g.sesion.get("https://graph.microsoft.com/v1.0/me?$select=displayName,userPrincipalName",
                     timeout=60)
    if r.ok:
        print(f"  sesion: {r.json().get('displayName')} <{r.json().get('userPrincipalName')}>")
    else:
        print(f"  sesion: no se pudo leer el usuario ({r.status_code}); la conexion funciona igual")
    print(f"  alcances: {', '.join(g.alcances_concedidos()) or '(no se pudieron leer)'}")
    raiz = g.get(f"/drives/{drive}/root:/{CARPETA_MATTERS}", **{"$select": "name,folder"})
    print(f"  {SITIO_MATTERS}/{CARPETA_MATTERS}: {raiz.get('folder', {}).get('childCount', '?')} expedientes")


def buscar(g: Graph, drive: str, texto: str) -> None:
    """Expedientes de Matters cuyo nombre contiene el texto (sin distinguir mayusculas)."""
    raiz = g.get(f"/drives/{drive}/root:/{CARPETA_MATTERS}")
    t = texto.lower()
    n = 0
    for x in hijos(g, drive, raiz["id"]):
        if "folder" in x and t in x["name"].lower():
            n += 1
            print(f"  {x['name']:<70} {x['folder'].get('childCount', 0):>4} elementos  "
                  f"modificado {x.get('lastModifiedDateTime', '')[:10]}")
    print(f"\n  {n} expediente(s) con '{texto}'")


def listar(g: Graph, drive: str, ruta: str, recursivo: bool) -> None:
    item = g.get(f"/drives/{drive}/root:/{quote(ruta.strip('/'))}")
    total, tamano, tipos = 0, 0, Counter()

    def recorrer(item_id: str, prefijo: str) -> None:
        nonlocal total, tamano
        for x in hijos(g, drive, item_id):
            if "folder" in x:
                print(f"  {prefijo}{x['name']}/")
                if recursivo:
                    recorrer(x["id"], prefijo + "    ")
                continue
            if PROPIO.match(x["name"]):
                continue
            total += 1
            tamano += x.get("size", 0)
            tipos[x["name"].rsplit(".", 1)[-1].lower() if "." in x["name"] else "(sin extension)"] += 1
            original = (x.get("fileSystemInfo") or {}).get("lastModifiedDateTime", "")[:10]
            print(f"  {prefijo}{x['name']:<60} {x.get('size', 0) / 2 ** 20:8.2f} MB  "
                  f"SharePoint {x.get('lastModifiedDateTime', '')[:10]}  original {original}")

    recorrer(item["id"], "")
    print(f"\n  {total} archivo(s), {tamano / 2 ** 20:,.1f} MB"
          + ("" if recursivo else "  (solo este nivel; --recursivo para entrar en subcarpetas)"))
    if tipos:
        print("  por tipo: " + ", ".join(f"{k} {v}" for k, v in tipos.most_common(10)))


def arbol(g: Graph, drive: str, salida: Path) -> None:
    """Todo Matters, una linea por archivo, como arbol_matters.jsonl de produccion.

    Se escribe en un .parcial y se renombra al final: un fichero a medias con el nombre
    bueno se tomaria por completo.
    """
    raiz = g.get(f"/drives/{drive}/root:/{CARPETA_MATTERS}")
    url, vistos, n, t0 = f"/drives/{drive}/items/{raiz['id']}/delta", set(), 0, time.time()
    salida.parent.mkdir(parents=True, exist_ok=True)
    parcial = salida.with_suffix(".parcial")
    with open(parcial, "w", encoding="utf-8") as fh:
        while url:
            r = g.get(url)
            for x in r.get("value", []):
                if x["id"] in vistos or "file" not in x:
                    continue
                vistos.add(x["id"])
                ruta = str(x.get("parentReference", {}).get("path", ""))
                if "root:" not in ruta:
                    continue
                resto = ruta.split("root:", 1)[1].lstrip("/")
                if not resto.startswith(CARPETA_MATTERS):
                    continue
                resto = resto[len(CARPETA_MATTERS):].lstrip("/")
                if not resto or PROPIO.match(x.get("name", "")):
                    continue
                exp, _, sub = resto.partition("/")
                fh.write(json.dumps({
                    "id": x["id"], "name": x.get("name"), "size": x.get("size", 0),
                    "hash": x.get("file", {}).get("hashes", {}).get("quickXorHash"),
                    "exp": exp, "sub": sub,
                    "modified": x.get("lastModifiedDateTime"),
                    "file_modified": (x.get("fileSystemInfo") or {}).get("lastModifiedDateTime"),
                }, ensure_ascii=False) + "\n")
                n += 1
                if n % 25000 == 0:
                    print(f"  {n:,} archivos  ({time.time() - t0:.0f}s)", flush=True)
            url = r.get("@odata.nextLink")
    parcial.replace(salida)
    print(f"\n  {n:,} archivos en {salida}  ({time.time() - t0:.0f}s)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--quien", action="store_true", help="Con que cuenta y permisos se conecta")
    p.add_argument("--buscar", metavar="TEXTO", help="Expedientes de Matters cuyo nombre contiene TEXTO")
    p.add_argument("--listar", metavar="RUTA", help="Archivos de una carpeta, p. ej. 'Matters/<expediente>'")
    p.add_argument("--recursivo", action="store_true", help="Con --listar, entrar tambien en las subcarpetas")
    p.add_argument("--arbol", action="store_true", help="Todo Matters a un jsonl (35-40 minutos)")
    p.add_argument("--salida", default=str(SALIDA_DIR / "arbol_matters.jsonl"))
    args = p.parse_args()
    if not any((args.quien, args.buscar, args.listar, args.arbol)):
        p.error("elige --quien, --buscar, --listar o --arbol")

    g = Graph()
    drive = drive_de(g, SITIO_MATTERS)
    if args.quien:
        quien(g, drive)
    if args.buscar:
        buscar(g, drive, args.buscar)
    if args.listar:
        listar(g, drive, args.listar, args.recursivo)
    if args.arbol:
        arbol(g, drive, Path(args.salida))


if __name__ == "__main__":
    main()
