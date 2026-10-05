r"""Crea la estructura de subcarpetas dentro de cada caso de Matter (New Names).

Dos plantillas, segun la materia:

    FCRA          Identity Theft, Wrongful Reporting, EFTA, RESPA y la familia FCRA
    Foreclosure   todo lo demas: Foreclosure, QWR, Article 15, Bankruptcy...

La estructura sale del esquema que paso produccion. Dos decisiones tomadas al
escribirlo, ambas revisables:

  - `Plaintiff Response to Rule 12 PMC Letter (1)` viene marcado como duplicado en
    el propio esquema y NO se crea: replicarlo serian 655 carpetas de basura.
  - `label template Petroff Amshen (2).docx` es un archivo, no una carpeta, asi que
    no se copia aqui. Hay que decidir de donde sale.

Seguridad: solo escribe debajo de Matter (New Names), y aborta si el destino no cae
ahi. Es reanudable: lo que ya existe se salta, asi que se puede cortar y relanzar.

Uso:
    .venv\Scripts\python.exe crear_subcarpetas.py --muestra 25 --simular
    .venv\Scripts\python.exe crear_subcarpetas.py --muestra 25
    .venv\Scripts\python.exe crear_subcarpetas.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from extraccion.config import drive_id
from sp_conexion import Graph
from ver_matters import SALIDA_DIR, tabla, titulo

RAIZ = Path(__file__).resolve().parent
INVENTARIO = RAIZ / "extraccion" / "json_cristian" / "inventario_completo"

RAIZ_PERMITIDA = "zz-pruebas-no-usar"

# El contenedor de destino. Se llamo 'Matter (New Names)' mientras era provisional;
# desde el 23-sep-2026 su nombre definitivo es 'Matters'.
CONTENEDOR = "Matters"

# El nombre viejo NO se borra, se sigue reconociendo. Hay dos sitios llenos de el
# que no cambian solos: el arbol cacheado y los JSON ya escritos. Y entre que
# alguien renombra la carpeta en SharePoint y el siguiente recorrido hay una
# ventana en la que los dos nombres conviven.
CONTENEDOR_ANTERIOR = "Matter (New Names)"

# Para filtrar. El orden es el de preferencia: primero el nombre de ahora.
NOMBRES_CONTENEDOR = (CONTENEDOR, CONTENEDOR_ANTERIOR)


def contenedor_real(g: Graph, drive: str) -> str:
    """Como se llama HOY el contenedor en SharePoint. Se PREGUNTA, no se supone.

    Si se supusiera y el renombrado todavia no estuviera hecho, cada carpeta que se
    fuera a tocar saldria como 'no estaba' -- 1.789 de golpe, sin un solo error y
    con pinta de resultado. Preguntarlo cuesta una llamada.
    """
    for nombre in NOMBRES_CONTENEDOR:
        try:
            g.get(f"/drives/{drive}/root:/{RAIZ_PERMITIDA}/{nombre}")
        except SystemExit:
            continue
        if nombre != CONTENEDOR:
            print(f"  AVISO: el contenedor todavia se llama {nombre!r} en SharePoint, "
                  f"no {CONTENEDOR!r}. Se trabaja sobre el nombre que existe.")
        return nombre
    raise SystemExit(
        f"No existe ningun contenedor en /{RAIZ_PERMITIDA} con estos nombres: "
        f"{', '.join(NOMBRES_CONTENEDOR)}.\n"
        "  Si le pusieron otro nombre, hay que añadirlo a NOMBRES_CONTENEDOR "
        "en crear_subcarpetas.py antes de seguir.")

# Materias que usan la plantilla FCRA. El resto usa la de Foreclosure.
MATERIAS_FCRA = {"ID Theft", "Wrongful Reporting", "FCRA", "Time-Barred", "EFTA", "RESPA",
                 # el caso combinado de la serie 777: es ID Theft y Wrongful
                 # Reporting a la vez, y las dos usan esta misma plantilla
                 "ID Theft + Wrongful Reporting"}

PLANTILLA_FORECLOSURE: dict[str, dict] = {
    "01_General": {},
    "02_Loss Mitigation": {
        "Bank Statements": {}, "Mortgage Statements": {}, "Pay Stubs": {},
        "Taxes": {}, "Utility Bills": {},
    },
    "03_Litigation": {
        "Discovery": {}, "Motions": {}, "Orders": {}, "Pleadings": {},
    },
    "04_Correspondence": {},
}

PLANTILLA_FCRA: dict[str, dict] = {
    "01_Documents from client": {},
    "02_FCRA Dispute": {
        "Credit Report": {}, "Dispute Letters": {}, "Identifying Documents": {},
    },
    "03_Complaint": {"Amended Complaint": {}},
    "04_Service of Process": {},
    "05_Answers": {},
    "06_Case Management Plan Initial Conference": {
        "Rule 26(a)(1)": {"Defendant Rule 26(a)(1)": {}},
    },
    "07_Discovery": {
        "Defendant Expert Witness Report": {}, "Defendants Demands": {},
        "Deficiency Letters": {}, "Plaintiff Demands": {},
        "Plaintiff Expert Witness Report": {},
        "Responses to Defendants Demands": {}, "Responses to Plaintiff Demands": {},
    },
    "08_Depositions": {"Notices of Deposition": {}},
    "09_Motions": {
        "Adjournment Requests": {}, "Pro Hac Vice Motions": {},
        "Rule 12": {
            "Defendants Rule 12 Motion": {},
            "Defendants Rule 12 PMC Letter": {},
            "Plaintiff Response to 12 Motion": {},
            "Plaintiff Response to Rule 12 PMC Letter": {},
        },
    },
    "10_Stipulations": {"Extension of Time": {}, "Voluntary Dismissal": {}},
    "11_Notices": {
        "Notice of Appeal": {}, "Notice of Appearance": {},
        "Notice of Dismissal": {}, "Notice of Settlement": {},
    },
    "12_Settlement Documents": {
        "Ex-Parte Settlement Letter": {}, "Itemized Damages to Defendants": {},
    },
    "13_Status Reports": {},
    "14_Billing and Invoices": {},
}


def contar(plantilla: dict) -> int:
    return sum(1 + contar(hijos) for hijos in plantilla.values())


def casos() -> pd.DataFrame:
    """Una fila por carpeta destino, con la plantilla que le toca."""
    filas = []
    for ruta in sorted(INVENTARIO.glob("*.json")):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for n in datos["carpetas_nuevas"]:
            if not n.get("nombre"):
                continue
            materia = n.get("materia") or datos["tipo_estructura"]
            filas.append({
                "Nombre": n["nombre"].replace("/", "-").strip(),
                "Seccion": datos["seccion"],
                "Materia": materia,
                "Plantilla": "FCRA" if materia in MATERIAS_FCRA else "Foreclosure",
            })
    return pd.DataFrame(filas).drop_duplicates(subset=["Nombre"])


def muestra_variada(plan: pd.DataFrame, cuantas: int) -> pd.DataFrame:
    """Casos de los dos tipos y de las dos secciones, no los primeros alfabeticos."""
    partes = []
    grupos = list(plan.groupby(["Plantilla", "Seccion"]))
    cupo = max(1, cuantas // max(len(grupos), 1))
    for _, g in grupos:
        partes.append(g.head(cupo))
    salida = pd.concat(partes)
    if len(salida) < cuantas:
        resto = plan[~plan.index.isin(salida.index)].head(cuantas - len(salida))
        salida = pd.concat([salida, resto])
    return salida.head(cuantas)


def crear_arbol(g: Graph, drive: str, padre: str, plantilla: dict,
                existentes: set[str], simular: bool) -> tuple[int, int, list]:
    """Crea la plantilla bajo `padre`. Devuelve (creadas, saltadas, errores)."""
    creadas = saltadas = 0
    errores = []
    for nombre, hijos in plantilla.items():
        if nombre in existentes:
            saltadas += 1
            hijo_id = existentes[nombre] if isinstance(existentes, dict) else None
        else:
            if simular:
                creadas += 1
                continue
            try:
                r = g.post(
                    f"/drives/{drive}/items/{padre}/children",
                    {"name": nombre, "folder": {},
                     "@microsoft.graph.conflictBehavior": "fail"},
                )
                if r.get("_conflicto"):
                    saltadas += 1
                    r = g.get(f"/drives/{drive}/items/{padre}:/{nombre}:/")
                else:
                    creadas += 1
                hijo_id = r["id"]
            except OSError as error:
                errores.append((nombre, str(error)[:110]))
                continue

        if hijos and not simular and hijo_id:
            dentro = hijos_de(g, drive, hijo_id)
            c, s, e = crear_arbol(g, drive, hijo_id, hijos, dentro, simular)
            creadas += c
            saltadas += s
            errores += e
        elif hijos and simular:
            creadas += contar(hijos)
    return creadas, saltadas, errores


def hijos_de(g: Graph, drive: str, padre: str) -> set[str]:
    nombres, url = set(), f"/drives/{drive}/items/{padre}/children?$top=999&$select=name"
    while url:
        r = g.get(url)
        nombres.update(h["name"] for h in r.get("value", []))
        url = r.get("@odata.nextLink")
    return nombres


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--muestra", type=int, default=0, help="Solo N casos, variados")
    p.add_argument("--simular", action="store_true", help="No escribe nada")
    args = p.parse_args()

    plan = casos()
    n_for, n_fcra = contar(PLANTILLA_FORECLOSURE), contar(PLANTILLA_FCRA)

    titulo(f"SUBCARPETAS ({len(plan):,} casos en {CONTENEDOR})")
    resumen = plan.groupby("Plantilla").size().rename("Casos").reset_index()
    resumen["Subcarpetas c/u"] = resumen["Plantilla"].map(
        {"Foreclosure": n_for, "FCRA": n_fcra}
    )
    resumen["Total"] = resumen["Casos"] * resumen["Subcarpetas c/u"]
    print(tabla(resumen))
    print(f"\n  total de subcarpetas: {resumen['Total'].sum():,}")

    if args.muestra:
        plan = muestra_variada(plan, args.muestra)
        titulo(f"MUESTRA ({len(plan)} casos)")
        print(tabla(plan[["Plantilla", "Seccion", "Materia", "Nombre"]]))

    g, drive = Graph(), drive_id()
    base = g.get(f"/drives/{drive}/root:/{RAIZ_PERMITIDA}/{CONTENEDOR}")
    if not base.get("parentReference", {}).get("path", "").endswith(f"root:/{RAIZ_PERMITIDA}"):
        sys.exit(f"ABORTADO: el contenedor no cuelga de {RAIZ_PERMITIDA}")

    if args.simular:
        print(f"\n(simulacion: se crearian "
              f"{sum(n_fcra if r['Plantilla']=='FCRA' else n_for for _, r in plan.iterrows()):,} subcarpetas)")
        return

    hijos_raiz = {h["name"]: h["id"] for h in
                  g.get(f"/drives/{drive}/items/{base['id']}/children", **{"$top": 999})["value"]}
    # con mas de 999 hay que paginar para encontrar cada caso
    url = f"/drives/{drive}/items/{base['id']}/children?$top=999&$select=name,id"
    hijos_raiz = {}
    while url:
        r = g.get(url)
        hijos_raiz.update({h["name"]: h["id"] for h in r.get("value", [])})
        url = r.get("@odata.nextLink")

    total_c = total_s = 0
    errores = []
    for i, (_, r) in enumerate(plan.iterrows(), 1):
        caso_id = hijos_raiz.get(r["Nombre"])
        if caso_id is None:
            errores.append((r["Nombre"], "el caso no existe en Matter (New Names)"))
            continue
        plantilla = PLANTILLA_FCRA if r["Plantilla"] == "FCRA" else PLANTILLA_FORECLOSURE
        dentro = hijos_de(g, drive, caso_id)
        c, s, e = crear_arbol(g, drive, caso_id, plantilla, dentro, False)
        total_c += c
        total_s += s
        errores += e
        print(f"  [{i:>4}/{len(plan)}] {r['Plantilla']:<12} {r['Nombre'][:44]:<46} +{c}")

    titulo("RESULTADO")
    print(f"  subcarpetas creadas: {total_c:,}")
    print(f"  ya existian:         {total_s:,}")
    print(f"  fallos:              {len(errores):,}")
    for n, e in errores[:8]:
        print(f"     {n[:50]}  ->  {e}")

    destino = SALIDA_DIR / "subcarpetas_creadas.xlsx"
    plan.to_excel(destino, index=False)
    print(f"\nExcel:  {destino}")


if __name__ == "__main__":
    main()
