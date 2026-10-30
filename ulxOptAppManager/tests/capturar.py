"""Abre la app real y la deja en un estado concreto para capturarla.

Uso:  python3 capturar.py <seleccion|dialogo|borrado|sistema|ejecucion|sinlanz> <salida.png>

Crea una instalación FALSA en un /opt temporal (con icono y lanzadores)
para no tocar el /opt real. El estado «sistema» selecciona una app sin
icono propio: su icono viene del lanzador del menú (prioridad 1b). El
estado «sinlanz» acepta la oferta de crear el lanzador que falta.
Requiere un entorno gráfico con `spectacle`.
"""
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk  # noqa: E402
import main  # noqa: E402

estado = sys.argv[1]
salida = sys.argv[2]

# --- /opt falso con una app que tiene icono y ejecutable ---
FAKE = Path("/tmp/opencode/fake_opt_appmgr")
LANZ = Path("/tmp/opencode/fake_lanz_appmgr")
shutil.rmtree(FAKE, ignore_errors=True)
shutil.rmtree(LANZ, ignore_errors=True)
app = FAKE / "myapp-1.0"
(app / "bin").mkdir(parents=True)
(app / "bin" / "myapp").write_text("#!/bin/sh\n")
(app / "bin" / "myapp").chmod(0o755)
(app / "share" / "applications").mkdir(parents=True)
(app / "share" / "applications" / "myapp.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Mi App\nExec=myapp %U\n")
logo = Path(__file__).resolve().parents[1] / "AppLogo.png"
(app / "icono.png").write_bytes(logo.read_bytes())
LANZ.mkdir(parents=True)
(LANZ / "myapp.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Mi App\n"
    f"Exec={app}/bin/myapp %U\nIcon={app}/icono.png\n")
(LANZ / "otra.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Otra\nExec=/opt/otra/x\n")

# --- segunda app SIN icono propio + lanzador en el menú del SISTEMA:
#     su icono solo puede venir de ahí (prioridad 1b) ---
ghost = FAKE / "ghostapp-2.0"
(ghost / "bin").mkdir(parents=True)
(ghost / "bin" / "ghostapp").write_text("#!/bin/sh\n")
(ghost / "bin" / "ghostapp").chmod(0o755)
# ...y una tercera app SIN lanzador en ningún sitio (para la oferta de
# «Missing menu launchers» del estado sinlanz)
nolanz = FAKE / "nolanz-1.0"
(nolanz / "bin").mkdir(parents=True)
(nolanz / "bin" / "nolanz").write_text("#!/bin/sh\n")
(nolanz / "bin" / "nolanz").chmod(0o755)
LANZ_SIS = Path("/tmp/opencode/fake_lanz_sistema_appmgr")
shutil.rmtree(LANZ_SIS, ignore_errors=True)
LANZ_SIS.mkdir(parents=True)
_icono_real = Path("/usr/share/icons/hicolor/256x256/apps/brave-desktop.png")
(LANZ_SIS / "ghostapp.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Ghost App\n"
    f"Exec={ghost}/bin/ghostapp %U\nIcon={_icono_real}\n")

main.RAIZ_OPT = FAKE
main.DIR_LANZADORES = LANZ
main.DIR_LANZADORES_SISTEMA = LANZ_SIS
# nolanz-1.0 no tiene lanzador: la oferta «Missing menu launchers» solo
# interesa en el estado sinlanz; en el resto no debe interrumpir
main._preguntado_lanzadores = estado != "sinlanz"

# estado 'sinlanz': la oferta de crear el lanzador se acepta sola
if estado == "sinlanz":
    main.messagebox.askyesno = lambda *_a, **_k: True  # type: ignore[assignment]

# estado 'borrado': simular pkexec (rm -rf sin root) y el Sí del diálogo
if estado == "borrado":
    import shutil as _shutil
    _run_orig = subprocess.run

    def _run_falso(argv, **_kwargs):
        # OJO: main.subprocess ES el módulo estándar (compartido): si se
        # parchea entero, se rompe también subprocess.run del harness (p. ej.
        # spectacle). Solo se intercepta lo que va para pkexec.
        if argv[:1] == ["pkexec"]:
            _shutil.rmtree(Path(argv[3]), ignore_errors=True)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return _run_orig(argv, **_kwargs)

    main.subprocess.run = _run_falso  # type: ignore[assignment]
    main.messagebox.askyesno = lambda *_a, **_k: True  # type: ignore[assignment]


def capturar(retraso: float) -> None:
    time.sleep(retraso)
    subprocess.run(["spectacle", "-a", "-b", "-n", "-o", salida], timeout=25)


orig = main._drenar_cola
tick = {"n": 0}


def parche() -> None:
    orig()
    tick["n"] += 1
    n = tick["n"]

    if n == 4:  # escaneo terminado: seleccionar la app
        hijos = main.lista.get_children()
        if hijos:
            objetivo = {"sistema": "ghostapp-2.0",
                        "sinlanz": "nolanz-1.0"}.get(estado, "myapp-1.0")
            if objetivo in hijos:
                main.lista.selection_set(objetivo)
                main.lista.event_generate("<<TreeviewSelect>>")

    if estado == "dialogo" and n == 8:
        # abrir el diálogo de confirmación (modal, pero after() sigue vivo)
        main.borrar()

    if estado == "ejecucion" and n == 8:
        # lanzar bin/myapp (script de mentira que sale con 0)
        main.ejecutar()

    if estado == "borrado" and n == 8:
        # con askyesno simulado, borra sin preguntar
        main.borrar()

    if estado == "dialogo" and n in (110, 125):
        # cerrar el diálogo de messagebox (y reintentar por si tarda)
        for hijo in main.ventana.winfo_children():
            if isinstance(hijo, tk.Toplevel):
                hijo.destroy()
        if n == 125:
            main.ventana.destroy()
    elif estado == "borrado" and n == 80:
        main.ventana.destroy()
    elif estado != "dialogo" and n == 60:
        main.ventana.destroy()


main._drenar_cola = parche
retraso = {"seleccion": 2.5, "dialogo": 4.5, "borrado": 2.0,
           "sistema": 2.5, "ejecucion": 2.5, "sinlanz": 2.5}[estado]
threading.Thread(target=lambda: capturar(retraso), daemon=True).start()
main.crear_ventana()
print(f"estado '{estado}' -> {salida}")
