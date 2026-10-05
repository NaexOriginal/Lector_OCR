# scripts/

Herramientas de **operación**, no de producción. El paquete `lector_ocr/` es la librería
que lee y archiva; lo que hay aquí son los scripts con los que se trabaja alrededor:
mirar SharePoint y repartir la lectura masiva entre varios equipos.

| Carpeta | Qué es |
|---|---|
| [`sharepoint/`](sharepoint/) | Conexión a SharePoint por Microsoft Graph y una consulta de solo lectura |
| [`reparto_ocr/`](reparto_ocr/) | El reparto de la lectura de Matters entre equipos: scripts, instrucciones y el código que viaja a cada máquina |

> **Nada de aquí lleva datos ni credenciales.** El `.env`, la sesión de Microsoft
> (`.msal_cache.json`), los diarios con texto de clientes, las colas y los árboles están
> en el `.gitignore`. Si alguna vez `git status` muestra uno de esos ficheros, no se sube.

---

## Configuración: las variables de entorno

Los dos apartados usan la misma conexión (`sp_conexion.py`). Lee las credenciales de las
**variables de entorno** o, si no están, de un fichero `.env`. Busca el `.env` en este
orden y se queda con el primero que exista:

1. junto al script (`scripts/sharepoint/.env`)
2. una carpeta más arriba (`scripts/.env`)
3. la carpeta desde donde se lanza el comando (por ejemplo, la raíz del repositorio)

Copia [`.env.example`](../.env.example), que está en la raíz del repositorio, como `.env` y rellénalo:

```
GRAPH_TENANT_ID=...      el tenant del despacho
GRAPH_CLIENT_ID=...      la app de Graph del despacho (cliente público, sin secret)
```

O en PowerShell, solo para esa ventana:

```powershell
$env:GRAPH_TENANT_ID = "..."
$env:GRAPH_CLIENT_ID = "..."
```

**La app actúa como la persona que inicia sesión** (modo delegado): solo alcanza lo que
esa persona ya ve en SharePoint. La primera vez abre el navegador; después reutiliza el
token guardado en `.msal_cache.json`, junto al script.

---

## `sharepoint/consultar.py`: mirar SharePoint

Solo lee: nombres, tamaños, fechas e identificadores. No descarga el contenido de los
documentos, no crea, no mueve y no borra.

```
cd scripts\sharepoint
python consultar.py --quien                                   # con qué cuenta y permisos
python consultar.py --buscar "Adeyemi"                        # expedientes por nombre
python consultar.py --listar "Matters/<expediente>"           # archivos de una carpeta
python consultar.py --listar "Matters/<expediente>" --recursivo
python consultar.py --arbol                                   # todo Matters a un jsonl
```

`--arbol` recorre todo Matters (~430.000 archivos, 35–40 minutos) y escribe una línea por
archivo con `id`, `name`, `size`, `hash`, `exp`, `sub`, `modified` y `file_modified`. Es
el mismo formato que `arbol_matters.jsonl` de producción, más las dos fechas.

**Ojo con las fechas:** en lo migrado, las dos son la fecha de la migración
(septiembre de 2026). No sirven para saber de qué año es un caso.

**Bibliotecas de origen:** `sites/clients` y `teams/ActiveMatters` tienen permisos propios
en cada carpeta. Con la cuenta de RevOps, la primera se ve vacía y la segunda da *access
denied*. Para leerlas hace falta una cuenta con acceso a esas carpetas.

---

## `reparto_ocr/`: la lectura repartida

Ver [`reparto_ocr/ESTADO_ACTUAL.md`](reparto_ocr/ESTADO_ACTUAL.md) para el reparto que
está corriendo **ahora**: qué equipo lee qué trozos, con qué comando y cómo se vigila. El
resto de documentos (`README.md`, `INSTALAR.md`, `EJECUTAR.md`, `COMANDOS.md`) explica el
diseño y la instalación.

`reparto_ocr/codigo/` es **la copia exacta del código que se manda a cada equipo**. Es la
que se ejecuta, así que cualquier cambio en el reparto se hace aquí y se reparte desde
aquí. Su `extractor_completo/` es una versión del paquete `lector_ocr/` adaptada a la
tanda: la bitácora, los pesados al final y la pasada en dos fases. Antes de mezclarlos,
compara.

Los scripts de `reparto_ocr/` (`asignar.py`, `cola_ocr.py`, `empaquetar.py`…) se
escribieron para ejecutarse **desde el repositorio de producción**
(`python_script_hubspot_info`), donde están el árbol, los diarios y la cola. Aquí quedan
versionados; para usarlos se copian allí, o se ajustan las rutas de `empaquetar.py`
(`RAIZ`).
