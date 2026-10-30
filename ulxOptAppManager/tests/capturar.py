"""Abre la app real y la deja en un estado concreto para capturarla.

Uso:  python3 capturar.py <seleccion|dialogo|borrado> <salida.png>

Crea una instalación FALSA en un /opt temporal (con icono y lanzadores)
para no tocar el /opt real. Requiere un entorno gráfico con `spectacle`.
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

main.RAIZ_OPT = FAKE
main.DIR_LANZADORES = LANZ

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
            main.lista.selection_set(hijos[0])
            main.lista.event_generate("<<TreeviewSelect>>")

    if estado == "dialogo" and n == 8:
        # abrir el diálogo de confirmación (modal, pero after() sigue vivo)
        main.borrar()

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
retraso = {"seleccion": 2.5, "dialogo": 4.5, "borrado": 2.0}[estado]
threading.Thread(target=lambda: capturar(retraso), daemon=True).start()
main.crear_ventana()
print(f"estado '{estado}' -> {salida}")
