"""
Conexion a SharePoint via Microsoft Graph y recorrido completo del arbol de carpetas.

Una biblioteca de documentos de SharePoint es un 'drive' en Graph, el mismo objeto que
OneDrive, asi que se recorre con los mismos endpoints. El recorrido usa 'delta', que
devuelve TODO el subarbol paginado en una sola secuencia de llamadas (~50-60 para
48.000 items) en vez de una llamada por carpeta.

Configuracion (.env junto a este script). Hay dos modos y el script elige solo:

    MODO DELEGADO  <- el que usamos: la app del firm es public client, sin secret
        GRAPH_TENANT_ID=...
        GRAPH_CLIENT_ID=...
      La app actua como el usuario y solo alcanza lo que el usuario ya ve. Autentica
      abriendo el navegador (redirect a http://localhost, que Entra acepta en
      cualquier puerto para clientes publicos) y cae a device code si no hay navegador.

      La autoridad SIEMPRE es tenant-specific: la app es single-tenant, y con /common
      Entra devuelve AADSTS50194, un error que no menciona que falte el tenant.

    MODO APLICACION (solo si algun dia hay secret y Sites.Selected)
        GRAPH_TENANT_ID=...
        GRAPH_CLIENT_ID=...
        GRAPH_CLIENT_SECRET=...
      Con Sites.Selected el acceso queda acotado a un sitio, pero un admin tiene que
      habilitar la app sobre el sitio con Grant-PnPAzureADAppSitePermission.

Uso:
    .venv\\Scripts\\python.exe sp_conexion.py                 # verifica y muestra el sitio
    .venv\\Scripts\\python.exe sp_conexion.py --recorrer      # recorre y guarda el arbol
    .venv\\Scripts\\python.exe sp_conexion.py --recorrer --refrescar
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import msal
import requests

BASE_DIR = Path(__file__).resolve().parent
SALIDA_DIR = BASE_DIR / "salida"


def _buscar_env() -> Path:
    """Donde esta el .env. Se mira en varios sitios, no solo junto al modulo.

    AL REPARTIR EL CODIGO, LA CARPETA DEL MODULO NO ES LA QUE UNO SUPONE. El paquete
    que se manda a los otros equipos tiene los .py en 'codigo/' y los requirements y
    las instrucciones en la raiz, asi que cualquiera deja el .env en la raiz -- que
    es donde esta todo lo demas que le mandaron. Mirando solo junto al modulo, el
    fichero esta y aun asi sale 'faltan las credenciales'.

    Se devuelve el primero que exista; si no hay ninguno, el de siempre, para que el
    mensaje de error siga senalando el sitio canonico.
    """
    candidatos = (BASE_DIR / ".env",              # junto al modulo: lo de siempre
                  BASE_DIR.parent / ".env",       # raiz del paquete repartido
                  Path.cwd() / ".env")            # desde donde se lanzo
    for c in candidatos:
        if c.exists():
            return c
    return BASE_DIR / ".env"


ARCHIVO_ENV = _buscar_env()
CACHE_TOKEN = BASE_DIR / ".msal_cache.json"
CACHE_ARBOL = SALIDA_DIR / "cache_sharepoint.json"

GRAPH = "https://graph.microsoft.com/v1.0"
# Para leer la biblioteca de un sitio basta Sites.Read.All. Pedir menos alcances
# reduce las probabilidades de chocar con el consentimiento.
ALCANCES = ["Sites.Read.All"]
TIMEOUT = 60

# El sitio y la carpeta que contienen el arbol de clientes.
# Se pueden sobrescribir en el .env con GRAPH_SITIO y GRAPH_CARPETA.
SITIO_POR_DEFECTO = "amshenllp.sharepoint.com:/teams/RevOps-2.Projects"
CARPETA_POR_DEFECTO = "zz-pruebas-no-usar"


# --------------------------------------------------------------------------- #
def leer_env(clave: str) -> str:
    valor = os.environ.get(clave, "").strip()
    if not valor and ARCHIVO_ENV.exists():
        for linea in ARCHIVO_ENV.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea.startswith("#") or "=" not in linea:
                continue
            k, _, v = linea.partition("=")
            if k.strip() == clave:
                valor = v.strip().strip('"').strip("'")
                break
    return valor


SITIO = leer_env("GRAPH_SITIO") or SITIO_POR_DEFECTO
CARPETA_RAIZ = leer_env("GRAPH_CARPETA") or CARPETA_POR_DEFECTO


def parecidos_al_env() -> str:
    """Si hay un fichero que alguien QUISO que fuera el .env, decirlo.

    Los tres fallos que se repiten al repartir la carpeta a otro equipo, y ninguno
    se ve mirando la pantalla:

        .env.txt        Windows oculta las extensiones, asi que el Bloc de notas
                        guarda '.env' y en disco queda '.env.txt'
        env             sin el punto, al renombrar a mano
        ../.env         puesto un nivel mas arriba, que es donde uno lo pondria

    Y hay un cuarto que no deja rastro: muchos compresores NO meten los ficheros
    que empiezan por punto, asi que el .rar llega sin el y nadie lo nota.
    """
    candidatos = [ARCHIVO_ENV.parent / ".env.txt",
                  ARCHIVO_ENV.parent / "env",
                  ARCHIVO_ENV.parent / "env.para-repartir",
                  ARCHIVO_ENV.parent.parent / ".env"]
    hay = [c for c in candidatos if c.exists()]
    if not hay:
        return ("\n  Si venia en un .rar: muchos compresores se saltan los ficheros\n"
                "  que empiezan por punto. Copialo a mano.")
    return ("\n  OJO: hay un fichero parecido que NO se esta leyendo:\n"
            + "".join(f"    {c}\n" for c in hay)
            + f"  Tiene que llamarse exactamente .env y estar en {ARCHIVO_ENV.parent}")


def credenciales() -> tuple[str, str]:
    tenant, cliente = leer_env("GRAPH_TENANT_ID"), leer_env("GRAPH_CLIENT_ID")
    if not (tenant and cliente):
        sys.exit(
            "Faltan las credenciales de Graph.\n"
            # LA RUTA ENTERA, no el nombre. Decia solo '.env' y con eso nadie sabe
            # DONDE ponerlo: va junto a este modulo, y cuando el codigo se reparte
            # a otros equipos esa carpeta no es la que uno supone.
            f"  Se busca en: {ARCHIVO_ENV}\n"
            f"  Ahora mismo: {'existe pero no trae las claves' if ARCHIVO_ENV.exists() else 'NO EXISTE'}\n"
            "  Tiene que contener:\n"
            "    GRAPH_TENANT_ID=...\n"
            "    GRAPH_CLIENT_ID=...\n"
            "  Salen de la app registration en entra.microsoft.com > App registrations."
            + parecidos_al_env()
        )
    return tenant, cliente


class Graph:
    """Cliente de Microsoft Graph. Soporta los dos modos de autenticacion:

    APLICACION (recomendado)
        Con GRAPH_CLIENT_SECRET en el .env. Usa el permiso Sites.Selected, que al
        consentirse NO da acceso a nada: un admin habilita la app sitio por sitio.
        Es el modo acotado y el que conviene pedirle al administrador.

    DELEGADO
        Sin secret. La app actua como el usuario mediante device code y solo ve lo
        que el usuario ya ve. Necesita Sites.Read.All delegado.
    """

    def __init__(self) -> None:
        tenant, cliente = credenciales()
        autoridad = f"https://login.microsoftonline.com/{tenant}"
        self.secreto = leer_env("GRAPH_CLIENT_SECRET")
        self.modo = "aplicacion" if self.secreto else "delegado"

        if self.secreto:
            self.app = msal.ConfidentialClientApplication(
                cliente, authority=autoridad, client_credential=self.secreto
            )
            self.cache = None
        else:
            cache = msal.SerializableTokenCache()
            if CACHE_TOKEN.exists():
                cache.deserialize(CACHE_TOKEN.read_text(encoding="utf-8"))
            self.app = msal.PublicClientApplication(
                cliente, authority=autoridad, token_cache=cache
            )
            self.cache = cache

        self.sesion = requests.Session()
        # El pool por defecto de requests son 10 conexiones. mudar.py trabaja con
        # varios hilos, y al pasarse el pool descarta conexiones y las rehace una y
        # otra vez: se pierde justo la velocidad que se fue a buscar, y lo unico que
        # se ve es un aviso suelto de urllib3.
        adaptador = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
        self.sesion.mount("https://", adaptador)
        self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})

    def _token(self) -> str:
        if self.secreto:
            # Modo aplicacion: los permisos ya vienen fijados en la app registration
            resultado = self.app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        else:
            # 1. Token guardado de una corrida anterior
            cuentas = self.app.get_accounts()
            resultado = (
                self.app.acquire_token_silent(ALCANCES, account=cuentas[0]) if cuentas else None
            )

            # 2. Navegador: la app tiene http://localhost registrado como redirect,
            #    y para clientes publicos Entra acepta cualquier puerto en loopback
            if not resultado:
                try:
                    print("Abriendo el navegador para iniciar sesion...")
                    print("  ELIGE tu cuenta de @petroffamshen.com (la que es miembro "
                          "de Matters)", flush=True)
                    # select_account: sin esto el navegador entra solo con la cuenta
                    # que tenga abierta, y en un equipo prestado esa suele ser otra.
                    # Paso el 28-sep: 403 en Matters con la sesion equivocada.
                    resultado = self.app.acquire_token_interactive(
                        scopes=ALCANCES, prompt="select_account")
                except Exception as error:  # sin navegador disponible, por ejemplo
                    print(f"  no se pudo abrir el navegador ({type(error).__name__})")
                    resultado = None

            # 3. Device code: sirve cuando no hay navegador en esta maquina
            if not resultado or "access_token" not in resultado:
                flujo = self.app.initiate_device_flow(scopes=ALCANCES)
                if "user_code" not in flujo:
                    sys.exit(
                        "No se pudo autenticar ni por navegador ni por device code.\n"
                        "  Revisa que la app tenga 'Allow public client flows' activado.\n"
                        f"  Respuesta: {flujo.get('error_description', flujo)}"
                    )
                print("\n" + "=" * 66)
                print(flujo["message"])
                print("=" * 66 + "\n")
                resultado = self.app.acquire_token_by_device_flow(flujo)

        if "access_token" not in resultado:
            sys.exit(
                "Fallo la autenticacion:\n"
                f"  {resultado.get('error')}: {resultado.get('error_description')}"
            )

        if self.cache is not None and self.cache.has_state_changed:
            CACHE_TOKEN.write_text(self.cache.serialize(), encoding="utf-8")

        self.token = resultado["access_token"]
        return self.token

    def alcances_concedidos(self) -> list[str]:
        """Los permisos que trae el token, leidos del claim 'scp'.

        Sirve para saber que quedo consentido de verdad sin depender de lo que diga
        el portal. No se valida la firma: es solo informativo.
        """
        try:
            carga = self.token.split(".")[1]
            carga += "=" * (-len(carga) % 4)  # base64url sin padding
            datos = json.loads(base64.urlsafe_b64decode(carga))
        except Exception:
            return []
        return sorted((datos.get("scp") or datos.get("roles") or "").split()) if isinstance(
            datos.get("scp") or datos.get("roles"), str
        ) else sorted(datos.get("roles") or [])

    def _pedir(self, metodo: str, destino: str, **kw):
        """La llamada HTTP, reintentando cuando falla LA RED y no el servidor.

        HACE FALTA Y SE PAGO CARO NO TENERLO. Los 401, 429 y 503 ya se manejaban;
        un fallo de red, no. El 23-sep-2026 una corrida de 11,5 horas y 23.310
        archivos murio con un traceback de `getaddrinfo failed`: se cayo el DNS un
        instante. No se perdio nada -- el manifiesto permite continuar -- pero nadie
        estaba delante y la mudanza se quedo parada toda la noche.

        Un corte de red es lo MAS esperable en un proceso de doce horas, y es
        justamente lo unico que no estaba previsto.
        """
        for intento in range(6):
            try:
                return getattr(self.sesion, metodo)(destino, timeout=TIMEOUT, **kw)
            except requests.exceptions.RequestException as error:
                if intento == 5:
                    raise
                espera = min(60, 2 ** intento)
                # flush: sin el, este aviso se queda en el buffer y desde fuera
                # el proceso parece colgado. Paso el 28-sep: 55 minutos mudo en un
                # recorrido que estaba esperando por throttling, sin forma de saberlo.
                print(f"    red caida ({type(error).__name__}), "
                      f"reintento {intento + 1}/5 en {espera}s...", flush=True)
                time.sleep(espera)

    def get(self, url: str, **params) -> dict:
        """GET con reintentos ante throttling. Acepta ruta relativa o URL completa."""
        destino = url if url.startswith("http") else f"{GRAPH}{url}"
        for intento in range(6):
            r = self._pedir("get", destino, params=params or None)

            if r.status_code in (429, 503, 504):
                espera = int(r.headers.get("Retry-After", 2 ** intento))
                print(f"    throttling ({r.status_code}), esperando {espera}s...",
                      flush=True)
                time.sleep(espera)
                continue
            if r.status_code == 401:
                # El token expiro a mitad del recorrido
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            if r.status_code == 403:
                sys.exit(
                    "Acceso denegado (403).\n"
                    "  Modo aplicacion (Sites.Selected): el permiso esta consentido pero la app\n"
                    "    todavia no fue habilitada sobre este sitio. Un admin de SharePoint debe\n"
                    "    correr Grant-PnPAzureADAppSitePermission (ver README).\n"
                    "  Modo delegado: falta Sites.Read.All con consentimiento otorgado."
                )
            if r.status_code == 404:
                sys.exit(f"No encontrado (404): {destino}")
            r.raise_for_status()
            return r.json()
        sys.exit("Graph sigue respondiendo con throttling despues de 6 intentos.")

    def post(self, url: str, cuerpo: dict) -> dict:
        """POST con los mismos reintentos que get(). Es la unica via de escritura.

        Devuelve el JSON de la respuesta. Un 409 (ya existe) no es un error para
        quien llama: se devuelve tal cual para que decida si saltarlo.
        """
        destino = url if url.startswith("http") else f"{GRAPH}{url}"
        for intento in range(6):
            r = self._pedir("post", destino, json=cuerpo)

            if r.status_code in (429, 503, 504):
                espera = int(r.headers.get("Retry-After", 2 ** intento))
                time.sleep(espera)
                continue
            if r.status_code == 401:
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            if r.status_code == 409:
                return {"_conflicto": True}
            if r.status_code >= 400:
                raise OSError(f"POST {r.status_code}: {r.text[:200]}")
            return r.json()
        raise OSError("throttling persistente tras 6 intentos")

    def post_respuesta(self, url: str, cuerpo: dict):
        """Como post(), pero devuelve la RESPUESTA entera en vez del JSON.

        Existe por /copy, que es asincrono: contesta 202 con el cuerpo vacio y pone
        la URL de seguimiento en la cabecera Location. post() haria r.json() sobre
        un cuerpo vacio y reventaria, y por eso mudar.py llamaba a self.sesion.post
        a pelo -- saltandose de paso el refresco del token y el manejo de 429.

        Lo que costo: 163 archivos de la corrida del 23-sep-2026 fallaron con
        'InvalidAuthenticationToken'. El token caduca cada hora y la mudanza dura
        doce; get(), post() y patch() lo renuevan solos desde siempre, pero la
        llamada que mueve los archivos era justo la que no pasaba por ahi.
        """
        destino = url if url.startswith("http") else f"{GRAPH}{url}"
        for intento in range(6):
            r = self._pedir("post", destino, json=cuerpo)
            if r.status_code in (429, 503, 504):
                time.sleep(int(r.headers.get("Retry-After", 2 ** intento)))
                continue
            if r.status_code == 401:
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            return r
        raise OSError("throttling persistente tras 6 intentos")

    def patch(self, url: str, cuerpo: dict) -> dict:
        """PATCH con los mismos reintentos que get(). Renombrar es un PATCH de name.

        El id del item NO cambia al renombrar: es el mismo objeto con otro nombre.
        Por eso el manifiesto guarda el id y no la ruta: permite volver atras aunque
        el nombre ya no sirva para encontrar la carpeta.
        """
        destino = url if url.startswith("http") else f"{GRAPH}{url}"
        for intento in range(6):
            r = self._pedir("patch", destino, json=cuerpo)

            if r.status_code in (429, 503, 504):
                time.sleep(int(r.headers.get("Retry-After", 2 ** intento)))
                continue
            if r.status_code == 401:
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            if r.status_code >= 400:
                raise OSError(f"PATCH {r.status_code}: {r.text[:200]}")
            return r.json()
        raise OSError("throttling persistente tras 6 intentos")

    def batch(self, peticiones: list[dict]) -> list[dict]:
        """Manda hasta 20 operaciones en una sola llamada y devuelve sus respuestas.

        OJO: $batch responde 200 aunque operaciones individuales fallen. El estado
        real de cada una esta en su propio `status`, y hay que mirarlo una por una
        o se pierden carpetas en silencio.

        Reintenta el lote completo si Graph frena la llamada entera; las operaciones
        que vuelven con 429 por separado las reintenta quien llama, porque solo el
        que las armo sabe cuales puede repetir sin duplicar.
        """
        if len(peticiones) > 20:
            raise ValueError("$batch admite 20 operaciones como maximo")

        for intento in range(6):
            r = self.sesion.post(
                f"{GRAPH}/$batch", json={"requests": peticiones}, timeout=TIMEOUT
            )
            if r.status_code in (429, 503, 504):
                time.sleep(int(r.headers.get("Retry-After", 2 ** intento)))
                continue
            if r.status_code == 401:
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            if r.status_code >= 400:
                raise OSError(f"$batch {r.status_code}: {r.text[:200]}")
            return r.json().get("responses", [])
        raise OSError("throttling persistente tras 6 intentos")

    def descargar(self, url: str) -> bytes:
        """GET binario con los mismos reintentos que get(), pero devuelve bytes.

        Hace falta para las descargas masivas: una corrida de miles de archivos se
        topa con throttling, y el token expira a la hora. Sin esto, cada tropiezo
        se perderia en silencio como un archivo no leido.
        """
        for intento in range(6):
            r = self.sesion.get(url, timeout=180)

            if r.status_code in (429, 503, 504):
                time.sleep(int(r.headers.get("Retry-After", 2 ** intento)))
                continue
            if r.status_code == 401:
                self.sesion.headers.update({"Authorization": f"Bearer {self._token()}"})
                continue
            if r.status_code != 200:
                raise OSError(f"descarga {r.status_code}")
            return r.content
        raise OSError("throttling persistente tras 6 intentos")


# --------------------------------------------------------------------------- #
def resolver_biblioteca(g: Graph) -> tuple[str, str]:
    """Devuelve (driveId, nombre) de la biblioteca de documentos del sitio."""
    sitio = g.get(f"/sites/{SITIO}")
    print(f"Sitio: {sitio.get('displayName')}  ({sitio.get('webUrl')})")

    drives = g.get(f"/sites/{sitio['id']}/drives").get("value", [])
    if not drives:
        sys.exit("El sitio no tiene bibliotecas de documentos visibles.")
    for d in drives:
        if d.get("name") in ("Documents", "Shared Documents", "Documentos"):
            return d["id"], d["name"]
    return drives[0]["id"], drives[0]["name"]


def recorrer(g: Graph, drive_id: str, refrescar: bool) -> list[dict]:
    """Recorre el subarbol de CARPETA_RAIZ con delta y lo guarda en cache."""
    SALIDA_DIR.mkdir(exist_ok=True)
    if CACHE_ARBOL.exists() and not refrescar:
        items = json.loads(CACHE_ARBOL.read_text(encoding="utf-8"))
        print(f"Usando el cache local: {len(items):,} items (--refrescar para volver a recorrer)")
        return items

    raiz = g.get(f"/drives/{drive_id}/root:/{CARPETA_RAIZ}")
    print(f"Carpeta raiz: {raiz['name']}  ({raiz.get('size', 0) / 2**30:,.1f} GB)")

    items: list[dict] = []
    url = f"/drives/{drive_id}/items/{raiz['id']}/delta"
    params = {"$top": 999}
    pagina = 0

    while url:
        datos = g.get(url, **params) if params else g.get(url)
        params = {}  # los links de continuacion ya traen los parametros
        nuevos = datos.get("value", [])
        items.extend(nuevos)
        pagina += 1
        print(f"  pagina {pagina}: +{len(nuevos):,} items (total {len(items):,})")
        url = datos.get("@odata.nextLink", "")

    # delta devuelve un item OTRA VEZ si cambia mientras se enumera, y al renombrar
    # 2.293 carpetas eso paso a lo grande: el cache acabo con cada carpeta duplicada
    # y todo lo que se midiera encima salia al doble. Se queda la ULTIMA aparicion,
    # que es el estado mas reciente, porque delta las devuelve en orden.
    por_id: dict[str, dict] = {}
    for i in items:
        if i.get("id"):
            por_id[i["id"]] = i
    if len(por_id) < len(items):
        print(f"  delta repitio {len(items) - len(por_id):,} items; se queda el estado final")
    items = list(por_id.values())

    # Solo se guarda lo que hace falta para el analisis
    reducidos = [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "es_carpeta": "folder" in i,
            "size": i.get("size", 0),
            "hijos": i.get("folder", {}).get("childCount", 0) if "folder" in i else 0,
            "ruta": (i.get("parentReference") or {}).get("path", ""),
        }
        for i in items
        if i.get("name")
    ]
    CACHE_ARBOL.write_text(json.dumps(reducidos, ensure_ascii=False), encoding="utf-8")
    print(f"\nGuardado: {CACHE_ARBOL}  ({len(reducidos):,} items)")
    return reducidos


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Conexion y recorrido de SharePoint via Graph.")
    p.add_argument("--recorrer", action="store_true", help="Recorre el arbol y lo guarda.")
    p.add_argument("--refrescar", action="store_true", help="Ignora el cache del arbol.")
    args = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows

    g = Graph()
    print(f"Modo de autenticacion: {g.modo}")
    concedidos = g.alcances_concedidos()
    if concedidos:
        print(f"Permisos en el token: {', '.join(concedidos)}")
        if not any(a.startswith("Sites.") for a in concedidos):
            print("  OJO: no hay ningun permiso Sites.* — leer el sitio va a fallar con 403.")
    if g.modo == "delegado":
        yo = g.get("/me")
        print(f"Conectado como: {yo.get('displayName')} <{yo.get('mail') or yo.get('userPrincipalName')}>")
    else:
        print("  (la app actua por si misma; solo alcanza los sitios donde fue habilitada)")

    drive_id, nombre = resolver_biblioteca(g)
    print(f"Biblioteca: {nombre}\n  driveId: {drive_id}")

    if not args.recorrer:
        print("\nTodo listo. Corre con --recorrer para traer el arbol completo.")
        return

    items = recorrer(g, drive_id, args.refrescar)
    carpetas = sum(1 for i in items if i["es_carpeta"])
    print(f"\n  carpetas: {carpetas:,}")
    print(f"  archivos: {len(items) - carpetas:,}")
    print("\nAhora corre:  .venv\\Scripts\\python.exe sp_indices.py")


if __name__ == "__main__":
    main()
