"""Prueba la DETECCIÓN y CREACIÓN de lanzadores de menú (sin root, sin GUI):

  1) _desktop_de(): el contenido del .desktop (misma receta que
     ulxTarGzInstaller: Name, Exec con comillas si hay espacios, Icon)
  2) crear_lanzador(): escribe en DIR_LANZADORES y actualiza app.lanzador
  3) detección: inspeccionar() ve «no está en el menú», y después de
     crearlo la ve (y lanzadores_de() lo encuentra, como al borrar)
  4) _trabajo_crear_lanzadores(): mensajes de éxito / nada / error de E/S

Todo con menús y apps en temporales: ni /opt ni ~/.local se tocan.
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


# Menús del usuario y del sistema en carpetas temporales (hermético)
main.DIR_LANZADORES = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
main.DIR_LANZADORES_SISTEMA = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
MENU = main.DIR_LANZADORES


def nueva_app(nombre: str = "myapp-1.0") -> main.Aplicacion:
    """App de mentira en un temporal, con ejecutable (y icono opcional)."""
    raiz = Path(tempfile.mkdtemp(dir="/tmp/opencode")) / nombre
    (raiz / "bin").mkdir(parents=True)
    ejec = raiz / "bin" / nombre.split("-")[0]
    ejec.write_text("#!/bin/sh\n")
    ejec.chmod(0o755)
    return main.Aplicacion(nombre=nombre, ruta=raiz,
                           ejecutables=[Path("bin") / ejec.name])


# =====================================================================
print("=" * 96)
print("1) _desktop_de(): el contenido del .desktop")
print("=" * 96)

app = main.Aplicacion(nombre="myapp-1.0", ruta=Path("/opt/myapp-1.0"),
                      ejecutables=[Path("bin/myapp")], icono=Path("icono.png"))
texto = main._desktop_de(app, Path("/opt/myapp-1.0/bin/myapp"))
comprobar(texto.splitlines()[0] == "[Desktop Entry]"
          and "Type=Application" in texto.splitlines(),
          "cabecera [Desktop Entry] + Type=Application", "")
comprobar("Name=myapp-1.0" in texto, "Name=<nombre de la carpeta>", "")
comprobar("Exec=/opt/myapp-1.0/bin/myapp\n" in texto,
          "Exec=<ruta absoluta del ejecutable>", "")
comprobar("Icon=/opt/myapp-1.0/icono.png" in texto,
          "Icon=<ruta absoluta del icono encontrado>", "")
comprobar("Terminal=false" in texto, "Terminal=false (GUI por defecto)", "")
comprobar("Categories=HelmTool;" in texto,
          "Categories=HelmTool; (igual que el instalador)", "")

con_espacios = main._desktop_de(app, Path("/opt/my app/bin/x"))
comprobar('Exec="/opt/my app/bin/x"' in con_espacios,
          "ruta con espacios -> comillas dobles (spec .desktop)", "")

sin_icono = main.Aplicacion(nombre="x", ruta=Path("/opt/x"),
                            ejecutables=[Path("x")])
texto = main._desktop_de(sin_icono, Path("/opt/x/x"))
comprobar("Icon=\n" in texto, "sin icono -> Icon= vacío (no se inventa)", "")

print()
print("=" * 96)
print("2) crear_lanzador(): escribe en DIR_LANZADORES y actualiza el campo")
print("=" * 96)

app = nueva_app()
app.icono = Path("icono.png")
(app.ruta / "icono.png").write_text("x")
escrito = main.crear_lanzador(app)
comprobar(escrito == MENU / "myapp-1.0.desktop" and escrito.is_file(),
          "escribe <DIR_LANZADORES>/<nombre>.desktop", str(escrito))
comprobar(app.lanzador == escrito,
          "actualiza app.lanzador (para pintar sin reescanear)", "")
comprobar(escrito is not None
          and escrito.read_text() == main._desktop_de(
              app, app.ruta / "bin" / "myapp"),
          "el contenido es el de _desktop_de(...)", "")

vacia = main.Aplicacion(nombre="vacia", ruta=Path("/opt/vacia"))
comprobar(main.crear_lanzador(vacia) is None
          and not (MENU / "vacia.desktop").exists(),
          "sin ejecutables -> None y no escribe nada", "")

print()
print("=" * 96)
print("3) detección: inspeccionar() antes y después de crearlo")
print("=" * 96)

raiz = Path(tempfile.mkdtemp(dir="/tmp/opencode")) / "nolanz-1.0"
(raiz / "bin").mkdir(parents=True)
ejec = raiz / "bin" / "nolanz"
ejec.write_text("#!/bin/sh\n")
ejec.chmod(0o755)
main.RAIZ_OPT = raiz.parent   # para comprobar también el criterio del borrado

antes = main.inspeccionar(raiz)
comprobar(main.es_aplicacion(antes) and antes.lanzador is None,
          "app con ejecutables y sin .desktop -> detectada como pendiente", "")

mensaje, exito = main._trabajo_crear_lanzadores([antes])
comprobar(exito and mensaje.startswith("✓ 1 launcher(s) created"),
          "el trabajo crea su lanzador", mensaje)

despues = main.inspeccionar(raiz)
comprobar(despues.lanzador == MENU / "nolanz-1.0.desktop",
          "re-escaneada: ya sale en el menú (no se vuelve a ofrecer)",
          str(despues.lanzador))
comprobar(main.lanzadores_de("nolanz-1.0") == [MENU / "nolanz-1.0.desktop"],
          "lanzadores_de() lo encuentra (criterio de Delete)", "")

print()
print("=" * 96)
print("4) _trabajo_crear_lanzadores(): éxito, nada y error de E/S")
print("=" * 96)

ap1, ap2 = nueva_app("dos"), nueva_app("tres")
mensaje, exito = main._trabajo_crear_lanzadores([ap1, ap2])
comprobar(exito and mensaje.startswith("✓ 2 launcher(s) created"),
          "dos apps -> ✓ 2 launcher(s) created", mensaje)

mensaje, exito = main._trabajo_crear_lanzadores(
    [main.Aplicacion(nombre="vacia2", ruta=Path("/opt/vacia2"))])
comprobar(not exito and mensaje.startswith("⚠ Nothing created"),
          "ninguna con ejecutable -> ⚠ Nothing created", mensaje)

fichero = Path(tempfile.mkdtemp(dir="/tmp/opencode")) / "no_soy_un_dir"
fichero.write_text("x")
main.DIR_LANZADORES = fichero       # mkdir encima de un fichero -> OSError
mensaje, exito = main._trabajo_crear_lanzadores([nueva_app("cuatro")])
comprobar(not exito and mensaje.startswith("✗ Error:"),
          "DIR_LANZADORES inaccesible -> ✗ Error: ...", mensaje)

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
