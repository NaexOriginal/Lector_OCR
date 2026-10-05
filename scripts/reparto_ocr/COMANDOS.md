# Los comandos, de principio a fin

**Todo se hace desde dentro de la carpeta `reparto_ocr`**, la que te llegó comprimida.

## Antes de nada: ¿qué equipo tienes?

Hay **dos tipos de trabajo**, y cada equipo hace uno:

| Tu equipo | Qué lee | Bandera | `--de` |
|---|---|---|---|
| **Con tarjeta NVIDIA** | Imágenes y documentos escaneados (con OCR) | `--solo-ocr` | **4** |
| **Sin NVIDIA** (Windows con Radeon o Intel, o Mac) | Todo lo que tiene texto de ordenador: PDF con texto, Word, Excel, correos… **sin OCR** | `--sin-ocr` | **20** |

Sin GPU, el OCR va a unos 8 archivos por hora; con GPU, a unos 4 por minuto. Por eso los
equipos sin GPU no hacen OCR: lo que resulte ser un escaneo lo dejan apartado para la
GPU y siguen.

**Todos los equipos abren DOS ventanas**, una por cada línea que te pase Rafa.

---

## 1. Entrar en la carpeta

**Windows**
```
cd C:\donde\lo\hayas\puesto\reparto_ocr
```
**Mac**
```
cd ~/donde/lo/hayas/puesto/reparto_ocr
```

## 2. Si el equipo YA estaba leyendo: borrar sus diarios viejos

Solo los diarios. **No borres nada más.**

**Windows**
```
del codigo\salida\textos_matters*.jsonl
```
**Mac**
```
rm codigo/salida/textos_matters*.jsonl
```

Después sustituye todo por lo del paquete nuevo. La carpeta `.venv` se puede quedar.

## 3. Instalar

### Equipo nuevo, CON NVIDIA
Comprueba primero tu versión de CUDA con `nvidia-smi` (arriba a la derecha). Si no es
12.9, cambia el `cu129` de `requirements-gpu.txt` por el tuyo.
```
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

### Equipo nuevo, SIN NVIDIA (Windows)
```
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-cpu.txt
```

### Equipo nuevo, Mac
```
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-cpu.txt
```

### Equipo que ya estaba leyendo (cualquiera)
Ya tiene Paddle instalado: solo faltan las librerías nuevas.
```
.venv\Scripts\python.exe -m pip install -r requirements-comun.txt
```
(En Mac, `.venv/bin/python` en vez de `.venv\Scripts\python.exe`.)

## 4. Las credenciales: no hay que hacer nada

El `.env` ya viene dentro del paquete. La primera vez se abrirá el navegador con la
**lista de cuentas de Microsoft: elige tu cuenta de @petroffamshen.com**, la que es
miembro de Matters. La sesión se guarda y no vuelve a preguntar.

## 5. Comprobar

Cambia la `N` por el número de tu **primera** ventana (te lo pasa Rafa).

**Con NVIDIA** (incluye una lectura de prueba con Paddle):
```
.venv\Scripts\python.exe comprobar.py --parte N --de 4
```
Tiene que salir `va por GPU` y `Paddle leyo 'PRUEBA DE LECTURA 12345'`.

**Sin NVIDIA** (no usa Paddle, así que se salta esa prueba):
```
.venv\Scripts\python.exe comprobar.py --parte N --de 20 --sin-ocr
```

En los dos casos tiene que terminar en **`LISTO`**.

## 6. Lanzar: dos ventanas

Abre **dos ventanas**, entra en `reparto_ocr` en cada una y pega **una línea en cada
ventana**. Solo cambia el `--parte`.

### Equipos CON NVIDIA: `--solo-ocr --de 4`

| Equipo | Ventana 1 | Ventana 2 |
|---|---|---|
| NVIDIA 1 | `--parte 0` | `--parte 1` |
| NVIDIA 2 | `--parte 2` | `--parte 3` |

```
.venv\Scripts\python.exe codigo\describir_casos.py --extraer --solo-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte 0 --de 4
```

### Equipos SIN NVIDIA: `--sin-ocr --de 20`

| Equipo | Ventana 1 | Ventana 2 |
|---|---|---|
| Mac 1 | `--parte 0` | `--parte 1` |
| Mac 2 | `--parte 2` | `--parte 3` |
| CPU 1 | `--parte 4` | `--parte 5` |
| CPU 2 | `--parte 6` | `--parte 7` |
| CPU 3 | `--parte 8` | `--parte 9` |
| CPU 4 | `--parte 10` | `--parte 11` |
| CPU 5 | `--parte 12` | `--parte 13` |
| CPU 6 | `--parte 14` | `--parte 15` |
| CPU 7 | `--parte 16` | `--parte 17` |
| CPU 8 | `--parte 18` | `--parte 19` |

**Windows**
```
.venv\Scripts\python.exe codigo\describir_casos.py --extraer --sin-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte 4 --de 20
```
**Mac**
```
.venv/bin/python codigo/describir_casos.py --extraer --sin-ocr --por-caso 0 --paginas 0 --max-mb 0 --parte 0 --de 20
```

> **El `--de` no se cambia nunca.** Es **4** en todos los equipos con NVIDIA y **20** en
> todos los demás. Si alguien pone otro número, lee cosas que ya leyó otro y deja huecos
> que no lee nadie.
>
> **No cambies `--paginas 0` ni `--max-mb 0`**: son los que hacen que la lectura sea
> completa.

La tabla es para 12 equipos (2 con NVIDIA, 2 Mac, 8 sin NVIDIA). Si son otros, Rafa
saca las líneas con `asignar.py --gpu G --mac M --cpu C`.

## 7. Mientras corre

Cada 25 archivos escribe una línea:
```
  [  1250/16,195]  42/min   faltan 6.0 h
```
Los avisos amarillos (`pikepdf`, `Multiple definitions`, `Resized image size`) son
**normales**.

- **Para parar:** `Ctrl+C` **una vez**. Termina lo que tiene a medias y lo guarda. Dos
  veces sale ya, perdiendo lo que tuviera a medias.
- **Para seguir:** la misma línea. Lo ya leído se salta solo.

En los equipos sin NVIDIA, al final verás en el resumen cuántos quedaron **«para la GPU»**:
son los escaneos que apartó. Es lo esperado.

## 8. Lo que se devuelve

Cada ventana deja su diario en `codigo\salida\`:

| Equipo | Fichero |
|---|---|
| Con NVIDIA | `textos_matters.parteN.ocr.jsonl` |
| Sin NVIDIA | `textos_matters.parteN.sinocr.jsonl` |

Cuando Rafa lo pida, se le mandan esos ficheros. **Solo esos: el resto no.**

---

## Para Rafa

### Cada mañana
1. Trae los `textos_matters.parte*.jsonl` de **todos** los equipos a tu `salida\`.
2. Genera la cola de OCR:
   ```
   .venv\Scripts\python.exe reparto_ocr\cola_ocr.py
   ```
3. Copia `reparto_ocr\codigo\salida\cola_ocr.txt` al `codigo\salida\` de **los equipos
   con NVIDIA**, que relanzan sus mismas líneas.

Los equipos sin NVIDIA no necesitan nada: al relanzar siguen donde iban.

### Al final
Con todos los diarios en tu `salida\`:
```
.venv\Scripts\python.exe describir_casos.py --subir --origen matters --simular
.venv\Scripts\python.exe describir_casos.py --subir --origen matters
```
El primero solo dice lo que subiría. Solo se suben los expedientes completos, y si un
archivo tiene «pendiente de OCR» en un diario y su texto en otro, gana el texto.

### Comprimir el paquete
```
.venv\Scripts\python.exe reparto_ocr\empaquetar.py --comprobar
tar -a -c -f reparto_ocr.zip reparto_ocr
```
El primero tiene que decir **«La copia esta al dia»**, sin ningún «NO COMPRIMAS».
