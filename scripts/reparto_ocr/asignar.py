r"""Reparte el trabajo entre los equipos y escribe el comando de cada uno.

POR QUE NO UNA TABLA EN UN MD. Doce equipos, cada uno con su numero de trozo y
alguno con varios. Una tabla que hay que leer bien acaba con dos personas
corriendo el mismo trozo y un tercero sin hacer -- y no se nota hasta el final,
cuando faltan archivos que nadie leyo.

Esto escribe la linea exacta de cada equipo, para copiar y pegar.

EL REPARTO NO ES A PARTES IGUALES, porque los equipos no van igual:

    con NVIDIA      ~4 archivos/min por proceso   (medido el 28-sep)
    sin grafica     bastante menos, y no esta medido

Se reparte por capacidad estimada, no por cabezas: dar el mismo trozo a todos deja
a los rapidos parados mientras los lentos siguen tres dias mas.

LAS ESTIMACIONES DE CPU SON ESO, ESTIMACIONES. Que un equipo sin grafica corra
`--limite 200` y diga su ritmo real; con eso se vuelve a correr esto con --cpu y el
reparto se ajusta.

Uso:
    .venv\Scripts\python.exe reparto_ocr\asignar.py
    .venv\Scripts\python.exe reparto_ocr\asignar.py --gpu 2 --mac 2 --cpu 8
    .venv\Scripts\python.exe reparto_ocr\asignar.py --ritmo-cpu 1.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

# Medido el 28-sep en el equipo de Rafa, con lectura completa y un solo proceso.
RITMO_GPU = 4.0

# No medido. Paddle en CPU va varias veces mas lento; se toma un cuarto del de GPU
# como punto de partida y se corrige en cuanto alguien mida el suyo.
RITMO_CPU = 1.0

# Procesos por equipo. El candado del OCR es por proceso, asi que varios procesos
# en la misma maquina si corren el OCR en paralelo de verdad -- pero cada uno carga
# su propio modelo, y ahi el limite es la memoria.
PROCESOS_GPU = 3      # VRAM: tres modelos caben, mas empieza a ser apretado
PROCESOS_CPU = 2      # con 32 GB entran dos; con 16, uno


def pendientes() -> int:
    """Cuantos archivos quedan por leer entre todos, en Matters.

    La cuenta es la de describir_casos.estado_de_lectura, la misma que usan la
    tanda y el comprobador. Tenerla copiada aqui fue lo que hizo que el 28-sep los
    tres dieran numeros distintos.
    """
    from describir_casos import clave_de_reparto, estado_de_lectura

    # DOCUMENTOS DISTINTOS, no archivos: las copias caen en el mismo trozo que su
    # original y copian su texto sin OCR, asi que no cuestan tiempo.
    return len({clave_de_reparto(r) for r in estado_de_lectura("matters")["pendientes"]})


def lineas(grupo: str, equipos: list[str], procesos: int, bandera: str) -> int:
    """Escribe las ventanas de un grupo. Devuelve su --de (un trozo por proceso)."""
    de = len(equipos) * procesos
    trozo = 0
    for nombre in equipos:
        print(f"  {'=' * 66}")
        print(f"  {nombre}   {procesos} ventana(s)")
        for n in range(1, procesos + 1):
            print(f"\n     ventana {n}:")
            print(f"       .venv\\Scripts\\python.exe codigo\\describir_casos.py --extraer "
                  f"{bandera} --por-caso 0 --paginas 0 --max-mb 0 --parte {trozo} --de {de}")
            trozo += 1
    return de


def dos_pasadas(args) -> None:
    """El reparto por TIPO DE TRABAJO (29-sep).

    Sin GPU, el OCR va a ~8 archivos por hora; con GPU, a ~4 por minuto. Repartir
    trozos iguales dejaba a los equipos sin GPU meses con los escaneos. Asi que:

        SIN GPU  --sin-ocr    todo lo que tiene texto de ordenador; lo que necesite
                              OCR se aparta para la GPU
        CON GPU  --solo-ocr   las imagenes y la cola (reparto_ocr/cola_ocr.py)

    Cada grupo se parte entre SUS procesos, un trozo por proceso, con su propio --de.
    Es el --de mas bajo posible: menos no se puede sin dejar un proceso sin trabajo.
    """
    from describir_casos import clave_de_reparto, estado_de_lectura

    gpu = [f"NVIDIA {i + 1}" for i in range(args.gpu)]
    cpu = ([f"Mac {i + 1}" for i in range(args.mac)]
           + [f"CPU {i + 1}" for i in range(args.cpu)])
    if not gpu or not cpu:
        raise SystemExit("Las dos pasadas necesitan equipos de los dos tipos.")

    if not args.sin_contar:
        sin = estado_de_lectura("matters", "sin-ocr")["pendientes"]
        con = estado_de_lectura("matters", "solo-ocr")["pendientes"]
        n_sin = len({clave_de_reparto(r) for r in sin})
        n_con = len({clave_de_reparto(r) for r in con})
        print(f"\n  PASADA SIN OCR: {n_sin:,} documentos distintos, entre "
              f"{len(cpu)} equipos sin GPU")
        print(f"  PASADA SOLO OCR: {n_con:,} (imagenes + cola) entre {len(gpu)} con GPU"
              f"  -> ~{n_con / (len(gpu) * args.procesos_gpu * args.ritmo_gpu) / 60:.0f} h"
              f" a {args.ritmo_gpu}/min por proceso")
        print("  La cola crece mientras corre la pasada sin OCR: cada manana,")
        print("  reparto_ocr\\cola_ocr.py y pasar cola_ocr.txt a los equipos con GPU.\n")

    print("  ##### EQUIPOS CON GPU (solo OCR) #####")
    de_gpu = lineas("gpu", gpu, args.procesos_gpu, "--solo-ocr")
    print("\n  ##### EQUIPOS SIN GPU (sin OCR) #####")
    de_cpu = lineas("cpu", cpu, args.procesos_cpu, "--sin-ocr")
    print(f"""
  {'=' * 66}
  CADA GRUPO TIENE SU --de: {de_gpu} en los equipos con GPU, {de_cpu} en los sin GPU.
  Dentro de un grupo tiene que ser el MISMO en todos; si alguien pone otro, lee cosas
  que ya leyo otro y deja huecos que no lee nadie.

  En los equipos SIN GPU: OMP_NUM_THREADS no hace falta (no cargan Paddle), y
  comprobar.py se corre con --sin-ocr.
  EN MAC Y LINUX cambia '.venv\\Scripts\\python.exe' por '.venv/bin/python'.""")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--gpu", type=int, default=2, help="Equipos con NVIDIA")
    p.add_argument("--mac", type=int, default=2, help="Mac (Apple Silicon, van por CPU)")
    p.add_argument("--cpu", type=int, default=8, help="Equipos sin grafica")
    p.add_argument("--ritmo-gpu", type=float, default=RITMO_GPU,
                   help="Archivos/min por proceso con GPU")
    p.add_argument("--ritmo-cpu", type=float, default=RITMO_CPU,
                   help="Archivos/min por proceso sin GPU (ESTIMADO: midelo)")
    p.add_argument("--sin-contar", action="store_true",
                   help="No preguntar cuantos quedan (mas rapido, sin estimacion)")
    p.add_argument("--procesos-gpu", type=int, default=2,
                   help="Ventanas por equipo con GPU (dos pasadas)")
    p.add_argument("--procesos-cpu", type=int, default=2,
                   help="Ventanas por equipo sin GPU (dos pasadas)")
    p.add_argument("--una-pasada", action="store_true",
                   help="El reparto de antes: todos hacen de todo, por capacidad")
    args = p.parse_args()

    if not args.una_pasada:
        dos_pasadas(args)
        return

    # Un equipo = (nombre, procesos, ritmo por proceso). Los Mac van con los de CPU:
    # ni el chip de Apple ni Metal aceleran Paddle, solo CUDA.
    equipos = ([(f"NVIDIA {i + 1}", PROCESOS_GPU, args.ritmo_gpu) for i in range(args.gpu)]
               + [(f"Mac {i + 1}", PROCESOS_CPU, args.ritmo_cpu) for i in range(args.mac)]
               + [(f"CPU {i + 1}", PROCESOS_CPU, args.ritmo_cpu) for i in range(args.cpu)])
    if not equipos:
        raise SystemExit("No hay equipos que repartir.")

    # Los trozos se reparten proporcionalmente a lo que cada equipo puede hacer.
    # Se usan muchos trozos pequenos (uno por unidad de capacidad) para que la
    # proporcion salga sin decimales raros.
    capacidad = [procesos * ritmo for _, procesos, ritmo in equipos]
    total_cap = sum(capacidad)
    trozos = sum(max(1, round(c)) for c in capacidad)
    reparto, siguiente = [], 0
    for (nombre, procesos, ritmo), cap in zip(equipos, capacidad):
        cuantos = max(1, round(cap))
        mios = list(range(siguiente, siguiente + cuantos))
        siguiente += cuantos
        reparto.append((nombre, procesos, ritmo, mios))

    quedan = None if args.sin_contar else pendientes()

    print(f"\n  REPARTO ENTRE {len(equipos)} EQUIPOS   ({trozos} trozos)")
    if quedan:
        horas = quedan / total_cap / 60
        print(f"  quedan {quedan:,} documentos distintos   ->  ~{horas:.0f} h "
              f"({horas / 24:.1f} dias) si todos corren a la vez")
    print(f"\n  ritmos usados: GPU {args.ritmo_gpu}/min por proceso, "
          f"CPU {args.ritmo_cpu}/min (estimado)\n")

    for nombre, procesos, ritmo, mios in reparto:
        suyos = quedan * len(mios) / trozos if quedan else 0
        print(f"  {'=' * 66}")
        print(f"  {nombre}   {procesos} proceso(s)   trozos {mios}")
        if quedan:
            print(f"     le tocan ~{suyos:,.0f} documentos, "
                  f"~{suyos / (procesos * ritmo) / 60:.0f} h")
        # Los trozos del equipo se parten entre sus procesos, una ventana cada uno.
        por_proceso = [mios[i::procesos] for i in range(procesos)]
        for n, trozos_suyos in enumerate(por_proceso, 1):
            if not trozos_suyos:
                continue
            cual = ("--parte " + str(trozos_suyos[0]) if len(trozos_suyos) == 1
                    else "--partes " + ",".join(str(x) for x in trozos_suyos))
            print(f"\n     ventana {n}:")
            print(f"       .venv\\Scripts\\python.exe codigo\\describir_casos.py --extraer "
                  f"--origen matters --por-caso 0 --paginas 0 --max-mb 0 {cual} --de {trozos}")

    print(f"""
  {'=' * 66}
  ANTES DE LANZAR, cada equipo:
     .venv\\Scripts\\python.exe reparto_ocr\\comprobar.py --parte N --de {trozos}

  EN MAC Y LINUX cambia '.venv\\Scripts\\python.exe' por '.venv/bin/python'.

  EL --de {trozos} TIENE QUE SER EL MISMO EN TODOS. Si alguien pone otro numero,
  su reparto no casa con el de los demas: leera cosas que ya leyo otro y dejara
  huecos que no lee nadie.""")


if __name__ == "__main__":
    main()
