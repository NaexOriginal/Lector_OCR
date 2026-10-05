r"""Por que esta cuenta recibe 403 en Matters. No lee ningun documento.

comprobar.py solo dice 'Acceso denegado'. Esto dice CON QUE CUENTA se entro y EN
QUE PASO se corta el camino hasta la carpeta Matters, que es lo que hace falta
para saber a quien pedirle que:

    1. el sitio        /sites/amshenllp.sharepoint.com:/teams/Matters
    2. la biblioteca   la de documentos del sitio
    3. la carpeta      Matters, dentro de la biblioteca
    4. su contenido    los expedientes

Uso (desde la carpeta reparto_ocr):
    .venv\Scripts\python.exe diagnostico_acceso.py
    .venv\Scripts\python.exe diagnostico_acceso.py --otra-cuenta   (olvida la sesion)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI / "codigo" if (AQUI / "codigo" / "describir_casos.py").exists() else AQUI.parent
sys.path.insert(0, str(RAIZ))


def paso(n: int, texto: str, g, url: str, **params):
    r = g.sesion.get(f"https://graph.microsoft.com/v1.0{url}",
                     headers={"Authorization": f"Bearer {g._token()}"},
                     params=params or None, timeout=60)
    bien = r.status_code == 200
    print(f"  {n}. {texto:<32} {'ok' if bien else f'HTTP {r.status_code}'}")
    if not bien:
        try:
            detalle = r.json().get("error", {}).get("message", "")
        except ValueError:
            detalle = r.text[:200]
        print(f"     {detalle[:200]}")
        return None
    return r.json()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--otra-cuenta", action="store_true",
                   help="Borra la sesion guardada y vuelve a pedir el inicio de sesion")
    args = p.parse_args()

    import sp_conexion
    if args.otra_cuenta and sp_conexion.CACHE_TOKEN.exists():
        sp_conexion.CACHE_TOKEN.unlink()
        print("  sesion borrada: se va a abrir el navegador. ELIGE la cuenta de la firma.")

    from copiar_a_matters import CARPETA_DESTINO, SITIO_DESTINO

    g = sp_conexion.Graph()
    g._token()
    cuentas = g.app.get_accounts() if hasattr(g.app, "get_accounts") else []
    cuenta = cuentas[0].get("username") if cuentas else "(modo aplicacion, sin usuario)"
    print(f"\n  CUENTA CON LA QUE SE ENTRO:  {cuenta}")
    print("  (si no es la de @petroffamshen.com que es miembro de Matters, repite con"
          " --otra-cuenta)\n")

    sitio = paso(1, "el sitio Matters", g, f"/sites/{SITIO_DESTINO}")
    if not sitio:
        print("\n  LA CUENTA NO VE EL SITIO. No es miembro de teams/Matters (o entro con"
              "\n  otra cuenta). Un propietario del sitio tiene que anadirla.")
        return
    bibliotecas = paso(2, "sus bibliotecas", g, f"/sites/{sitio['id']}/drives")
    if not bibliotecas:
        return
    nombres = [d.get("name") for d in bibliotecas.get("value", [])]
    print(f"     ve {len(nombres)}: {', '.join(map(str, nombres))}")
    if not nombres:
        print("\n  VE EL SITIO PERO NINGUNA BIBLIOTECA: permisos rotos en la biblioteca.")
        return
    drive = bibliotecas["value"][0]
    if drive.get("name") not in ("Documents", "Documentos", "Shared Documents"):
        print(f"     OJO: la primera que ve es '{drive.get('name')}', no la de documentos."
              "\n     El programa usa la primera: con esta cuenta apuntaria mal.")
    carpeta = paso(3, f"la carpeta {CARPETA_DESTINO}", g,
                   f"/drives/{drive['id']}/root:/{CARPETA_DESTINO}")
    if not carpeta:
        print(f"\n  VE LA BIBLIOTECA PERO NO LA CARPETA {CARPETA_DESTINO}: tiene permisos"
              "\n  unicos (herencia rota). Hay que darle acceso a esa carpeta.")
        return
    hijos = paso(4, "los expedientes de dentro", g,
                 f"/drives/{drive['id']}/items/{carpeta['id']}/children", **{"$top": "5"})
    if hijos:
        print(f"\n  TODO BIEN: ve {len(hijos.get('value', []))} expedientes de muestra."
              "\n  Si comprobar.py seguia dando 403, era la sesion: ya esta renovada.")


if __name__ == "__main__":
    main()
