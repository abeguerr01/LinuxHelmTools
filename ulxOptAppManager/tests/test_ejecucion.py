"""Prueba la lógica de EJECUTAR de ulxOptAppManager (sin root, sin GUI):

  1) ejecutable_de(): qué binario se lanza de cada app (lógica pura)
  2) _trabajo_ejecutar(): Popen real sobre scripts de mentira en /tmp
     (exit 0 -> éxito, exit != 0 -> error, sin permisos/falta -> error)

Los ejecutables se dan como Aplicacion montada a mano: no hace falta
crear carpetas para probar el orden de preferencia.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str = "") -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:58} {detalle}")


def app(nombre: str, *ejecutables: str) -> main.Aplicacion:
    """Aplicacion de mentira con los ejecutables indicados (relativos)."""
    return main.Aplicacion(nombre=nombre, ruta=Path("/opt") / nombre,
                           ejecutables=[Path(e) for e in ejecutables])


# =====================================================================
print("=" * 96)
print("1) ejecutable_de(): el binario más probable de cada app")
print("=" * 96)

# --- brave-bin: muchas chingueras en la raíz, el bueno es `brave` ---
brave = app("brave-bin",
            "chrome-management-service", "chrome-sandbox",
            "chrome_crashpad_handler", "brave", "brave-browser")
comprobar(main.ejecutable_de(brave) == Path("brave"),
          "brave-bin -> brave («bin» es ficha genérica)", "")

# --- desktop-plus: se llama como la carpeta, aunque NO sea el primero ---
desktop = app("desktop-plus",
              "chrome-sandbox", "chrome_crashpad_handler", "desktop-plus")
comprobar(main.ejecutable_de(desktop) == Path("desktop-plus"),
          "desktop-plus -> desktop-plus (nombre exacto de la carpeta)", "")

# --- jetbrains: carpeta con versión, binario en bin/ ---
jetbrains = app("jetbrains-toolbox-3.8.1.88030",
                "bin/askpass", "bin/jetbrains-toolbox")
comprobar(main.ejecutable_de(jetbrains) == Path("bin/jetbrains-toolbox"),
          "quita la versión y encuentra bin/jetbrains-toolbox", "")

# --- bin/myapp: carpeta myapp-1.0 ---
comprobar(main.ejecutable_de(app("myapp-1.0", "bin/myapp")) == Path("bin/myapp"),
          "myapp-1.0 -> bin/myapp", "")

# --- sin nombre coincidente: el primero de la lista (ya ordenada) ---
varias = app("cosas", "aaa", "bbb")
comprobar(main.ejecutable_de(varias) == Path("aaa"),
          "sin coincidencia de nombre -> el primero", "")

# --- sin ejecutables: None (el botón Execute queda deshabilitado) ---
comprobar(main.ejecutable_de(app("containerd")) is None,
          "sin ejecutables -> None", "")

print()
print("=" * 96)
print("2) _trabajo_ejecutar(): lanza de verdad y coge los fallos")
print("=" * 96)

carpeta = Path(tempfile.mkdtemp(dir="/tmp/opencode"))

ok = carpeta / "ok.sh"
ok.write_text("#!/bin/sh\nexit 0\n")
ok.chmod(0o755)
mensaje, exito = main._trabajo_ejecutar(ok)
comprobar(exito and mensaje == "✓ ok.sh executed",
          "script que sale con 0 -> éxito", mensaje)

mal = carpeta / "mal.sh"
mal.write_text("#!/bin/sh\nexit 3\n")
mal.chmod(0o755)
mensaje, exito = main._trabajo_ejecutar(mal)
comprobar(not exito and mensaje == "✗ mal.sh exited with code 3",
          "script que sale con 3 -> error con el código", mensaje)

sin_permisos = carpeta / "sin_permisos.sh"
sin_permisos.write_text("#!/bin/sh\n")
sin_permisos.chmod(0o644)          # existe pero no se puede ejecutar
mensaje, exito = main._trabajo_ejecutar(sin_permisos)
comprobar(not exito and mensaje.startswith("✗ Error:"),
          "fichero sin bit X -> ✗ Error: ...", mensaje)

mensaje, exito = main._trabajo_ejecutar(carpeta / "no_existe")
comprobar(not exito and mensaje.startswith("✗ Error:"),
          "ruta que no existe -> ✗ Error: ...", mensaje)

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
