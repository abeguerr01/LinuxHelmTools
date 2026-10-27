import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox
from tkinter import ttk

import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

# Donde se instala. Constante para poder testear sin ser root.
RAIZ_OPT: Path = Path("/opt")

# Dónde se dejan los .desktop del usuario. También es conste para testear.
DIR_LANZADORES: Path = Path.home() / ".local" / "share" / "applications"

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

# El logo vive junto a este script, no en el directorio de trabajo
RUTA_LOGO: Path = Path(__file__).resolve().parent / "AppLogo.png"
RUTA_LOGO_GENERICO: Path = Path(__file__).resolve().parent / "TarGzLogo.png"
logo: tk.PhotoImage | None = None  # referencia para que Tkinter no lo recoja (GC)

# Formatos que sirven como icono de escritorio
EXTENSIONES_LOGO: set[str] = {".png", ".svg", ".ico", ".xpm", ".bmp", ".webp"}

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
    """
    estado = tk.DISABLED if ocupar else tk.NORMAL
    boton.config(state=estado)
    btn_abrir.config(state=estado)
    btn_deshacer.config(state=estado)


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
    icono = logo_del_app() or (RUTA_LOGO_GENERICO if RUTA_LOGO_GENERICO.exists() else None)
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
    etiquetaConfir.config(text="⏳ Checking the package...")
    _en_hilo(_trabajo_preparar, _preparar_terminado)


def _preparar_terminado(mensaje: str, exito: bool) -> None:
    if not exito:
        _bloquear_botones(False)
        etiquetaConfir.config(text=mensaje)
        return

    if ya_instalado:
        respuesta = messagebox.askyesno(
            "Already installed",
            f"{dir_instalacion()} already exists.\n\n"
            "Overwrite it? (old files may remain)"
        )
        if not respuesta:
            _bloquear_botones(False)
            etiquetaConfir.config(text="✗ Cancelled, nothing changed")
            return

    etiquetaConfir.config(text="⏳ Extracting, please wait...")
    _en_hilo(_trabajo_extraer, _extraer_terminado)


def _extraer_terminado(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    etiquetaConfir.config(text=mensaje)
    if exito:
        frame_seccion.pack()
        configurar_lanzador()
    else:
        frame_seccion.pack_forget()


def abrir() -> None:
    """Ejecuta el binario instalado en /opt/<extraido>/."""
    _bloquear_botones(True)
    etiquetaConfirCond.config(text="⏳ Executing...")
    _en_hilo(_trabajo_abrir, _abrir_terminado)


def _abrir_terminado(mensaje: str, _exito: bool) -> None:
    _bloquear_botones(False)
    etiquetaConfirCond.config(text=mensaje)


def deshacer() -> None:
    """Borra de /opt/ lo que se extrajo y elimina su lanzador .desktop."""
    _bloquear_botones(True)
    etiquetaConfirCond.config(text="⏳ Deleting...")
    _en_hilo(_trabajo_deshacer, _deshacer_terminado)


def _deshacer_terminado(mensaje: str, exito: bool) -> None:
    global ejecutable_elegido
    _bloquear_botones(False)
    if exito:
        ejecutable_elegido = None
    etiquetaConfirCond.config(text=mensaje)
    if exito:
        frame_seccion.pack()
    else:
        frame_seccion.pack_forget()


# ----- Lanzador -----
def configurar_lanzador() -> None:
    """Pide ejecutable y terminal y escribe el .desktop (se puede omitir)."""
    global ejecutable_elegido

    eleccion = dialogo_lanzador()
    if eleccion is None:
        etiquetaConfirCond.config(text="⚠ Installed, but no launcher created")
        return

    ejecutable, terminal, lineas_paquete = eleccion
    ejecutable_elegido = ejecutable
    try:
        escrito = escribir_desktop(ejecutable, terminal, lineas_paquete)
    except OSError as exc:
        etiquetaConfirCond.config(text=f"✗ Error: {exc}")
        return

    etiquetaConfirCond.config(text=f"✓ Launcher created: {escrito.name}")


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
    dlg.transient(ventana)
    dlg.grab_set()

    seleccion = tk.StringVar(value=str(candidatos[0]) if candidatos else "")
    terminal = tk.BooleanVar(value=terminal_inicial)
    resultado: list[tuple[Path, bool, list[str]]] = []

    marco = tk.Frame(dlg, padx=16, pady=14)
    marco.pack(fill=tk.BOTH, expand=True)

    if exec_original:
        tk.Label(marco, text=f"The package brings its own launcher (Exec={exec_original})",
                 font=("Arial", 9), fg="#666").pack(anchor="w")
    tk.Label(marco, text=f"Install: {dir_instalacion()}",
             font=("Arial", 11, "bold")).pack(anchor="w", pady=(6, 0))

    tk.Label(marco, text="Executable:").pack(anchor="w", pady=(12, 2))
    fila = tk.Frame(marco)
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

    tk.Button(fila, text="...", width=3, command=examinar).pack(side=tk.LEFT, padx=(6, 0))

    tk.Checkbutton(marco, text="Run in a terminal (console application)",
                   variable=terminal).pack(anchor="w", pady=(12, 0))

    botones = tk.Frame(marco)
    botones.pack(fill=tk.X, pady=(16, 0))

    def aceptar() -> None:
        elegido = seleccion.get()
        if not elegido:
            return
        resultado.append((Path(elegido), terminal.get(), lineas))
        dlg.destroy()

    def omitir() -> None:
        dlg.destroy()

    tk.Button(botones, text="Create launcher", command=aceptar).pack(side=tk.RIGHT)
    tk.Button(botones, text="Skip", command=omitir).pack(side=tk.RIGHT, padx=(0, 8))

    dlg.wait_window()
    return resultado[0] if resultado else None

#endregion

#region Definición de ventana
def crear_ventana() -> None:
    """Construye la interfaz y entra en el bucle principal."""
    global ventana, etiqueta, etiquetaConfir, boton
    global frame_seccion, btn_abrir, btn_deshacer, etiquetaConfirCond, logo

    ventana = tk.Tk()
    ventana.title(".tar.gz INSTALLER")
    ventana.geometry("440x300")

    if RUTA_LOGO.exists():
        logo = tk.PhotoImage(file=str(RUTA_LOGO))
        ventana.iconphoto(True, logo)

    etiqueta = tk.Label(ventana, text="Select a file .tar.gz", font=("Arial", 14))
    etiqueta.pack(pady=20)

    etiquetaConfir = tk.Label(ventana, text="", font=("Arial", 14))
    etiquetaConfir.pack(pady=20)

    boton = tk.Button(ventana, text="Search", command=ejecutar)
    boton.pack(pady=10)

    # ===== SECCIÓN CONDICIONAL =====
    frame_seccion = tk.Frame(ventana)

    btn_abrir = tk.Button(frame_seccion, text="Execute", command=abrir)
    btn_abrir.pack(side=tk.LEFT, padx=10, pady=10)

    btn_deshacer = tk.Button(frame_seccion, text="return", command=deshacer)
    btn_deshacer.pack(side=tk.LEFT, padx=10, pady=10)

    etiquetaConfirCond = tk.Label(frame_seccion, text="", font=("Arial", 14))
    etiquetaConfirCond.pack(side=tk.TOP, fill=tk.X, pady=10)

    # No se llama frame_seccion.pack() → nace oculta
    # ==================================

    # Arranca el vaciado de la cola: por aquí entra todo lo que piden los hilos
    ventana.after(50, _drenar_cola)

    ventana.mainloop()


if __name__ == "__main__":
    crear_ventana()
#endregion
