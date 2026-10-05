"""
Vista mejorada de la exportacion de Matters (HubSpot) usando pandas.

Uso rapido:
    .venv\\Scripts\\python.exe ver_matters.py
    .venv\\Scripts\\python.exe ver_matters.py --stage Active --case-type Foreclosure
    .venv\\Scripts\\python.exe ver_matters.py --buscar "Chin" --detalle 20
    .venv\\Scripts\\python.exe ver_matters.py --excel --html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SALIDA_DIR = BASE_DIR / "salida"

COLUMNAS_FECHA = ["Retainer Sent Date", "Retainer Signed Date", "Initial Payment Date"]

# Columnas del CSV -> nombres cortos para trabajar y mostrar
RENOMBRES = {
    "Record ID": "ID",
    "Matter Name": "Matter",
    "Matter Stage": "Stage",
    "Case Type": "Case Type",
    "Retainer Sent Date": "Retainer Enviado",
    "Retainer Signed Date": "Retainer Firmado",
    "Initial Payment Date": "Primer Pago",
    "Number of Associated Contacts": "# Contactos",
    "Number of Associated Matters": "# Matters Asoc.",
}

COLUMNAS_DETALLE = [
    "ID",
    "Cliente",
    "Email",
    "Case Type",
    "Stage",
    "Book Value",
    "Retainer Firmado",
    "Primer Pago",
    "Dias desde firma",
]


# --------------------------------------------------------------------------- #
# Carga y limpieza
# --------------------------------------------------------------------------- #
def buscar_csv(ruta: str | None) -> Path:
    """Devuelve el CSV indicado o el export de HubSpot mas reciente de la carpeta."""
    if ruta:
        csv = Path(ruta)
        if not csv.is_absolute():
            csv = BASE_DIR / csv
        if not csv.exists():
            sys.exit(f"No encontre el archivo: {csv}")
        return csv

    candidatos = sorted(BASE_DIR.glob("hubspot-crm-exports-*.csv"))
    if not candidatos:
        sys.exit(f"No hay ningun 'hubspot-crm-exports-*.csv' en {BASE_DIR}")
    return candidatos[-1]


def cargar(csv: Path) -> pd.DataFrame:
    df = pd.read_csv(csv, dtype=str, keep_default_na=False, na_values=[""])
    df = df.rename(columns=RENOMBRES)

    # Texto: quitar espacios sobrantes (el export trae varios "Nombre  -   ")
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].str.strip().replace("", None)

    # Numeros
    df["Book Value"] = pd.to_numeric(df["Book Value"], errors="coerce")
    for col in ("# Contactos", "# Matters Asoc."):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Fechas
    for col in ("Retainer Enviado", "Retainer Firmado", "Primer Pago"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    # Stage: el export marca los migrados con un emoji + "(RM Migration - Do Not Use)"
    df["No Usar (RM)"] = df["Stage"].str.contains("RM Migration", na=False)
    df["Stage"] = df["Stage"].str.replace(r"^[^\w(]+\s*", "", regex=True).str.strip()

    df["Case Type"] = df["Case Type"].fillna("(sin tipo)")

    # Contacto principal: "Nombre (correo);Nombre2 (correo2)"
    principal = df["Associated Contact"].str.split(";").str[0]
    df["Cliente"] = principal.str.replace(r"\s*\(.*\)\s*$", "", regex=True).str.strip()
    df["Email"] = principal.str.extract(r"\(([^)]*)\)", expand=False).str.strip()

    # Derivadas utiles
    hoy = pd.Timestamp.today().normalize()
    df["Dias desde firma"] = (hoy - df["Retainer Firmado"]).dt.days.astype("Int64")
    df["Mes Firma"] = df["Retainer Firmado"].dt.to_period("M").astype("string")
    df["Anio Firma"] = df["Retainer Firmado"].dt.year.astype("Int64")
    df["Pagado"] = df["Primer Pago"].notna()

    return df


def filtrar(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    out = df

    if not args.incluir_rm:
        out = out[~out["No Usar (RM)"]]
    if args.stage:
        out = out[out["Stage"].str.contains(args.stage, case=False, na=False)]
    if args.case_type:
        out = out[out["Case Type"].str.contains(args.case_type, case=False, na=False)]
    if args.buscar:
        texto = (
            out["Matter"].fillna("")
            + " "
            + out["Cliente"].fillna("")
            + " "
            + out["Email"].fillna("")
        )
        out = out[texto.str.contains(args.buscar, case=False, na=False)]
    if args.desde:
        out = out[out["Retainer Firmado"] >= pd.Timestamp(args.desde)]
    if args.hasta:
        out = out[out["Retainer Firmado"] <= pd.Timestamp(args.hasta)]
    if args.solo_pagados:
        out = out[out["Pagado"]]

    return out


# --------------------------------------------------------------------------- #
# Reporte en consola
# --------------------------------------------------------------------------- #
def titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


def tabla(df: pd.DataFrame) -> str:
    """Formatea fechas, dinero y porcentajes antes de imprimir la tabla."""
    if df.empty:
        return "(sin datos)"

    vista = df.copy()
    for col in vista.columns:
        serie = vista[col]
        if pd.api.types.is_datetime64_any_dtype(serie):
            vista[col] = serie.dt.strftime("%Y-%m-%d")
        elif col.startswith("%") and pd.api.types.is_numeric_dtype(serie):
            vista[col] = serie.map(lambda v: f"{v:.1f}%" if pd.notna(v) else "-")
        elif pd.api.types.is_numeric_dtype(serie) and not pd.api.types.is_bool_dtype(serie):
            vista[col] = serie.map(lambda v: f"{v:,.0f}" if pd.notna(v) else "-")
    return vista.fillna("-").to_markdown(index=False, stralign="left", numalign="right")


def resumen_por(df: pd.DataFrame, columna: str) -> pd.DataFrame:
    g = df.groupby(columna, dropna=False).agg(
        Matters=("ID", "count"),
        **{"Con Book Value": ("Book Value", "count")},
        **{"Book Value Total": ("Book Value", "sum")},
        Pagados=("Pagado", "sum"),
    )
    g["% del total"] = (g["Matters"] / len(df) * 100).round(1)
    return g.sort_values("Matters", ascending=False).reset_index()


def por_mes(df: pd.DataFrame) -> pd.DataFrame:
    firmados = df[df["Mes Firma"].notna()]
    if firmados.empty:
        return pd.DataFrame()
    g = firmados.groupby("Mes Firma").agg(
        Matters=("ID", "count"),
        **{"Book Value": ("Book Value", "sum")},
        Pagados=("Pagado", "sum"),
    )
    return g.sort_index().reset_index()


def reporte(df: pd.DataFrame, origen: Path, detalle: int) -> None:
    bv = df["Book Value"]

    titulo("RESUMEN GENERAL")
    generales = {
        "Archivo": origen.name,
        "Matters": f"{len(df):,}",
        "Case types": df["Case Type"].nunique(),
        "Stages": df["Stage"].nunique(),
        "Con Book Value": f"{bv.notna().sum():,}",
        "Book Value total": f"${bv.sum():,.0f}",
        "Book Value promedio": f"${bv.mean():,.0f}" if bv.notna().any() else "-",
        "Con primer pago": f"{df['Pagado'].sum():,}",
        "Rango de firma": (
            f"{df['Retainer Firmado'].min():%Y-%m-%d} a "
            f"{df['Retainer Firmado'].max():%Y-%m-%d}"
            if df["Retainer Firmado"].notna().any()
            else "-"
        ),
    }
    for clave, valor in generales.items():
        print(f"  {clave:<22} {valor}")

    titulo("POR CASE TYPE")
    print(tabla(resumen_por(df, "Case Type")))

    titulo("POR STAGE")
    print(tabla(resumen_por(df, "Stage")))

    titulo("ULTIMOS 12 MESES (por Retainer Firmado)")
    print(tabla(por_mes(df).tail(12)))

    if detalle:
        titulo(f"TOP {detalle} POR BOOK VALUE")
        top = df.sort_values("Book Value", ascending=False).head(detalle)
        print(tabla(top[COLUMNAS_DETALLE]))

        titulo(f"ULTIMOS {detalle} MATTERS FIRMADOS")
        recientes = df.sort_values("Retainer Firmado", ascending=False).head(detalle)
        print(tabla(recientes[COLUMNAS_DETALLE]))

    titulo("CALIDAD DE DATOS (campos vacios)")
    campos = [c for c in COLUMNAS_DETALLE if c != "Dias desde firma"] + ["Matter"]
    vacios = df[campos].isna().sum().rename("Vacios").to_frame()
    vacios["% vacios"] = (vacios["Vacios"] / len(df) * 100).round(1)
    print(tabla(vacios[vacios["Vacios"] > 0].reset_index(names="Campo")))


# --------------------------------------------------------------------------- #
# Exportaciones
# --------------------------------------------------------------------------- #
def exportar_excel(df: pd.DataFrame) -> Path:
    SALIDA_DIR.mkdir(exist_ok=True)
    destino = SALIDA_DIR / "matters_vista.xlsx"

    hojas = {
        "Matters": df[COLUMNAS_DETALLE + ["Matter", "# Contactos", "No Usar (RM)"]],
        "Por Case Type": resumen_por(df, "Case Type"),
        "Por Stage": resumen_por(df, "Stage"),
        "Por Mes": por_mes(df),
    }

    with pd.ExcelWriter(destino, engine="openpyxl", datetime_format="yyyy-mm-dd") as xls:
        for nombre, hoja in hojas.items():
            hoja.to_excel(xls, sheet_name=nombre, index=False)
            ws = xls.sheets[nombre]
            ws.freeze_panes = "A2"
            if len(hoja):
                ws.auto_filter.ref = ws.dimensions
            for celdas in ws.columns:
                letra = celdas[0].column_letter
                ancho = max(len(str(c.value)) if c.value is not None else 0 for c in celdas)
                ws.column_dimensions[letra].width = min(max(ancho + 2, 10), 45)

    return destino


def exportar_html(df: pd.DataFrame) -> Path:
    SALIDA_DIR.mkdir(exist_ok=True)
    destino = SALIDA_DIR / "matters_vista.html"

    vista = df[COLUMNAS_DETALLE].sort_values("Retainer Firmado", ascending=False)
    vista = vista.assign(
        **{
            "Book Value": vista["Book Value"].map(lambda v: f"${v:,.0f}" if pd.notna(v) else "-"),
            "Retainer Firmado": vista["Retainer Firmado"].dt.strftime("%Y-%m-%d"),
            "Primer Pago": vista["Primer Pago"].dt.strftime("%Y-%m-%d"),
        }
    ).astype("string").fillna("-")

    tabla_html = vista.to_html(index=False, border=0, escape=True, na_rep="-")
    html = f"""<meta charset="utf-8"><title>Matters HubSpot</title>
<style>
  body {{ font: 14px system-ui, sans-serif; margin: 24px; color: #253342; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.sub {{ color: #7c98b6; margin: 0 0 16px; }}
  input {{ padding: 8px 12px; width: 320px; border: 1px solid #cbd6e2;
           border-radius: 6px; font-size: 14px; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #33475b; color: #fff; text-align: left; padding: 8px;
        position: sticky; top: 0; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #eaf0f6; white-space: nowrap; }}
  tbody tr:hover {{ background: #f5f8fa; }}
</style>
<h1>Matters HubSpot</h1>
<p class="sub">{len(vista):,} registros &middot; generado con ver_matters.py</p>
<input id="q" placeholder="Filtrar (cliente, email, stage, case type)..." autofocus>
{tabla_html}
<script>
  const q = document.getElementById('q');
  const filas = [...document.querySelectorAll('tbody tr')];
  q.addEventListener('input', () => {{
    const t = q.value.toLowerCase();
    filas.forEach(f => f.hidden = !f.textContent.toLowerCase().includes(t));
  }});
</script>"""
    destino.write_text(html, encoding="utf-8")
    return destino


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Vista mejorada de Matters de HubSpot.")
    p.add_argument("--csv", help="Ruta del CSV (por defecto: el export mas reciente).")
    p.add_argument("--stage", help="Filtra por Matter Stage (coincidencia parcial).")
    p.add_argument("--case-type", help="Filtra por Case Type (coincidencia parcial).")
    p.add_argument("--buscar", help="Busca texto en matter, cliente o email.")
    p.add_argument("--desde", help="Retainer firmado desde (YYYY-MM-DD).")
    p.add_argument("--hasta", help="Retainer firmado hasta (YYYY-MM-DD).")
    p.add_argument("--solo-pagados", action="store_true", help="Solo con primer pago.")
    p.add_argument(
        "--incluir-rm",
        action="store_true",
        help="Incluye los stages 'RM Migration - Do Not Use' (excluidos por defecto).",
    )
    p.add_argument(
        "--detalle",
        type=int,
        default=10,
        help="Filas en las tablas de detalle (0 las oculta). Def: 10",
    )
    p.add_argument("--excel", action="store_true", help="Exporta salida/matters_vista.xlsx")
    p.add_argument("--html", action="store_true", help="Exporta salida/matters_vista.html")
    args = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)

    csv = buscar_csv(args.csv)
    df = cargar(csv)
    total = len(df)
    df = filtrar(df, args)

    print(f"Leido: {csv}  ({total:,} filas, {len(df):,} tras filtros)")
    if df.empty:
        sys.exit("Los filtros no dejaron ningun registro.")

    reporte(df, csv, args.detalle)

    if args.excel:
        print(f"\nExcel:  {exportar_excel(df)}")
    if args.html:
        print(f"HTML:   {exportar_html(df)}")


if __name__ == "__main__":
    main()
