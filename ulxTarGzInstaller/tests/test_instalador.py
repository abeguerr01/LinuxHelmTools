"""Prueba las mejoras de ulxTarGzInstaller:
  1) extracción segura (sin ficheros sueltos ni raíces de sistema)
  2) elección de ejecutable (sin adivinar)
  3) icono (busca en share/icons/hicolor, tamaño, nombre)
  4) lanzador (.desktop propio, argumentos conservados, Terminal)
Todo sin root: RAIZ_OPT y DIR_LANZADORES se apuntan a un temporal.
"""
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402  (el guard __main__ evita que abra la GUI)

BASE = Path("/tmp/opencode/test_instalador")
TARBALLS = BASE / "tarballs"
OPT = BASE / "opt"
LANZ = BASE / "lanzadores"

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str = "") -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:56} {detalle}")


def reiniciar() -> None:
    shutil.rmtree(OPT, ignore_errors=True)
    shutil.rmtree(LANZ, ignore_errors=True)
    OPT.mkdir(parents=True)
    LANZ.mkdir(parents=True)
    main.RAIZ_OPT = OPT
    main.DIR_LANZADORES = LANZ
    main.extraido = ""
    main.comando_tar = []
    main.ejecutable_elegido = None
    main.ya_instalado = False


def fabricar(nombre: str, entradas: list[str]) -> Path:
    TARBALLS.mkdir(parents=True, exist_ok=True)
    ruta = TARBALLS / nombre
    with tarfile.open(ruta, "w:gz") as t:
        for entrada in entradas:
            datos = b"x" * 64
            info = tarfile.TarInfo(entrada)
            info.size = len(datos)
            info.mode = 0o755 if entrada.endswith(("foo", "bin")) else 0o644
            t.addfile(info, io.BytesIO(datos))
    return ruta


def arbol(raiz: Path) -> list[str]:
    return sorted(str(p.relative_to(raiz)) for p in raiz.rglob("*"))


# =====================================================================
print("=" * 90)
print("1) EXTRACCIÓN SEGURA: analizar() + ejecutar el tar de verdad")
print("=" * 90)

casos = [
    ("a_normal.tar.gz", ["myapp-1.0/bin/foo", "myapp-1.0/README"]),
    ("b_flat.tar.gz", ["bin/foo", "lib/bar.so"]),
    ("c_dot.tar.gz", ["./myapp/foo", "./myapp/README"]),
    ("d_multi.tar.gz", ["app1/x", "app2/y"]),
    ("e_readme.tar.gz", ["README", "myapp/bin/foo"]),
    ("f_deep.tar.gz", ["usr/local/share/myapp/data.txt"]),
    ("g_sysroot.tar.gz", ["bin/tool", "share/doc/x"]),
]

for nombre, entradas in casos:
    tarball = fabricar(nombre, entradas)
    reiniciar()
    main.ruta = str(tarball)

    mensaje, ok = main.analizar()
    if not ok:
        comprobar(False, f"{nombre}: analizar()", mensaje)
        continue

    # ejecutar EXACTAMENTE el comando que decidiría, sin pkexec
    r = subprocess.run(["tar"] + main.comando_tar, capture_output=True, text=True)
    if r.returncode != 0:
        comprobar(False, f"{nombre}: extracción", r.stderr[:70])
        continue

    entradas_raiz = [e for e in OPT.iterdir()]
    unico = len(entradas_raiz) == 1
    coincide = unico and entradas_raiz[0].name == main.extraido
    comprobar(
        unico and coincide,
        f"{nombre:20} -> 1 sola carpeta '{main.extraido}'",
        mensaje.split("[")[-1] if "[" in mensaje else "",
    )

# ya instalado
reiniciar()
main.ruta = str(TARBALLS / "a_normal.tar.gz")
main.analizar()
main.RAIZ_OPT.mkdir(parents=True, exist_ok=True)
(main.RAIZ_OPT / main.extraido).mkdir()
main.analizar()
comprobar(main.ya_instalado, "detecta que ya está instalado", f"extraido={main.extraido}")

# raíz de sistema -> contenida, nunca /opt/usr ni /opt/bin
reiniciar()
main.ruta = str(TARBALLS / "g_sysroot.tar.gz")
main.analizar()
comprobar(main.extraido != "bin" and main.extraido != "share",
          "raíz de sistema NO se usa como nombre", f"extraido={main.extraido}")

# fichero con nombre de raíz de sistema (modo contenida): el caso que
# RAICES_SISTEMA se saltaba y acababa creando /opt/bin de verdad
for nombre_tar in ("bin.tar.gz", "usr.tar.gz", "lib.tar.gz"):
    tb = TARBALLS / nombre_tar
    with tarfile.open(tb, "w:gz") as t:
        for entrada in ("app1/x", "app2/y"):
            datos = b"x" * 32
            info = tarfile.TarInfo(entrada)
            info.size = len(datos)
            t.addfile(info, io.BytesIO(datos))
    reiniciar()
    main.ruta = str(tb)
    mensaje, ok = main.analizar()
    if ok:
        r = subprocess.run(["tar"] + main.comando_tar, capture_output=True, text=True)
        ok = r.returncode == 0
    comprobar(ok and main.extraido not in ("bin", "usr", "lib")
              and not (OPT / "bin").exists() and not (OPT / "usr").exists()
              and not (OPT / "lib").exists(),
              f"{nombre_tar}: NO crea /opt/bin|usr|lib",
              f"extraido={main.extraido!r}")

# =====================================================================
print()
print("=" * 90)
print("2) ELECCIÓN DE EJECUTABLE: buscar_ejecutables()")
print("=" * 90)

reiniciar()
main.extraido = "myapp"
raiz = OPT / "myapp"
for ruta_f, modo in [
    ("myapp", 0o755),           # raíz
    ("bin/helper", 0o755),      # bin/
    ("lib/libfoo.so", 0o755),   # .so -> excluido
    ("lib/interno", 0o755),     # lib -> última prioridad
    ("share/datos.txt", 0o644), # sin bit X -> excluido
    ("share/pixmaps/otro.sh", 0o644),
]:
    p = raiz / ruta_f
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("hola")
    p.chmod(modo)

encontrados = [str(p.relative_to(raiz)) for p in main.buscar_ejecutables()]
comprobar("lib/libfoo.so" not in encontrados, "excluye las .so", str(encontrados))
comprobar("share/datos.txt" not in encontrados, "excluye sin bit de ejecución", "")
comprobar(encontrados[0] == "myapp", "el de la raíz va primero", str(encontrados))
comprobar(encontrados.index("bin/helper") < encontrados.index("lib/interno"),
          "bin/ antes que lib/", str(encontrados))

# =====================================================================
print()
print("=" * 90)
print("3) ICONO: logo_del_app() (antes solo miraba la raíz)")
print("=" * 90)


def caso_icono(nombre: str, ficheros: list[str], esperado: str | None) -> None:
    reiniciar()
    main.extraido = "myapp"
    for f in ficheros:
        p = OPT / "myapp" / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG")
    obtenido = main.logo_del_app()
    obtenido_str = str(obtenido.relative_to(OPT / "myapp")) if obtenido else None
    comprobar(obtenido_str == esperado, nombre, f"={obtenido_str!r}")


caso_icono("icono solo en hicolor (lo típico real)",
           ["share/icons/hicolor/256x256/apps/myapp.png"], "share/icons/hicolor/256x256/apps/myapp.png")
caso_icono("elige 256x256 antes que 16x16",
           ["share/icons/hicolor/16x16/apps/myapp.png",
            "share/icons/hicolor/256x256/apps/myapp.png"],
           "share/icons/hicolor/256x256/apps/myapp.png")
caso_icono("prefiere svg escalable",
           ["share/icons/hicolor/48x48/apps/myapp.png",
            "share/icons/hicolor/scalable/apps/myapp.svg"],
           "share/icons/hicolor/scalable/apps/myapp.svg")
caso_icono("logo de la raíz gana a una imagen lejana sin nombre útil",
           ["logo.png", "share/pixmaps/fondo.png"], "logo.png")
caso_icono("el icono real (hicolor, nombre=app) gana a uno genérico de la raíz",
           ["foto.png", "share/icons/hicolor/256x256/apps/myapp.png"],
           "share/icons/hicolor/256x256/apps/myapp.png")
caso_icono("sin ningún icono -> None (usa TarGzLogo)",
           ["share/doc/leeme.txt"], None)

# =====================================================================
print()
print("=" * 90)
print("4) LANZADOR: escribir_desktop()")
print("=" * 90)

reiniciar()
main.extraido = "myapp"
(raiz := OPT / "myapp").mkdir(parents=True)
(raiz / "myapp").write_text("#!/bin/sh")
(raiz / "myapp").chmod(0o755)
(raiz / "logo.png").write_bytes(b"\x89PNG")

# a) el nuestro
destino = main.escribir_desktop(raiz / "myapp", True, [])
texto = destino.read_text()
comprobar(destino.name == "myapp.desktop" and destino.parent == LANZ,
          "se escribe en DIR_LANZADORES", str(destino))
comprobar(f"Exec={raiz / 'myapp'}" in texto, "Exec apunta al binario elegido", "")
comprobar("Terminal=true" in texto, "respeta Terminal=true", "")
comprobar(f"Icon={raiz / 'logo.png'}" in texto, "Icon usa el logo de la app", "")
comprobar("Categories=HelmTool;" in texto, "mantiene la categoría HelmTool", "")

# b) el del paquete: se conserva lo suyo y se arregla Exec/Icon/Terminal
lineas = [
    "[Desktop Entry]",
    "Type=Application",
    "Name=Mi App Bonita",
    "Comment=un comentario",
    "Exec=miapp %U --bandera",
    "Icon=miapp-otro",
    "Terminal=false",
    "Categories=Utility;Game;",
    "",
    "[Desktop Action otra]",
    "Exec=miapp --secundario",
]
destino = main.escribir_desktop(raiz / "myapp", True, lineas)
texto = destino.read_text()
comprobar("Name=Mi App Bonita" in texto, "conserva Name del paquete", "")
comprobar("Comment=un comentario" in texto, "conserva Comment", "")
comprobar(f"Exec={raiz / 'myapp'} %U --bandera" in texto,
          "arregla Exec pero conserva argumentos", "")
comprobar("Exec=miapp --secundario" in texto, "NO toca el Exec de las Actions", "")
comprobar("Terminal=true" in texto, "cambia Terminal a true", "")
comprobar(f"Icon={raiz / 'logo.png'}" in texto, "sustituye el Icon que no existía", "")
comprobar("Categories=Utility;Game;" in texto, "conserva Categories original", "")

# c) reinstalar sobrescribe (antes se saltaba si ya existía)
main.escribir_desktop(raiz / "myapp", False, [])
texto2 = destino.read_text()
comprobar("Terminal=false" in texto2 and "Terminal=true" not in texto2,
          "al reinstalar se ACTUALIZA el .desktop", "")

# d) comillas si la ruta lleva espacio, conservando argumentos
main.extraido = "mi app"
con_espacio = Path("/opt/mi app/bin/x")
obtenido = main._exec_para(con_espacio, "miapp %U")
comprobar(obtenido == f'"{con_espacio}" %U',
          "Exec entre comillas si hay espacios", obtenido)

# e) y si el Exec del paquete YA venía entre comillas, no parte el camino
obtenido = main._exec_para(con_espacio, '"/opt/otra app/x" --flag')
comprobar(obtenido == f'"{con_espacio}" --flag',
          "interpreta un Exec ya entrecomillado", obtenido)

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
