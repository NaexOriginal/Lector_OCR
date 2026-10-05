r"""Copia las carpetas de caso al SharePoint nuevo, YA CON EL ID NUEVO EN EL NOMBRE.

    origen   RevOps-2.Projects / zz-pruebas-no-usar/Matters/108 Queens Management - 416087 - 712887-2023
    destino  Matters           / Matters/108 Queens Management - 200025 - 712887-2023

UNA SOLA PASADA, y esa es toda la gracia. El /copy de Graph acepta un 'name', asi
que la carpeta LLEGA renombrada: no hace falta copiar primero y renombrar despues.
Son 1.296 operaciones en vez de 2.592, y desaparece la ventana en la que conviven
dos esquemas de nombres.

Y ARRASTRA LA ESTRUCTURA. Copiar la carpeta del caso se lleva sus subcarpetas
enteras -- 35.567 en total para Active -- dentro de esas mismas 1.296 llamadas. No
hay que crear estructura en el destino.

QUE SE COPIA SALE DE UN EXCEL, no de una deduccion: el mismo que produce
plan_ids_nuevos.py, con 'Se llamaba' y 'Debe llamarse'. Asi lo que se va a tocar se
puede leer antes de tocarlo, y el candado de colisiones ya se aplico ahi.

ES ENTRE DOS SITIOS DISTINTOS. Eso Graph lo admite pasando driveId en el
parentReference, pero no se comporta igual que copiar dentro del mismo drive: puede
ser mas lento y el 202 tarda mas en resolverse. Por eso conviene empezar con
--limite 1 y mirar que llega bien antes de soltar las 1.296.

NO BORRA NADA EN EL ORIGEN. Copia.

Uso:
    .venv\Scripts\python.exe copiar_a_matters.py --lista salida/ids_nuevos.xlsx --simular
    .venv\Scripts\python.exe copiar_a_matters.py --lista salida/ids_nuevos.xlsx --limite 1
    .venv\Scripts\python.exe copiar_a_matters.py --lista salida/ids_nuevos.xlsx
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import pandas as pd

from crear_subcarpetas import RAIZ_PERMITIDA, contenedor_real
from extraccion.config import drive_id
from sp_conexion import Graph
from ver_matters import SALIDA_DIR, tabla, titulo

DESDE, HASTA = "Se llamaba", "Debe llamarse"

# El sitio de destino. Se resuelve por su ruta y no por un id escrito a mano: un id
# pegado aqui no dice a que apunta, y el dia que apunte a otro sitio nadie lo vera.
SITIO_DESTINO = "amshenllp.sharepoint.com:/teams/Matters"
CARPETA_DESTINO = "Matters"

DIARIO = SALIDA_DIR / "copia_a_matters.jsonl"

# Pausa por hilo. Igual que en mudar.py: el limite de SharePoint es por operacion.
PAUSA = 0.25


def destino(g: Graph) -> tuple[str, str]:
    """(drive del sitio nuevo, id de la carpeta que recibe). Preguntado, no supuesto."""
    sitio = g.get(f"/sites/{SITIO_DESTINO}")
    unidad = g.get(f"/sites/{sitio['id']}/drives")["value"][0]
    carpeta = g.get(f"/drives/{unidad['id']}/root:/{CARPETA_DESTINO}")
    return unidad["id"], carpeta["id"]


def ya_hechas() -> set[str]:
    """Los nombres de destino que ya se copiaron bien. Permite cortar y seguir."""
    if not DIARIO.exists():
        return set()
    hechas = set()
    for linea in DIARIO.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            registro = json.loads(linea)
            if registro.get("estado") == "ok":
                hechas.add(registro["destino"])
    return hechas


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lista", default=str(SALIDA_DIR / "ids_nuevos.xlsx"),
                   help=f"Excel con '{DESDE}' y '{HASTA}'")
    p.add_argument("--limite", type=int, default=0, help="Solo las N primeras")
    p.add_argument("--hilos", type=int, default=8)
    p.add_argument("--simular", action="store_true", help="No escribe: dice que haria")
    # PARA LAS QUE NUNCA ENTRARON AL CONTENEDOR. El camino normal es
    # 'Closed Matters' -> renombrar al contenedor -> copiar al sitio nuevo. Los
    # casos que el plan dejo en 'el caso no esta identificado' no dieron el primer
    # paso: no tenian ID, y sin ID no hay nombre nuevo que ponerles. Siguen en su
    # seccion original con su nombre de siempre.
    #
    # Lo unico que cambia es DE DONDE se lee; el nombre de destino, el diario, el
    # candado y el trato del 409 son los mismos. Duplicar el script para cambiar
    # una ruta habria dejado dos sitios donde arreglar el proximo fallo del /copy.
    p.add_argument("--desde-seccion", choices=["Active Matters", "Closed Matters"],
                   help="Leer del origen en esa seccion en vez del contenedor")
    args = p.parse_args()

    lista = pd.read_excel(args.lista, sheet_name="Renombrar")
    faltan = [c for c in (DESDE, HASTA) if c not in lista.columns]
    if faltan:
        raise SystemExit(f"A {args.lista} le faltan columnas: {faltan}")
    lista = lista[lista[DESDE].notna() & lista[HASTA].notna()]

    hechas = ya_hechas()
    pares = [(str(r[DESDE]), str(r[HASTA])) for _, r in lista.iterrows()
             if str(r[HASTA]) not in hechas]
    saltadas = len(lista) - len(pares)
    if args.limite:
        pares = pares[: args.limite]

    titulo(f"COPIA AL SHAREPOINT NUEVO ({len(pares):,} carpetas)")
    print(f"  destino: {SITIO_DESTINO} / {CARPETA_DESTINO}")
    if saltadas:
        print(f"  {saltadas:,} ya estaban copiadas: se saltan")
    print(tabla(pd.DataFrame([{DESDE: a[:52], HASTA: b[:52]} for a, b in pares[:8]])))

    if args.simular:
        print("\n(simulacion: no se escribio nada)")
        return

    g = Graph()
    origen_drive = drive_id()
    contenedor = args.desde_seccion or contenedor_real(g, origen_drive)
    destino_drive, destino_id = destino(g)
    print(f"\n  origen  drive {origen_drive[:22]}...  /{RAIZ_PERMITIDA}/{contenedor}")
    print(f"  destino drive {destino_drive[:22]}...  /{CARPETA_DESTINO}\n")

    candado = threading.Lock()
    cuenta, errores = Counter(), []
    arranque = time.time()

    def anotar(registro: dict) -> None:
        linea = json.dumps(registro, ensure_ascii=False)
        with candado, DIARIO.open("a", encoding="utf-8") as diario:
            diario.write(linea + "\n")

    def una(par) -> None:
        viejo, nuevo = par
        anotacion = {"origen": viejo, "destino": nuevo,
                     "cuando": datetime.now().isoformat(timespec="seconds")}

        def cerrar(estado: str, detalle: str = "") -> None:
            anotar({**anotacion, "estado": estado,
                    **({"error": detalle} if estado == "error" else {"monitor": detalle})})
            with candado:
                cuenta[estado] += 1
                if estado == "error":
                    errores.append((viejo, detalle))

        ruta = quote(f"{RAIZ_PERMITIDA}/{contenedor}/{viejo}", safe="/")
        try:
            item = g.get(f"/drives/{origen_drive}/root:/{ruta}")
        except SystemExit:
            cerrar("error", "no existe en el origen")
            return

        # Candado: nada que no cuelgue del contenedor esperado.
        padre = item.get("parentReference", {}).get("path", "")
        if not padre.endswith(f"root:/{RAIZ_PERMITIDA}/{contenedor}"):
            cerrar("error", f"candado: esta en {padre!r}")
            return

        anotar({**anotacion, "estado": "intento", "id": item["id"]})
        try:
            r = g.post_respuesta(
                f"/drives/{origen_drive}/items/{item['id']}/copy",
                {"parentReference": {"driveId": destino_drive, "id": destino_id},
                 "name": nuevo})
            if r.status_code in (200, 202):
                cerrar("ok", r.headers.get("Location", ""))
            elif r.status_code == 409 and "nameAlreadyExists" in r.text:
                # NO ES UN FALLO, casi siempre es el reintento viendo su propio
                # trabajo. /copy no es idempotente: si Graph acepta la copia pero la
                # respuesta se pierde en un 429, el reintento choca con la carpeta
                # que creo el primer intento. Paso de verdad: 7 de los 8 'fallos'
                # de la corrida del 24-sep estaban en el destino, completos.
                #
                # Aun asi NO se da por bueno a ciegas: se comprueba que este. Un 409
                # tambien puede venir de una carpeta que se llame igual por otro
                # motivo, y ahi si hay algo que mirar.
                try:
                    g.get(f"/drives/{destino_drive}/root:/{CARPETA_DESTINO}/"
                          f"{quote(nuevo, safe='')}")
                except SystemExit:
                    cerrar("error", "409 nameAlreadyExists pero NO esta en el destino")
                else:
                    cerrar("ok", "ya estaba en el destino (409 comprobado)")
            else:
                cerrar("error", f"{r.status_code}: {r.text[:120]}")
        except Exception as error:  # noqa: BLE001
            cerrar("error", f"{type(error).__name__}: {error}"[:160])
        time.sleep(PAUSA)

    with ThreadPoolExecutor(max_workers=args.hilos) as piscina:
        for n, _ in enumerate(piscina.map(una, pares), 1):
            if n % 10 == 0:
                ritmo = n / max(1e-9, (time.time() - arranque) / 60)
                print(f"  [{n:>5}/{len(pares):,}] ok {cuenta['ok']:,}"
                      f"  fallos {cuenta['error']:,}   {ritmo:,.0f}/min"
                      f"   faltan {(len(pares)-n)/max(ritmo,1e-9)/60:,.1f} h ",
                      end="\r", flush=True)
    print()

    titulo("RESULTADO")
    print(f"  copiadas: {cuenta['ok']:,}\n  fallos:   {cuenta['error']:,}")
    for viejo, motivo in errores[:10]:
        print(f"    {viejo[:46]:<48} {motivo}")
    print(f"\nDiario: {DIARIO}")
    print("  /copy es asincrono: espera unos minutos antes de comprobar el destino.")


if __name__ == "__main__":
    main()
