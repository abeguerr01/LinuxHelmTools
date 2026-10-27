import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox

import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

ruta: str = ""
extraido: str = ""  # nombre real creado dentro de /opt/

# Los hilos de trabajo dejan aquí lo que la interfaz debe pintar
cola: queue.Queue[Callable[[], None]] = queue.Queue()

# El logo vive junto a este script, no en el directorio de trabajo
RUTA_LOGO: Path = Path(__file__).resolve().parent / "AppLogo.png"
RUTA_LOGO_GENERICO: Path = Path(__file__).resolve().parent / "TarGzLogo.png"
logo: tk.PhotoImage | None = None  # referencia para que Tkinter no lo recoja (GC)

# Formatos que sirven como icono de escritorio
EXTENSIONES_LOGO: set[str] = {".png", ".svg", ".ico", ".xpm", ".bmp"}

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
    evitar dobles clics (dos 'pkexec tar' a la vez estropearían /opt/).
    """
    estado = tk.DISABLED if ocupar else tk.NORMAL
    boton.config(state=estado)
    btn_abrir.config(state=estado)
    btn_deshacer.config(state=estado)


# ----- Lógica de instalación -----
def base_extraida() -> str:
    """Fallback: "app.tar.gz" -> "app"."""
    nombre = ruta.split("/")[-1]
    for sufijo in (".tar.gz", ".tgz", ".tar"):
        if nombre.endswith(sufijo):
            return nombre[: -len(sufijo)]
    return nombre


def detectar_extraido() -> None:
    """Lee el índice del .tar.gz para no adivinar cómo se llama lo que se extrae."""
    global extraido
    extraido = base_extraida()

    indice = subprocess.run(
        ["tar", "tzf", ruta],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if indice.returncode == 0 and indice.stdout:
        primera = indice.stdout.decode(errors="replace").splitlines()[0]
        extraido = primera.split("/")[0] or extraido


def binario() -> Path | None:
    """Devuelve el ejecutable de /opt/<extraido>/ o None si no existe."""
    carpeta = Path("/opt") / extraido
    mismo_nombre = carpeta / extraido
    if mismo_nombre.is_file():
        return mismo_nombre
    if carpeta.is_dir():
        for elemento in sorted(carpeta.iterdir()):
            if elemento.is_file() and os.access(elemento, os.X_OK):
                return elemento
    return None


def logo_del_app() -> Path | None:
    """Imagen que trae el propio programa en /opt/<extraido>/ o None si no trae."""
    carpeta = Path("/opt") / extraido
    if not carpeta.is_dir():
        return None

    imagenes = sorted(
        elemento for elemento in carpeta.iterdir()
        if elemento.is_file() and elemento.suffix.lower() in EXTENSIONES_LOGO
    )
    if not imagenes:
        return None

    # Prefiere un nombre que parezca logo/icono; si no, el primero
    for imagen in imagenes:
        nombre = imagen.stem.lower()
        if "logo" in nombre or "icon" in nombre or extraido.lower() in nombre:
            return imagen
    return imagenes[0]


# ----- Trabajos (van en hilo, no tocan la interfaz) -----
def _trabajo_extraer() -> tuple[str, bool]:
    """Comprueba el índice y extrae el .tar.gz en /opt/."""
    detectar_extraido()

    resultado = subprocess.run(
        ["pkexec", "tar", "xzf", ruta, "-C", "/opt/"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        return "✓ Extracted in /opt/", True
    return "✗ Error: " + error_de(resultado), False


def _trabajo_abrir() -> tuple[str, bool]:
    """Lanza el programa instalado sin esperar a que termine."""
    ejecutable = binario()
    if ejecutable is None:
        return f"✗ No executable found in /opt/{extraido}", False

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
        ["pkexec", "rm", "-rf", f"/opt/{nombre}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        lanzador = Path.home() / ".local" / "share" / "applications" / f"{nombre}.desktop"
        lanzador.unlink(missing_ok=True)
        return f"✓ {nombre} deleted", True
    return "✗ Error: " + error_de(resultado), False


# ----- Callbacks de la interfaz (van en el hilo de Tk) -----
def ejecutar() -> None:
    """Pide un .tar.gz, lo extrae en /opt/ y ofrece ejecutarlo o deshacerlo."""
    global ruta

    ruta = fd.askopenfilename(
        title="Abrir",
        initialdir="/home",
        filetypes=[(".tar.gz", "*.tar.gz"), ("All", "*.*")]
    )
    if not ruta:
        return

    _bloquear_botones(True)
    etiquetaConfir.config(text="⏳ Extracting, please wait...")
    _en_hilo(_trabajo_extraer, _extraer_terminado)


def _extraer_terminado(mensaje: str, exito: bool) -> None:
    _bloquear_botones(False)
    etiquetaConfir.config(text=mensaje)
    if exito:
        frame_seccion.pack()
        dot_desktop()  # Verificamos si tiene .desktop
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
    _bloquear_botones(False)
    etiquetaConfirCond.config(text=mensaje)
    if exito:
        frame_seccion.pack()
    else:
        frame_seccion.pack_forget()


def dot_desktop() -> None:
    """Crea el lanzador .desktop si ni el paquete ni el sistema ya tienen uno."""
    carpetaApp = Path("/opt") / extraido
    propios = list(carpetaApp.glob("*.desktop")) if carpetaApp.is_dir() else []

    carpetaDestino = Path.home() / ".local" / "share" / "applications"
    carpetaDestino.mkdir(parents=True, exist_ok=True)
    nuestro = carpetaDestino / f"{extraido}.desktop"

    if not propios and not nuestro.exists():
        respuesta = messagebox.askyesno("No .desktop", "There aren't any \".desktop\" file.\nDo you want to create it?")

        if respuesta:
            ejecutable = binario()

            # Logo del programa instalado; si no trae, el genérico TarGzLogo
            icono = logo_del_app()
            if icono is None and RUTA_LOGO_GENERICO.exists():
                icono = RUTA_LOGO_GENERICO

            with open(nuestro, "w") as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={extraido}\n"
                    f"Exec={ejecutable if ejecutable else ''}\n"
                    f"Icon={icono or ''}\n"
                    "Terminal=false\n"
                    "Categories=HelmTool;\n"
                )

#endregion

#region Definición de ventana
def crear_ventana() -> None:
    """Construye la interfaz y entra en el bucle principal."""
    global ventana, etiqueta, etiquetaConfir, boton
    global frame_seccion, btn_abrir, btn_deshacer, etiquetaConfirCond, logo

    ventana = tk.Tk()
    ventana.title(".tar.gz INSTALLER")
    ventana.geometry("400x300")

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
