# Ejecutar

## Tu comando

Hay dos, segun tu equipo. **Abre dos ventanas** y pega una linea en cada una; solo
cambia el `--parte`. La tabla completa esta en [COMANDOS.md](COMANDOS.md).

**Con NVIDIA** (`--de 4`):
```
.venv\Scripts\python.exe codigo\describir_casos.py --extraer --solo-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte 0 --de 4
```

**Sin NVIDIA** (`--de 20`):
```
.venv\Scripts\python.exe codigo\describir_casos.py --extraer --sin-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte 4 --de 20
```

Desde la carpeta `reparto_ocr`.

### Que significa cada cosa

| | |
|---|---|
| `--por-caso 0` | todos los archivos de cada expediente, no una muestra |
| `--paginas 0` | **todas** las paginas de cada documento |
| `--max-mb 0` | sin descartar archivos grandes |
| `--solo-ocr` / `--sin-ocr` | con NVIDIA: imagenes y escaneos; sin NVIDIA: todo lo demas, sin OCR |
| `--parte N --de 4` o `--de 20` | el trozo que te toca. El `--de` es 4 en los de NVIDIA y 20 en los demas |

Los tres primeros son lo que hace que la lectura sea **completa**. No los cambies:
con un tope de paginas los escaneos quedan a medias, y son justo los documentos
donde el texto solo existe si se hace OCR.

---

## Como saber si va bien

Cada 25 archivos escribe una linea:

```
  [  1250/33,978]  42/min   faltan 13.0 h
```

**Si el ritmo baja de 2/min sostenido, avisa**: en CPU es normal ir lento, pero
tanto suele ser otra cosa.

Los avisos de `pikepdf`, `Multiple definitions in dictionary` o `Resized image
size` son **normales**: PDFs mal construidos que las librerias arreglan solas.

---

## Pararlo y seguir

**Ctrl+C una vez** — termina lo que tiene en vuelo, lo guarda y dice cuantos
quedan. **Ctrl+C dos veces** — sale ya.

Para seguir: **el mismo comando**. Lo ya leido se salta solo.

Tambien hay tope de tiempo, si prefieres tandas fijas:

```
... --parte 4 --de 20 --minutos 480
```

### Por que es seguro cortar

Cada archivo se anota en el diario **nada mas leerlo**, abriendo y cerrando el
fichero por linea: esta en disco, no en un buffer. Lo peor que se pierde al cortar
son los pocos que estuvieran a medio leer en ese instante, y esos se releen luego.

---

## Al terminar

**Manda tus diarios**, uno por ventana, a quien vaya a subir:
`codigo\salida\textos_matters.parteN.ocr.jsonl` (con NVIDIA) o
`textos_matters.parteN.sinocr.jsonl` (sin NVIDIA). Es lo unico que hay que devolver.

El resto lo hace una sola persona:

```
.venv\Scripts\python.exe describir_casos.py --subir --origen matters
```

Junta todos los diarios, arma un JSONL por expediente y lo sube a SharePoint. No
vuelve a leer nada, asi que es cuestion de minutos.

---

## Si algo va mal

| Sintoma | Que es |
|---|---|
| `Falta salida/arbol_matters.jsonl` | el paquete llego incompleto: ver [INSTALAR.md](INSTALAR.md) |
| `Acceso denegado (403)` | el `.env` no tiene permiso sobre el sitio |
| `throttling (429), esperando Ns` | SharePoint nos frena. **Es normal**, el script espera y sigue |
| `red caida (...), reintento 1/5` | corte de red. Reintenta solo |
| Todo va lentisimo y no tienes NVIDIA | comprueba que tu linea lleva `--sin-ocr`: sin ella intentas hacer OCR en CPU |
| Se queda sin memoria | baja a un solo proceso en ese equipo |

**Lo que NO es un problema:** que salgan muchos avisos amarillos. Casi todos los
PDF de un juzgado estan mal construidos.
