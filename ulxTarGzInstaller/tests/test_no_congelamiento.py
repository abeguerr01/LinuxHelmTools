"""Comprueba que la ventana NO se congela: el bucle de Tk sigue corriendo
mientras un trabajo pesado (1,5 s) va en su hilo.

Antes del arreglo, subprocess.run() ejecutaba ese trabajo EN el hilo de Tk,
 así que los ticks del bucle se habrían parado durante 1,5 s.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk

import main

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str) -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:44} {detalle}")


# --- ventana de prueba (sin mainloop, la bombeamos a mano con update()) ---
main.ventana = tk.Tk()
main.ventana.withdraw()
main.ventana.after(50, main._drenar_cola)

# --- contador de ticks del bucle de eventos ---
ticks = 0


def tick() -> None:
    global ticks
    ticks += 1
    main.ventana.after(50, tick)


main.ventana.after(50, tick)

# --- trabajo que tarda 1,5 s, como un 'pkexec tar' real ---
resultado: dict = {}


def trabajo_lento() -> tuple[str, bool]:
    time.sleep(1.5)
    return "hecho", True


def al_terminar(mensaje: str, exito: bool) -> None:
    resultado["mensaje"] = mensaje
    resultado["exito"] = exito


# --- 1) ¿_en_hilo devuelve al instante? ---
t0 = time.monotonic()
main._en_hilo(trabajo_lento, al_terminar)
espera_llamada = (time.monotonic() - t0) * 1000
comprobar(
    espera_llamada < 100,
    "_en_hilo() no bloquea al llamarlo",
    f"={espera_llamada:.1f} ms (trabajo dura 1500 ms)",
)

# --- 2) ¿el bucle sigue vivo MIENTRAS el trabajo corre? ---
ticks_antes = ticks
fin = time.monotonic() + 2.5
while time.monotonic() < fin:
    main.ventana.update()
    time.sleep(0.01)
ticks_durante = ticks - ticks_antes

comprobar(
    ticks_durante >= 25,
    "el bucle de Tk sigue vivo durante el trabajo",
    f"={ticks_durante} ticks en 2,5 s",
)

# --- 3) ¿llega el resultado por la cola? ---
comprobar(
    resultado.get("mensaje") == "hecho" and resultado.get("exito") is True,
    "el resultado llega a la interfaz",
    f"={resultado}",
)

# --- 4) ¿_bloquear_botones funciona? ---
main.boton = tk.Button(main.ventana)
main.btn_abrir = tk.Button(main.ventana)
main.btn_deshacer = tk.Button(main.ventana)

main._bloquear_botones(True)
bloqueados = all(str(w["state"]) == "disabled"
                 for w in (main.boton, main.btn_abrir, main.btn_deshacer))
comprobar(bloqueados, "_bloquear_botones(True) deshabilita los 3", "")

main._bloquear_botones(False)
libres = all(str(w["state"]) == "normal"
             for w in (main.boton, main.btn_abrir, main.btn_deshacer))
comprobar(libres, "_bloquear_botones(False) los reactiva", "")

# --- 5) ¿una excepción en el hilo llega a la interfaz en vez de morir? ---
resultado.clear()
main._en_hilo(lambda: (_ for _ in ()).throw(ValueError("boom")), al_terminar)
fin = time.monotonic() + 1.0
while time.monotonic() < fin:
    main.ventana.update()
    time.sleep(0.01)
comprobar(
    "boom" in str(resultado.get("mensaje", "")),
    "una excepción en el hilo se reporta",
    f"={resultado.get('mensaje')!r}",
)

main.ventana.destroy()
print(f"\n{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
