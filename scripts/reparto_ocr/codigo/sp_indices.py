"""
Cosecha los numeros de indice que estan en SharePoint y HubSpot no tiene.

Este es el objetivo real del recorrido: en la muestra de 20 carpetas, 10 de los 15
numeros de indice encontrados NO existian en HubSpot. El index number es el unico
identificador duro que comparten los dos sistemas, y hoy solo esta lleno en 1.091 de
3.894 registros Back Up. Poblarlo convierte toda la conciliacion en determinista.

Los indices aparecen en dos lugares y el recorrido con delta trae los dos:

    subcarpetas   "703250-2015 (Foreclosure)", "502884-2018 (Article 15)"
    archivos      "Billing - Index No. 503536-2020.pdf"

Tambien clasifica como esta organizada cada carpeta de cliente, que es un hallazgo de
auditoria en si mismo:

    POR CASO        sus subcarpetas son numeros de indice
    POR DOCUMENTO   sus subcarpetas son categorias (LITIGATION, MORTGAGE STATEMENT...)
    PLANA           no tiene subcarpetas

Requiere haber corrido antes:  sp_conexion.py --recorrer

Uso:
    .venv\\Scripts\\python.exe sp_indices.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import pandas as pd

from crear_subcarpetas import NOMBRES_CONTENEDOR
from regla_nombres import indices
from sp_conexion import CACHE_ARBOL, CARPETA_RAIZ
from ver_matters import SALIDA_DIR, buscar_csv, cargar, tabla, titulo

CACHE_HUBSPOT = SALIDA_DIR / "cache_hubspot.json"

# Categorias tipicas de documentos: si la subcarpeta se llama asi, no es un caso
PALABRAS_DOCUMENTO = {
    "litigation", "correspondence", "general", "court", "docs", "mortgage", "statement",
    "statements", "bank", "pay", "stubs", "tax", "returns", "financial", "worksheet",
    "hardship", "letter", "loss", "mitigation", "mit", "liens", "judgments", "hoa",
    "insurance", "utility", "taxes", "submission", "submissions", "old", "new", "qwr",
    "complaint", "bankruptcy", "mediation", "answer", "motion", "exhibits", "billing",
    "retainer", "scanned", "self", "employed", "closing", "discovery", "demands",
    "responses", "claim", "files", "federal", "fee", "heloc", "lien", "default",
}


# --------------------------------------------------------------------------- #
def indices_de(texto: str) -> set[str]:
    """Numeros de indice de un texto. La regla vive en regla_nombres.py."""
    return indices(texto)


def parece_documento(nombre: str) -> bool:
    """La subcarpeta es una categoria de documentos, no un caso."""
    palabras = re.sub(r"[^a-z ]+", " ", str(nombre).lower()).split()
    return bool(palabras) and any(p in PALABRAS_DOCUMENTO for p in palabras)


# --------------------------------------------------------------------------- #
def cargar_arbol() -> pd.DataFrame:
    if not CACHE_ARBOL.exists():
        sys.exit(
            f"Falta {CACHE_ARBOL.name}.\n"
            "  Corre primero:  .venv\\Scripts\\python.exe sp_conexion.py --recorrer"
        )
    arbol = pd.DataFrame(json.loads(CACHE_ARBOL.read_text(encoding="utf-8")))

    # La ruta del padre viene como /drive/root:/zz-pruebas-no-usar/Active Matters/Cliente/...
    marca = f"/{CARPETA_RAIZ}"
    resto = arbol["ruta"].str.split(marca, n=1).str[-1].fillna("")
    segmentos = resto.str.strip("/").str.split("/").map(lambda t: [x for x in t if x])

    # Las areas de primer nivel que nos interesan. Se nombran una por una y no por
    # un 'Matters' in nombre: el contenedor de destino se llamaba 'Matter (New
    # Names)', SIN la ese, asi que la comprobacion por substring lo descartaba
    # entero -- 3.043 casos y sus subcarpetas -- y dejaba ciega a cualquier
    # verificacion de la mudanza. Van los dos nombres del contenedor, el de ahora
    # y el anterior, porque un cache recorrido antes del renombrado trae el viejo.
    AREAS = ("Active Matters", "Closed Matters", *NOMBRES_CONTENEDOR)

    def ubicar(fila_segmentos: list[str], nombre: str, es_carpeta: bool) -> tuple:
        """(ubicacion, carpeta de cliente, nivel). El nivel 2 es la carpeta del cliente."""
        if not fila_segmentos or fila_segmentos[0] not in AREAS:
            return None, None, 0
        if len(fila_segmentos) == 1:
            # El padre es 'Active/Closed Matters': este item ES la carpeta del cliente
            return (fila_segmentos[0], nombre, 2) if es_carpeta else (fila_segmentos[0], None, 2)
        return fila_segmentos[0], fila_segmentos[1], len(fila_segmentos) + 1

    ubicado = [
        ubicar(s, n, c) for s, n, c in zip(segmentos, arbol["name"], arbol["es_carpeta"])
    ]
    arbol["Ubicacion"] = [u[0] for u in ubicado]
    arbol["Carpeta"] = [u[1] for u in ubicado]
    arbol["Nivel"] = [u[2] for u in ubicado]
    return arbol[arbol["Carpeta"].notna() & (arbol["Carpeta"] != "")]


def indices_de_hubspot() -> set[str]:
    """Todo indice que HubSpot ya conoce: del Back Up y del nombre del deal."""
    conocidos = set()
    if CACHE_HUBSPOT.exists():
        cache = json.loads(CACHE_HUBSPOT.read_text(encoding="utf-8"))
        for registro in cache.get("backups", {}).values():
            # Solo index_number: el file_number ('982-001') no es un numero de indice
            # y al normalizarlo se convertiria en un '982-2001' inexistente
            conocidos |= indices_de(registro.get("index_number"))
    for nombre in cargar(buscar_csv(None))["Matter"].dropna():
        conocidos |= indices_de(nombre)
    return conocidos


# --------------------------------------------------------------------------- #
def analizar(arbol: pd.DataFrame, conocidos: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (indices encontrados, resumen por carpeta de cliente)."""
    filas = []
    for _, r in arbol.iterrows():
        for indice in indices_de(r["name"]):
            filas.append(
                {
                    "Index Number": indice,
                    "Carpeta": r["Carpeta"],
                    "Ubicacion": r["Ubicacion"],
                    "Origen": "subcarpeta" if r["es_carpeta"] else "archivo",
                    "Nombre en SharePoint": r["name"],
                    "Nivel": r["Nivel"],
                    "En HubSpot": indice in conocidos,
                }
            )

    indices = pd.DataFrame(filas)
    if indices.empty:
        return indices, pd.DataFrame()

    # Un mismo indice puede aparecer en varios archivos de la misma carpeta
    indices = indices.sort_values(["Origen", "Nivel"]).drop_duplicates(
        ["Index Number", "Carpeta"], keep="first"
    )

    # Como esta organizada cada carpeta de cliente
    subcarpetas = arbol[arbol["es_carpeta"] & (arbol["Nivel"] == 3)]
    organizacion = {}
    for carpeta, grupo in subcarpetas.groupby("Carpeta"):
        nombres = list(grupo["name"])
        con_indice = sum(1 for n in nombres if indices_de(n))
        if con_indice:
            organizacion[carpeta] = "POR CASO"
        elif all(parece_documento(n) for n in nombres):
            organizacion[carpeta] = "POR DOCUMENTO"
        else:
            organizacion[carpeta] = "MIXTA"

    por_carpeta = (
        indices.groupby("Carpeta")
        .agg(
            Ubicacion=("Ubicacion", "first"),
            Indices=("Index Number", "nunique"),
            **{"Nuevos para HubSpot": ("En HubSpot", lambda s: int((~s).sum()))},
            **{"Ya en HubSpot": ("En HubSpot", "sum")},
            **{"Lista de indices": ("Index Number", lambda s: "; ".join(sorted(set(s))))},
        )
        .reset_index()
    )

    todas = pd.DataFrame(
        {"Carpeta": sorted(arbol["Carpeta"].dropna().unique())}
    ).merge(por_carpeta, on="Carpeta", how="left")
    todas["Ubicacion"] = todas["Ubicacion"].fillna(
        arbol.drop_duplicates("Carpeta").set_index("Carpeta")["Ubicacion"]
    )
    for col in ("Indices", "Nuevos para HubSpot", "Ya en HubSpot"):
        todas[col] = todas[col].fillna(0).astype(int)
    todas["Lista de indices"] = todas["Lista de indices"].fillna("")
    todas["Organizacion"] = todas["Carpeta"].map(organizacion).fillna("PLANA")

    return indices, todas.sort_values(
        ["Nuevos para HubSpot", "Indices"], ascending=False
    )


# --------------------------------------------------------------------------- #
def exportar_excel(indices: pd.DataFrame, carpetas: pd.DataFrame, resumen: pd.DataFrame) -> Path:
    SALIDA_DIR.mkdir(exist_ok=True)
    destino = SALIDA_DIR / "indices_sharepoint.xlsx"

    nuevos = indices[~indices["En HubSpot"]]
    columnas = [
        "Index Number", "Carpeta", "Ubicacion", "Origen", "Nombre en SharePoint", "En HubSpot"
    ]
    hojas = {
        "Resumen": resumen,
        "Indices nuevos": nuevos[columnas].sort_values(["Carpeta", "Index Number"]),
        "Indices ya en HubSpot": indices[indices["En HubSpot"]][columnas],
        "Por carpeta": carpetas,
        "Carpetas sin indice": carpetas[carpetas["Indices"] == 0],
    }
    with pd.ExcelWriter(destino, engine="openpyxl") as xls:
        for nombre, hoja in hojas.items():
            hoja.to_excel(xls, sheet_name=nombre[:31], index=False)
            ws = xls.sheets[nombre[:31]]
            ws.freeze_panes = "A2"
            if len(hoja):
                ws.auto_filter.ref = ws.dimensions
            for celdas in ws.columns:
                letra = celdas[0].column_letter
                ancho = max(len(str(c.value)) if c.value is not None else 0 for c in celdas)
                ws.column_dimensions[letra].width = min(max(ancho + 2, 10), 50)
    return destino


def exportar_html(indices: pd.DataFrame, carpetas: pd.DataFrame) -> Path:
    SALIDA_DIR.mkdir(exist_ok=True)
    destino = SALIDA_DIR / "indices_sharepoint.html"
    celda = lambda v: html.escape(str(v)) if pd.notna(v) and str(v) != "" else "-"

    nuevos = int((~indices["En HubSpot"]).sum()) if not indices.empty else 0
    kpis = [
        ("Indices encontrados", f"{indices['Index Number'].nunique() if not indices.empty else 0:,}"),
        ("Nuevos para HubSpot", f"{nuevos:,}"),
        ("Carpetas con indice", f"{int((carpetas['Indices'] > 0).sum()):,}"),
        ("Carpetas sin indice", f"{int((carpetas['Indices'] == 0).sum()):,}"),
        ("Organizadas por caso", f"{int((carpetas['Organizacion'] == 'POR CASO').sum()):,}"),
    ]
    tarjetas = "".join(
        f'<div class="kpi"><span class="kpi-n">{v}</span><span class="kpi-l">{k}</span></div>'
        for k, v in kpis
    )

    filas = []
    for _, r in indices.sort_values(["En HubSpot", "Carpeta"]).iterrows():
        c = "nuevo" if not r["En HubSpot"] else "conocido"
        filas.append(
            f'<tr class="{c}">'
            f'<td><span class="pill {c}">{"NUEVO" if c == "nuevo" else "ya esta"}</span></td>'
            f'<td class="idx">{celda(r["Index Number"])}</td>'
            f"<td>{celda(r['Carpeta'])}</td><td>{celda(r['Ubicacion'])}</td>"
            f'<td class="g">{celda(r["Origen"])}</td>'
            f'<td class="ev">{celda(r["Nombre en SharePoint"])}</td></tr>'
        )

    doc = f"""<meta charset="utf-8">
<title>Indices en SharePoint</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.45 system-ui, sans-serif; margin: 0; padding: 24px 28px;
          color: #253342; background: #f5f8fa; }}
  h1 {{ font-size: 21px; margin: 0 0 2px; }}
  .sub {{ color: #7c98b6; margin: 0 0 18px; font-size: 13px; max-width: 900px; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }}
  .kpi {{ background: #fff; border: 1px solid #dfe3eb; border-radius: 8px; padding: 12px 16px;
          min-width: 156px; }}
  .kpi-n {{ display: block; font-size: 21px; font-weight: 600; color: #33475b; }}
  .kpi-l {{ display: block; font-size: 12px; color: #7c98b6; text-transform: uppercase;
            letter-spacing: .04em; }}
  .filtros {{ display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }}
  input {{ padding: 8px 10px; border: 1px solid #cbd6e2; border-radius: 6px; font-size: 14px;
           background: #fff; width: 300px; }}
  label {{ font-size: 13px; color: #506e91; }}
  #conteo {{ color: #7c98b6; font-size: 13px; }}
  .tabla-wrap {{ background: #fff; border: 1px solid #dfe3eb; border-radius: 8px;
                 overflow: auto; max-height: 70vh; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #33475b; color: #fff; text-align: left; padding: 9px 10px; position: sticky;
        top: 0; white-space: nowrap; font-size: 12.5px; font-weight: 600; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #eaf0f6; white-space: nowrap; }}
  td.idx {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  td.g {{ color: #7c98b6; font-size: 12px; }}
  td.ev {{ white-space: normal; min-width: 300px; font-size: 13px; color: #506e91; }}
  tbody tr:hover {{ background: #f5f8fa; }}
  tr.nuevo td:first-child {{ border-left: 3px solid #00bda5; }}
  tr.conocido td:first-child {{ border-left: 3px solid #cbd6e2; }}
  .pill {{ display: inline-block; padding: 1px 8px; border-radius: 99px; font-size: 11px;
           font-weight: 600; }}
  .pill.nuevo {{ background: #e5f8ed; color: #1a7f4b; }}
  .pill.conocido {{ background: #eaf0f6; color: #7c98b6; }}
</style>
<h1>Numeros de indice encontrados en SharePoint</h1>
<p class="sub">Los marcados <b>NUEVO</b> no existen en HubSpot: son casos que el sistema de
  archivos conoce y el CRM no. Cargarlos en el campo <code>index_number</code> del Back Up
  convierte la conciliacion en determinista.</p>
<div class="kpis">{tarjetas}</div>
<div class="filtros">
  <input id="q" placeholder="Buscar indice o carpeta...">
  <label><input type="checkbox" id="fNuevo" checked> Solo los nuevos</label>
  <span id="conteo"></span>
</div>
<div class="tabla-wrap">
<table>
  <thead><tr><th>Estado</th><th>Index Number</th><th>Carpeta</th><th>Ubicacion</th>
    <th>Origen</th><th>Nombre en SharePoint</th></tr></thead>
  <tbody>{"".join(filas)}</tbody>
</table>
</div>
<script>
  const filas = [...document.querySelectorAll('tbody tr')];
  const q = document.getElementById('q'), fNuevo = document.getElementById('fNuevo'),
        conteo = document.getElementById('conteo');
  function aplicar() {{
    const t = q.value.toLowerCase();
    let n = 0;
    for (const f of filas) {{
      const ok = (!t || f.textContent.toLowerCase().includes(t))
        && (!fNuevo.checked || f.classList.contains('nuevo'));
      f.hidden = !ok; if (ok) n++;
    }}
    conteo.textContent = n.toLocaleString() + ' de ' + filas.length.toLocaleString();
  }}
  [q, fNuevo].forEach(e => e.addEventListener('input', aplicar));
  aplicar();
</script>"""
    destino.write_text(doc, encoding="utf-8")
    return destino


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Extrae los indices de SharePoint y los cruza.")
    p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows

    arbol = cargar_arbol()
    print(f"Arbol de SharePoint: {len(arbol):,} items en {arbol['Carpeta'].nunique():,} carpetas de cliente")

    conocidos = indices_de_hubspot()
    print(f"Indices que HubSpot ya conoce: {len(conocidos):,}")

    indices, carpetas = analizar(arbol, conocidos)
    if indices.empty:
        sys.exit("No se encontro ningun numero de indice en el arbol.")

    nuevos = indices[~indices["En HubSpot"]]
    resumen = pd.DataFrame(
        [
            ("Items recorridos en SharePoint", len(arbol)),
            ("Carpetas de cliente", int(arbol["Carpeta"].nunique())),
            ("Indices distintos encontrados", int(indices["Index Number"].nunique())),
            ("  ya presentes en HubSpot", int(indices[indices["En HubSpot"]]["Index Number"].nunique())),
            ("  NUEVOS para HubSpot", int(nuevos["Index Number"].nunique())),
            ("Carpetas con al menos un indice", int((carpetas["Indices"] > 0).sum())),
            ("Carpetas sin ningun indice", int((carpetas["Indices"] == 0).sum())),
        ],
        columns=["Concepto", "Valor"],
    )

    titulo("COSECHA DE INDICES")
    for _, r in resumen.iterrows():
        print(f"  {r['Concepto']:<36} {r['Valor']:>8,}")

    titulo("DE DONDE SALEN LOS INDICES")
    print(tabla(indices["Origen"].value_counts().rename_axis("Origen").reset_index(name="Indices")))

    titulo("COMO ESTA ORGANIZADA CADA CARPETA")
    print(
        tabla(
            carpetas["Organizacion"].value_counts().rename_axis("Organizacion").reset_index(name="Carpetas")
        )
    )

    titulo("CARPETAS QUE MAS APORTAN INDICES NUEVOS")
    print(
        tabla(
            carpetas[carpetas["Nuevos para HubSpot"] > 0]
            .head(15)[["Carpeta", "Ubicacion", "Organizacion", "Indices", "Nuevos para HubSpot", "Lista de indices"]]
        )
    )

    print(f"\nExcel:  {exportar_excel(indices, carpetas, resumen)}")
    print(f"Web:    {exportar_html(indices, carpetas)}")


if __name__ == "__main__":
    main()
