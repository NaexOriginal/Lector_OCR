r"""Deja reparto_ocr/ listo para copiar a los otros equipos. Lo corre Rafa.

POR QUE HACE FALTA. `describir_casos.py` vive en la raiz del repositorio y arrastra
dieciseis modulos mas. Compartir solo la carpeta de instrucciones no sirve de nada:
el que la recibe no tiene con que leer.

POR QUE ES UN SCRIPT Y NO UNA COPIA A MANO. Doce equipos van a correr esto durante
dias. Si alguien arregla un fallo en la raiz y la copia se queda vieja, once
maquinas siguen leyendo con el codigo de antes y nadie se entera hasta el final.
Volviendo a correr esto, la copia se rehace entera.

QUE HACE

    codigo/          los 17 ficheros .py, con su estructura de paquetes
    codigo/salida/   los cuatro ficheros que NO se pueden generar en el otro equipo

LOS DATOS VAN DENTRO DE codigo/salida/ Y NO EN UNA CARPETA APARTE. No es una
preferencia: los modulos hacen `SALIDA_DIR = Path(__file__).parent / "salida"`, o
sea que buscan sus ficheros AL LADO DE SI MISMOS. Puestos en otro sitio, el primer
equipo que lo intente se estrella con "Falta cache_sharepoint.json" sin saber por
que, porque el fichero SI estaba, solo que donde nadie lo mira.

EL .env NO SE COPIA, y es a proposito: lleva las credenciales de Graph. Va por
canal interno, a mano, y esta dicho en INSTALAR.md.

Uso:
    .venv\Scripts\python.exe reparto_ocr\empaquetar.py
    .venv\Scripts\python.exe reparto_ocr\empaquetar.py --comprobar
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent

# El punto de partida. Todo lo demas se descubre siguiendo sus imports.
ENTRADA = "describir_casos"

# Lo que no se puede generar en el equipo que recibe: o se copia, o no hay tanda.
# LEYENDO DESDE MATTERS BASTA EL ARBOL DE PRODUCCION. El expediente de cada archivo
# es la carpeta en la que esta, asi que ya no viajan el plan de fase 2, el mapa de
# nombres ni el reparto: menos datos que copiar y ninguno que cruzar.
DATOS = {
    "salida/arbol_matters.jsonl": "cada archivo de produccion, con su expediente",
}


def indice_de_leidos(destino: Path) -> int:
    """Los IDs DE MATTERS de lo ya leido, SIN el texto.

    POR QUE NO SE MANDA EL DIARIO. Pesa mas de 100 MB y lleva el texto integro de
    lo leido: reportes de credito, extractos bancarios y numeros de la Seguridad
    Social, en claro. Repartirlo entre doce personas seria sacar eso del sitio
    donde el despacho decidio que estuviera.

    LOS IDS SON LOS DE MATTERS, no los del diario. Lo leido hasta ahora se leyo
    desde el sitio de origen y su id es el de alli; en Matters el mismo documento
    tiene otro. Por eso se reconoce por expediente + nombre + tamano (ver
    describir_casos.estado_de_lectura) y se escribe el id que va a ver el otro
    equipo. Con el id viejo, ninguno casaria y cada maquina releeria su parte.
    """
    from describir_casos import estado_de_lectura

    ids = estado_de_lectura("matters")["ya_leidos_ids"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(chr(10).join(sorted(ids)), encoding="utf-8")
    return len(ids)


def resolver(modulo: str) -> Path | None:
    for candidato in (RAIZ / f"{modulo.replace('.', '/')}.py",
                      RAIZ / modulo.replace(".", "/") / "__init__.py"):
        if candidato.exists():
            return candidato
    return None


def que_hace_falta() -> set[Path]:
    """Los .py del repositorio que se necesitan, siguiendo los imports de verdad.

    Se descubren en vez de escribirse a mano porque una lista escrita se queda
    corta el dia que alguien anade un import, y el fallo aparece en el equipo del
    otro con un ImportError a mitad de la noche.
    """
    propios = {p.stem for p in RAIZ.glob("*.py")} | {"extractor_completo", "extraccion"}
    pendientes, vistos, encontrados = [ENTRADA], set(), set()
    while pendientes:
        modulo = pendientes.pop()
        if modulo in vistos:
            continue
        vistos.add(modulo)
        fichero = resolver(modulo)
        if not fichero:
            continue
        encontrados.add(fichero)
        es_paquete = (RAIZ / modulo.replace(".", "/") / "__init__.py").exists()
        paquete = modulo if es_paquete else (modulo.rsplit(".", 1)[0] if "." in modulo else "")
        for nodo in ast.walk(ast.parse(fichero.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.ImportFrom):
                mod = nodo.module or ""
                if nodo.level and paquete:            # from . import ocr
                    mod = f"{paquete}.{mod}" if mod else paquete
                if mod.split(".")[0] in propios:
                    pendientes.append(mod)
                    pendientes += [f"{mod}.{a.name}" for a in nodo.names]
            elif isinstance(nodo, ast.Import):
                pendientes += [a.name for a in nodo.names
                               if a.name.split(".")[0] in propios]
    # Los __init__ de los paquetes no siempre salen por import y sin ellos no
    # se puede importar nada de dentro.
    for paquete in ("extractor_completo", "extraccion"):
        ini = RAIZ / paquete / "__init__.py"
        if ini.exists():
            encontrados.add(ini)
    return encontrados


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--comprobar", action="store_true",
                   help="Solo dice que copiaria y si la copia esta al dia")
    args = p.parse_args()

    ficheros = que_hace_falta()
    destino_codigo = AQUI / "codigo"
    destino_datos = AQUI / "codigo" / "salida"

    print(f"\n  CODIGO ({len(ficheros)} ficheros)")
    desfasados = []
    for fichero in sorted(ficheros):
        relativa = fichero.relative_to(RAIZ)
        copia = destino_codigo / relativa
        estado = "nuevo"
        if copia.exists():
            if copia.read_bytes() == fichero.read_bytes():
                estado = "igual"
            else:
                estado = "DESFASADO"
                desfasados.append(str(relativa))
        print(f"     {estado:<10} {relativa}")
        if not args.comprobar:
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fichero, copia)

    print(f"\n  DATOS ({len(DATOS)} ficheros)")
    faltan = []
    for relativa, para_que in DATOS.items():
        origen = RAIZ / relativa
        if not origen.exists():
            print(f"     FALTA      {relativa}")
            faltan.append(relativa)
            continue
        mb = origen.stat().st_size / 2 ** 20
        copia = destino_datos / Path(relativa).name
        print(f"     {mb:>7.0f} MB  {Path(relativa).name}   ({para_que})")
        if not args.comprobar:
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, copia)

    if not args.comprobar:
        sys.path.insert(0, str(RAIZ))
        cuantos = indice_de_leidos(destino_datos / "ya_leidos_matters.txt")
        print(f"\n  YA LEIDOS\n     {cuantos:,} identificadores  (sin el texto: "
              "el diario lleva SSN y reportes de credito en claro)")

    # LO QUE NO PUEDE VIAJAR. Un diario (textos_*.jsonl) lleva el texto entero de
    # los documentos, SSN y reportes de credito en claro; y los datos del sitio de
    # origen (cache_sharepoint.json, plan_fase2...) ya no se usan y delatan un
    # paquete viejo. El 29-sep habia de las dos cosas dentro de reparto_ocr: un
    # diario de 7 MB en codigo/salida y los entregados en json_entregados/.
    permitidos = {"arbol_matters.jsonl", "ya_leidos_matters.txt", "cola_ocr.txt"}
    intrusos = [p for p in AQUI.rglob("*")
                if p.is_file() and ".venv" not in p.parts
                and (p.name.startswith("textos_")
                     or (p.parent == destino_datos and p.name not in permitidos))]
    if intrusos:
        print(f"\n  NO COMPRIMAS TODAVIA: hay {len(intrusos)} fichero(s) que no deben viajar:")
        for p in intrusos:
            print(f"     {p.relative_to(AQUI)}")
        print("  Sacalos de reparto_ocr (los diarios, a salida\\ del repositorio).")
        if args.comprobar:
            sys.exit(1)

    total = sum((RAIZ / r).stat().st_size for r in DATOS if (RAIZ / r).exists())
    print(f"\n  los datos pesan {total / 2**20:.0f} MB en total")

    if args.comprobar:
        if desfasados:
            print(f"\n  LA COPIA ESTA VIEJA en {len(desfasados)} fichero(s):")
            for f in desfasados:
                print(f"     {f}")
            print("  Vuelve a correr esto sin --comprobar antes de repartir.")
            sys.exit(1)
        print("\n  La copia esta al dia.")
        return

    # LA SESION DE MICROSOFT NO VIAJA. Si se ha corrido comprobar.py aqui, en
    # codigo/ queda el token de la cuenta de Rafa: quien recibiera la carpeta
    # entraria a SharePoint como el. Cada equipo inicia su propia sesion.
    for sesion in AQUI.rglob(".msal_cache.json"):
        sesion.unlink()
        print(f"\n  BORRADA la sesion {sesion.relative_to(AQUI)} (no se reparte)")

    if faltan:
        print(f"\n  OJO: faltan {len(faltan)} fichero(s) de datos. Sin ellos el otro "
              "equipo no puede empezar.")

    print(f"""
  LISTO. Para repartir:

     1. Comprime la carpeta reparto_ocr entera
     2. Mandala a cada equipo
     3. El .env va APARTE, por canal interno -- lleva credenciales.
        Se deja en reparto_ocr/codigo/.env, junto a sp_conexion.py
     4. Cada uno sigue INSTALAR.md y luego EJECUTAR.md

  Antes de mandarla, mira que no se te haya quedado vieja:
     .venv\\Scripts\\python.exe reparto_ocr\\empaquetar.py --comprobar""")


if __name__ == "__main__":
    main()
