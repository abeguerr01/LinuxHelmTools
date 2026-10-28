"""Abre la app real y la deja en un estado concreto para capturarla.

Uso:  python3 capturar.py <inicial|instalado|ocupado|dialogo> <salida.png>

Requiere un entorno gráfico Wayland/KDE con `spectacle` (captura solo la
ventana activa = la de la app, sin tocar el resto del escritorio).
"""
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

# Instalación falsa (sin tocar /opt) para que el diálogo muestre cosas reales
FAKE = Path("/tmp/opencode/fake_opt")
binario = FAKE / "myapp" / "myapp"
binario.parent.mkdir(parents=True, exist_ok=True)
binario.write_text("#!/bin/sh\n")
binario.chmod(0o755)
app_dir = FAKE / "myapp" / "share" / "applications"
app_dir.mkdir(parents=True, exist_ok=True)
(app_dir / "myapp.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Mi App\n"
    "Exec=miapp %U\nIcon=miapp\nTerminal=false\n"
)
app_dir.parent.joinpath("logo.png").write_bytes(
    (Path(__file__).resolve().parents[1] / "AppLogo.png").read_bytes())

main.RAIZ_OPT = FAKE
main.DIR_LANZADORES = Path("/tmp/opencode/fake_lanz")
main.extraido = "myapp"

DEMANDA = 2.0 if estado != "dialogo" else 3.0


def capturar() -> None:
    time.sleep(DEMANDA)
    subprocess.run(["spectacle", "-a", "-b", "-n", "-o", salida], timeout=25)


orig = main._drenar_cola
tick = {"n": 0}


def parche() -> None:
    orig()
    tick["n"] += 1
    n = tick["n"]

    if estado == "instalado" and n == 4:
        main._poner_mensaje(main.etiquetaConfir,
                            "✓ Will install as /opt/myapp-1.0 [directo]")
        main.frame_seccion.pack(fill=tk.X)
        main._poner_mensaje(main.etiquetaConfirCond,
                            "✓ Launcher created: myapp.desktop")
    elif estado == "ocupado" and n == 4:
        main._bloquear_botones(True)
        main._poner_mensaje(main.etiquetaConfir, "⏳ Extracting, please wait...")
    elif estado == "dialogo" and n == 4:
        main._poner_mensaje(main.etiquetaConfir, "✓ Extracted in /opt/myapp-1.0")
        main.frame_seccion.pack(fill=tk.X)
    elif estado == "dialogo" and n == 8:
        main.dialogo_lanzador()

    # Cierre ordenado: la captura ya se ha hecho para entonces
    if estado == "dialogo" and n in (150, 175):
        hijos = [h for h in main.ventana.winfo_children() if isinstance(h, tk.Toplevel)]
        if hijos:
            hijos[-1].destroy()
        elif n == 175:
            main.ventana.destroy()
    elif estado != "dialogo" and n == 110:
        main.ventana.destroy()


main._drenar_cola = parche
threading.Thread(target=capturar, daemon=True).start()
main.crear_ventana()
print(f"estado '{estado}' -> {salida}")
