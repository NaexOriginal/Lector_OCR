r"""Arma el Claude-{ID}.jsonl de cada caso COMPLETO DE ESTE EQUIPO y lo sube a Documentos/JSONL/Casos_<equipo>/.

CADA EQUIPO ARMA SOLO SUS CASOS (los que le tocan en el reparto por casos). Lo hace la
propia lectura, en segundo plano, con `describir_casos.py ... --lista lista_casos2022_<equipo>.txt
--armar-casos 2022`: el equipo sale del nombre de la lista. Una sola ventana.

Lo que necesita, en codigo\salida\:
    arbol_matters.jsonl                       el listado de Matters de la etapa
    etapa_<etapa>\reparto_casos_<etapa>.csv   que caso lleva cada equipo
    etapa_<etapa>\base_fichas_<etapa>_<equipo>.jsonl
                                              lo ya leido ANTES de la etapa de sus casos (y los
                                              originales de sus copias). Lo prepara Rafael con
                                              --preparar-bases. Lleva texto de clientes.

Cada vuelta (al arrancar y luego cada hora):
  1. Junta lo leido: su base y sus diarios de etapa (textos_matters.lista_*.jsonl).
  2. Un caso esta COMPLETO cuando cada uno de sus archivos esta leido, resuelto sin texto de
     forma definitiva o es copia exacta (mismo hash) de uno leido. Con OCR y librerias.
  3. Antes de subirlo lo lista EN DIRECTO en SharePoint: si tiene archivos que no estaban en
     el listado de la etapa (o le faltan), no se sube y se apunta.
  4. Lo arma con el formato de siempre y lo sube a Documentos/JSONL/Casos_<equipo>/
     Claude-{ID}.jsonl. Si dos carpetas tienen el mismo ID: Claude-{ID}_{indice}.jsonl.
  5. Un caso subido no se vuelve a subir mientras no cambie su numero de archivos.

La base se indexa UNA vez (id -> donde esta su linea) y el texto se va a buscar solo a las
lineas de los casos que se arman: dentro de la lectura casi no pesa.

NO LEE DOCUMENTOS Y NO HACE OCR. Los Claude-{ID}.jsonl NO van a las carpetas de los casos.

Uso suelto (desde la carpeta que contiene codigo\):
    python codigo\armar_casos.py --equipo rafael --simular --una-vez     (escribe en local)
Preparar las bases de los 3 equipos (solo Rafael, una vez por etapa):
    python codigo\armar_casos.py --preparar-bases --reparto <reparto>.csv --arbol <arbol>.jsonl ^
        --fuentes <respaldo entero> <diarios de etapa ya hechos> --salida <carpeta>
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))
from describir_casos import (CARPETA_DESTINO, SUBIDA_CARPETA, armar, es_definitiva,  # noqa: E402
                             hay_que_releer, linea_json, subir_a_sharepoint)
from sp_conexion import GRAPH, Graph  # noqa: E402

CAMPOS_DE_LECTURA = ("was_read", "read_with", "not_read_because", "pages_read", "ocr_ok",
                     "extracted_text_length", "extracted_text", "reader_version")
PROPIO = re.compile(r"^Claude-[^/]*\.jsonl?$", re.I)


def _id_interno(exp: str) -> str | None:
    m = re.search(r" - (\d{6})(?: - |$)", exp)
    return m.group(1) if m else None


class Armador:
    """Lleva la cuenta de lo leido entre vueltas y arma/sube los casos completos del equipo."""

    def __init__(self, etapa: str, g: Graph, drive: str, equipo: str, carpeta: Path | None = None,
                 arbol: Path | None = None, diarios_locales: Path | None = None, apuntar=None,
                 simular: bool = False):
        self.etapa, self.g, self.drive, self.equipo, self.simular = etapa, g, drive, equipo, simular
        self.diarios_locales = diarios_locales or AQUI / "salida"
        self.carpeta = carpeta or self.diarios_locales / f"etapa_{etapa}"
        self.log = self.carpeta / f"log_armar_casos_{etapa}_{equipo}.txt"
        self.registro = self.carpeta / f"casos_subidos_{etapa}_{equipo}.json"
        self._apuntar_fuera = apuntar
        reparto = pd.read_csv(self.carpeta / f"reparto_casos_{etapa}.csv", dtype=str, encoding="utf-8-sig")
        self.casos = set(reparto[reparto.equipo == equipo].expediente)
        if not self.casos:
            raise ValueError(f"el reparto no tiene casos para el equipo '{equipo}'")
        self.arbol: dict[str, list[str]] = collections.defaultdict(list)
        self.meta: dict[str, dict] = {}
        for l in open(arbol or self.diarios_locales / "arbol_matters.jsonl", encoding="utf-8"):
            r = json.loads(l)
            if r["exp"] in self.casos:
                self.arbol[r["exp"]].append(r["id"])
                self.meta[r["id"]] = r
        sin_archivos = self.casos - set(self.arbol)
        if sin_archivos:
            raise ValueError(f"{len(sin_archivos)} casos del reparto no estan en el arbol: el arbol no es el de la etapa")
        cuenta = collections.Counter(_id_interno(e) for e in reparto.expediente if _id_interno(e))
        self.repetidos = {i for i, n in cuenta.items() if n > 1}
        # estado[id] = (ok, was_read, hash, fichero, offset, largo). Gana una lectura con texto.
        self.estado: dict[str, tuple] = {}
        self.leido_hasta: dict[str, int] = {}
        self._cargar_base()

    def apuntar(self, texto: str) -> None:
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%d/%m %H:%M:%S}  {texto}\n")
        if self._apuntar_fuera:
            self._apuntar_fuera("ARMADO  " + texto)
        else:
            print(f"  {datetime.now():%d/%m %H:%M:%S}  {texto}", flush=True)

    def _indexar(self, fichero: Path) -> int:
        """Lee de 'fichero' solo lo nuevo desde la ultima vez y apunta donde esta cada ficha."""
        desde = self.leido_hasta.get(str(fichero), 0)
        if fichero.stat().st_size < desde:      # el fichero es otro (se borro y empezo de cero)
            desde = 0
        nuevas = 0
        with open(fichero, "rb") as fh:
            fh.seek(desde)
            pos = desde
            for crudo in fh:
                if not crudo.endswith(b"\n"):    # linea a medio escribir: la proxima vez
                    break
                inicio, pos = pos, pos + len(crudo)
                if b'"id"' not in crudo:
                    continue
                try:
                    x = json.loads(crudo)
                except ValueError:
                    continue
                i = x.get("id")
                if not i or "was_read" not in x or hay_que_releer(x):
                    continue
                previa = self.estado.get(i)
                if previa and previa[1] and not x.get("was_read"):
                    continue
                # Un fallo definitivo tampoco lo pisa uno pasajero. Los diarios se leen
                # por orden de NOMBRE, no de fecha: el 9-oct el de 'relectura' (viejo)
                # pisaba al de 'incompletos' (nuevo) y el zip de audio ya cerrado seguia
                # dejando su caso incompleto.
                definitiva = es_definitiva(x)
                if previa and previa[0] and not definitiva:
                    continue
                self.estado[i] = (definitiva, bool(x.get("was_read")), x.get("hash"),
                                  str(fichero), inicio, len(crudo))
                nuevas += 1
            self.leido_hasta[str(fichero)] = pos
        return nuevas

    def _ficha(self, i: str) -> dict:
        _, _, _, fichero, inicio, largo = self.estado[i]
        with open(fichero, "rb") as fh:
            fh.seek(inicio)
            return json.loads(fh.read(largo))

    def _cargar_base(self) -> None:
        base = self.carpeta / f"base_fichas_{self.etapa}_{self.equipo}.jsonl"
        if not base.exists():
            raise FileNotFoundError(f"falta {base}: pidesela a Rafael (lo ya leido de tus casos)")
        indice = self.carpeta / f"base_indice_{self.etapa}_{self.equipo}.json"
        if indice.exists():
            datos = json.loads(indice.read_text(encoding="utf-8"))
            if datos.get("bytes") == base.stat().st_size:
                self.estado = {i: tuple(v[:3]) + (str(base),) + tuple(v[3:]) for i, v in datos["fichas"].items()}
                self.leido_hasta[str(base)] = datos["bytes"]
        if str(base) not in self.leido_hasta:
            self._indexar(base)
            indice.write_text(json.dumps({"bytes": base.stat().st_size, "fichas": {
                i: [v[0], v[1], v[2], v[4], v[5]] for i, v in self.estado.items()}}), encoding="utf-8")
        self.apuntar(f"equipo {self.equipo}: {len(self.arbol)} casos, {len(self.meta):,} archivos | "
                     f"base: {len(self.estado):,} fichas")

    def _get(self, url: str) -> dict:
        r = self.g.sesion.get(url, timeout=120)
        if r.status_code == 401:
            self.g.sesion.headers.update({"Authorization": f"Bearer {self.g._token()}"})
            r = self.g.sesion.get(url, timeout=120)
        r.raise_for_status()
        return r.json()

    def _vivos(self, carpeta_id: str) -> set:
        vivos = set()

        def recorrer(item_id: str) -> None:
            u = f"{GRAPH}/drives/{self.drive}/items/{item_id}/children?$select=id,name,folder&$top=999"
            while u:
                rr = self._get(u)
                for x in rr.get("value", []):
                    if "folder" in x:
                        recorrer(x["id"])
                    elif not PROPIO.match(x["name"]):
                        vivos.add(x["id"])
                u = rr.get("@odata.nextLink")

        recorrer(carpeta_id)
        return vivos

    def vuelta(self) -> None:
        nuevas = sum(self._indexar(f) for f in sorted(self.diarios_locales.glob("textos_matters.lista_*.jsonl")))
        hash_leido = {v[2] for v in self.estado.values() if v[1] and v[2]}

        def resuelto(i: str) -> bool:
            e, h = self.estado.get(i), self.meta[i].get("hash")
            return bool(e and e[0]) or bool(h and h in hash_leido)

        hechos = json.loads(self.registro.read_text(encoding="utf-8")) if self.registro.exists() else {}
        completos = [e for e, ids in self.arbol.items() if all(resuelto(i) for i in ids)]
        # Se sube lo que no se subio, o lo subido con otro numero de archivos. "Cambio en
        # SharePoint" no cuenta como subido: se reintenta en cada vuelta.
        por_subir = [e for e in completos
                     if not (hechos.get(e, {}).get("estado") == "subido"
                             and hechos[e].get("archivos") == len(self.arbol[e]))]
        self.apuntar(f"vuelta: {nuevas:,} fichas nuevas | casos completos {len(completos)} de {len(self.arbol)} | "
                     f"ya subidos {sum(1 for v in hechos.values() if v.get('estado') == 'subido')} | "
                     f"por subir {len(por_subir)}")
        if not por_subir:
            return
        hoy = {}
        url = f"{GRAPH}/drives/{self.drive}/root:/{CARPETA_DESTINO}:/children?$select=id,name&$top=999"
        while url:
            r = self._get(url)
            hoy.update({x["name"]: x["id"] for x in r.get("value", [])})
            url = r.get("@odata.nextLink")
        original_de = {}
        for i, v in self.estado.items():
            if v[1] and v[2]:
                original_de.setdefault(v[2], i)
        for e in por_subir:
            try:
                self._subir_caso(e, hoy, original_de, hechos)
            except Exception as error:  # noqa: BLE001 - un caso que falla no para los demas
                self.apuntar(f"FALLO {e}: {type(error).__name__}: {str(error)[:140]} | se reintenta")
        if not self.simular:
            self.registro.write_text(json.dumps(hechos, ensure_ascii=False, indent=1), encoding="utf-8")

    def _subir_caso(self, e: str, hoy: dict, original_de: dict, hechos: dict) -> None:
        carpeta_id = hoy.get(e)
        if not carpeta_id:
            self.apuntar(f"NO SUBIDO {e}: ya no existe con ese nombre en Matters (renombrado o borrado)")
            return
        vivos = self._vivos(carpeta_id)
        if vivos != set(self.arbol[e]):
            self.apuntar(f"NO SUBIDO {e}: cambio en SharePoint desde el listado de la etapa (hoy "
                         f"{len(vivos)} archivos, en la etapa {len(self.arbol[e])}): hay que leer lo nuevo")
            hechos[e] = {"archivos": len(self.arbol[e]), "estado": "cambio en SharePoint",
                         "cuando": f"{datetime.now():%Y-%m-%d %H:%M}"}
            return
        fichas = []
        for i in self.arbol[e]:
            v = self.estado.get(i)
            if v and v[0]:
                fichas.append({**self._ficha(i), "id": i, "caso": e})
                continue
            m = self.meta[i]
            orig = self._ficha(original_de[m["hash"]])
            sub = m.get("sub") or ""
            fichas.append({"id": i, "caso": e, "file_name": m["name"],
                           "subfolder": sub.rsplit("/", 1)[0] if "/" in sub else "(folder root)",
                           "size_mb": round(m["size"] / 2 ** 20, 2), "hash": m["hash"],
                           **{k: orig.get(k) for k in CAMPOS_DE_LECTURA}, "copied_from": orig.get("id")})
        crudo = armar(e, fichas, len(vivos), carpeta_id).encode("utf-8")
        ident = _id_interno(e) or "sin-id"
        nombre = (f"Claude-{ident}_{e.rsplit(' - ', 1)[-1].strip().replace('/', '-')}.jsonl"
                  if ident in self.repetidos else f"Claude-{ident}.jsonl")
        ruta = f"{SUBIDA_CARPETA}/Casos_{self.equipo}/{nombre}"
        if self.simular:
            destino = self.carpeta / "simulados" / f"Casos_{self.equipo}"
            destino.mkdir(parents=True, exist_ok=True)
            (destino / nombre).write_bytes(crudo)
            self.apuntar(f"SIMULADO {ruta}: {len(fichas)} archivos, {len(crudo) / 2 ** 20:.2f} MB  <- {e}")
            return
        subir_a_sharepoint(self.g, self.drive, ruta, crudo)
        hechos[e] = {"archivos": len(self.arbol[e]), "estado": "subido", "jsonl": ruta,
                     "sha256": hashlib.sha256(crudo).hexdigest(), "cuando": f"{datetime.now():%Y-%m-%d %H:%M}"}
        self.registro.write_text(json.dumps(hechos, ensure_ascii=False, indent=1), encoding="utf-8")
        self.apuntar(f"SUBIDO {ruta}: {len(fichas)} archivos, {len(crudo) / 2 ** 20:.2f} MB  <- {e}")

    def aparte(self) -> None:
        """LOS CASOS QUE NO SE PUEDEN SUBIR NORMALES, A CARPETAS APARTE (8-oct).

        Al terminar la lectura de una etapa quedan tres clases de casos que el armado normal
        no sube, a proposito: (1) completos, pero en SharePoint cambiaron desde el listado de
        la etapa; (2) incompletos porque algun archivo no se puede leer nunca (ZIP con
        contrasena, borrado, PDF danado); (3) su carpeta ya no existe con ese nombre en
        Matters. Para tener ya su texto, se suben a Casos_<equipo>_cambiados/,
        _incompletos/ y _sin_carpeta/, con un 'aviso' en la cabecera que dice que falta.
        Los archivos no leidos van como ficha sin texto y con el motivo. No toca el
        registro normal: cuando el caso se complete, el armado normal lo subira a
        Casos_<equipo>/ como siempre, y estas carpetas se pueden borrar.
        """
        for f in sorted(self.diarios_locales.glob("textos_matters.lista_*.jsonl")):
            self._indexar(f)
        hash_leido = {v[2] for v in self.estado.values() if v[1] and v[2]}
        original_de = {}
        for i, v in self.estado.items():
            if v[1] and v[2]:
                original_de.setdefault(v[2], i)

        def resuelto(i: str) -> bool:
            e, h = self.estado.get(i), self.meta[i].get("hash")
            return bool(e and e[0]) or bool(h and h in hash_leido)

        hechos = json.loads(self.registro.read_text(encoding="utf-8")) if self.registro.exists() else {}
        pendientes = [e for e in sorted(self.arbol) if hechos.get(e, {}).get("estado") != "subido"]
        hoy = {}
        url = f"{GRAPH}/drives/{self.drive}/root:/{CARPETA_DESTINO}:/children?$select=id,name&$top=999"
        while url:
            r = self._get(url)
            hoy.update({x["name"]: x["id"] for x in r.get("value", [])})
            url = r.get("@odata.nextLink")
        registro = self.carpeta / f"casos_aparte_{self.etapa}_{self.equipo}.json"
        aparte = json.loads(registro.read_text(encoding="utf-8")) if registro.exists() else {}
        for e in pendientes:
            ids = self.arbol[e]
            sin_leer = [i for i in ids if not resuelto(i)]
            carpeta_id = hoy.get(e)
            avisos = []
            if not carpeta_id:
                tipo = "sin_carpeta"
                avisos.append("La carpeta del caso ya no existe con este nombre en Matters (renombrada o "
                              f"borrada). Lo leido es del listado de la etapa {self.etapa}.")
            else:
                vivos = self._vivos(carpeta_id)
                nuevos, quitados = len(vivos - set(ids)), len(set(ids) - vivos)
                tipo = "incompletos" if sin_leer else "cambiados"
                if nuevos or quitados:
                    avisos.append(f"Desde el listado de la etapa, en SharePoint hay {nuevos} archivos nuevos "
                                  f"que no estan en este JSONL y {quitados} que ya no estan alli.")
                elif not sin_leer:
                    self.apuntar(f"APARTE: {e} esta completo y sin cambios: lo sube el armado normal")
                    continue
            if sin_leer:
                avisos.append(f"{len(sin_leer)} archivos no se pudieron leer: van como ficha sin texto, "
                              "con el motivo en 'not_read_because'.")
            fichas = []
            for i in ids:
                v, m = self.estado.get(i), self.meta[i]
                sub = m.get("sub") or ""
                base = {"id": i, "caso": e, "file_name": m["name"],
                        "subfolder": sub.rsplit("/", 1)[0] if "/" in sub else "(folder root)",
                        "size_mb": round(m["size"] / 2 ** 20, 2), "hash": m.get("hash")}
                if v and v[0]:
                    fichas.append({**self._ficha(i), "id": i, "caso": e})
                elif m.get("hash") in original_de:
                    orig = self._ficha(original_de[m["hash"]])
                    fichas.append({**base, **{k: orig.get(k) for k in CAMPOS_DE_LECTURA},
                                   "copied_from": orig.get("id")})
                elif v:
                    fichas.append({**self._ficha(i), "id": i, "caso": e})
                else:
                    fichas.append({**base, "was_read": False, "read_with": None,
                                   "not_read_because": "not read in this stage"})
            texto = armar(e, fichas, len(ids), carpeta_id)
            cabecera, resto = texto.split("\n", 1)
            texto = linea_json({**json.loads(cabecera), "aviso": " ".join(avisos)}) + "\n" + resto
            crudo = texto.encode("utf-8")
            ident = _id_interno(e) or "sin-id"
            nombre = (f"Claude-{ident}_{e.rsplit(' - ', 1)[-1].strip().replace('/', '-')}.jsonl"
                      if ident in self.repetidos else f"Claude-{ident}.jsonl")
            ruta = f"{SUBIDA_CARPETA}/Casos_{self.equipo}_{tipo}/{nombre}"
            if self.simular:
                destino = self.carpeta / "simulados" / f"Casos_{self.equipo}_{tipo}"
                destino.mkdir(parents=True, exist_ok=True)
                (destino / nombre).write_bytes(crudo)
                self.apuntar(f"SIMULADO {ruta}: {len(fichas)} archivos, {len(sin_leer)} sin leer  <- {e}")
                continue
            subir_a_sharepoint(self.g, self.drive, ruta, crudo)
            aparte[e] = {"tipo": tipo, "jsonl": ruta, "sin_leer": len(sin_leer), "aviso": " ".join(avisos),
                         "cuando": f"{datetime.now():%Y-%m-%d %H:%M}"}
            registro.write_text(json.dumps(aparte, ensure_ascii=False, indent=1), encoding="utf-8")
            self.apuntar(f"SUBIDO APARTE {ruta}: {len(fichas)} archivos, {len(sin_leer)} sin leer  <- {e}")


def preparar_bases(etapa: str, reparto_csv: Path, arbol: Path, fuentes: list[Path], salida: Path) -> None:
    """Parte lo ya leido en una base por equipo: las fichas de SUS casos y los ORIGINALES de las
    copias que tengan, esten en el caso que esten (muchas copias de 2022 tienen el original en
    un caso de antes). Lo corre Rafael una vez por etapa, pasando TODO lo leido (el respaldo
    entero y los diarios de etapa). Solo guarda en memoria lo que hace falta. Las bases llevan
    texto de clientes."""
    reparto = pd.read_csv(reparto_csv, dtype=str, encoding="utf-8-sig")
    equipo_de = dict(zip(reparto.expediente, reparto.equipo))
    ids_de, hashes_de = collections.defaultdict(set), collections.defaultdict(set)
    for l in open(arbol, encoding="utf-8"):
        r = json.loads(l)
        eq = equipo_de.get(r["exp"])
        if eq:
            ids_de[eq].add(r["id"])
            if r.get("hash"):
                hashes_de[eq].add(r["hash"])
    todos_ids = set().union(*ids_de.values())
    todos_hashes = set().union(*hashes_de.values())
    ficheros = []
    for f in fuentes:
        ficheros += sorted(f.rglob("*.jsonl")) if f.is_dir() else [f]
    propias: dict[str, dict] = {}      # fichas de los archivos de la etapa
    originales: dict[str, dict] = {}   # hash -> una ficha leida con ese contenido (de donde sea)
    for f in ficheros:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for linea in fh:
                if '"was_read"' not in linea:
                    continue
                try:
                    x = json.loads(linea)
                except ValueError:
                    continue
                i = x.get("id")
                if not i or hay_que_releer(x):
                    continue
                if i in todos_ids:
                    previa = propias.get(i)
                    if not (previa and previa.get("was_read") and not x.get("was_read")):
                        propias[i] = x
                if x.get("was_read") and x.get("hash") in todos_hashes:
                    originales.setdefault(x["hash"], x)
    salida.mkdir(parents=True, exist_ok=True)
    for eq in sorted(ids_de):
        escritos = set()
        destino = salida / f"base_fichas_{etapa}_{eq}.jsonl"
        with open(destino, "w", encoding="utf-8", newline=chr(10)) as out:
            for i in ids_de[eq]:
                if i in propias:
                    out.write(linea_json(propias[i]) + chr(10))
                    escritos.add(i)
            for h in hashes_de[eq]:
                o = originales.get(h)
                if o and o["id"] not in escritos:
                    out.write(linea_json(o) + chr(10))
                    escritos.add(o["id"])
        print(f"  {destino.name}: {len(escritos):,} fichas ({destino.stat().st_size / 2 ** 20:,.0f} MB)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--etapa", default="2022")
    p.add_argument("--equipo", help="rafael, gpu2 o gpu3")
    p.add_argument("--simular", action="store_true", help="No sube: escribe los JSONL en local")
    p.add_argument("--una-vez", action="store_true", help="Una vuelta y termina")
    p.add_argument("--aparte", action="store_true",
                   help="Al terminar la etapa: sube los casos cambiados, incompletos o sin carpeta a "
                        "Casos_<equipo>_cambiados/, _incompletos/ y _sin_carpeta/, con un aviso")
    p.add_argument("--cada", type=int, default=60, help="Minutos entre vueltas (por defecto 60)")
    p.add_argument("--preparar-bases", action="store_true", help="Solo Rafael: partir lo leido por equipo")
    p.add_argument("--fuentes", type=Path, nargs="*", default=[],
                   help="Con --preparar-bases: TODO lo leido (carpetas o ficheros .jsonl)")
    p.add_argument("--reparto", type=Path)
    p.add_argument("--arbol", type=Path)
    p.add_argument("--salida", type=Path)
    args = p.parse_args()
    if args.preparar_bases:
        preparar_bases(args.etapa, args.reparto, args.arbol, args.fuentes, args.salida)
        return
    if not args.equipo:
        p.error("falta --equipo")
    from copiar_a_matters import destino
    g = Graph()
    armador = Armador(args.etapa, g, destino(g)[0], args.equipo, simular=args.simular)
    if args.aparte:
        armador.aparte()
        return
    while True:
        try:
            armador.vuelta()
        except Exception as error:  # noqa: BLE001
            armador.apuntar(f"VUELTA FALLIDA: {type(error).__name__}: {str(error)[:160]} | se reintenta")
        if args.una_vez:
            break
        time.sleep(args.cada * 60)


if __name__ == "__main__":
    main()
