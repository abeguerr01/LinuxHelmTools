"""ulxOptAppManager — gestor de las aplicaciones instaladas en /opt.

Muestra las carpetas de /opt (donde quedan las apps instaladas con
ulxTarGzInstaller, o a mano con cualquier .tar.gz) y, de cada una, enseña
sus ejecutables, sus lanzadores .desktop, su icono y su tamaño.

Además puede LANZAR el ejecutable de la app seleccionada (Execute, como
usuario normal), crear el lanzador de menú que le falte (se detecta al
escanear y se ofrece) y BORRAR la app: pide confirmación con detalles,
ejecuta `pkexec rm -rf` (una sola petición de contraseña) y elimina los
lanzadores del menú que apuntaban a ella.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

# Dónde mira. Constante para poder testear sin tocar /opt de verdad.
RAIZ_OPT: Path = Path("/opt")

# Dónde están los lanzadores del menú del usuario: al borrar una app se
# quitan también los .desktop que apuntan a ella. Constante para testear.
DIR_LANZADORES: Path = Path.home() / ".local" / "share" / "applications"

# Lanzadores del menú donde buscar el Icon= de una app cuando el .desktop
# no vive dentro de su carpeta (lo normal: se instaló un .deb a mano y su
# .desktop fue a /usr/share/applications). Constantes para testear.
DIR_LANZADORES_SISTEMA: Path = Path("/usr/share/applications")

# Temas de iconos del sistema: donde el menú resuelve los Icon= que son
# NOMBRES (brave-desktop, gh-desktop-plus…). Se miran de mayor a menor y
# del usuario al sistema. Constante para testear.
TEMAS_SISTEMA: tuple[Path, ...] = (
    Path.home() / ".local" / "share" / "icons" / "hicolor",
    Path("/usr/share/icons/hicolor"),
)

# Los recursos empaquetados (los PNG) viven junto a este script. Con
# PyInstaller --onefile se extraen a sys._MEIPASS (un temporal que se borra
# al cerrar); al ejecutar desde el fuente, junto a main.py.
DIR_RECURSOS: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# Logo de la ventana y de la cabecera. Es OPCIONAL: si no hay AppLogo.png,
# la app funciona igual sin logo (build.sh solo lo embebe si existe).
RUTA_LOGO: Path = DIR_RECURSOS / "AppLogo.png"
logo: tk.PhotoImage | None = None          # icono de la ventana (GC de Tk)
logo_cabecera: tk.PhotoImage | None = None  # logo pequeño de la cabecera
logo_app: tk.PhotoImage | None = None      # icono del panel de detalles

# ----- Paleta: la misma que ulxTarGzInstaller -----
FONDO: str = "#eef1f6"        # gris claro (fondo de la ventana)
TARJETA: str = "#ffffff"      # tarjeta blanca encima del gris
BORDE: str = "#d7dee7"        # borde suave de las tarjetas
AZUL: str = "#1f6fd0"         # azul principal: títulos y botones
AZUL_HOVER: str = "#1759ab"   # azul más oscuro al pasar el ratón
AZUL_CLARO: str = "#dceafd"   # azul muy claro, para acentos
TEXTO: str = "#26313d"        # texto normal
TEXTO_SUAVE: str = "#6a7887"  # texto secundario
VERDE: str = "#1a8a3c"        # mensajes de éxito
ROJO: str = "#c62828"         # mensajes de error y botón de borrar
ROJO_HOVER: str = "#a52121"   # rojo más oscuro al pasar el ratón
AMBAR: str = "#b26a00"        # avisos

# Color de cada mensaje según su primer símbolo
COLORES_MENSAJE: dict[str, str] = {"✓": VERDE, "✗": ROJO, "⚠": AMBAR, "⏳": AZUL}


@dataclass
class Aplicacion:
    """Una carpeta de /opt y lo que contiene."""
    nombre: str
    ruta: Path
    legible: bool = True
    ejecutables: list[Path] = field(default_factory=list[Path])  # relativas
    escritorios: list[Path] = field(default_factory=list[Path])  # relativas
    icono: Path | None = None        # relativa a la raíz; absoluta si es del sistema
    lanzador: Path | None = None     # su .desktop del menú (absoluta); None = no está en el menú
    tamano: int | None = None                               # bytes; None = ilegible


# Los hilos de trabajo dejan aquí lo que la interfaz debe pintar
cola: queue.Queue[Callable[[], None]] = queue.Queue()

# Estado que llena el hilo de escaneo y lee la interfaz
aplicaciones: list[Aplicacion] = []
aplicaciones_por_nombre: dict[str, Aplicacion] = {}

# ¿Ya se ha preguntado en esta sesión por crear los lanzadores que faltan?
# Solo se pregunta una vez: si el usuario dice que no, no se insiste.
_preguntado_lanzadores: bool = False

# Widgets creados por crear_ventana() y usados después por los callbacks
ventana: tk.Tk
lista: ttk.Treeview
etiqueta_detalles: tk.Label
etiqueta_icono: tk.Label
etiquetaConfir: tk.Label
boton: tk.Button
btn_abrir: tk.Button
btn_ejecutar: tk.Button
btn_borrar: tk.Button


# ============================================================
# Escaneo (toda la lógica es pura: funciona contra cualquier raíz)
# ============================================================
def es_aplicacion(app: Aplicacion) -> bool:
    """¿Tiene pinta de app (ejecutables o lanzadores) o es solo una carpeta?"""
    return bool(app.ejecutables or app.escritorios)


def ejecutable_de(app: Aplicacion) -> Path | None:
    """El ejecutable más probable para lanzar la app (relativo a su raíz).

    No basta con el primero de la lista: en brave-bin hay chingueras
    (chrome-sandbox, crashpad handler…) antes que `brave`. Se prefiere,
    por este orden:
      1. El que se llama como la carpeta (sin versión): desktop-plus.
      2. El que lleva el nombre de la carpeta como ficha: brave-bin ->
         «brave» («bin» es genérica y no cuenta).
      3. El primero de la lista, ya ordenada, como último recurso.
    """
    if not app.ejecutables:
        return None
    base = re.sub(r"[-_]?\d[\d._-]*$", "", app.nombre) or app.nombre
    for ejecutable in app.ejecutables:
        if ejecutable.stem == base:
            return ejecutable
    fichas = _fichas(base)
    for ejecutable in app.ejecutables:
        if ejecutable.stem in fichas:
            return ejecutable
    return app.ejecutables[0]


def _es_ejecutable(fichero: Path) -> bool:
    """Fichero con bit de ejecución, que no sea una biblioteca .so."""
    return (fichero.is_file() and os.access(fichero, os.X_OK)
            and fichero.suffix != ".so")


def buscar_ejecutables(raiz: Path) -> list[Path]:
    """Ejecutables en la raíz y en bin/ y sbin/ (sin entrar más profundo:
    en apps grandes hay miles de binarios de apoyo en lib/ que solo
    ensucian el listado)."""
    encontrados: list[Path] = []
    try:
        for fichero in raiz.iterdir():
            if _es_ejecutable(fichero):
                encontrados.append(fichero.relative_to(raiz))
        for subcarpeta in ("bin", "sbin"):
            carpeta = raiz / subcarpeta
            if carpeta.is_dir():
                for fichero in carpeta.iterdir():
                    if _es_ejecutable(fichero):
                        encontrados.append(fichero.relative_to(raiz))
    except OSError:
        pass
    return sorted(encontrados)


def buscar_escritorios(raiz: Path) -> list[Path]:
    """Lanzadores .desktop que trae la propia app (raíz o share/applications)."""
    encontrados: list[Path] = []
    try:
        for fichero in raiz.glob("*.desktop"):
            if fichero.is_file():
                encontrados.append(fichero.relative_to(raiz))
        carpeta = raiz / "share" / "applications"
        if carpeta.is_dir():
            for fichero in carpeta.glob("*.desktop"):
                if fichero.is_file():
                    encontrados.append(fichero.relative_to(raiz))
    except OSError:
        pass
    return sorted(encontrados)


def _iconos_de(ruta_fichero: Path) -> list[str]:
    """Valores del campo Icon= de un .desktop (en orden, sin repetidos)."""
    try:
        contenido = ruta_fichero.read_text(errors="replace")
    except OSError:
        return []
    valores: list[str] = []
    for linea in contenido.splitlines():
        if linea.startswith("Icon="):
            valor = linea.split("=", 1)[1].strip()
            if valor and valor not in valores:
                valores.append(valor)
    return valores


def _iconos_de_escritorios(raiz: Path, escritorios: list[Path]) -> list[str]:
    """Valores del campo Icon= de los .desktop de la propia app.

    Es EL icono que el escritorio muestra en su menú de aplicaciones, así
    que es lo primero que se intenta. Se devuelven en el orden en que
    aparecen (el primero es el que manda).
    """
    valores: list[str] = []
    for relativo in escritorios:
        for valor in _iconos_de(raiz / relativo):
            if valor not in valores:
                valores.append(valor)
    return valores


def _fichas(texto: str) -> set[str]:
    """Fichas del nombre de una app o lanzador, para compararlas.

    Minúsculas y separadas por todo lo que no sea letra o dígito, con las
    genéricas fuera («bin», «app»…) y las de menos de 4 caracteres
    (versiones y siglas sueltas no dicen nada).
    """
    genericas = {"bin", "app", "apps", "run", "exec", "opt", "usr", "lib"}
    return {f for f in re.split(r"[^a-z0-9]+", texto.lower())
            if len(f) >= 4 and f not in genericas}


def _lanzadores_del_menu(nombre: str) -> list[Path]:
    """Lanzadores del menú (usuario/sistema) que apuntan a esta app.

    El .desktop de muchas apps de /opt NO vive en la carpeta: se instaló
    a mano y su lanzador quedó en ~/.local/share/applications o en
    /usr/share/applications. Dos señales de que un .desktop es de esta
    app, por este orden:
      - fuerte: su Exec=/Path= menciona /opt/<nombre>;
      - débil: su nombre y su Name= contienen TODAS las fichas del
        nombre de la carpeta (brave-bin -> «brave», que «bin» es
        genérica; desktop-plus -> «desktop» y «plus»).
    Devuelve rutas absolutas, del usuario al sistema.
    """
    carpetas = (DIR_LANZADORES, DIR_LANZADORES_SISTEMA)  # al vuelo, testeable
    # sin la versión: «jetbrains-toolbox-3.8.1.88030» -> «jetbrains-toolbox»
    base = re.sub(r"[-_]?\d[\d._-]*$", "", nombre) or nombre
    fichero_fichas = _fichas(base)
    fuertes: list[Path] = []
    debiles: list[Path] = []
    for directorio in carpetas:
        try:
            with os.scandir(directorio) as contenido:
                entradas = sorted(contenido, key=lambda e: e.name)
        except OSError:
            continue
        for entrada in entradas:
            if not entrada.name.endswith(".desktop") or not entrada.is_file():
                continue
            ruta = Path(entrada.path)
            try:
                texto = ruta.read_text(errors="replace")
            except OSError:
                continue
            referencia_hecha = False
            for linea in texto.splitlines():
                if linea.startswith(("Exec=", "Path=", "TryExec=")):
                    if f"/opt/{nombre}" in linea.split("=", 1)[1]:
                        referencia_hecha = True
                        break
            if referencia_hecha:
                fuertes.append(ruta)
                continue
            if not fichero_fichas:
                continue
            # el nombre del fichero y su Name= tienen que decir «de qué va»
            nombre_fichero = ruta.stem
            name = next((linea.split("=", 1)[1] for linea in texto.splitlines()
                         if linea.startswith("Name=")), "")
            lanzador_fichas = _fichas(f"{nombre_fichero} {name}")
            if fichero_fichas <= lanzador_fichas:
                debiles.append(ruta)
    return fuertes + debiles


def _icono_sistema(valor: str) -> Path | None:
    """Resuelve un Icon= del menú en los temas del sistema (absoluta).

    Acepta ruta absoluta (si existe tal cual) o nombre de icono, que se
    busca en los hicolor del usuario y del sistema, de mayor a menor y
    con .png antes que .svg (Tk solo sabe pintar PNG).
    """
    if valor.startswith("/"):
        ruta = Path(valor)
        return ruta if ruta.is_file() else None
    tamaños = ("scalable", "512x512", "256x256", "128x128", "64x64",
               "48x48", "32x32", "24x24", "16x16")
    # PNG antes que SVG: Tk solo sabe pintar PNG, y de PNG, el grande
    for extension in (".png", ".svg"):
        for tamano in tamaños:
            for tema in TEMAS_SISTEMA:
                candidato = tema / tamano / "apps" / f"{valor}{extension}"
                if candidato.is_file():
                    return candidato
    return None


def _en_tema(raiz: Path, nombre_icono: str) -> Path | None:
    """Busca un nombre de icono en los temas que trae la propia app.

    Recorre share/icons/hicolor de MAYOR a menor (lo escalable primero,
    que es lo que usa el escritorio para pintar nítido), y después
    share/pixmaps. Devuelve la ruta relativa a la raíz de la app.
    """
    hicolor = raiz / "share" / "icons" / "hicolor"
    for tamano in ("scalable", "256x256", "128x128", "64x64",
                   "48x48", "32x32", "24x24", "16x16"):
        for extension in (".png", ".svg", ".xpm"):
            candidato = hicolor / tamano / "apps" / f"{nombre_icono}{extension}"
            if candidato.is_file():
                return candidato.relative_to(raiz)
    for extension in (".png", ".svg", ".xpm"):
        candidato = raiz / "share" / "pixmaps" / f"{nombre_icono}{extension}"
        if candidato.is_file():
            return candidato.relative_to(raiz)
    return None


def buscar_icono(raiz: Path, nombre: str, escritorios: list[Path] | None = None,
                 menu: list[Path] | None = None
                 ) -> Path | None:
    """El icono más representativo de la app.

    Relativo a su raíz, salvo el del menú del sistema (1b), que se
    devuelve como ruta absoluta (al pintarlo se une con la raíz y una
    ruta absoluta a la derecha manda, así que funciona igual).

    Orden, de mejor a peor fuente:
      1. El Icon= de los .desktop de la propia app: es literalmente el
         icono del menú de aplicaciones del escritorio.
      1b. El Icon= del lanzador del menú que apunta a /opt/<nombre>
          (el .desktop suele vivir en el sistema, no en la app).
      2. El nombre de la carpeta (quitándole la versión si la trae),
         buscado en TODOS los tamaños del tema que trae la app.
      3. Cualquier icono de hicolor que traiga la app (nombre que no
         coincide con la carpeta).
      4. Un PNG de la raíz.
    """
    # 1) el icono que el .desktop declara para el menú
    if escritorios:
        for valor in _iconos_de_escritorios(raiz, escritorios):
            # ¿es una ruta absoluta? si apunta dentro de la app, se usa
            if valor.startswith("/"):
                ruta = Path(valor)
                try:
                    relativo = ruta.relative_to(raiz)
                except ValueError:
                    continue          # apunta fuera de la app: se ignora
                if (raiz / relativo).is_file():
                    return relativo
            else:                      # nombre de icono: buscarlo en su tema
                hallado = _en_tema(raiz, valor)
                if hallado is not None:
                    return hallado

    # 1b) el icono del lanzador del menú (vive fuera de la app)
    if menu is None:              # si no lo ha traído el escaneo, buscarlo
        menu = _lanzadores_del_menu(nombre)
    for lanzador in menu:
        for valor in _iconos_de(lanzador):
            hallado = _icono_sistema(valor)
            if hallado is not None:
                return hallado

    # 2) el nombre de la carpeta (con y sin versión)
    base = re.sub(r"[-_]?\d[\d._-]*$", "", nombre) or nombre
    nombres = [nombre] if base == nombre else [nombre, base]
    for n in nombres:
        hallado = _en_tema(raiz, n)
        if hallado is not None:
            return hallado
        if (raiz / f"{n}.png").is_file():
            return Path(f"{n}.png")
    if (raiz / "logo.png").is_file():
        return Path("logo.png")

    # 3) el primer icono de hicolor que traiga la app, del tamaño que sea
    hicolor = raiz / "share" / "icons" / "hicolor"
    try:
        for tamano in sorted(hicolor.iterdir()):
            carpeta = tamano / "apps"
            if carpeta.is_dir():
                for fichero in sorted(carpeta.iterdir()):
                    if fichero.suffix.lower() in (".png", ".svg", ".xpm"):
                        return fichero.relative_to(raiz)
    except OSError:
        pass
    try:  # 4) cualquier PNG de la raíz sirve como último recurso
        for fichero in sorted(raiz.iterdir()):
            if fichero.is_file() and fichero.suffix.lower() == ".png":
                return fichero.relative_to(raiz)
    except OSError:
        pass
    return None


def peso_de(raiz: Path) -> int:
    """Tamaño en bytes de todo el árbol (ignora lo ilegible)."""
    total = 0
    for carpeta, _subcarpetas, ficheros in os.walk(raiz):
        for nombre_fichero in ficheros:
            try:
                total += os.stat(os.path.join(carpeta, nombre_fichero)).st_size
            except OSError:
                pass
    return total


def inspeccionar(ruta: Path) -> Aplicacion:
    """Lee una carpeta de /opt y devuelve todo lo que interesa de ella."""
    nombre = ruta.name
    try:
        with os.scandir(ruta) as contenido:
            next(contenido, None)   # basta con poder abrirla
    except OSError:                 # permiso denegado u otra razón
        return Aplicacion(nombre=nombre, ruta=ruta, legible=False)

    escritorios = buscar_escritorios(ruta)
    menu = _lanzadores_del_menu(nombre)   # una sola pasada: icono 1b + campo
    return Aplicacion(
        nombre=nombre,
        ruta=ruta,
        ejecutables=buscar_ejecutables(ruta),
        escritorios=escritorios,
        icono=buscar_icono(ruta, nombre, escritorios, menu),
        lanzador=menu[0] if menu else None,
        tamano=peso_de(ruta),
    )


def escanear(raiz: Path) -> list[Aplicacion]:
    """Lista las aplicaciones instaladas en `raiz`, ordenadas por nombre.

    Los ficheros sueltos que haya en /opt se ignoran (no son apps) y las
    carpetas que no se pueden leer se marcan como ilegibles, no tronan.
    """
    try:
        entradas = sorted(raiz.iterdir())
    except OSError:
        return []
    return [inspeccionar(e) for e in entradas if e.is_dir()]


def tamano_legible(bytes_: int) -> str:
    """1234567 -> '1.2 MB'."""
    valor = float(bytes_)
    unidad = "B"
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if valor < 1024:
            break
        valor /= 1024
    return f"{valor:.0f} {unidad}" if unidad == "B" else f"{valor:.1f} {unidad}"


def texto_detalles(app: Aplicacion) -> str:
    """Texto del panel derecho para una aplicación."""
    if not app.legible:
        return f"✗ Cannot read this folder (permission denied)\n\nPath: {app.ruta}"

    lineas = [f"Path: {app.ruta}"]
    if app.tamano is not None:
        lineas.append(f"Size: {tamano_legible(app.tamano)}")
    try:
        fecha = time.strftime("%Y-%m-%d %H:%M",
                              time.localtime(app.ruta.stat().st_mtime))
        lineas.append(f"Modified: {fecha}")
    except OSError:
        pass

    lineas.append("")
    if app.ejecutables:
        lineas.append(f"Executables ({len(app.ejecutables)}):")
        for ejecutable in app.ejecutables[:12]:
            lineas.append(f"  {ejecutable}")
        if len(app.ejecutables) > 12:
            lineas.append(f"  … and {len(app.ejecutables) - 12} more")
    else:
        lineas.append("Executables: none at root, bin/ or sbin/")

    if app.escritorios:
        lineas.append("")
        lineas.append(f"Launchers (.desktop) ({len(app.escritorios)}):")
        for escritorio in app.escritorios[:6]:
            lineas.append(f"  {escritorio}")
        if len(app.escritorios) > 6:
            lineas.append(f"  … and {len(app.escritorios) - 6} more")

    if es_aplicacion(app):
        lineas.append("")
        lineas.append(f"Menu launcher: {app.lanzador.name}" if app.lanzador
                      else "Menu launcher: none (not in the applications menu)")

    if app.icono is not None:
        lineas.append("")
        lineas.append(f"Icon: {app.icono}")

    return "\n".join(lineas)


# ============================================================
# Borrado (lógica pura, comprobable sin root)
# ============================================================
def ruta_borrable(nombre: str) -> Path | None:
    """La ruta a borrar si es segura, o None si no lo es.

    El `rm -rf` va como root, así que lo que se le pase está LA LEGALES:
    se comprueba que `nombre` es un hijo directo de RAIZ_OPT y no puede
    escaparse (vacío, '.', '..', o con barra). Esto protege también si en
    el futuro otro código llama a esta función con un nombre de fuente
    dudosa.
    """
    if not nombre or nombre in (".", "..") or "/" in nombre:
        return None
    ruta = RAIZ_OPT / nombre
    # .resolve() sigue los enlaces: si /opt/app fuera un enlace a /etc,
    # el padre resuelto dejaría de ser /opt y se rechaza
    if ruta.parent != RAIZ_OPT or ruta.resolve().parent != RAIZ_OPT.resolve():
        return None
    return ruta


def lanzadores_de(nombre: str, dir_lanzadores: Path | None = None
                  ) -> list[Path]:
    """Lanzadores .desktop del menú que apuntan a /opt/<nombre>/.

    Busca en los valores de Exec= y Icon= la ruta absoluta de la app, con
    el cierre del camino para no confundir 'myapp' con 'myapp2'.
    """
    destino = f"{RAIZ_OPT}/{nombre}"
    raiz = dir_lanzadores if dir_lanzadores is not None else DIR_LANZADORES
    encontrados: list[Path] = []
    try:
        for fichero in sorted(raiz.glob("*.desktop")):
            try:
                contenido = fichero.read_text(errors="replace")
            except OSError:
                continue
            for linea in contenido.splitlines():
                if not (linea.startswith("Exec=") or linea.startswith("Icon=")):
                    continue
                valor = linea.split("=", 1)[1].strip().strip('"')
                # cierre de camino: o es exactamente /opt/<nombre> o sigue
                # con '/' — así 'myapp' no arrastra a 'myapp2'
                if valor == destino or valor.startswith(destino + "/"):
                    encontrados.append(fichero)
                    break
    except OSError:
        pass
    return encontrados


# ============================================================
# Creación del lanzador que falte (misma receta que ulxTarGzInstaller)
# ============================================================
def _desktop_de(app: Aplicacion, ejecutable: Path) -> str:
    """Contenido del .desktop del menú para una app.

    Mismo formato que el que escribe ulxTarGzInstaller (Categories,
    Name con el nombre de la carpeta…): si el instalador la instaló a
    mano, el resultado en el menú es indistinguible.
    """
    icono = str(app.ruta / app.icono) if app.icono is not None else ""
    programa = str(ejecutable)
    if " " in programa:   # el spec de .desktop usa comillas dobles
        programa = f'"{programa}"'
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={app.nombre}\n"
        f"Exec={programa}\n"
        f"Icon={icono}\n"
        "Terminal=false\n"
        "Categories=HelmTool;\n"
    )


def crear_lanzador(app: Aplicacion) -> Path | None:
    """Escribe el .desktop de la app en el menú del usuario (sin root).

    Lanza su ejecutable preferido (ejecutable_de) con su icono. Actualiza
    app.lanzador para que la interfaz lo pinte sin reescanear. Devuelve
    la ruta escrita, o None si la app no tiene ejecutable que lanzar.
    """
    relativo = ejecutable_de(app)
    if relativo is None:
        return None
    DIR_LANZADORES.mkdir(parents=True, exist_ok=True)
    destino = DIR_LANZADORES / f"{app.nombre}.desktop"
    destino.write_text(_desktop_de(app, app.ruta / relativo), encoding="utf-8")
    app.lanzador = destino
    return destino


# ============================================================
# Hilos e interfaz (mismo patrón que ulxTarGzInstaller)
# ============================================================
def _poner_mensaje(etiqueta: tk.Label, texto: str) -> None:
    """Muestra un mensaje con color según su resultado."""
    etiqueta.config(text=texto, fg=COLORES_MENSAJE.get(texto[:1], TEXTO))


def _boton(parent: tk.Widget, texto: str, comando: Callable[[], None],
           primario: bool = True, peligro: bool = False) -> tk.Button:
    """Botón plano: azul con letra blanca (primario), blanco con letra y
    borde azul (secundario) o rojo con letra blanca (peligro: borra cosas)."""
    if peligro:
        return tk.Button(parent, text=texto, command=comando, bg=ROJO, fg="white",
                         activebackground=ROJO_HOVER, activeforeground="white",
                         relief="flat", bd=0, highlightthickness=0,
                         font=("Arial", 11, "bold"),
                         padx=20, pady=7, cursor="hand2")
    if primario:
        return tk.Button(parent, text=texto, command=comando, bg=AZUL, fg="white",
                         activebackground=AZUL_HOVER, activeforeground="white",
                         relief="flat", bd=0, highlightthickness=0,
                         font=("Arial", 11, "bold"),
                         padx=20, pady=7, cursor="hand2")
    return tk.Button(parent, text=texto, command=comando, bg=TARJETA, fg=AZUL,
                     activebackground=AZUL_CLARO, activeforeground=AZUL_HOVER,
                     relief="flat", bd=0, highlightthickness=1,
                     highlightbackground=AZUL, highlightcolor=AZUL_HOVER,
                     font=("Arial", 11), padx=16, pady=6, cursor="hand2")


def _mini_logo() -> tk.PhotoImage | None:
    """Logo reducido a ~30 px para la cabecera, o None si no hay."""
    if not RUTA_LOGO.exists():
        return None
    try:
        original = tk.PhotoImage(file=str(RUTA_LOGO))
        factor = max(1, original.width() // 30)
        return original.subsample(factor, factor)
    except tk.TclError:   # Tk sin soporte de reducción: se prescinde del logo
        return None


def _programar(accion: Callable[[], None]) -> None:
    """Deja una acción en cola para que la ejecute el hilo de Tk."""
    cola.put(accion)


def _drenar_cola() -> None:
    """Ejecuta lo que hayan dejado los hilos. Se replanifica sola."""
    while True:
        try:
            accion = cola.get_nowait()
        except queue.Empty:
            break
        accion()
    ventana.after(50, _drenar_cola)


def _en_hilo(funcion: Callable[[], tuple[str, bool]],
             al_terminar: Callable[[str, bool], None]) -> None:
    """Ejecuta `funcion()` en un hilo y entrega su resultado a la interfaz."""
    def trabajo() -> None:
        try:
            mensaje, exito = funcion()
        except Exception as exc:
            mensaje, exito = f"✗ Error: {exc}", False
        _programar(lambda: al_terminar(mensaje, exito))

    threading.Thread(target=trabajo, daemon=True).start()


def _bloquear_botones(ocupar: bool) -> None:
    """Deshabilita los botones mientras hay trabajo."""
    estado = tk.DISABLED if ocupar else tk.NORMAL
    boton.config(state=estado, bg=("#c3ccd8" if ocupar else AZUL))
    btn_borrar.config(state=estado, bg=("#c3ccd8" if ocupar else ROJO))
    # los de abrir y ejecutar solo se reactivan si hay selección
    # (lo decide la selección)
    if ocupar:
        btn_abrir.config(state=tk.DISABLED)
        btn_ejecutar.config(state=tk.DISABLED)


def _actualizar_botones_seleccion() -> None:
    """Activa o deshabilita Abrir/Ejecutar/Borrar según la selección.

    Es la única decide cómo quedan los tres botones; así todos los
    caminos (seleccionar, refrescar, borrar, ejecutar) acaban igual.
    """
    app = _app_seleccionada()
    if app is None:
        btn_abrir.config(state=tk.DISABLED)
        btn_ejecutar.config(state=tk.DISABLED)
        btn_borrar.config(state=tk.DISABLED)
        return
    btn_abrir.config(state=tk.NORMAL if app.legible else tk.DISABLED)
    btn_ejecutar.config(state=tk.NORMAL if app.ejecutables else tk.DISABLED)
    # se puede borrar aunque la carpeta no sea legible para el usuario
    # (el rm -rf va como root): solo hace falta que sea una ruta válida
    btn_borrar.config(state=tk.NORMAL if ruta_borrable(app.nombre) else tk.DISABLED)


# ============================================================
# Trabajos (van en hilo, no tocan la interfaz)
# ============================================================
def _trabajo_escanear() -> tuple[str, bool]:
    global aplicaciones, aplicaciones_por_nombre
    if not RAIZ_OPT.is_dir():
        return f"✗ {RAIZ_OPT} does not exist", False
    aplicaciones = escanear(RAIZ_OPT)
    aplicaciones_por_nombre = {app.nombre: app for app in aplicaciones}
    if not aplicaciones:
        return f"⚠ No folders found in {RAIZ_OPT}", False
    con_ejec = sum(1 for app in aplicaciones if es_aplicacion(app))
    return (f"✓ {len(aplicaciones)} folders in {RAIZ_OPT}"
            f" · {con_ejec} with executables"), True


def _trabajo_borrar(ruta: Path) -> tuple[str, bool]:
    """Borra la carpeta con root y sus lanzadores del menú.

    El rm -rf va con pkexec: una sola petición de contraseña. Los .desktop
    se borran después, como usuario (están en ~/.local, no hace falta root).
    """
    resultado = subprocess.run(
        ["pkexec", "rm", "-rf", str(ruta)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if resultado.returncode != 0:
        return "✗ Error: " + resultado.stderr.decode(errors="replace"), False

    borrados = 0
    for escritorio in lanzadores_de(ruta.name):
        try:
            escritorio.unlink()
            borrados += 1
        except OSError:
            pass
    extra = f" · {borrados} launcher(s) removed" if borrados else ""
    return f"✓ {ruta.name} deleted{extra}", True


def _trabajo_ejecutar(ruta: Path) -> tuple[str, bool]:
    """Lanza el ejecutable sin esperar a que termine.

    Va como usuario normal (sin pkexec): la app se lanza con los mismos
    permisos que tendría desde el menú del escritorio.
    """
    try:
        proceso = subprocess.Popen(
            [str(ruta)],
            cwd=str(ruta.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"✗ Error: {exc}", False

    # Solo bloquea este hilo: da tiempo a coger un arranque fallido
    time.sleep(0.3)
    if proceso.poll() is not None and proceso.returncode != 0:
        return f"✗ {ruta.name} exited with code {proceso.returncode}", False
    return f"✓ {ruta.name} executed", True


def _trabajo_crear_lanzadores(apps: list[Aplicacion]) -> tuple[str, bool]:
    """Escribe los .desktop que faltan en el menú del usuario (sin root)."""
    creados = 0
    sin_ejecutable: list[str] = []
    error: str | None = None
    for app in apps:
        try:
            if crear_lanzador(app) is None:
                sin_ejecutable.append(app.nombre)
            else:
                creados += 1
        except OSError as exc:   # p. ej. ~/.local inaccesible: sigue igual
            error = str(exc)
            break
    if error is not None:
        return f"✗ Error: {error}", False
    extra = (f" · {len(sin_ejecutable)} skipped (no executable)"
             if sin_ejecutable else "")
    if creados == 0:
        return f"⚠ Nothing created{extra}", False
    return f"✓ {creados} launcher(s) created{extra}", True


# ============================================================
# Callbacks de la interfaz (van en el hilo de Tk)
# ============================================================
def refrescar() -> None:
    """Vuelve a escanear /opt."""
    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfir, f"⏳ Scanning {RAIZ_OPT} ...")
    _en_hilo(_trabajo_escanear, _escaneo_terminado)


def _escaneo_terminado(mensaje: str, exito: bool) -> None:
    lista.delete(*lista.get_children())
    for app in aplicaciones:
        tipo = "app" if es_aplicacion(app) else "folder"
        tamano = tamano_legible(app.tamano) if app.tamano is not None else "—"
        lista.insert("", tk.END, iid=app.nombre, text=app.nombre,
                     values=(tipo, len(app.ejecutables), tamano),
                     tags=() if tipo == "app" else ("carpeta",))
    etiqueta_detalles.config(text="")
    etiqueta_icono.config(image="")
    _bloquear_botones(False)
    _actualizar_botones_seleccion()   # sin selección: los tres a off
    _poner_mensaje(etiquetaConfir, mensaje)
    _preguntar_lanzadores()           # ¿falta meter alguna app en el menú?


def _app_seleccionada() -> Aplicacion | None:
    seleccion = lista.selection()
    if not seleccion:
        return None
    return aplicaciones_por_nombre.get(seleccion[0])


def _seleccion_cambiada() -> None:
    global logo_app
    app = _app_seleccionada()
    if app is None:
        return
    etiqueta_detalles.config(text=texto_detalles(app))
    _actualizar_botones_seleccion()

    # icono del panel de detalles (solo PNG, Tk no abre SVG)
    logo_app = None
    etiqueta_icono.config(image="")
    if app.icono is not None and app.icono.suffix.lower() == ".png":
        try:
            imagen = tk.PhotoImage(file=str(app.ruta / app.icono))
            factor = max(1, imagen.width() // 48)
            logo_app = imagen.subsample(factor, factor)
            etiqueta_icono.config(image=logo_app)
        except tk.TclError:
            pass


def abrir_carpeta() -> None:
    """Abre la carpeta de la app en el gestor de archivos del usuario."""
    app = _app_seleccionada()
    if app is None:
        return
    try:
        subprocess.Popen(["xdg-open", str(app.ruta)])
        _poner_mensaje(etiquetaConfir, f"✓ Opening {app.nombre}")
    except Exception as exc:
        _poner_mensaje(etiquetaConfir, f"✗ Error: {exc}")


def ejecutar() -> None:
    """Lanza el ejecutable de la app seleccionada (sin esperar)."""
    app = _app_seleccionada()
    if app is None:
        return
    relativo = ejecutable_de(app)
    if relativo is None:
        _poner_mensaje(etiquetaConfir, f"✗ No executable found in {app.nombre}")
        return

    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfir, f"⏳ Executing {relativo} ...")
    _en_hilo(lambda: _trabajo_ejecutar(app.ruta / relativo), _ejecucion_terminada)


def _ejecucion_terminada(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    _actualizar_botones_seleccion()   # la selección sigue ahí: se reactivan
    _poner_mensaje(etiquetaConfir, mensaje)


def borrar() -> None:
    """Pide confirmación (con detalles) y borra la app seleccionada."""
    app = _app_seleccionada()
    if app is None:
        return
    ruta = ruta_borrable(app.nombre)
    if ruta is None:
        _poner_mensaje(etiquetaConfir, f"✗ Refused to delete {app.nombre}")
        return

    tamano = tamano_legible(app.tamano) if app.tamano is not None else "?"
    escritorios = lanzadores_de(app.nombre)
    linea_lanz = (f"\n\nIt also has {len(escritorios)} menu launcher(s),"
                  f" which will be removed too."
                  if escritorios else "")
    respuesta = messagebox.askyesno(
        "Delete application",
        f"Do you want to permanently delete\n\n"
        f"    {ruta}\n\n"
        f"Size: {tamano}{linea_lanz}\n\n"
        "This cannot be undone. Continue?",
        icon="warning",
        default=messagebox.NO,
    )
    if not respuesta:
        _poner_mensaje(etiquetaConfir, "✗ Cancelled, nothing changed")
        return

    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfir, f"⏳ Deleting {app.nombre} ...")
    _en_hilo(lambda: _trabajo_borrar(ruta), _borrado_terminado)


def _borrado_terminado(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    _poner_mensaje(etiquetaConfir, mensaje)
    if exito:
        # quitar la fila al momento, sin esperar a un Refresh manual
        seleccion = lista.selection()
        if seleccion:
            lista.delete(seleccion[0])
            lista.selection_remove(*seleccion)
        etiqueta_detalles.config(text="Select an application")
        etiqueta_icono.config(image="")
        _actualizar_botones_seleccion()   # ya no hay selección: los tres off


def _preguntar_lanzadores() -> None:
    """Ofrece crear los .desktop de las apps que no salen en el menú.

    Se pregunta una sola vez por sesión (si el usuario dice que no, en
    los siguientes Refresh no se insiste). El aviso también queda en el
    detalle de cada app («Menu launcher: none»).
    """
    global _preguntado_lanzadores
    pendientes = [app for app in aplicaciones
                  if es_aplicacion(app) and app.lanzador is None]
    if not pendientes or _preguntado_lanzadores:
        return
    _preguntado_lanzadores = True

    nombres = "\n".join(f"    {app.nombre}" for app in pendientes[:6])
    if len(pendientes) > 6:
        nombres += f"\n    … and {len(pendientes) - 6} more"
    respuesta = messagebox.askyesno(
        "Missing menu launchers",
        f"These applications are not in the applications menu:\n\n"
        f"{nombres}\n\n"
        "Create launchers for them now?\n"
        f"(They are created in {DIR_LANZADORES})",
    )
    if not respuesta:
        _poner_mensaje(etiquetaConfir,
                       f"⚠ {len(pendientes)} app(s) not in the menu")
        return

    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfir,
                   f"⏳ Creating {len(pendientes)} launcher(s) ...")
    _en_hilo(lambda: _trabajo_crear_lanzadores(pendientes), _lanzadores_creados)


def _lanzadores_creados(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    if exito:
        app = _app_seleccionada()
        if app is not None:
            etiqueta_detalles.config(text=texto_detalles(app))
    _actualizar_botones_seleccion()
    _poner_mensaje(etiquetaConfir, mensaje)


# ============================================================
# Definición de ventana
# ============================================================
def _scroll_lista(*argumentos: str | int) -> None:
    """Mueve la lista al hacer scroll.

    typeshed declara tk.yview con argumentos "Unknown" (sin tipar), así que
    el acceso al miembro incumple el modo estricto de pyright aunque aquí se
    le pasan argumentos tipados: se silencia esa línea a propósito.
    """
    lista.yview(*argumentos)  # pyright: ignore[reportUnknownMemberType]


def crear_ventana() -> None:
    """Construye la interfaz y entra en el bucle principal."""
    global ventana, lista, etiqueta_detalles, etiqueta_icono
    global etiquetaConfir, boton, btn_abrir, btn_ejecutar, btn_borrar
    global logo, logo_cabecera

    ventana = tk.Tk()
    ventana.title("OPT APP MANAGER")
    ventana.configure(bg=FONDO)
    ventana.geometry("780x470")
    ventana.minsize(640, 380)

    if RUTA_LOGO.exists():
        logo = tk.PhotoImage(file=str(RUTA_LOGO))
        ventana.iconphoto(True, logo)

    # ===== CABECERA AZUL =====
    cabecera = tk.Frame(ventana, bg=AZUL, padx=16, pady=10)
    cabecera.pack(fill=tk.X)
    logo_cabecera = _mini_logo()
    if logo_cabecera is not None:
        tk.Label(cabecera, image=logo_cabecera, bg=AZUL).pack(
            side=tk.LEFT, padx=(0, 10))
    tk.Label(cabecera, text="OPT APP MANAGER", font=("Arial", 15, "bold"),
             bg=AZUL, fg="white").pack(side=tk.LEFT)

    # ===== BARRA: estado + refrescar =====
    barra = tk.Frame(ventana, bg=FONDO, padx=18, pady=12)
    barra.pack(fill=tk.X)

    boton = _boton(barra, "Refresh", refrescar)
    boton.pack(side=tk.LEFT)
    btn_abrir = _boton(barra, "Open folder", abrir_carpeta, primario=False)
    btn_abrir.pack(side=tk.LEFT, padx=(8, 0))
    btn_abrir.config(state=tk.DISABLED)
    btn_ejecutar = _boton(barra, "Execute", ejecutar, primario=False)
    btn_ejecutar.pack(side=tk.LEFT, padx=(8, 0))
    btn_ejecutar.config(state=tk.DISABLED)
    btn_borrar = _boton(barra, "Delete", borrar, peligro=True)
    btn_borrar.pack(side=tk.LEFT, padx=(8, 0))
    btn_borrar.config(state=tk.DISABLED)

    etiquetaConfir = tk.Label(barra, text="", font=("Arial", 10), bg=FONDO,
                              fg=TEXTO, justify="right")
    etiquetaConfir.pack(side=tk.RIGHT)

    # ===== PANELS: lista (izquierda) + detalles (derecha) =====
    paneles = tk.Frame(ventana, bg=FONDO, padx=18)
    paneles.pack(fill=tk.BOTH, expand=True, pady=(0, 18))
    paneles.grid_columnconfigure(0, weight=1)
    paneles.grid_columnconfigure(1, weight=1)
    paneles.grid_rowconfigure(0, weight=1)

    # --- lista de apps (tarjeta blanca) ---
    tarjeta_lista = tk.Frame(paneles, bg=TARJETA, highlightbackground=BORDE,
                             highlightthickness=1, padx=8, pady=8)
    tarjeta_lista.grid(row=0, column=0, sticky="nsew", padx=(0, 9))

    estilo = ttk.Style(paneles)
    estilo.theme_use("clam")
    estilo.configure("App.Treeview", background=TARJETA, fieldbackground=TARJETA,
                     foreground=TEXTO, font=("Arial", 10), rowheight=26,
                     borderwidth=0)
    estilo.configure("App.Treeview.Heading", background=AZUL_CLARO,
                     foreground=TEXTO, font=("Arial", 9, "bold"), relief="flat")
    estilo.map("App.Treeview", background=[("selected", AZUL_CLARO)],
               foreground=[("selected", TEXTO)])

    lista = ttk.Treeview(tarjeta_lista, columns=("tipo", "ejecs", "tamano"),
                         show="tree headings", style="App.Treeview",
                         selectmode="browse")
    lista.heading("#0", text="Application")
    lista.heading("tipo", text="Type")
    lista.heading("ejecs", text="Execs")
    lista.heading("tamano", text="Size")
    lista.column("#0", width=150, stretch=True)
    lista.column("tipo", width=60, anchor="center", stretch=False)
    lista.column("ejecs", width=50, anchor="center", stretch=False)
    lista.column("tamano", width=80, anchor="e", stretch=False)
    lista.tag_configure("carpeta", foreground=TEXTO_SUAVE)

    barra_lista = tk.Scrollbar(tarjeta_lista, command=_scroll_lista)
    lista.configure(yscrollcommand=barra_lista.set)
    lista.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    barra_lista.pack(side=tk.RIGHT, fill=tk.Y)
    lista.bind("<<TreeviewSelect>>", lambda _evento: _seleccion_cambiada())

    # --- detalles (tarjeta blanca) ---
    tarjeta_detalles = tk.Frame(paneles, bg=TARJETA, highlightbackground=BORDE,
                                highlightthickness=1, padx=16, pady=14)
    tarjeta_detalles.grid(row=0, column=1, sticky="nsew", padx=(9, 0))

    tk.Label(tarjeta_detalles, text="Details", font=("Arial", 12, "bold"),
             bg=TARJETA, fg=AZUL).pack(anchor="w")

    fila_icono = tk.Frame(tarjeta_detalles, bg=TARJETA)
    fila_icono.pack(fill=tk.X, pady=(8, 0))
    etiqueta_icono = tk.Label(fila_icono, bg=TARJETA)
    etiqueta_icono.pack(side=tk.LEFT)

    etiqueta_detalles = tk.Label(tarjeta_detalles, text="Select an application",
                                 font=("Arial", 10), bg=TARJETA, fg=TEXTO,
                                 justify="left", anchor="nw", wraplength=330)
    etiqueta_detalles.pack(fill=tk.BOTH, expand=True, pady=(6, 0), anchor="w")

    refrescar()
    ventana.after(50, _drenar_cola)
    ventana.mainloop()


if __name__ == "__main__":
    crear_ventana()
