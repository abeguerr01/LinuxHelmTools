"""Prueba el diálogo de lanzador: construcción, preselección y ambos botones.

El diálogo bloquea con wait_window(), así que programamos un pulsador con
after() que lo encuentra y le da a "Create launcher" / "Skip".
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk  # noqa: E402
import main  # noqa: E402

BASE = Path("/tmp/opencode/test_dialogo")
OPT = BASE / "opt"
LANZ = BASE / "lanzadores"

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str = "") -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:58} {detalle}")


def encontrar(raiz, texto: str):
    """Busca un tk.Button con ese texto dentro de la jerarquía."""
    for hijo in raiz.winfo_children():
        if isinstance(hijo, tk.Button) and hijo.cget("text") == texto:
            return hijo
        hallado = encontrar(hijo, texto)
        if hallado is not None:
            return hallado
    return None


def preparar(trae_propio: bool) -> None:
    shutil.rmtree(OPT, ignore_errors=True)
    shutil.rmtree(LANZ, ignore_errors=True)
    OPT.mkdir(parents=True)
    LANZ.mkdir(parents=True)

    main.RAIZ_OPT = OPT
    main.DIR_LANZADORES = LANZ
    main.extraido = "myapp"

    raiz = OPT / "myapp"
    for nombre, modo in (("myapp", 0o755), ("bin/helper", 0o755), ("lib/interno", 0o755)):
        p = raiz / nombre
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n")
        p.chmod(modo)

    if trae_propio:
        d = raiz / "share" / "applications"
        d.mkdir(parents=True)
        (d / "myapp.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=App Original\n"
            "Exec=miapp %U\nIcon=miapp\nTerminal=true\nCategories=Utility;\n"
        )


def ejecutar_dialogo(accion: str):
    """Abre el diálogo y le da a `accion` ('Create launcher' o 'Skip')."""
    pulsado: list = []

    def buscar() -> None:
        for hijo in main.ventana.winfo_children():
            if isinstance(hijo, tk.Toplevel):
                boton = encontrar(hijo, accion)
                if boton is not None:
                    pulsado.append(accion)
                    boton.invoke()
                    return
        main.ventana.after(80, buscar)

    main.ventana.after(250, buscar)
    resultado = main.dialogo_lanzador()
    main.ventana.destroy()
    return resultado, bool(pulsado)


def abrir_ventana() -> None:
    main.ventana = tk.Tk()
    main.ventana.geometry("260x90+40+40")


# ---------------------------------------------------------------------
print("=" * 96)
print("A) Paquete SIN .desktop propio")
print("=" * 96)
preparar(trae_propio=False)
abrir_ventana()
resultado, pulsado = ejecutar_dialogo("Create launcher")

comprobar(pulsado, "el diálogo se construye y responde", "")
comprobar(resultado is not None, "devuelve la elección", str(resultado))
if resultado:
    ejecutable, terminal, lineas = resultado
    comprobar(ejecutable == OPT / "myapp" / "myapp",
              "preselecciona el de la raíz (el más probable)", str(ejecutable.name))
    comprobar(terminal is False, "Terminal empieza en False", str(terminal))
    comprobar(lineas == [], "sin .desktop propio -> lineas vacías", "")

    # ahora escribir de verdad
    escrito = main.escribir_desktop(ejecutable, terminal, lineas)
    comprobar(escrito.exists() and escrito.parent == LANZ,
              "escribe el .desktop tras aceptar", escrito.name)

# ---------------------------------------------------------------------
print()
print("=" * 96)
print("B) Paquete CON .desktop propio (Terminal=true, Exec=miapp %U)")
print("=" * 96)
preparar(trae_propio=True)
abrir_ventana()
resultado, pulsado = ejecutar_dialogo("Create launcher")

comprobar(pulsado, "el diálogo se construye y responde", "")
comprobar(resultado is not None, "devuelve la elección", str(resultado))
if resultado:
    ejecutable, terminal, lineas = resultado
    comprobar(terminal is True, "premarca Terminal=true (lo trae el paquete)", str(terminal))
    comprobar(any("Name=App Original" in l for l in lineas),
              "carga las líneas del .desktop del paquete", f"{len(lineas)} líneas")
    comprobar(ejecutable == OPT / "myapp" / "myapp", "preselecciona el binario", ejecutable.name)

# ---------------------------------------------------------------------
print()
print("=" * 96)
print("C) Botón 'Skip' -> no devuelve nada")
print("=" * 96)
preparar(trae_propio=False)
abrir_ventana()
resultado, pulsado = ejecutar_dialogo("Skip")

comprobar(pulsado, "el diálogo acepta 'Skip'", "")
comprobar(resultado is None, "devuelve None (no se crea lanzador)", str(resultado))

# ---------------------------------------------------------------------
print()
print("=" * 96)
print("D) Sin ejecutables -> no revienta")
print("=" * 96)
preparar(trae_propio=False)
shutil.rmtree(OPT / "myapp", ignore_errors=True)
(OPT / "myapp").mkdir(parents=True)
(OPT / "myapp" / "leeme.txt").write_text("nada ejecutable")
abrir_ventana()
resultado, pulsado = ejecutar_dialogo("Skip")

comprobar(pulsado, "se construye aunque no haya binarios", "")
comprobar(resultado is None, "se omite sin error", str(resultado))

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
