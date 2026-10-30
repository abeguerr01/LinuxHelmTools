"""Prueba la lógica de BORRADO de ulxOptAppManager (sin root, sin pkexec):

  1) ruta_borrable(): la validación de seguridad ANTES del rm -rf
  2) lanzadores_de(): qué .desktop apuntan a cada app (para borrarlos)
  3) _trabajo_borrar() con pkexec simulado: éxito y error

El borrado real es lo más peligroso de la herramienta, así que la
validación de la ruta se prueba con nombres hostiles de verdad.
"""
import shutil
import subprocess
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


# =====================================================================
print("=" * 96)
print("1) ruta_borrable(): qué rutas se permiten al rm -rf")
print("=" * 96)

BASE = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
OPT = BASE / "opt"
OPT.mkdir()
main.RAIZ_OPT = OPT

for nombre in ("myapp", "myapp-2", "con espacios", "punto.exe", ".oculta", ".git"):
    (OPT / nombre).mkdir()

# --- válidas ---
for nombre in ("myapp", "myapp-2", "con espacios", "punto.exe", ".oculta", ".git"):
    ruta = main.ruta_borrable(nombre)
    comprobar(ruta == OPT / nombre, f"VÁLIDA: {nombre!r}", str(ruta))

# --- hostiles: ninguna debe dar ruta ---
hostiles = [
    ("", "vacío"),
    (".", "el propio /opt"),
    ("..", "saltar al padre"),
    ("../etc", "escape con .."),
    ("../../", "escape doble"),
    ("/etc", "ruta absoluta"),
    ("myapp/../otra", "ruta con barra"),
    ("myapp/", "barra final"),
]
for nombre, motivo in hostiles:
    ruta = main.ruta_borrable(nombre)
    comprobar(ruta is None, f"RECHAZA: {motivo:22} {nombre!r}", str(ruta))

# --- enlace simbólico que apunta FUERA de /opt ---
victima = BASE / "victima"
victima.mkdir()
(OPT / "enlace").symlink_to(victima)
ruta = main.ruta_borrable("enlace")
comprobar(ruta is None, "RECHAZA: enlace simbólico fuera de /opt", str(ruta))

# --- enlace simbólico DENTRO de /opt: sí se puede borrar (es el enlace) ---
(OPT / "destino").mkdir()
(OPT / "enlace_bueno").symlink_to(OPT / "destino")
ruta = main.ruta_borrable("enlace_bueno")
comprobar(ruta == OPT / "enlace_bueno", "PERMITE: enlace dentro de /opt", str(ruta))

print()
print("=" * 96)
print("2) lanzadores_de(): qué .desktop apuntan a la app")
print("=" * 96)

LANZ = BASE / "lanzadores"
LANZ.mkdir()
main.DIR_LANZADORES = LANZ


def escribir_desktop(nombre_fichero: str, *lineas: str) -> None:
    (LANZ / nombre_fichero).write_text("[Desktop Entry]\n" + "\n".join(lineas) + "\n")


escribir_desktop("myapp.desktop", f"Exec={OPT}/myapp/bin/myapp %U",
                 f"Icon={OPT}/myapp/share/icono.png")
escribir_desktop("myapp-otro.desktop", f"Exec={OPT}/myapp/myapp")   # otro lanzador
escribir_desktop("sin_ruta.desktop", "Exec=myapp", "Icon=myapp")    # nombre suelto
escribir_desktop("myapp2.desktop", f"Exec={OPT}/myapp2/bin/x")      # app distinta
escribir_desktop("comillas.desktop", f'Exec="{OPT}/myapp/con espacios/x" --flag')
(LANZ / "subcarpeta").mkdir()
(LANZ / "subcarpeta" / "ignorado.desktop").write_text(f"Exec={OPT}/myapp/x")

encontrados = sorted(p.name for p in main.lanzadores_de("myapp"))
comprobar(encontrados == ["comillas.desktop", "myapp-otro.desktop", "myapp.desktop"],
          "encuentra los que apuntan a /opt/myapp", str(encontrados))
comprobar(main.lanzadores_de("myapp2") == [LANZ / "myapp2.desktop"],
          "'myapp2' solo ve SU lanzador (cierre de camino)", "")
comprobar(main.lanzadores_de("no-existe") == [], "app sin lanzadores -> []", "")

# un .desktop ilegible (sin permisos) no debe tronar la búsqueda
malo = LANZ / "ilegible.desktop"
malo.write_text(f"Exec={OPT}/myapp/x")
malo.chmod(0o000)
encontrados_ilegible = sorted(p.name for p in main.lanzadores_de("myapp"))
malo.chmod(0o644)
comprobar("ilegible.desktop" not in encontrados_ilegible,
          "ignora un .desktop ilegible sin tronar", str(encontrados_ilegible))

print()
print("=" * 96)
print("3) _trabajo_borrar() con pkexec simulado")
print("=" * 96)

# sustituir subprocess.run para no pedir root: simulamos el rm -rf
ejecutados: list[list[str]] = []
orig_run = subprocess.run


def run_falso(argv, **_kwargs):
    ejecutados.append(list(argv))
    destino = Path(argv[3])
    shutil.rmtree(destino, ignore_errors=True)
    return subprocess.CompletedProcess(argv, 0, b"", b"")


main.subprocess.run = run_falso  # type: ignore[assignment]

# caso éxito: borra carpeta + sus .desktop
(OPT / "myapp" / "bin").mkdir(parents=True)
(OPT / "myapp" / "bin" / "x").write_text("#!/bin/sh")
mensaje, exito = main._trabajo_borrar(OPT / "myapp")
comprobar(exito and not (OPT / "myapp").exists(),
          "éxito: borra la carpeta", mensaje)
comprobar(not (LANZ / "myapp.desktop").exists()
          and not (LANZ / "myapp-otro.desktop").exists(),
          "éxito: borra sus .desktop", "")
comprobar((LANZ / "myapp2.desktop").exists(),
          "éxito: NO toca el .desktop de otra app", "")
comprobar(ejecutados and ejecutados[-1][:3] == ["pkexec", "rm", "-rf"],
          "usa pkexec rm -rf", str(ejecutados[-1][:4]))
comprobar("removed" in mensaje, "mensaje cuenta lanzadores borrados", mensaje)

# caso error: pkexec devuelve stderr (p. ej. "Dismissed by user")
def run_error(argv, **_kwargs):
    ejecutados.append(list(argv))
    return subprocess.CompletedProcess(argv, 1, b"", b"Dismissed by user")


main.subprocess.run = run_error  # type: ignore[assignment]
(OPT / "otra").mkdir()
mensaje, exito = main._trabajo_borrar(OPT / "otra")
comprobar(not exito and (OPT / "otra").exists(),
          "error: la carpeta NO se toca si pkexec falla", mensaje)
comprobar(mensaje.startswith("✗"), "error: mensaje con ✗", mensaje)

main.subprocess.run = orig_run  # type: ignore[assignment]

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
