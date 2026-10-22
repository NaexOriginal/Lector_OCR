"""El registro de lo que el agente mueve o renombra en SharePoint, para poder deshacerlo.

    from lector_ocr.movimientos_db import RegistroMovimientos

    registro = RegistroMovimientos.desde_entorno()   # POSTGRES_*, como claves_db
    registro.crear_tabla()                           # una vez; no borra nada
    id_ = registro.anotar(sharepoint_id=..., nombre_antes=..., nombre_despues=..., motivo=...)
    inversa = registro.deshacer(id_)                 # que hay que hacer para volver atras

NUNCA SE BORRA UN DOCUMENTO: lo unico que el Docs Agent cambia en SharePoint es donde
esta un archivo (o una carpeta) y como se llama. Cada cambio deja aqui una fila con el
antes y el despues, y con eso basta para volver atras.

QUIEN ANOTA ES QUIEN MUEVE, y DESPUES de que SharePoint confirme el cambio: una fila
sin cambio haria que deshacer mueva algo que nunca se movio. Este modulo no habla con
SharePoint; `deshacer` devuelve la operacion inversa, quien llama la ejecuta (PATCH con
conflictBehavior=fail) y solo si sale bien llama a `marcar_deshecho`.

POR QUE UNA TABLA APARTE Y NO DENTRO DE docs_agent_claves. Un renombrado no es un
documento nuevo: mezclarlos haria que deshacer un cambio tocara la deduplicacion. Las
dos tablas viven en la misma base y se crean juntas:

    python -m lector_ocr.movimientos_db --crear-tablas
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

TABLA = "docs_agent_movimientos"

# El id es un uuid que pone el codigo, no un SERIAL: asi el esquema es el mismo en
# PostgreSQL y en sqlite3, que es con lo que se prueba.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS {tabla} (
    id                 TEXT PRIMARY KEY,
    sharepoint_id      TEXT NOT NULL,
    tipo_item          TEXT NOT NULL,
    matter             TEXT,
    padre_antes_id     TEXT,
    padre_antes_ruta   TEXT,
    padre_despues_id   TEXT,
    padre_despues_ruta TEXT,
    nombre_antes       TEXT,
    nombre_despues     TEXT,
    motivo             TEXT NOT NULL,
    origen             TEXT,
    hecho              TIMESTAMPTZ NOT NULL,
    deshecho           TIMESTAMPTZ,
    deshecho_por       TEXT,
    datos              JSONB
);
CREATE INDEX IF NOT EXISTS {tabla}_sharepoint ON {tabla} (sharepoint_id);
CREATE INDEX IF NOT EXISTS {tabla}_matter ON {tabla} (matter);
"""

COLUMNAS = ("id", "sharepoint_id", "tipo_item", "matter", "padre_antes_id", "padre_antes_ruta",
            "padre_despues_id", "padre_despues_ruta", "nombre_antes", "nombre_despues", "motivo",
            "origen", "hecho", "deshecho", "deshecho_por", "datos")

TIPOS_ITEM = ("archivo", "carpeta")


class RegistroMovimientos:
    """Las filas de docs_agent_movimientos. `conectar` y `marcador` como en
    claves_db.RegistroPostgres: psycopg2 con '%s', o sqlite3 con '?' para probar."""

    def __init__(self, conectar: Callable[[], Any], *, tabla: str = TABLA,
                 marcador: str = "%s") -> None:
        self._conectar = conectar
        self.tabla = tabla
        self.m = marcador

    @classmethod
    def desde_entorno(cls, tabla: str = TABLA) -> "RegistroMovimientos":
        """Con las mismas variables POSTGRES_* que la base de claves."""
        from .claves_db import RegistroPostgres

        return cls(RegistroPostgres.desde_entorno()._conectar, tabla=tabla)

    def crear_tabla(self) -> None:
        """La tabla y sus indices, si no existen. No borra ni cambia nada."""
        with self._conexion() as (con, cur):
            for sentencia in ESQUEMA.format(tabla=self.tabla).split(";"):
                if sentencia.strip():
                    cur.execute(sentencia)
            con.commit()

    # ------------------------------------------------------------------ #
    def anotar(self, *, sharepoint_id: str, motivo: str, tipo_item: str = "archivo",
               matter: str = "", padre_antes_id: str = "", padre_antes_ruta: str = "",
               padre_despues_id: str = "", padre_despues_ruta: str = "",
               nombre_antes: str = "", nombre_despues: str = "", origen: str = "",
               **datos: Any) -> str:
        """Un cambio ya hecho en SharePoint. Devuelve el id de la fila.

        Un movimiento lleva los dos padres; un renombrado, los dos nombres; si fue las
        dos cosas a la vez, todo. Sin ninguno de los dos no hay nada que deshacer."""
        if tipo_item not in TIPOS_ITEM:
            raise ValueError(f"tipo_item '{tipo_item}': tiene que ser uno de {TIPOS_ITEM}")
        movio = bool(padre_despues_id or padre_despues_ruta)
        renombro = bool(nombre_despues) and nombre_despues != nombre_antes
        if not (movio or renombro):
            raise ValueError("ni se movio ni se renombro: no hay nada que anotar")
        if movio and not (padre_antes_id or padre_antes_ruta):
            raise ValueError("un movimiento sin la carpeta de antes no se puede deshacer")
        if renombro and not nombre_antes:
            raise ValueError("un renombrado sin el nombre de antes no se puede deshacer")
        id_ = str(uuid.uuid4())
        fila = (id_, sharepoint_id, tipo_item, matter or None, padre_antes_id or None,
                padre_antes_ruta or None, padre_despues_id or None, padre_despues_ruta or None,
                nombre_antes or None, nombre_despues or None, motivo, origen or None,
                _ahora(), None, None,
                json.dumps(datos, ensure_ascii=False, default=str) if datos else None)
        marcas = ", ".join([self.m] * len(COLUMNAS))
        with self._conexion() as (con, cur):
            cur.execute(f"INSERT INTO {self.tabla} ({', '.join(COLUMNAS)}) VALUES ({marcas})", fila)
            con.commit()
        return id_

    def buscar(self, id_: str) -> dict | None:
        with self._conexion() as (_, cur):
            filas = self._select(cur, f"WHERE id = {self.m}", (id_,))
        return filas[0] if filas else None

    def historial(self, sharepoint_id: str) -> list[dict]:
        """Todos los cambios de un archivo o carpeta, del mas viejo al mas nuevo."""
        with self._conexion() as (_, cur):
            return self._select(cur, f"WHERE sharepoint_id = {self.m} ORDER BY hecho, id",
                                (sharepoint_id,))

    def deshacer(self, id_: str) -> dict:
        """La operacion que deja el item como estaba antes de ese cambio:
            {'sharepoint_id', 'parent_id', 'parent_path', 'name'}
        (parent_* solo si se movio, name solo si se renombro). No toca SharePoint.

        Solo se deshace el ULTIMO cambio pendiente de un item: deshacer uno de en medio
        dejaria el item en un sitio que no corresponde a ningun momento real."""
        fila = self.buscar(id_)
        if fila is None:
            raise LookupError(f"no hay ningun movimiento con id {id_}")
        if fila["deshecho"]:
            raise ValueError(f"el movimiento {id_} ya se deshizo el {fila['deshecho']}")
        pendientes = [f for f in self.historial(fila["sharepoint_id"]) if not f["deshecho"]]
        if pendientes and pendientes[-1]["id"] != id_:
            raise ValueError(f"el item {fila['sharepoint_id']} tiene cambios posteriores sin "
                             f"deshacer: primero {pendientes[-1]['id']}")
        inversa: dict[str, Any] = {"sharepoint_id": fila["sharepoint_id"]}
        if fila["padre_despues_id"] or fila["padre_despues_ruta"]:
            inversa["parent_id"] = fila["padre_antes_id"]
            inversa["parent_path"] = fila["padre_antes_ruta"]
        if fila["nombre_despues"] and fila["nombre_despues"] != fila["nombre_antes"]:
            inversa["name"] = fila["nombre_antes"]
        return inversa

    def marcar_deshecho(self, id_: str, por: str = "") -> None:
        """Despues de que SharePoint confirme la operacion inversa. La fila no se borra:
        queda como registro de que hubo un cambio y se deshizo."""
        with self._conexion() as (con, cur):
            cur.execute(f"UPDATE {self.tabla} SET deshecho = {self.m}, deshecho_por = {self.m} "
                        f"WHERE id = {self.m} AND deshecho IS NULL", (_ahora(), por or None, id_))
            con.commit()

    # ------------------------------------------------------------------ #
    def _select(self, cur, donde: str, parametros: tuple) -> list[dict]:
        cur.execute(f"SELECT {', '.join(COLUMNAS)} FROM {self.tabla} {donde}", parametros)
        salida = []
        for fila in cur.fetchall():
            registro = dict(zip(COLUMNAS, fila))
            if isinstance(registro.get("datos"), str):
                registro["datos"] = json.loads(registro["datos"])
            for campo in ("hecho", "deshecho"):
                if registro[campo] is not None:
                    registro[campo] = str(registro[campo])
            salida.append(registro)
        return salida

    def _conexion(self):
        from .claves_db import RegistroPostgres

        return RegistroPostgres._Conexion(self._conectar())


def _ahora() -> str:
    # Con microsegundos: dos cambios del mismo item en el mismo segundo tienen que
    # quedar en orden para que `deshacer` sepa cual fue el ultimo.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def crear_tablas() -> None:
    """Las dos tablas del Docs Agent: docs_agent_claves y docs_agent_movimientos."""
    from .claves_db import RegistroPostgres

    RegistroPostgres.desde_entorno().crear_tabla()
    RegistroMovimientos.desde_entorno().crear_tabla()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["--crear-tablas"]:
        sys.exit("uso: python -m lector_ocr.movimientos_db --crear-tablas")
    crear_tablas()
    print(f"tablas listas: docs_agent_claves, {TABLA}")
