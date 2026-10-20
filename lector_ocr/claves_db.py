"""La base de claves del paso 1 en PostgreSQL: un documento entra una sola vez.

    from lector_ocr.claves_db import RegistroPostgres

    registro = RegistroPostgres.desde_entorno()      # POSTGRES_HOST, _PORT, _DB, _USER, _PASSWORD
    registro.crear_tabla()                           # una vez; no borra nada
    nuevo, primera = registro.anotar(clave, sharepoint_id=..., matter=..., file_name=...)

POR QUE UNA TABLA Y NO EL JSONL DE recepcion.RegistroDeClaves. Con varias instancias
del agente, dos copias del mismo documento pueden llegar a la vez; con un fichero las
dos se creen la primera. Aqui la LLAVE ES CLAVE PRIMARIA: la segunda insercion choca
y PostgreSQL la hace esperar a que la primera termine. 'Gana la primera copia' lo
garantiza la base, no el codigo.

UN DOCUMENTO TIENE VARIAS LLAVES y basta que coincida UNA para que sea el mismo:
    court    'nyscef:kings:507148-2026:12'   el juzgado: tribunal, caso, numero
    qxh      'qxh:VIayqWAn...'               el quickXorHash, la huella de SharePoint
    sha256   'sha256:9f2c...'                la huella propia
Se insertan todas en una transaccion; si alguna ya estaba, se deshace y se devuelve
la primera copia. Todas apuntan al mismo 'documento' (su primera llave), para poder
reunirlas.

CARGAR LO QUE YA HAY no exige bajar nada: el listado de Matters trae el quickXorHash
de cada archivo, y la clave del juzgado sale del texto ya leido (el sello va en la
capa de texto). `cargar_existentes` lo inserta sin pisar nada y dice cuantos ya
estaban: los que chocan son duplicados exactos, que van al plan de duplicados.

El paquete no trae psycopg2 como dependencia: solo hace falta donde se use esto
(pip install psycopg2-binary, la misma que usa Petroff-Litigation-Assistant).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

TABLA = "docs_agent_claves"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS {tabla} (
    llave          TEXT PRIMARY KEY,
    tipo           TEXT NOT NULL,
    documento      TEXT NOT NULL,
    sharepoint_id  TEXT,
    matter         TEXT,
    file_name      TEXT,
    origen         TEXT,
    recibido       TIMESTAMPTZ NOT NULL DEFAULT now(),
    datos          JSONB
);
CREATE INDEX IF NOT EXISTS {tabla}_documento ON {tabla} (documento);
CREATE INDEX IF NOT EXISTS {tabla}_sharepoint ON {tabla} (sharepoint_id);
"""

COLUMNAS = ("llave", "tipo", "documento", "sharepoint_id", "matter", "file_name", "origen",
            "recibido", "datos")


def tipo_de_llave(llave: str) -> str:
    if llave.startswith("qxh:"):
        return "qxh"
    if llave.startswith("sha256:"):
        return "sha256"
    return "court"


class RegistroPostgres:
    """La misma interfaz que recepcion.RegistroDeClaves (buscar, anotar), en una tabla.

    `conectar` devuelve una conexion DB-API nueva (psycopg2.connect, o sqlite3 para
    probar). `marcador` es el de los parametros: '%s' en psycopg2, '?' en sqlite3.
    """

    def __init__(self, conectar: Callable[[], Any], *, tabla: str = TABLA,
                 marcador: str = "%s") -> None:
        self._conectar = conectar
        self.tabla = tabla
        self.m = marcador

    @classmethod
    def desde_entorno(cls, tabla: str = TABLA) -> "RegistroPostgres":
        """Con las variables POSTGRES_* (las mismas de Petroff-Litigation-Assistant)."""
        import psycopg2

        def conectar():
            return psycopg2.connect(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                dbname=os.environ.get("POSTGRES_DB", "petroff_rag"),
                user=os.environ.get("POSTGRES_USER", "postgres"),
                password=os.environ.get("POSTGRES_PASSWORD", ""),
                connect_timeout=int(os.environ.get("POSTGRES_CONNECT_TIMEOUT_SEGUNDOS", "5")))

        return cls(conectar, tabla=tabla)

    def crear_tabla(self) -> None:
        """La tabla y sus indices, si no existen. No borra ni cambia nada."""
        with self._conexion() as (con, cur):
            for sentencia in ESQUEMA.format(tabla=self.tabla).split(";"):
                if sentencia.strip():
                    cur.execute(sentencia)
            con.commit()

    # ------------------------------------------------------------------ #
    def buscar(self, clave) -> dict | None:
        """La primera copia de este documento, o None si nunca llego."""
        llaves = list(clave.llaves)
        if not llaves:
            return None
        with self._conexion() as (_, cur):
            return self._buscar(cur, llaves)

    def anotar(self, clave, **datos: Any) -> tuple[bool, dict]:
        """(True, su fila) si es nuevo y quedan anotadas sus llaves; (False, la primera
        copia) si alguna llave ya estaba. Atomico: o entran todas o ninguna."""
        llaves = list(clave.llaves)
        documento = llaves[0]
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conexion() as (con, cur):
            entraron = 0
            for llave in llaves:
                cur.execute(self._insertar(), self._fila(llave, documento, ahora, datos))
                entraron += 1 if cur.fetchone() else 0
            if entraron < len(llaves):
                con.rollback()
                primera = self._buscar(cur, llaves)
                return False, primera or {}
            con.commit()
            return True, {"documento": documento, "llaves": llaves, "recibido": ahora, **datos}

    def cargar_existentes(self, documentos: Iterable[tuple[list[str], dict]],
                          lote: int = 500) -> dict:
        """Lo que ya esta en Matters: [(llaves, datos), ...]. No pisa nada: lo que choca
        es un duplicado exacto de algo ya cargado (o un reintento) y solo se cuenta."""
        cuenta = {"documentos": 0, "nuevos": 0, "duplicados": 0}
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conexion() as (con, cur):
            for n, (llaves, datos) in enumerate(documentos, 1):
                llaves = [l for l in llaves if l]
                if not llaves:
                    continue
                cuenta["documentos"] += 1
                choco = False
                for llave in llaves:
                    cur.execute(self._insertar(), self._fila(llave, llaves[0], ahora, datos))
                    choco |= cur.fetchone() is None
                cuenta["duplicados" if choco else "nuevos"] += 1
                if n % lote == 0:
                    con.commit()
            con.commit()
        return cuenta

    # ------------------------------------------------------------------ #
    def _insertar(self) -> str:
        marcas = ", ".join([self.m] * len(COLUMNAS))
        return (f"INSERT INTO {self.tabla} ({', '.join(COLUMNAS)}) VALUES ({marcas}) "
                f"ON CONFLICT (llave) DO NOTHING RETURNING llave")

    @staticmethod
    def _fila(llave: str, documento: str, ahora: str, datos: dict) -> tuple:
        extra = {k: v for k, v in datos.items()
                 if k not in ("sharepoint_id", "matter", "file_name", "origen")}
        return (llave, tipo_de_llave(llave), documento, datos.get("sharepoint_id"),
                datos.get("matter"), datos.get("file_name"), datos.get("origen"), ahora,
                json.dumps(extra, ensure_ascii=False, default=str) if extra else None)

    def _buscar(self, cur, llaves: list[str]) -> dict | None:
        marcas = ", ".join([self.m] * len(llaves))
        cur.execute(f"SELECT {', '.join(COLUMNAS)} FROM {self.tabla} WHERE llave IN ({marcas}) "
                    f"ORDER BY recibido LIMIT 1", tuple(llaves))
        fila = cur.fetchone()
        if not fila:
            return None
        registro = dict(zip(COLUMNAS, fila))
        if isinstance(registro.get("datos"), str):
            registro["datos"] = json.loads(registro["datos"])
        registro["recibido"] = str(registro["recibido"])
        return registro

    class _Conexion:
        def __init__(self, con):
            self.con = con

        def __enter__(self):
            return self.con, self.con.cursor()

        def __exit__(self, *exc):
            self.con.close()

    def _conexion(self):
        return RegistroPostgres._Conexion(self._conectar())
