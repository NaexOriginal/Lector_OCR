# Reparto actual de la lectura de Matters

**Actualizado: 6-oct-2026.** Si cambias el reparto, cambia también este documento.

> **AHORA MISMO (desde el 6-oct): etapa 2022.** Los 3 equipos con GPU leen **primero** los
> expedientes con índice de 2022 en adelante, cada uno con su lista
> (`lista_2022_rafael.txt`, `lista_2022_gpu2.txt`, `lista_2022_gpu3.txt`). Lo hacen en
> lectura completa con `--lista` y escriben en `textos_matters.lista_2022_<equipo>.jsonl`.
> Ver [`ETAPAS.md`](ETAPAS.md): cómo se creó, cómo se aplica y el registro de la etapa.
> Los diarios de la etapa **se suben solos cada hora** a `Documentos/JSONL/` del sitio
> Matters (carpeta solo de RevOps, fuera de `Matters/`).
> Todos los equipos usan el listado de Matters del **5-oct**. Cuando termine, se vuelve a la
> pasada 2 de abajo quitando `--lista`.
>
> Los diarios hasta el 6-oct están en un **respaldo congelado** fuera del proyecto
> (`C:\1_Documentos_Personales\Respaldo_JSONL_OCR\2026-10-06`). En el equipo de Rafael se
> borró el diario de la pasada 2: lo leído allí solo está en el respaldo.

La lectura va en **dos pasadas**, y cada una tiene su propio reparto (su propio `--de`).
El trozo de cada archivo sale del md5 de su contenido (quickXorHash) o, si no lo tiene,
de su id, módulo `--de`. Así, el mismo archivo le toca siempre al mismo equipo, y todas
las copias de un documento caen en el mismo trozo.

## Pasada 1: sin OCR (equipos sin GPU) — terminada

| | |
|---|---|
| Comando | `--extraer --sin-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte N --de 20` |
| Trozos | 0 a 19, uno por equipo o proceso |
| Diarios | `textos_matters.parteN.sinocr.jsonl` |
| Estado | Terminada el 30-sep: 427.516 de 427.845 archivos con ficha |
| Pendiente | **~8.450 archivos que fallaron con HTTP 429** (SharePoint frenó la descarga). Se reintentan relanzando cualquier trozo: lo ya leído se salta solo |

Lo que necesita OCR queda marcado `needs OCR: left for a machine with GPU`.
`cola_ocr.py` junta esas marcas de todos los diarios en `codigo/salida/cola_ocr.txt`
(la última, del 30-sep, tiene 222.739 escaneos).

## Pasada 2: solo OCR (equipos con GPU) — en curso

Doce trozos (`--de 12`) entre tres equipos con GPU (RTX 5070 Ti Laptop, 12 GB):

| Equipo | Trozos | Diario |
|---|---|---|
| Rafael | `--partes 0,1,4,5` | `textos_matters.parte0.ocr.jsonl` |
| GPU 2 | `--partes 2,3,6,7` | `textos_matters.parte2.ocr.jsonl` |
| GPU 3 | `--partes 8,9,10,11` | `textos_matters.parte8.ocr.jsonl` |

El diario lleva el número del **primer** trozo del proceso.

**El comando**, en PowerShell, desde la carpeta que contiene `codigo\`:

```powershell
$env:OMP_NUM_THREADS=8
python codigo\describir_casos.py --extraer --solo-ocr --por-caso 0 --paginas 0 --max-mb 0 --partes 0,1,4,5 --de 12 --hilos 1
```

**Con reinicio cada 12 horas** (recomendado para dejarlo días seguidos):

```powershell
$env:OMP_NUM_THREADS=8
while ($true) { python codigo\describir_casos.py --extraer --solo-ocr --por-caso 0 --paginas 0 --max-mb 0 --partes 0,1,4,5 --de 12 --hilos 1 --minutos 720 }
```

Para pararlo del todo: Ctrl+C dos veces (la primera para la lectura y la segunda corta
el bucle).

### Por qué así

- **Una sola ventana por equipo.** Con dos ventanas (1,9× más rápido en la prueba) la RAM
  llegó al 98% en minutos con escaneos reales: cada proceso sube a ~8 GB con los
  escaneos de cientos de páginas.
- **`--hilos 1`.** Dentro de un proceso el OCR va de uno en uno (Paddle no admite usar
  el mismo motor desde varios hilos). Más hilos solo suben la RAM.
- **`OMP_NUM_THREADS=8`.** Con el valor por defecto o con 1, va más lento.
- **Reinicio cada 12 h.** En 3 días seguidos un proceso pasó de 3 a 11 GB de RAM y no los
  soltaba. Al reiniciar no se pierde nada: se retoma donde lo dejó.
- **Los escaneos de más de 5 MB se leen al final** (`--pesados-al-final 5`, que es el
  valor por defecto). No acorta la tanda, pero el 95% de los archivos sale en ~40% del
  tiempo, y los que disparan la RAM quedan para el final.

### Cómo se vigila

Cada proceso escribe `codigo/salida/log_<diario>.txt`:

- `EMPIEZA OCR` y `pagina N de M` durante cada escaneo;
- `LEIDO` o `NO LEIDO` al terminar, con caracteres y tiempo;
- **`VIVO` cada 5 minutos**, también en la ventana. Si en 5 minutos no aparece, el
  proceso está parado.

Para verlo en directo, desde otra ventana:

```powershell
Get-Content codigo\salida\log_textos_matters.parte0.ocr.txt -Wait -Tail 20
```

### Cómo se actualiza el código en los equipos

Se copian **solo** los `.py` que cambiaron, encima de los suyos y en la misma ruta (por
ejemplo `codigo\describir_casos.py` y `codigo\extractor_completo\ocr.py`), y se relanza
el mismo comando. **Nunca se borra ni se reemplaza `codigo\salida\`**: ahí está lo que
ese equipo ya leyó.

## Avance al 5-oct

| Equipo | Escaneos leídos |
|---|---|
| Rafael | ~8.900 |
| GPU 2 | ~9.000 |
| GPU 3 | ~3.700 |

Quedan ~162.000 escaneos únicos (~1,56 millones de páginas). Al ritmo medido (~743
páginas por hora por equipo) son **30–40 días** con los tres equipos.

## Reglas que no se tocan

- **Los diarios nunca viajan en el paquete:** llevan SSN y reportes de crédito en
  claro. A otros equipos solo van identificadores (`ya_leidos_matters.txt`,
  `cola_ocr.txt`). `empaquetar.py` se niega a comprimir si encuentra uno.
- **Dos equipos no pueden tener los mismos trozos con el mismo `--de`:** leerían lo
  mismo. Para dar trabajo a un equipo nuevo, se parte un trozo existente con un `--de`
  múltiplo (24, 36, 48…). Esos trozos encajan dentro de los de `--de 12` y no pisan a
  nadie. Por ejemplo, el trozo 0 de `--de 12` contiene los trozos 0 y 12 de `--de 24`.
- **Los JSONL no se suben a SharePoint** hasta terminar el OCR y rehacer el árbol de
  Matters.
