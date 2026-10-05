# Preparar un equipo

Hay **una sola diferencia** entre un equipo con NVIDIA y uno sin ella: que rueda de
Paddle se instala. Todo lo demas es igual.

---

## 1. ¿Tengo GPU que sirva?

Solo valen las **NVIDIA**. Paddle acelera por CUDA y eso es de NVIDIA.

> Una **Radeon** o una **Intel** integrada NO sirven, por muy grafica que sean. El
> equipo con `AMD Radeon 840M` va por CPU aunque tenga tarjeta.

```
nvidia-smi
```

- **Sale una tabla** -> tienes NVIDIA. Sigue por *Equipo con NVIDIA*.
- **Dice que no existe el comando** -> vas por CPU. Sigue por *Equipo sin NVIDIA*.

---

## 2. El repositorio y el entorno

```
git clone <el repositorio>
cd python_script_hubspot_info
python -m venv .venv
```

## 3. Las dependencias: UN fichero u OTRO, no los dos

Hay un `requirements` por caso y **solo se instala el que toque**:

### 3a. Equipo CON NVIDIA

```
.venv\Scripts\python.exe -m pip install -r reparto_ocr\requirements-gpu.txt
```

> **Mira la version de CUDA de tu tarjeta antes.** El fichero apunta a `cu129`,
> que es lo que necesita la RTX 5070 Ti de RevOps. Si la tuya es otra, corre
> `nvidia-smi` y cambia el indice por el que te toque -- esta explicado dentro del
> propio fichero. Instalar la rueda equivocada **no da error**: cae a CPU en
> silencio y el equipo va diez veces mas lento sin que nadie sepa por que.

### 3b. Equipo SIN NVIDIA

```
.venv\Scripts\python.exe -m pip install -r reparto_ocr\requirements-cpu.txt
```

Aqui entran tambien los que tienen grafica **que no es NVIDIA**: una Radeon 840M o
una Intel integrada no aceleran Paddle.

Y **baja los hilos de OpenMP** antes de lanzar, en cada ventana:

```
set OMP_NUM_THREADS=4
```

Por defecto coge todos los nucleos, asi que dos procesos en la misma maquina se
pelean por el procesador y los dos van mas lentos que uno solo.

> Si en ese equipo llegaste a instalar la rueda de GPU, **quitala primero**:
> `pip uninstall -y paddlepaddle-gpu`. Con las dos puestas Paddle elige cual carga,
> y la respuesta cambia entre arranques.

## 4. Los ficheros que hacen falta

**Ya vienen en el paquete**: no hay que copiar nada aparte.

| Fichero | Tamaño | Para qué |
|---|---:|---|
| `.env` (en la raíz de `reparto_ocr`) | — | el tenant y la aplicación de Graph. Sin contraseñas |
| `codigo/salida/arbol_matters.jsonl` | ~98 MB | cada archivo de Matters, con su expediente |
| `codigo/salida/ya_leidos_matters.txt` | — | lo ya leído, solo los identificadores (nunca el texto) |

`comprobar.py` dice si falta alguno.

> El `.env` es oculto (empieza por punto). Algunos compresores se lo saltan; el
> paquete se comprime con `tar` para que vaya dentro.
>
> Ya no hacen falta `cache_sharepoint.json`, `plan_fase2.json`, `mapa_nombres.json` ni
> `reparto.jsonl`: eran para leer del sitio de origen. **Si tu paquete los trae, es
> uno viejo.**

---

## 5. Comprobar antes de empezar

```
.venv\Scripts\python.exe reparto_ocr\comprobar.py
```

Dice si falta algo y si el OCR va a correr por GPU o por CPU. **Si esto no sale
todo en verde, no lances la tanda**: descubrirlo a las tres horas cuesta mas que
mirarlo ahora.

---

## 6. ¿Y si el equipo es un Mac o un Linux?

**El codigo funciona.** Se reviso: no hay rutas de Windows codificadas, ni
`os.system`, ni modulos exclusivos de Windows en ninguno de los trece modulos que
intervienen. Lo unico de Windows son los comandos de estas instrucciones.

### Lo que cambia

| Windows | Mac / Linux |
|---|---|
| `.venv\Scripts\python.exe` | `.venv/bin/python` |
| `set OMP_NUM_THREADS=4` | `export OMP_NUM_THREADS=4` |
| `python -m venv .venv` | `python3 -m venv .venv` |

### Mac: SOLO Apple Silicon

`paddlepaddle==3.3.1` publica rueda `macosx_11_0_arm64` para Python 3.9 a 3.13 --
comprobado en PyPI, no supuesto. Sirve para M1, M2, M3 y M4.

**Un Mac con Intel no tiene rueda de esa version.** Si hay uno en el reparto, o se
busca una version anterior de paddlepaddle que si la publique, o ese equipo se
queda fuera.

**Ningun Mac acelera.** Paddle solo usa CUDA, que es de NVIDIA; ni el chip de Apple
ni Metal cuentan. Un Mac va por CPU igual que el del AMD, asi que usa
`requirements-cpu.txt` y bajale los hilos:

```
.venv/bin/python -m pip install -r requirements-cpu.txt
.venv/bin/python comprobar.py --parte N --de 20 --sin-ocr
```

### Lo que NO se ha probado

El codigo se ha ejecutado solo en Windows. No hay motivo para que falle en otro
sistema, pero **eso es un razonamiento, no una prueba**: si metes un Mac en el
reparto, corre `comprobar.py` y despues un `--limite 200` antes de dejarlo toda la
noche. Si algo se rompe, se rompera ahi y no a las seis horas.
