"""Prueba la búsqueda de ICONOS de ulxOptAppManager (sin root, sin GUI):

  1) _iconos_de_escritorios(): qué dice el campo Icon= de los .desktop
  2) _en_tema(): recorrido de share/icons/hicolor (mayor a menor) + pixmaps
  3) buscar_icono(): el orden completo de las 4 fuentes
  4) _lanzadores_del_menu() + _icono_sistema(): la prioridad 1b, el icono
     del menú del sistema (el .desktop NO vive en la app)

La prioridad 1 es "el icono del menú del escritorio" (Icon= del .desktop
de la propia app), que es lo que el usuario pidió ver: se prueba con rutas
absolutas dentro y fuera de la app, y con nombres de icono en temas.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

# Las secciones 1-3 no deben leer el menú real de la máquina: se apunta
# a dos carpetas vacías (la sección 4 usa su propio menú falso).
main.DIR_LANZADORES = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
main.DIR_LANZADORES_SISTEMA = Path(tempfile.mkdtemp(dir="/tmp/opencode"))

fallos = 0


def comprobar(ok: bool, titulo: str, detalle: str = "") -> None:
    global fallos
    fallos += (not ok)
    print(f"{'✓' if ok else '✗'} {titulo:58} {detalle}")


def nueva_app(*ficheros: str) -> Path:
    """Crea una app falsa con los ficheros indicados (rutas relativas)."""
    raiz = Path(tempfile.mkdtemp(dir="/tmp/opencode")) / "myapp-1.0"
    raiz.mkdir()
    for relativo in ficheros:
        destino = raiz / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("[Desktop Entry]\n")  # contenido irrelevante
    return raiz


# =====================================================================
print("=" * 96)
print("1) _iconos_de_escritorios(): qué dice el Icon= de los .desktop")
print("=" * 96)

raiz = nueva_app("myapp.desktop",
                 "share/applications/otro.desktop",
                 "share/applications/sin_icono.desktop")
(raiz / "myapp.desktop").write_text(
    "[Desktop Entry]\nName=My App\nIcon=/opt/myapp-1.0/icono.png\n")
(raiz / "share/applications/otro.desktop").write_text(
    "[Desktop Entry]\nName=Otro\nIcon=myapp\n")          # nombre, no ruta
(raiz / "share/applications/sin_icono.desktop").write_text(
    "[Desktop Entry]\nName=Sin Icon\nExec=myapp\n")       # sin Icon=

valores = main._iconos_de_escritorios(raiz, main.buscar_escritorios(raiz))
comprobar(valores == ["/opt/myapp-1.0/icono.png", "myapp"],
          "lee Icon= de la raíz y de share/applications, en orden",
          str(valores))

# un .desktop ilegible no debe tronar la lectura
malo = raiz / "ilegible.desktop"
malo.write_text("Icon=x\n")
malo.chmod(0o000)
valores = main._iconos_de_escritorios(raiz, [Path("ilegible.desktop")])
malo.chmod(0o644)
comprobar(valores == [], "un .desktop ilegible no trona (se ignora)", str(valores))

print()
print("=" * 96)
print("2) _en_tema(): hicolor de mayor a menor + pixmaps")
print("=" * 96)

raiz = nueva_app()
# solo el tamaño pequeño: si se mirara solo 256x256, esto fallaría
peque = raiz / "share/icons/hicolor/16x16/apps"
peque.mkdir(parents=True)
(peque / "myapp.png").write_text("x")
hallado = main._en_tema(raiz, "myapp")
comprobar(hallado == Path("share/icons/hicolor/16x16/apps/myapp.png"),
          "encuentra hasta 16x16 (no solo 256x256)", str(hallado))

# scalable (SVG) manda sobre PNG del mismo nombre
(raiz / "share/icons/hicolor/scalable/apps").mkdir(parents=True, exist_ok=True)
(raiz / "share/icons/hicolor/scalable/apps/myapp.svg").write_text("x")
hallado = main._en_tema(raiz, "myapp")
comprobar(hallado == Path("share/icons/hicolor/scalable/apps/myapp.svg"),
          "prefiere scalable (SVG) a 16x16 (PNG)", str(hallado))

# pixmaps como último recurso del tema
otra = nueva_app()
(otra / "share/pixmaps").mkdir(parents=True)
(otra / "share/pixmaps/logo.png").write_text("x")
hallado = main._en_tema(otra, "logo")
comprobar(hallado == Path("share/pixmaps/logo.png"), "cae en share/pixmaps", str(hallado))

comprobar(main._en_tema(nueva_app(), "no-existe") is None, "sin icono -> None", "")

print()
print("=" * 96)
print("3) buscar_icono(): el orden completo de las 4 fuentes")
print("=" * 96)

# --- PRIORIDAD 1: el Icon= del .desktop, ruta absoluta dentro de la app ---
raiz = nueva_app("myapp.desktop", "icono.png", "share/icons/hicolor/256x256/apps/myapp.png")
(raiz / "myapp.desktop").write_text(f"Icon={raiz}/icono.png\n")   # ruta real de la app
hallado = main.buscar_icono(raiz, "myapp-1.0", main.buscar_escritorios(raiz))
comprobar(hallado == Path("icono.png"),
          "1) Icon= absoluto DENTRO de la app gana a todo", str(hallado))

# --- Icon= absoluto que apunta FUERA de la app: se ignora, no escapa ---
raiz = nueva_app("myapp.desktop", "share/icons/hicolor/256x256/apps/myapp.png")
(raiz / "myapp.desktop").write_text("Icon=/usr/share/pixmaps/de_otra.png\n")
hallado = main.buscar_icono(raiz, "myapp", main.buscar_escritorios(raiz))
comprobar(hallado == Path("share/icons/hicolor/256x256/apps/myapp.png"),
          "1) Icon= absoluto FUERA de la app se ignora (no escapa)", str(hallado))

# --- PRIORIDAD 1: Icon= como NOMBRE, buscado en el tema de la app ---
raiz = nueva_app("myapp.desktop", "share/icons/hicolor/48x48/apps/otro_nombre.png")
(raiz / "myapp.desktop").write_text("Icon=otro_nombre\n")
hallado = main.buscar_icono(raiz, "myapp", main.buscar_escritorios(raiz))
comprobar(hallado == Path("share/icons/hicolor/48x48/apps/otro_nombre.png"),
          "1) Icon= como nombre se busca en el tema de la app", str(hallado))

# --- PRIORIDAD 2: nombre de la carpeta SIN la versión, en cualquier tamaño ---
raiz = nueva_app()
peque = raiz / "share/icons/hicolor/24x24/apps"
peque.mkdir(parents=True)
(peque / "myapp.png").write_text("x")
hallado = main.buscar_icono(raiz, "myapp-1.0")     # carpeta con versión
comprobar(hallado == Path("share/icons/hicolor/24x24/apps/myapp.png"),
          "2) quita la versión (myapp-1.0 -> myapp) y mira 24x24", str(hallado))

# --- PRIORIDAD 2: myapp.png en la raíz ---
raiz = nueva_app()
(raiz / "myapp.png").write_text("x")
hallado = main.buscar_icono(raiz, "myapp")
comprobar(hallado == Path("myapp.png"), "2) myapp.png en la raíz", str(hallado))

# --- PRIORIDAD 2: logo.png como comodín ---
raiz = nueva_app()
(raiz / "logo.png").write_text("x")
hallado = main.buscar_icono(raiz, "myapp")
comprobar(hallado == Path("logo.png"), "2) logo.png en la raíz", str(hallado))

# --- PRIORIDAD 3: cualquier icono de hicolor, del tamaño que sea ---
raiz = nueva_app()
otro = raiz / "share/icons/hicolor/32x32/apps"
otro.mkdir(parents=True)
(otro / "algo_distinto.png").write_text("x")
hallado = main.buscar_icono(raiz, "myapp")
comprobar(hallado == Path("share/icons/hicolor/32x32/apps/algo_distinto.png"),
          "3) cualquier icono de hicolor (nombre distinto)", str(hallado))

# --- PRIORIDAD 4: cualquier PNG de la raíz ---
raiz = nueva_app()
(raiz / "foto.png").write_text("x")
hallado = main.buscar_icono(raiz, "myapp")
comprobar(hallado == Path("foto.png"), "4) cualquier PNG de la raíz", str(hallado))

# --- nada: None ---
hallado = main.buscar_icono(nueva_app(), "myapp")
comprobar(hallado is None, "sin nada -> None", "")

print()
print("=" * 96)
print("4) prioridad 1b: el Icon= del lanzador del menú (fuera de la app)")
print("=" * 96)

# Menú falso: usuario + sistema, y un tema falso del sistema
menu_usuario = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
menu_sistema = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
main.DIR_LANZADORES = menu_usuario
main.DIR_LANZADORES_SISTEMA = menu_sistema
tema = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
main.TEMAS_SISTEMA = (tema,)


def lanzador(directorio: Path, nombre: str, *lineas: str) -> Path:
    """Escribe un .desktop falso con las líneas indicadas."""
    fichero = directorio / nombre
    fichero.write_text("[Desktop Entry]\n" + "\n".join(lineas) + "\n")
    return fichero


# --- señal FUERTE: el Exec= apunta a /opt/<nombre> aunque el nombre no ---
lanzador(menu_sistema, "otra-cosa.desktop",
         "Name=Otra Cosa", "Exec=/opt/myapp-1.0/bin/myapp %u")
comprobar(main._lanzadores_del_menu("myapp-1.0") == [menu_sistema / "otra-cosa.desktop"],
          "fuerte: Exec=/opt/<nombre> casa aunque el nombre no", "")

# --- señal DÉBIL: el nombre y el Name= traen las fichas de la carpeta ---
main.DIR_LANZADORES_SISTEMA = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
lanzador(main.DIR_LANZADORES_SISTEMA, "brave-browser.desktop",
         "Name=Brave", "Exec=brave %U")
lanzador(main.DIR_LANZADORES_SISTEMA, "no-es.desktop",
         "Name=Sin Relation", "Exec=otra-cosa")
comprobar(main._lanzadores_del_menu("brave-bin")
          == [main.DIR_LANZADORES_SISTEMA / "brave-browser.desktop"],
          "débil: brave-bin casa con brave-browser («bin» es genérica)", "")

# --- la versión del nombre no cuenta como ficha ---
lanzador(main.DIR_LANZADORES_SISTEMA, "jetbrains-toolbox.desktop",
         "Name=JetBrains Toolbox", "Exec=jetbrains-toolbox %u")
comprobar(main._lanzadores_del_menu("jetbrains-toolbox-3.8.1.88030")
          == [main.DIR_LANZADORES_SISTEMA / "jetbrains-toolbox.desktop"],
          "quita la versión antes de comparar (3.8.1.88030 no es ficha)", "")

# --- solo fichas genéricas: sin señales débiles, solo el /opt/ ---
lanzador(main.DIR_LANZADORES_SISTEMA, "bin.desktop", "Name=Bin")
comprobar(main._lanzadores_del_menu("bin") == [],
          "fichas todas genéricas -> solo casa el Exec=/opt/", "")

# --- el usuario manda sobre el sistema; los fuertes sobre los débiles ---
main.DIR_LANZADORES = Path(tempfile.mkdtemp(dir="/tmp/opencode"))
lanzador(main.DIR_LANZADORES, "brave.desktop", "Name=Brave", "Exec=brave")
comprobar(main._lanzadores_del_menu("brave-bin")
          == [main.DIR_LANZADORES / "brave.desktop",
              main.DIR_LANZADORES_SISTEMA / "brave-browser.desktop"],
          "usuario antes que sistema", "")
main.DIR_LANZADORES = menu_usuario

# --- _icono_sistema(): ruta absoluta existente / nombre en el tema ---
solto = Path(tempfile.mkdtemp(dir="/tmp/opencode")) / "suelto.png"
solto.write_text("x")
comprobar(main._icono_sistema(str(solto)) == solto,
          "ruta absoluta existente se usa tal cual", str(solto))
comprobar(main._icono_sistema(str(solto) + "inexistente") is None,
          "ruta absoluta que no existe -> None", "")

grande = tema / "512x512" / "apps"
grande.mkdir(parents=True)
(grande / "icono.png").write_text("x")
peque = tema / "16x16" / "apps"
peque.mkdir(parents=True)
(peque / "icono.png").write_text("x")
comprobar(main._icono_sistema("icono") == grande / "icono.png",
          "nombre en el tema: el tamaño grande gana", "")
# si solo hay SVG (Tk no lo pinta, pero es lo único que hay), se usa
(tema / "scalable" / "apps").mkdir(parents=True)
(tema / "scalable" / "apps" / "solo_svg.svg").write_text("x")
comprobar(main._icono_sistema("solo_svg")
          == tema / "scalable" / "apps" / "solo_svg.svg",
          "si solo hay SVG, se usa el SVG", "")
comprobar(main._icono_sistema("no-existe") is None, "nombre sin icono -> None", "")

# --- integración: buscar_icono() con el lanzador del menú ---
app = nueva_app()                # sin ningún .desktop ni PNG dentro
lanzador(main.DIR_LANZADORES_SISTEMA, "myapp.desktop",
         "Name=My App", "Exec=/opt/myapp-1.0/myapp", "Icon=icono")
comprobar(main.buscar_icono(app, "myapp-1.0") == grande / "icono.png",
          "1b) icono del menú del sistema, ruta ABSOLUTA", "")

# si el Icon= del lanzador no resuelve, se cae a las fuentes de siempre
lanzador(main.DIR_LANZADORES_SISTEMA, "myapp.desktop",
         "Name=My App", "Exec=/opt/myapp-1.0/myapp",
         "Icon=no-esta-por-aqui")            # se reescribe: icono roto
(app / "logo.png").write_text("x")
comprobar(main.buscar_icono(app, "myapp-1.0") == Path("logo.png"),
          "1b) Icon= irresoluble -> sigue al 2/3/4 (logo.png)", "")

# el lanzador del menú no tapa al .desktop de la propia app (1 > 1b)
app2 = nueva_app("myapp.desktop", "icono.png")
(app2 / "myapp.desktop").write_text(f"Icon={app2}/icono.png\n")
comprobar(main.buscar_icono(app2, "myapp", main.buscar_escritorios(app2))
          == Path("icono.png"),
          "1 (dentro de la app) gana a 1b (menú del sistema)", "")

print()
print(f"{'FALLÓ' if fallos else 'TODO OK'}")
sys.exit(1 if fallos else 0)
