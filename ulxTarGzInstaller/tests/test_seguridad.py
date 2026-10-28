"""Seguridad de la extracción, más allá de lo que ya cubría test_instalador:

A) SYMLINK: ¿puede un .tar.gz escribir FUERA del destino a través de un
   enlace simbólico? (GNU tar sanea ../ y rutas absolutas —probado—; aquí
   se fija en symlinks relativos y absolutos, que la extracción va como root)
B) Fichero con nombre de raíz de sistema: "bin.tar.gz" con estructura
   contenida no debe acabar creando /opt/bin.
"""
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str = "") -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:58} {detalle}")


print("=" * 96)
print("A) SYMLINK: enlace 'link -> /tmp/...' + fichero escrito a través")
print("=" * 96)

BASE = Path("/tmp/opencode/test_symlink")
shutil.rmtree(BASE, ignore_errors=True)
(BASE / "destino").mkdir(parents=True)
(BASE / "victima").mkdir(parents=True)

# exactamente los flags que usa la herramienta
def extraer(ruta_tar: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tar", "--no-same-owner", "--no-same-permissions", "-xzf", str(ruta_tar),
         "-C", str(BASE / "destino") + "/"],
        capture_output=True, text=True,
    )


def fabricar_enlace(nombre: str, destino: str, contenido: str) -> Path:
    ruta = BASE / nombre
    with tarfile.open(ruta, "w:gz") as t:
        enlace = tarfile.TarInfo("app/link")
        enlace.type = tarfile.SYMTYPE
        enlace.linkname = destino
        t.addfile(enlace)
        datos = contenido.encode()
        fichero = tarfile.TarInfo(f"app/link/{contenido}.txt")
        fichero.size = len(datos)
        t.addfile(fichero, io.BytesIO(datos))
    return ruta


# relativo que escapa del destino
r = extraer(fabricar_enlace("enlace.tar.gz",
                            "../../../../tmp/opencode/test_symlink/victima",
                            "VIA_RELATIVO"))
fuera = BASE / "victima" / "VIA_RELATIVO.txt"
comprobar(not fuera.exists(), "GNU tar NO sigue symlinks relativos que escapan",
          f"exit={r.returncode} fuera={fuera.exists()}")

# absoluto
r = extraer(fabricar_enlace("abs.tar.gz", str(BASE / "victima"), "VIA_ABSOLUTO"))
fuera = BASE / "victima" / "VIA_ABSOLUTO.txt"
comprobar(not fuera.exists(), "GNU tar NO sigue symlinks absolutos",
          f"exit={r.returncode} fuera={fuera.exists()}")

print()
print("=" * 96)
print("B) Fichero con nombre de raíz de sistema")
print("=" * 96)

OPT = Path("/tmp/opencode/opt_seg")
LANZ = Path("/tmp/opencode/lanz_seg")
TARBALLS = Path("/tmp/opencode/tarballs_seg")
shutil.rmtree(OPT, ignore_errors=True)
shutil.rmtree(TARBALLS, ignore_errors=True)
OPT.mkdir(parents=True)
TARBALLS.mkdir(parents=True)
main.RAIZ_OPT = OPT
main.DIR_LANZADORES = LANZ

for nombre in ("bin.tar.gz", "usr.tar.gz", "lib.tar.gz"):
    tb = TARBALLS / nombre
    with tarfile.open(tb, "w:gz") as t:
        for entrada in ("app1/x", "app2/y"):
            d = b"x" * 32
            info = tarfile.TarInfo(entrada)
            info.size = len(d)
            t.addfile(info, io.BytesIO(d))

    shutil.rmtree(OPT, ignore_errors=True)
    OPT.mkdir(parents=True)
    main.ruta = str(tb)
    main.extraido = ""
    main.comando_tar = []
    mensaje, ok = main.analizar()
    if ok:
        r = subprocess.run(["tar"] + main.comando_tar, capture_output=True, text=True)
        ok = r.returncode == 0

    tocados = [n for n in ("bin", "usr", "lib") if (OPT / n).exists()]
    comprobar(ok and main.extraido not in ("bin", "usr", "lib") and not tocados,
              f"{nombre}: NO crea /opt/bin|usr|lib",
              f"extraido={main.extraido!r} tocados={tocados}")

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
