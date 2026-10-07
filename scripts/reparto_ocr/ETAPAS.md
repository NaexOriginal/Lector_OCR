# Etapas: leer primero un grupo de casos

Una **etapa** es «leer primero estos expedientes» (por ejemplo, los de 2022 en adelante)
sin desmontar el reparto de fondo. Cada equipo recibe **una lista con lo que le falta** de
esos casos y lo lee en un **diario aparte**, sin mezclarlo con lo anterior.

## Cómo funciona

| Pieza | Qué hace |
|---|---|
| `crear_etapa.py` | Calcula qué está leído y qué falta, y escribe una lista por equipo |
| `describir_casos.py --lista lista_<etapa>_<equipo>.txt` | Lee solo los archivos de esa lista |
| Diario `textos_matters.lista_<etapa>_<equipo>.jsonl` | Donde se escribe lo de la etapa. Sale del nombre de la lista |

Reglas que no cambian:

- **El reparto sigue los trozos de siempre** (`--de 12`: Rafael 0,1,4,5 · GPU 2 2,3,6,7 ·
  GPU 3 8,9,10,11). Así, lo que un equipo leyó por su cuenta sigue siendo suyo, y todas las
  copias de un documento caen en el mismo equipo.
- **Si un equipo queda mucho más cargado**, se le pasa medio trozo (módulo 24) a otro.
- **Las listas no se solapan** y suman exactamente lo pendiente. El script lo comprueba y
  se para si no es así.
- **La lista tiene que llamarse `lista_<algo>.txt`**: de ese nombre sale el del diario, y así
  la lectura lo vuelve a encontrar al relanzar y no repite nada.

## 1. Crear la etapa (equipo de Rafael)

Desde `python_script_hubspot_info`, con su `.venv`:

```powershell
python reparto_ocr\crear_etapa.py --etapa 2022 `
    --expedientes salida\expedientes_2022_en_adelante.csv `
    --arbol <listado de Matters de hoy>.jsonl `
    --diarios <carpeta con TODOS los diarios: respaldo + equipos> `
    --log <una bitacora de lectura, para estimar páginas>
```

- `--expedientes`: un CSV con una columna `expediente` (los nombres de carpeta de Matters).
- `--diarios`: carpetas o ficheros. Hay que pasar **todo lo leído**. Lo que falte aquí se
  repetirá.
- El listado de Matters se saca con `scripts\sharepoint\consultar.py --arbol` (35–40 min).
- Sale en `salida\etapa_<etapa>\`:
  - `lista_<etapa>_rafael.txt`, `lista_<etapa>_gpu2.txt`, `lista_<etapa>_gpu3.txt`;
  - `expedientes_<etapa>_estado.csv`;
  - `archivos_pendientes_<etapa>.csv`, que lleva nombres de archivos: no sale del equipo.

Para **reproducir** una etapa ya repartida se pasan los mismos `--mover` que se usaron
(ver abajo); sin ellos, el script iguala la carga por su cuenta y puede elegir otros
medios trozos.

## 2. Aplicarla en cada equipo

**Si el equipo aún no tiene el entorno instalado**, se instala con el `.whl` de Paddle, no
desde el índice de Paddle, que suele fallar. La rueda (810 MB) no está en el repositorio: se
la pide a Rafael.

```powershell
cd scripts\reparto_ocr
python -m venv .venv
(Get-FileHash <ruta>\paddlepaddle_gpu-3.3.1-cp312-cp312-win_amd64.whl -Algorithm SHA256).Hash
#   tiene que salir F5B26250B46F1F7FE8F51571392D2E8F9C07C1E32FA127F4CA563FE1A0D01103
.venv\Scripts\python.exe -m pip install -r requirements-comun.txt
.venv\Scripts\python.exe -m pip install <ruta>\paddlepaddle_gpu-3.3.1-cp312-cp312-win_amd64.whl
.venv\Scripts\python.exe -c "import paddle; print(paddle.device.is_compiled_with_cuda())"
#   tiene que salir True: si sale False, Paddle va por procesador
```

Primero lo común y **después** el `.whl`: al revés, pip podría cambiar la rueda por otra.
Hace falta Python 3.12, porque la rueda es solo para esa versión.

Cada equipo recibe, en su `codigo\salida\`:

1. **su** lista (`lista_<etapa>_<equipo>.txt`), y solo la suya;
2. el **mismo listado de Matters** que se usó para crear la etapa, como `arbol_matters.jsonl`.
   Con uno más viejo no vería los archivos nuevos y los saltaría sin avisar;
3. el `describir_casos.py` actual en `codigo\`.

**No se borra nada de `codigo\salida\`:** los diarios de antes le dicen a la lectura lo que
ya hizo ese equipo.

Se para la lectura en curso (Ctrl+C; dos veces si va en el bucle `while`) y se lanza la
**lectura completa** con la lista, sin `--solo-ocr` y sin `--partes`:

```powershell
$env:OMP_NUM_THREADS=8
while ($true) { python codigo\describir_casos.py --extraer --por-caso 0 --paginas 0 --max-mb 0 --hilos 1 --minutos 720 --lista codigo\salida\lista_<etapa>_<equipo>.txt }
```

Es lectura completa porque la lista puede traer archivos que nunca se leyeron. Usa OCR solo
donde hace falta.

**Cómo saber que va bien:** al arrancar sale `--lista lista_…: N de M archivos…`, y la línea
`ARRANQUE` termina en `| solo la lista lista_…`. Cada 5 minutos sale `VIVO`. La bitácora es
`codigo\salida\log_textos_matters.lista_<etapa>_<equipo>.txt`.

**La subida a SharePoint es automática.** Cada hora, la lectura sube **lo nuevo** del diario
(un trozo numerado: `textos_matters.lista_<etapa>_<equipo>.00012.<fecha>.jsonl`) y la
bitácora entera a `Documentos/JSONL/lista_<etapa>_<equipo>/` del sitio Matters. También
sube al arrancar y al terminar. Esa carpeta está **fuera de `Matters/`**, para que el árbol
no la tome por un expediente, y tiene permisos propios: solo RevOps.

- Juntando los trozos de una carpeta, en orden de nombre, sale el diario completo.
- Hace falta que la cuenta con la que se inició sesión pueda **escribir** en esa carpeta.
- Si una subida falla, sale `SUBIDA FALLIDA` en la bitácora. La lectura sigue igual y la
  subida siguiente lleva también lo que quedó.
- Hasta dónde se ha subido se guarda en `codigo\salida\subida_<diario>.json`. **No se
  borra.**
- `--sin-subir` desactiva la subida.

**Comprobar que sube.** En la ventana no sale nada si va bien; solo `SUBIDA FALLIDA` si
falla. Para verlo, desde otra ventana en la misma carpeta:

```powershell
Select-String "SUBIDA" codigo\salida\log_textos_matters.lista_<etapa>_<equipo>.txt
```

Tiene que salir algo como `SUBIDA  textos_matters.lista_2022_gpu2.00001.… N fichas, X MB ->
JSONL/lista_2022_gpu2 (al arrancar)`. En SharePoint (Documentos → JSONL →
`lista_<etapa>_<equipo>`) aparecen los trozos numerados y la bitácora.

**Cuando termina:** la línea `FIN` dice `TERMINADO` y, al relanzarse, salen 0 pendientes.
El diario ya está subido en SharePoint: no hay que mandarlo a nadie.

### Las líneas de la etapa 2022, por equipo

Cada una en PowerShell, desde la carpeta que contiene `codigo\`. Va siempre después de
`$env:OMP_NUM_THREADS=8`, en la misma ventana:

```powershell
# Rafael
while ($true) { python codigo\describir_casos.py --extraer --por-caso 0 --paginas 0 --max-mb 0 --hilos 1 --minutos 720 --lista codigo\salida\lista_casos2022_rafael.txt --armar-casos 2022 }
# GPU 2
while ($true) { python codigo\describir_casos.py --extraer --por-caso 0 --paginas 0 --max-mb 0 --hilos 1 --minutos 720 --lista codigo\salida\lista_casos2022_gpu2.txt --armar-casos 2022 }
# GPU 3
while ($true) { python codigo\describir_casos.py --extraer --por-caso 0 --paginas 0 --max-mb 0 --hilos 1 --minutos 720 --lista codigo\salida\lista_casos2022_gpu3.txt --armar-casos 2022 }
```

Si el entorno se creó con `python -m venv .venv` y no está activado, se cambia `python`
por `.venv\Scripts\python.exe`. Las listas y el listado de Matters **no están en el
repositorio**: los reparte Rafael.

## Reparto POR CASOS y armado de los `Claude-{ID}.jsonl` (desde el 6-oct)

La etapa 2022 se reparte **por casos enteros**, no por trozos: cada caso lo lee un solo
equipo, y los casos que comparten un documento pendiente van juntos para no pasarle el
OCR dos veces. Las listas son `lista_casos2022_<equipo>.txt`, y la tabla de qué caso lleva
cada equipo es `salida\etapa_2022\reparto_casos_2022.csv`. Los equipos leen igual que
antes, con `--lista`, y suben su diario cada hora.

**Cada equipo arma SUS casos**, en su misma ventana de lectura, con la opción
`--armar-casos 2022`. El equipo sale del nombre de su lista. La lógica está en
`codigo\armar_casos.py`. Al arrancar, y luego cada hora:

1. junta lo leído: su base y sus diarios de etapa;
2. arma cada caso suyo **completo**: todos sus archivos leídos, resueltos sin texto de forma
   definitiva o copia exacta de uno leído;
3. lo comprueba **en directo** contra su carpeta de Matters, y no lo sube si cambió;
4. lo sube a `Documentos/JSONL/Casos_<equipo>/Claude-{ID}.jsonl`. Si dos carpetas tienen
   el mismo ID, va el índice detrás: `Claude-900043_0.jsonl`.

Si falla, sale `ARMADO FALLIDO` y la lectura sigue. Los `Claude-{ID}.jsonl` **no van a las
carpetas de los casos**: se quedan en `JSONL/` hasta el proceso de limpieza. Un caso subido no
se vuelve a subir mientras no cambie su número de archivos.

**Lo que necesita cada equipo en `codigo\salida\`:**

| Fichero | Qué es |
|---|---|
| `arbol_matters.jsonl` | El listado de Matters de la etapa |
| `lista_casos2022_<equipo>.txt` | Lo que le falta leer de sus casos |
| `etapa_2022\reparto_casos_2022.csv` | Qué caso lleva cada equipo |
| `etapa_2022\base_fichas_2022_<equipo>.jsonl` | Lo ya leído **antes** de la etapa de sus casos, y los originales de sus copias. **Lleva texto de clientes**: solo por canales internos |

Las bases las prepara Rafael una vez por etapa, pasándole **todo** lo leído (el respaldo
entero y los diarios de etapa que ya haya). Así encuentra también los originales de las
copias que están en casos de antes:

```powershell
python codigo\armar_casos.py --preparar-bases --etapa 2022 `
    --reparto <reparto_casos_2022.csv> --arbol <arbol de la etapa>.jsonl `
    --fuentes <respaldo\1_lectura_ocr_gpu> <respaldo\2_lectura_sin_ocr> <respaldo\7_versiones_antiguas> <diarios de etapa> `
    --salida <carpeta>
```

Comprobado el 6-oct: lo que cada base deja sin resolver es **exactamente** la lista de su
equipo. Al terminar la lista, todos sus casos quedan completos.

Para probar el armado sin subir nada, desde la carpeta que contiene `codigo\`:
`python codigo\armar_casos.py --equipo <equipo> --simular --una-vez`. Los JSONL quedan en
`codigo\salida\etapa_2022\simulados\`.

**Si un equipo ya había leído un archivo de su lista en un diario anterior**, no lo relee:
al arrancar copia su ficha al diario de la etapa, para que se suba y su caso se pueda
armar.

## Leer en un servidor Linux con GPU (7-oct)

Probado en Azure (NC24ads A100 v4, Ubuntu 22.04). Con **varios procesos de Paddle en la
misma GPU**, de vez en cuando uno da `CUDA error(700), an illegal memory access` o
`CUDA error(716), misaligned address`, y desde ese momento ese proceso no puede volver a
usar la GPU. Lo que hace el lector:

- Al primer error de CUDA, el proceso **termina, sube su diario y sale**. El bucle
  `while true` lo relanza con la GPU limpia (Paddle carga en ~5 s). En la bitácora:
  `GPU ROTA en <archivo>` y `FIN REINICIO POR GPU ROTA`.
- El archivo que la rompió pasa **al final** de la cola en los siguientes arranques
  (`codigo/salida/gpu_rota_<diario>.txt`).
- Si se rompe a mitad de un archivo, ese archivo queda como fallo del motor y se relee:
  antes quedaba LEIDO con solo las páginas de antes del error. Lo que se guardó así se
  limpia, con la lectura parada, con `python reparar_lecturas_partidas.py --equipo <equipo>`
  (primero sin nada más, para ver qué haría; luego con `--aplicar`).

Dos ajustes de Paddle por variable de entorno. Sin ellas, todo queda como siempre:

| Variable | Qué hace |
|---|---|
| `OCR_GIRO=0` | No endereza la página antes de leerla. Una hoja escaneada de lado se lee peor |
| `OCR_LADO_MAX=2048` | Lado mayor de la página que entra al detector de texto (Paddle llega a 4.000 px) |

Un proceso por trozo de la lista, y solo uno arma:

```bash
for n in 1 2 3 4 5 6; do
  extra=""; [ $n = 1 ] && extra="--armar-casos 2022"
  nohup bash -c "export OMP_NUM_THREADS=4 OCR_GIRO=0 OCR_LADO_MAX=2048; while true; do .venv/bin/python codigo/describir_casos.py --extraer --por-caso 0 --paginas 0 --max-mb 0 --hilos 1 --minutos 720 --lista codigo/salida/lista_casos2022p${n}_<equipo>.txt $extra; sleep 5; done" > codigo/salida/pantalla_p$n.txt 2>&1 &
done
```

Los trozos se llaman `lista_<etapa>p<N>_<equipo>.txt`: el equipo sigue saliendo del final
del nombre, y el armado junta todos los diarios `lista_*` de la carpeta. Los procesos
siguen corriendo al cerrar SSH; la máquina de Azure cobra mientras esté encendida, aunque
no lea: al terminar, **Detener** en el portal hasta que diga *desasignada*.

## 3. Al terminar la etapa

Se vuelve a la lectura de fondo quitando `--lista` (con la línea de `ESTADO_ACTUAL.md`). No se
repite nada: la lectura salta lo que está en los diarios, incluidos los de la etapa.

Para armar los JSONL hay que juntar **todos** los diarios: los de antes, el respaldo y los
de cada etapa. Los archivos marcados como «copia de un archivo ya leído» no se leen: toman
el texto de su original por el hash.

---

## Registro de etapas

### Etapa 2022 (6-oct-2026)

Expedientes cuyo número de índice es de 2022 en adelante: el índice sale del nombre de la
carpeta (355), de HubSpot (14) o del texto de sus documentos (84, ~92% fiable).

| | |
|---|---|
| Expedientes | 453 · 70.473 archivos · 95,6 GB |
| Ya leídos | 47.297, más 2.893 copias de leídos y 421 sin texto definitivo |
| Pendientes | 19.862 (16.434 por OCR, 2.378 nunca leídos y 1.050 fallos) |
| Completos al empezar | 39 expedientes |
| Listado de Matters | 5-oct-2026 |
| Lo ya leído | Respaldo de los diarios del 6-oct |
| Movimiento a mano | `--mover 23:gpu3:rafael` |

| Equipo | Archivos | Tiempo aprox. |
|---|---|---|
| Rafael | 6.649 | ~3,5 días |
| GPU 2 | 6.259 | ~3,1 días |
| GPU 3 | 6.954 | ~3,6 días |

No incluye los 915 expedientes sin índice ni lo anterior a 2022.
