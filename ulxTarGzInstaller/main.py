import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox
from tkinter import ttk

import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

# Donde se instala. Constante para poder testear sin ser root.
RAIZ_OPT: Path = Path("/opt")

# Dónde se dejan los .desktop del usuario. También es conste para testear.
DIR_LANZADORES: Path = Path.home() / ".local" / "share" / "applications"

# Dónde se deja el icono PERMANENTE de los .desktop que crea la herramienta.
# Con --onefile los PNG embebidos se extraen a un temporal que se borra al
# cerrar: si el Icon= apuntara ahí, el lanzador quedaría roto nada más cerrar
# (por eso antes se usaba --onedir). La solución es copiar el logo una vez a
# esta carpeta fija y que el .desktop apunte a esa copia. También es constante
# para testear.
DIR_ICONOS: Path = Path.home() / ".local" / "share" / "icons"

# Raíces que NUNCA se usan como nombre propio: si el .tar.gz trae una de
# estas, se instala dentro de un subdirectorio nuestro en vez de tocar
# /opt/bin, /opt/usr... (que podrían tener cosas de otros paquetes).
RAICES_SISTEMA: set[str] = {
    "bin", "sbin", "lib", "lib32", "lib64", "usr", "etc", "var", "opt",
    "run", "srv", "boot", "dev", "home", "root", "proc", "sys", "tmp",
    "mnt", "media", "share", "include", "local",
}

ruta: str = ""
extraido: str = ""            # nombre real de la carpeta dentro de RAIZ_OPT
comando_tar: list[str] = []   # argv listo, sin pkexec
ya_instalado: bool = False
ejecutable_elegido: Path | None = None

# Los hilos de trabajo dejan aquí lo que la interfaz debe pintar
cola: queue.Queue[Callable[[], None]] = queue.Queue()

# Los recursos empaquetados (los 2 PNG) viven junto a este script. Con
# PyInstaller --onefile se extraen a sys._MEIPASS (un temporal que se borra
# al cerrar), así que ahí hay que buscarlos; al ejecutar desde el fuente,
# junto a main.py. En --onedir apunta a _internal/, igual que antes.
DIR_RECURSOS: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# Logo de la ventana (iconphoto) y logo genérico para los .desktop
RUTA_LOGO: Path = DIR_RECURSOS / "AppLogo.png"
RUTA_LOGO_GENERICO: Path = DIR_RECURSOS / "TarGzLogo.png"
logo: tk.PhotoImage | None = None        # icono de la ventana (GC de Tk)
logo_cabecera: tk.PhotoImage | None = None  # logo pequeño de la cabecera (GC de Tk)

# Formatos que sirven como icono de escritorio
EXTENSIONES_LOGO: set[str] = {".png", ".svg", ".ico", ".xpm", ".bmp", ".webp"}

# ----- Paleta: gris claro de fondo, azul para resaltar -----
FONDO: str = "#eef1f6"        # gris claro (fondo de la ventana)
TARJETA: str = "#ffffff"      # tarjeta blanca encima del gris
BORDE: str = "#d7dee7"        # borde suave de las tarjetas
AZUL: str = "#1f6fd0"         # azul principal: títulos y botones
AZUL_HOVER: str = "#1759ab"   # azul más oscuro al pasar el ratón
AZUL_CLARO: str = "#dceafd"   # azul muy claro, para acentos
TEXTO: str = "#26313d"        # texto normal
TEXTO_SUAVE: str = "#6a7887"  # texto secundario
GRIS_BOTON: str = "#dde3ea"   # botón deshabilitado
VERDE: str = "#1a8a3c"        # mensajes de éxito
ROJO: str = "#c62828"         # mensajes de error
AMBAR: str = "#b26a00"        # avisos

# Color de cada mensaje según su primer símbolo
COLORES_MENSAJE: dict[str, str] = {"✓": VERDE, "✗": ROJO, "⚠": AMBAR, "⏳": AZUL}

# Widgets creados por crear_ventana() y usados después por los callbacks
ventana: tk.Tk
etiqueta: tk.Label
etiquetaConfir: tk.Label
boton: tk.Button
frame_seccion: tk.Frame
btn_abrir: tk.Button
btn_deshacer: tk.Button
etiquetaConfirCond: tk.Label

#region Funciones
# ----- Hilos e interfaz -----
def error_de(resultado: subprocess.CompletedProcess[bytes]) -> str:
    """stderr legible, sin tronar si trae bytes inválidos."""
    return resultado.stderr.decode(errors="replace")


def _poner_mensaje(etiqueta: tk.Label, texto: str) -> None:
    """Muestra un mensaje con color según su resultado.

    ✓ verde (éxito), ✗ rojo (error), ⏳ azul (en marcha), ⚠ ámbar (aviso).
    """
    etiqueta.config(text=texto, fg=COLORES_MENSAJE.get(texto[:1], TEXTO))


def _boton(parent: tk.Widget, texto: str, comando: Callable[[], None],
           primario: bool = True) -> tk.Button:
    """Botón plano: azul con letra blanca (primario) o blanco con letra y
    borde azul (secundario). El color lo mantiene _bloquear_botones().

    El secundario usa el "highlight ring" de Tk en vez de relief, porque
    Tk dibuja relief='solid' en negro y no en el color que le digas.
    """
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
    """Deja una acción en cola para que la ejecute el hilo de Tk.

    Tk solo es seguro desde su propio hilo, así que los hilos de trabajo
    jamás tocan widgets: encolan, y la cola se vacía en el hilo principal.
    """
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
    """Ejecuta `funcion()` en un hilo y entrega su resultado a la interfaz.

    `funcion` corre fuera del bucle de Tk (la ventana sigue viva);
    `al_terminar(mensaje, exito)` se llama siempre en el hilo de Tk.
    """
    def trabajo() -> None:
        try:
            mensaje, exito = funcion()
        except Exception as exc:  # un hilo muerto en silencio no avisa de nada
            mensaje, exito = f"✗ Error: {exc}", False
        _programar(lambda: al_terminar(mensaje, exito))

    threading.Thread(target=trabajo, daemon=True).start()


def _bloquear_botones(ocupar: bool) -> None:
    """Deshabilita los botones mientras hay trabajo.

    Antes lo impedía el propio bloqueo de la ventana; al quitarlo hay que
    evitar dobles clics (dos 'pkexec tar' a la vez estropearían la instalación).
    Además, el botón deshabilitado se pone gris para que se note.
    """
    estado = tk.DISABLED if ocupar else tk.NORMAL
    # (widget, color de fondo libre, color de letra libre, color de su anillo)
    for widget, fondo, letra, anillo in (
        (boton, AZUL, "white", GRIS_BOTON),
        (btn_abrir, AZUL, "white", GRIS_BOTON),
        (btn_deshacer, TARJETA, AZUL, AZUL),
    ):
        if ocupar:
            widget.config(state=estado, bg=GRIS_BOTON, fg=TEXTO_SUAVE,
                          highlightbackground=GRIS_BOTON)
        else:
            widget.config(state=estado, bg=fondo, fg=letra,
                          highlightbackground=anillo)


# ----- Análisis del .tar.gz (sin privilegios) -----
def base_extraida() -> str:
    """Fallback: "app.tar.gz" -> "app"."""
    nombre = ruta.split("/")[-1]
    for sufijo in (".tar.gz", ".tgz", ".tar"):
        if nombre.endswith(sufijo):
            return nombre[: -len(sufijo)]
    return nombre


def _normalizar(linea: str) -> str:
    """Quita el prefijo './' y cualquier '/' inicial de una entrada del tar."""
    limpia = linea.strip()
    while limpia.startswith("./"):
        limpia = limpia[2:]
    return limpia.lstrip("/")


def _raices(lineas: list[str]) -> set[str]:
    """Primer componente de cada entrada: los directorios de primer nivel."""
    raices: set[str] = set()
    for linea in lineas:
        normalizada = _normalizar(linea)
        if normalizada:
            raices.add(normalizada.split("/", 1)[0])
    return raices


def analizar() -> tuple[str, bool]:
    """Lee el índice del .tar.gz y decide CÓMO instalarlo. Sin privilegios.

    Decide dos cosas que antes se adivinaban y salían mal en 5 de 6 casos:
      - `extraido`: la carpeta real que va a existir en RAIZ_OPT
      - `comando_tar`: si va directo a RAIZ_OPT o dentro de una carpeta
        nuestra con --one-top-level (para no dejar ficheros sueltos)
    """
    global extraido, comando_tar, ya_instalado

    indice = subprocess.run(
        ["tar", "tzf", ruta],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if indice.returncode != 0:
        return "✗ Not a valid .tar.gz: " + error_de(indice), False

    lineas = [l for l in indice.stdout.decode(errors="replace").splitlines() if l.strip()]
    if not lineas:
        return "✗ The archive is empty", False

    raices = _raices(lineas)
    normalizadas = [n for n in (_normalizar(l) for l in lineas) if n]
    hay_fichero_en_raiz = any("/" not in n for n in normalizadas)

    # Solo es seguro instalar directo si hay UNA raíz, que sea un directorio
    # y que no sea una carpeta de sistema compartida.
    unica = len(raices) == 1
    raiz = next(iter(raices)) if unica else ""
    directo = unica and not hay_fichero_en_raiz and raiz not in RAICES_SISTEMA
    directo = directo and raiz not in (".", "..", "")

    if directo:
        extraido = raiz
        comando_tar = ["--no-same-owner", "--no-same-permissions",
                       "-xzf", ruta, "-C", str(RAIZ_OPT) + "/"]
    else:
        extraido = base_extraida()
        if not extraido or extraido in (".", ".."):
            return "✗ Cannot tell where to install this archive", False
        if extraido in RAICES_SISTEMA:
            # p. ej. un "bin.tar.gz" con estructura contenida no debe acabar
            # creando (y en "return" borrando) /opt/bin
            extraido += "_app"
        comando_tar = ["--no-same-owner", "--no-same-permissions",
                       "-xzf", ruta, "-C", str(RAIZ_OPT) + "/",
                       f"--one-top-level={extraido}"]

    carpeta = RAIZ_OPT / extraido
    ya_instalado = carpeta.exists()

    aviso = ""
    if len(raices) > 1:
        aviso = f" ({len(raices)} top folders -> contained)"
    elif hay_fichero_en_raiz:
        aviso = " (loose files -> contained)"
    elif raiz in RAICES_SISTEMA:
        aviso = f" (system-like root '{raiz}' -> contained)"

    modo = "directo" if directo else "contenida"
    return f"✓ Will install as /opt/{extraido} [{modo}]{aviso}", True


def dir_instalacion() -> Path:
    """Carpeta donde se instala el programa."""
    return RAIZ_OPT / extraido


# ----- Búsqueda dentro de lo instalado -----
def binario() -> Path | None:
    """Ejecutable de /opt/<extraido>/ por si no se eligió ninguno."""
    carpeta = dir_instalacion()
    mismo_nombre = carpeta / extraido
    if mismo_nombre.is_file() and os.access(mismo_nombre, os.X_OK):
        return mismo_nombre
    if carpeta.is_dir():
        for elemento in sorted(carpeta.iterdir()):
            if elemento.is_file() and os.access(elemento, os.X_OK):
                return elemento
    return None


def _es_so(ruta: Path) -> bool:
    """¿Es una biblioteca compartida? (tienen bit X pero no se ejecutan)"""
    return ruta.suffix == ".so" or ".so." in ruta.name


def buscar_ejecutables() -> list[Path]:
    """Ejecutables reales de la instalación, ordenados por probabilidad."""
    carpeta = dir_instalacion()
    if not carpeta.is_dir():
        return []

    encontrados: list[Path] = []
    for elemento in carpeta.rglob("*"):
        if not elemento.is_file() or _es_so(elemento):
            continue
        if os.access(elemento, os.X_OK):
            encontrados.append(elemento)

    def prioridad(p: Path) -> tuple[int, str]:
        rel = p.relative_to(carpeta)
        if len(rel.parts) == 1:
            return (0, str(rel))          # en la raíz, lo más probable
        if rel.parts[0] in ("bin", "sbin", "exe", "usr"):
            return (1, str(rel))          # bin/<algo>
        if rel.parts[0] in ("lib", "lib64", "lib32"):
            return (3, str(rel))          # casi nunca se ejecuta
        return (2, str(rel))

    encontrados.sort(key=prioridad)
    return encontrados[:200]


def buscar_escritorio_propio() -> Path | None:
    """El .desktop que traiga el propio paquete, si lo trae."""
    carpeta = dir_instalacion()
    if not carpeta.is_dir():
        return None
    for candidato in carpeta.rglob("*.desktop"):
        return candidato
    return None


def leer_desktop(ruta_desktop: Path | None) -> tuple[str, bool, list[str]]:
    """(Exec original, Terminal original, líneas) de un .desktop ajeno."""
    if ruta_desktop is None:
        return "", False, []
    try:
        lineas = ruta_desktop.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", False, []

    exec_original, terminal = "", False
    for linea in lineas:
        if linea.startswith("Exec=") and not exec_original:
            exec_original = linea[5:].strip()
        elif linea.startswith("Terminal="):
            terminal = linea[9:].strip().lower() == "true"
    return exec_original, terminal, lineas


def _tamano_icono(rel: Path) -> int:
    """Mayor dimensión del icono leída del path (256 de 'hicolor/256x256')."""
    if any("scalable" in parte.lower() for parte in rel.parts):
        return 1024
    mejor = 0
    for parte in rel.parts:
        busqueda = re.search(r"(\d+)x(\d+)", parte)
        if busqueda:
            mejor = max(mejor, int(busqueda.group(1)), int(busqueda.group(2)))
    return mejor


def logo_del_app() -> Path | None:
    """Mejor imagen de /opt/<extraido>/ para el Icon, o None.

    Mira en la raíz y en las carpetas típicas de iconos
    (share/icons/hicolor/..., share/pixmaps, ...) — antes solo miraba la
    raíz, así que casi siempre acababa en el logo genérico.
    """
    carpeta = dir_instalacion()
    if not carpeta.is_dir():
        return None

    preferentes = [carpeta] + [carpeta / c for c in (
        "share/icons", "usr/share/icons", "share/pixmaps", "usr/share/pixmaps",
        "icons", "pixmaps", "share/art", "usr/share/art",
        "share/applnk", "usr/share/applnk",
    )]

    mejores: list[tuple[int, str, Path]] = []
    for sitio in preferentes:
        if not sitio.is_dir():
            continue
        elementos = sitio.iterdir() if sitio == carpeta else sitio.rglob("*")
        for imagen in elementos:
            if not imagen.is_file() or imagen.suffix.lower() not in EXTENSIONES_LOGO:
                continue
            rel = imagen.relative_to(carpeta)
            nombre = imagen.stem.lower()

            puntos = len(rel.parts) * 3          # cuanto más cerca de la raíz, mejor
            if "logo" in nombre or "icon" in nombre or extraido.lower() in nombre:
                puntos -= 100                    # nombre que parece el del programa
            if imagen.suffix.lower() == ".svg":
                puntos -= 30                     # escalable, mejor que un PNG pequeño
            puntos -= _tamano_icono(rel) // 8    # 256x256 gana a 16x16
            mejores.append((puntos, str(rel), imagen))

    if mejores:
        return min(mejores)[2]

    # Último recurso: cualquier imagen cuyo nombre parezca logo/icono
    for imagen in carpeta.rglob("*"):
        if (imagen.is_file() and imagen.suffix.lower() in EXTENSIONES_LOGO
                and any(c in imagen.stem.lower() for c in ("logo", "icon", extraido.lower()))):
            return imagen
    return None


def icono_permanente() -> Path | None:
    """Copia TarGzLogo.png a DIR_ICONOS (si hacía falta) y devuelve esa ruta fija.

    El Icon= de un .desktop debe apuntar a un fichero que EXISTA siempre: con
    --onefile el logo embebido vive en un temporal que se borra al cerrar la
    app, así que aquí se guarda una copia duradera en ~/.local/share/icons/.
    Se copia solo si falta o cambió de tamaño (logo nuevo en otra versión).
    """
    if not RUTA_LOGO_GENERICO.exists():
        return None
    destino = DIR_ICONOS / "ulxTarGzInstaller.png"
    try:
        if (not destino.exists()
                or destino.stat().st_size != RUTA_LOGO_GENERICO.stat().st_size):
            DIR_ICONOS.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(RUTA_LOGO_GENERICO.read_bytes())
    except OSError:
        return None    # sin permisos: el .desktop se queda sin Icon, no tronar
    return destino


# ----- Escritura del .desktop -----
def _argumentos_de(exec_original: str) -> str:
    """Argumentos de un Exec, quitándole el programa de delante.

    Soporta el programa entre comillas (Exec="/opt/mi app/x" %U), que si no
    partiría el camino por la mitad.
    """
    resto = exec_original.strip()
    if resto.startswith('"'):
        cierre = resto.find('"', 1)
        return resto[cierre + 1:].strip() if cierre != -1 else ""
    partes = resto.split(None, 1)
    return partes[1] if len(partes) > 1 else ""


def _exec_para(ejecutable: Path, exec_original: str) -> str:
    """Exec con la ruta real del binario, conservando los argumentos."""
    argumentos = _argumentos_de(exec_original)

    programa = str(ejecutable)
    if " " in programa:  # el spec de .desktop usa comillas dobles
        programa = f'"{programa}"'
    return f"{programa} {argumentos}" if argumentos else programa


def _desktop_nuestro(ejecutable: Path, terminal: bool) -> str:
    icono = logo_del_app() or icono_permanente()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={extraido}\n"
        f"Exec={_exec_para(ejecutable, '')}\n"
        f"Icon={icono or ''}\n"
        f"Terminal={'true' if terminal else 'false'}\n"
        "Categories=HelmTool;\n"
    )


def _desktop_del_paquete(lineas: list[str], ejecutable: Path, terminal: bool) -> str:
    """Copia el .desktop del paquete, arreglando Exec/Icon/Terminal."""
    icono = logo_del_app()
    salida: list[str] = []
    exec_puesto = False

    for linea in lineas:
        if linea.startswith("Exec="):
            if not exec_puesto:  # solo el principal; las 'Actions' se quedan
                salida.append("Exec=" + _exec_para(ejecutable, linea[5:].strip()))
                exec_puesto = True
            else:
                salida.append(linea)  # el Exec de un [Desktop Action] no se toca
        elif linea.startswith("Terminal="):
            salida.append(f"Terminal={'true' if terminal else 'false'}")
        elif linea.startswith("Icon=") and icono is not None:
            salida.append(f"Icon={icono}")
        else:
            salida.append(linea)

    if not exec_puesto:
        salida.append("Exec=" + _exec_para(ejecutable, ""))
    return "\n".join(salida) + "\n"


def escribir_desktop(ejecutable: Path, terminal: bool, lineas: list[str]) -> Path:
    """Escribe (o sobrescribe, si ya existía) el lanzador del usuario."""
    DIR_LANZADORES.mkdir(parents=True, exist_ok=True)
    destino = DIR_LANZADORES / f"{extraido}.desktop"

    contenido = _desktop_del_paquete(lineas, ejecutable, terminal) if lineas \
        else _desktop_nuestro(ejecutable, terminal)
    destino.write_text(contenido, encoding="utf-8")
    return destino


def ruta_lanzador() -> Path:
    """Dónde queda el .desktop que creamos (para poder borrarlo)."""
    return DIR_LANZADORES / f"{extraido}.desktop"


# ----- Trabajos (van en hilo, no tocan la interfaz) -----
def _trabajo_preparar() -> tuple[str, bool]:
    """Analiza el .tar.gz sin privilegios."""
    return analizar()


def _trabajo_extraer() -> tuple[str, bool]:
    """Extrae con root: una sola petición de contraseña."""
    resultado = subprocess.run(
        ["pkexec", "tar"] + comando_tar,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if resultado.returncode == 0:
        return f"✓ Extracted in {dir_instalacion()}", True
    return "✗ Error: " + error_de(resultado), False


def _trabajo_abrir() -> tuple[str, bool]:
    """Lanza el programa instalado sin esperar a que termine."""
    ejecutable = ejecutable_elegido or binario()
    if ejecutable is None:
        return f"✗ No executable found in {dir_instalacion()}", False

    try:
        proceso = subprocess.Popen(
            [str(ejecutable)],
            cwd=str(ejecutable.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"✗ Error: {exc}", False

    # Solo bloquea este hilo: da tiempo a coger un arranque fallido
    time.sleep(0.3)
    if proceso.poll() is not None and proceso.returncode != 0:
        return f"✗ Error: {ejecutable.name} exited with code {proceso.returncode}", False
    return f"✓ {ejecutable.name} executed!!!", True


def _trabajo_deshacer() -> tuple[str, bool]:
    """Borra de /opt/ lo extraído y su lanzador .desktop."""
    nombre = extraido

    resultado = subprocess.run(
        ["pkexec", "rm", "-rf", str(RAIZ_OPT / nombre)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        ruta_lanzador().unlink(missing_ok=True)
        return f"✓ {nombre} deleted", True
    return "✗ Error: " + error_de(resultado), False


# ----- Callbacks de la interfaz (van en el hilo de Tk) -----
def ejecutar() -> None:
    """Pide un .tar.gz y lo instala en /opt/."""
    global ruta, ejecutable_elegido

    ruta = fd.askopenfilename(
        title="Abrir",
        initialdir=str(Path.home()),
        filetypes=[(".tar.gz", "*.tar.gz"), ("All", "*.*")]
    )
    if not ruta:
        return

    ejecutable_elegido = None
    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfir, "⏳ Checking the package...")
    _en_hilo(_trabajo_preparar, _preparar_terminado)


def _preparar_terminado(mensaje: str, exito: bool) -> None:
    if not exito:
        _bloquear_botones(False)
        _poner_mensaje(etiquetaConfir, mensaje)
        return

    if ya_instalado:
        respuesta = messagebox.askyesno(
            "Already installed",
            f"{dir_instalacion()} already exists.\n\n"
            "Overwrite it? (old files may remain)"
        )
        if not respuesta:
            _bloquear_botones(False)
            _poner_mensaje(etiquetaConfir, "✗ Cancelled, nothing changed")
            return

    _poner_mensaje(etiquetaConfir, "⏳ Extracting, please wait...")
    _en_hilo(_trabajo_extraer, _extraer_terminado)


def _extraer_terminado(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    _poner_mensaje(etiquetaConfir, mensaje)
    if exito:
        frame_seccion.pack(fill=tk.X)
        configurar_lanzador()
    else:
        frame_seccion.pack_forget()


def abrir() -> None:
    """Ejecuta el binario instalado en /opt/<extraido>/."""
    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfirCond, "⏳ Executing...")
    _en_hilo(_trabajo_abrir, _abrir_terminado)


def _abrir_terminado(mensaje: str, _exito: bool) -> None:
    _bloquear_botones(False)
    _poner_mensaje(etiquetaConfirCond, mensaje)


def deshacer() -> None:
    """Borra de /opt/ lo que se extrajo y elimina su lanzador .desktop."""
    _bloquear_botones(True)
    _poner_mensaje(etiquetaConfirCond, "⏳ Deleting...")
    _en_hilo(_trabajo_deshacer, _deshacer_terminado)


def _deshacer_terminado(mensaje: str, exito: bool) -> None:
    global ejecutable_elegido
    _bloquear_botones(False)
    if exito:
        ejecutable_elegido = None
    _poner_mensaje(etiquetaConfirCond, mensaje)
    if exito:
        frame_seccion.pack(fill=tk.X)
    else:
        frame_seccion.pack_forget()


# ----- Lanzador -----
def configurar_lanzador() -> None:
    """Pide ejecutable y terminal y escribe el .desktop (se puede omitir)."""
    global ejecutable_elegido

    eleccion = dialogo_lanzador()
    if eleccion is None:
        _poner_mensaje(etiquetaConfirCond, "⚠ Installed, but no launcher created")
        return

    ejecutable, terminal, lineas_paquete = eleccion
    ejecutable_elegido = ejecutable
    try:
        escrito = escribir_desktop(ejecutable, terminal, lineas_paquete)
    except OSError as exc:
        _poner_mensaje(etiquetaConfirCond, f"✗ Error: {exc}")
        return

    _poner_mensaje(etiquetaConfirCond, f"✓ Launcher created: {escrito.name}")


def dialogo_lanzador() -> tuple[Path, bool, list[str]] | None:
    """Modal para elegir ejecutable y si va en terminal.

    Devuelve (ejecutable, terminal, líneas del .desktop del paquete)
    o None si el usuario lo omite.
    """
    escritorio_propio = buscar_escritorio_propio()
    exec_original, terminal_inicial, lineas = leer_desktop(escritorio_propio)

    candidatos = buscar_ejecutables()
    if not candidatos and binario():
        candidatos = [binario()]

    dlg = tk.Toplevel(ventana)
    dlg.title("Create launcher")
    dlg.resizable(False, False)
    dlg.configure(bg=FONDO)
    dlg.transient(ventana)
    dlg.grab_set()

    seleccion = tk.StringVar(value=str(candidatos[0]) if candidatos else "")
    terminal = tk.BooleanVar(value=terminal_inicial)
    resultado: list[tuple[Path, bool, list[str]]] = []

    # Cabecera azul con el título
    cabecera = tk.Frame(dlg, bg=AZUL, padx=16, pady=8)
    cabecera.pack(fill=tk.X)
    tk.Label(cabecera, text="Create launcher", font=("Arial", 12, "bold"),
             bg=AZUL, fg="white").pack(anchor="w")

    # Tarjeta blanca con el contenido
    marco = tk.Frame(dlg, bg=TARJETA, padx=18, pady=16,
                     highlightbackground=BORDE, highlightthickness=1)
    marco.pack(padx=14, pady=(10, 14), fill=tk.BOTH, expand=True)

    if exec_original:
        tk.Label(marco, text=f"The package brings its own launcher (Exec={exec_original})",
                 font=("Arial", 9), bg=TARJETA, fg=TEXTO_SUAVE,
                 justify="left", wraplength=360).pack(anchor="w")
    tk.Label(marco, text=f"Install: {dir_instalacion()}",
             font=("Arial", 11, "bold"), bg=TARJETA, fg=AZUL).pack(anchor="w", pady=(8, 0))

    tk.Label(marco, text="Executable:", font=("Arial", 10, "bold"),
             bg=TARJETA, fg=TEXTO).pack(anchor="w", pady=(14, 4))
    fila = tk.Frame(marco, bg=TARJETA)
    fila.pack(fill=tk.X)

    combo = ttk.Combobox(fila, textvariable=seleccion, state="readonly",
                         values=[str(p) for p in candidatos])
    combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def examinar() -> None:
        elegida = fd.askopenfilename(title="Choose executable",
                                     initialdir=str(dir_instalacion()))
        if elegida:
            valores = list(combo["values"])
            if elegida not in valores:
                valores.append(elegida)
                combo["values"] = valores
            seleccion.set(elegida)

    _boton(fila, "...", examinar, primario=False).pack(side=tk.LEFT, padx=(6, 0))

    tk.Checkbutton(marco, text="Run in a terminal (console application)",
                   variable=terminal, bg=TARJETA, fg=TEXTO, font=("Arial", 10),
                   activebackground=TARJETA, activeforeground=AZUL,
                   selectcolor=AZUL_CLARO, cursor="hand2").pack(anchor="w", pady=(14, 0))

    tk.Frame(marco, height=1, bg=BORDE).pack(fill=tk.X, pady=(16, 0))

    botones = tk.Frame(marco, bg=TARJETA)
    botones.pack(fill=tk.X, pady=(14, 0))

    def aceptar() -> None:
        elegido = seleccion.get()
        if not elegido:
            return
        resultado.append((Path(elegido), terminal.get(), lineas))
        dlg.destroy()

    def omitir() -> None:
        dlg.destroy()

    _boton(botones, "Create launcher", aceptar).pack(side=tk.RIGHT)
    _boton(botones, "Skip", omitir, primario=False).pack(side=tk.RIGHT, padx=(0, 8))

    # Centrado sobre la ventana principal
    dlg.update_idletasks()
    x = ventana.winfo_rootx() + max(0, (ventana.winfo_width() - dlg.winfo_width()) // 2)
    y = ventana.winfo_rooty() + max(0, (ventana.winfo_height() - dlg.winfo_height()) // 2)
    dlg.geometry(f"+{x}+{y}")

    dlg.wait_window()
    return resultado[0] if resultado else None

#endregion

#region Definición de ventana
def crear_ventana() -> None:
    """Construye la interfaz y entra en el bucle principal."""
    global ventana, etiqueta, etiquetaConfir, boton
    global frame_seccion, btn_abrir, btn_deshacer, etiquetaConfirCond, logo
    global logo_cabecera

    ventana = tk.Tk()
    ventana.title(".tar.gz INSTALLER")
    ventana.configure(bg=FONDO)
    ventana.resizable(False, False)

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
    tk.Label(cabecera, text=".tar.gz INSTALLER", font=("Arial", 15, "bold"),
             bg=AZUL, fg="white").pack(side=tk.LEFT)

    # ===== TARJETA BLANCA =====
    tarjeta = tk.Frame(ventana, bg=TARJETA, highlightbackground=BORDE,
                       highlightthickness=1, padx=26, pady=22)
    tarjeta.pack(padx=18, pady=18, fill=tk.BOTH, expand=True)

    etiqueta = tk.Label(tarjeta, text="Select a file .tar.gz",
                        font=("Arial", 15, "bold"), bg=TARJETA, fg=AZUL)
    etiqueta.pack()

    etiquetaConfir = tk.Label(tarjeta, text="", font=("Arial", 10), bg=TARJETA,
                              fg=TEXTO, wraplength=340, justify="center")
    etiquetaConfir.pack(pady=(14, 16))

    boton = _boton(tarjeta, "Search", ejecutar)
    boton.pack()

    # ===== SECCIÓN CONDICIONAL =====
    frame_seccion = tk.Frame(tarjeta, bg=TARJETA)
    tk.Frame(frame_seccion, height=1, bg=BORDE).pack(fill=tk.X, pady=(18, 0))

    # ancho natural -> la fila de botones se centra bajo el separador ancho
    fila_botones = tk.Frame(frame_seccion, bg=TARJETA)
    fila_botones.pack(pady=(12, 0))

    btn_abrir = _boton(fila_botones, "Execute", abrir)
    btn_abrir.pack(side=tk.LEFT)

    btn_deshacer = _boton(fila_botones, "return", deshacer, primario=False)
    btn_deshacer.pack(side=tk.LEFT, padx=(10, 0))

    etiquetaConfirCond = tk.Label(frame_seccion, text="", font=("Arial", 10),
                                  bg=TARJETA, fg=TEXTO, wraplength=340,
                                  justify="center")
    etiquetaConfirCond.pack(fill=tk.X, pady=(12, 0))

    # No se llama frame_seccion.pack(fill=tk.X) → nace oculta
    # ==================================

    # Pie de la tarjeta: qué hace la app y por qué pide privilegios
    tk.Label(tarjeta, text="Installs into /opt/ · asks for administrator access",
             font=("Arial", 9), bg=TARJETA, fg=TEXTO_SUAVE).pack(
        side=tk.BOTTOM, pady=(18, 0))

    # Tamaño fijo y centrada en pantalla
    ancho, alto = 470, 350
    x = (ventana.winfo_screenwidth() - ancho) // 2
    y = (ventana.winfo_screenheight() - alto) // 2
    ventana.geometry(f"{ancho}x{alto}+{x}+{y}")

    # Arranca el vaciado de la cola: por aquí entra todo lo que piden los hilos
    ventana.after(50, _drenar_cola)

    ventana.mainloop()


if __name__ == "__main__":
    crear_ventana()
#endregion
