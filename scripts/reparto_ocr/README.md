# Lectura repartida entre varios equipos

Leer **todos** los documentos de los expedientes y dejar un `Claude-<ID>.jsonl` en
cada carpeta del SharePoint de produccion.

Son **256.776 archivos y 329 GB**. En un solo equipo con GPU van a **44 dias**, asi
que se reparte: cada maquina lee un trozo distinto y al final se juntan.

| | |
|---|---:|
| Archivos por leer | 256.776 |
| A descargar | 329 GB |
| Un equipo con NVIDIA, un proceso | ~4/min |
| Con el reparto de abajo | **dias, no semanas** |

> **Esto no se termina en una noche.** Con cuatro equipos toda la noche entran unos
> 15.000-20.000 de los 256.776. Conviene decirlo antes de repartir los equipos.

---

## Que hace cada equipo

**No lo mires en una tabla: pidelo.** Rafa corre esto y le manda a cada uno su linea:

```
.venv\Scripts\python.exe reparto_ocrsignar.py
```

Escribe el comando exacto de cada equipo, listo para copiar. Una tabla que hay que
leer con cuidado acaba con dos personas corriendo el mismo trozo y un tercero sin
hacer, y eso no se nota hasta el final.

### El reparto NO es a partes iguales

| | procesos | ritmo por proceso |
|---|---:|---|
| Con NVIDIA | 3 | ~4 archivos/min (medido) |
| Sin grafica, y los Mac | 2 | menos, y **sin medir** |

Se reparte por capacidad, no por cabezas: con doce trozos iguales los equipos
rapidos terminan y se quedan parados mientras los lentos siguen dias.

**Los Mac van con los de CPU.** Paddle solo acelera por CUDA; ni el chip de Apple
ni Metal cuentan.

### Varios procesos en la misma maquina

El OCR esta serializado dentro de cada proceso a proposito: Paddle no garantiza que
un predictor se pueda usar desde varios hilos, y al intentarlo el 24-sep se agoto
la VRAM. Ese candado es **por proceso**, asi que dos procesos separados tienen cada
uno su motor y el OCR si corre en paralelo de verdad.

Cada proceso es **una ventana de terminal distinta**.

## Como no se pisan

- **El trozo se decide por el ID del archivo**, con md5. No por su posicion en una
  lista, que seria distinta en cada maquina segun lo que llevara leido.
- **md5 y no `hash()`**: el hash de Python lleva semilla aleatoria por proceso, o
  sea que el reparto saldria distinto en cada arranque y dos maquinas leerian lo
  mismo mientras otro trozo no lo lee nadie.
- **Cada proceso escribe su propio diario** (`salida/textos_matters.parteN.jsonl`).
  Un unico fichero con diez procesos anadiendo lineas se corrompe.
- **Al arrancar se leen todos los diarios**, no solo el propio: si un equipo se cae
  y su trozo se reparte, lo que ya leyo no se vuelve a leer.

Comprobado sobre los 270.533 archivos: **1% de desviacion entre trozos, 0 solapes,
ningun archivo sin dueno.**

---

## Esta carpeta NO se comparte sola

Aqui solo estan las instrucciones, los `requirements` y el comprobador. **El trabajo
lo hace `describir_casos.py`, que esta en la raiz del repositorio** y arrastra otros
doce modulos (`sp_conexion`, `lectores`, `clasificacion`, `regla_nombres`...).

**Hay que clonar el repositorio entero** y ademas copiar cinco ficheros que no se
pueden generar en el equipo -- ver [INSTALAR.md](INSTALAR.md).

---

## Por donde empezar

1. **[INSTALAR.md](INSTALAR.md)** — que hace falta en cada equipo. Es distinto
   segun tenga NVIDIA o no.
2. **[EJECUTAR.md](EJECUTAR.md)** — el comando, como pararlo y como seguir.
3. `comprobar.py` — dilo antes de empezar: comprueba que el equipo esta listo.

---

## Cuando terminen todos

Una sola persona, desde un equipo con los diarios de todos:

```
.venv\Scripts\python.exe describir_casos.py --subir
```

Junta los diarios, arma un JSONL por expediente y lo sube. No vuelve a leer nada.

---

## Para Rafa: preparar y comprimir el paquete

Desde la raíz del repositorio:

```
.venv\Scripts\python.exe reparto_ocr\empaquetar.py
.venv\Scripts\python.exe reparto_ocr\empaquetar.py --comprobar
tar -a -c -f reparto_ocr.zip reparto_ocr
```

- El segundo comando tiene que decir **«La copia esta al dia»**.
- **Hay que usar `tar`**, no el menú de Windows ni *Compress-Archive*: esos se saltan
  el `.env`, que es oculto, y el otro equipo se queda sin credenciales.
- Antes de mandarlo, abre el zip y comprueba dos cosas:
  - `codigo\salida\` trae `arbol_matters.jsonl` y **no** trae `cache_sharepoint.json`
    (ese es del paquete viejo);
  - no hay ningún `.msal_cache.json`. `empaquetar.py` lo borra: es la sesión de quien
    lo corrió.
