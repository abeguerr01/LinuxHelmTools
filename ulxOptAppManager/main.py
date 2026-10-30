"""ulxOptAppManager — gestor de las aplicaciones instaladas en /opt.

Muestra las carpetas de /opt (donde quedan las apps instaladas con
ulxTarGzInstaller, o a mano con cualquier .tar.gz) y, de cada una, enseña
sus ejecutables, sus lanzadores .desktop, su icono y su tamaño.

Además puede BORRAR la app seleccionada: pide confirmación con detalles,
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
    icono: Path | None = None                               # ruta relativa
    tamano: int | None = None                               # bytes; None = ilegible


# Los hilos de trabajo dejan aquí lo que la interfaz debe pintar
cola: queue.Queue[Callable[[], None]] = queue.Queue()

# Estado que llena el hilo de escaneo y lee la interfaz
aplicaciones: list[Aplicacion] = []
aplicaciones_por_nombre: dict[str, Aplicacion] = {}

# Widgets creados por crear_ventana() y usados después por los callbacks
ventana: tk.Tk
lista: ttk.Treeview
etiqueta_detalles: tk.Label
etiqueta_icono: tk.Label
etiquetaConfir: tk.Label
boton: tk.Button
btn_abrir: tk.Button
btn_borrar: tk.Button


# ============================================================
# Escaneo (toda la lógica es pura: funciona contra cualquier raíz)
# ============================================================
def es_aplicacion(app: Aplicacion) -> bool:
    """¿Tiene pinta de app (ejecutables o lanzadores) o es solo una carpeta?"""
    return bool(app.ejecutables or app.escritorios)


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


def buscar_icono(raiz: Path, nombre: str) -> Path | None:
    """El icono más representativo de la app (relativo a su raíz)."""
    # el nombre de la carpeta suele llevar la versión (myapp-1.0) mientras
    # que el icono se llama solo myapp: se prueba con los dos nombres
    base = re.sub(r"[-_]?\d[\d._-]*$", "", nombre) or nombre
    nombres = [nombre] if base == nombre else [nombre, base]
    for n in nombres:
        for relativo in (f"share/icons/hicolor/256x256/apps/{n}.png",
                         f"share/icons/hicolor/scalable/apps/{n}.svg",
                         f"share/icons/hicolor/48x48/apps/{n}.png",
                         f"share/pixmaps/{n}.png",
                         f"{n}.png"):
            if (raiz / relativo).is_file():
                return Path(relativo)
    if (raiz / "logo.png").is_file():
        return Path("logo.png")
    try:
        # el primer icono de hicolor que traiga la app, si el nombre no coincide
        for carpeta_rel in ("share/icons/hicolor/256x256/apps",
                            "share/icons/hicolor/scalable/apps"):
            carpeta = raiz / carpeta_rel
            if carpeta.is_dir():
                for fichero in sorted(carpeta.iterdir()):
                    if fichero.suffix.lower() in (".png", ".svg"):
                        return fichero.relative_to(raiz)
    except OSError:
        pass
    try:  # cualquier PNG de la raíz sirve como último recurso
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

    return Aplicacion(
        nombre=nombre,
        ruta=ruta,
        ejecutables=buscar_ejecutables(ruta),
        escritorios=buscar_escritorios(ruta),
        icono=buscar_icono(ruta, nombre),
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
    # el de abrir solo se reactiva si hay selección (lo decide la selección)
    if ocupar:
        btn_abrir.config(state=tk.DISABLED)


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
    btn_abrir.config(state=tk.DISABLED)
    btn_borrar.config(state=tk.DISABLED)   # sin selección no hay nada que borrar
    _poner_mensaje(etiquetaConfir, mensaje)


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
    btn_abrir.config(state=tk.NORMAL if app.legible else tk.DISABLED)
    # se puede borrar aunque la carpeta no sea legible para el usuario
    # (el rm -rf va como root): solo hace falta que sea una ruta válida
    btn_borrar.config(state=tk.NORMAL if ruta_borrable(app.nombre) else tk.DISABLED)

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
        etiqueta_detalles.config(text="Select an application")
        etiqueta_icono.config(image="")
        btn_abrir.config(state=tk.DISABLED)
        btn_borrar.config(state=tk.DISABLED)


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
    global etiquetaConfir, boton, btn_abrir, btn_borrar, logo, logo_cabecera

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
