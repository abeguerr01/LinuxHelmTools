import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox

import os
import subprocess
from pathlib import Path

ruta: str = ""
extraido: str = ""  # nombre real creado dentro de /opt/

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
def error_de(resultado: subprocess.CompletedProcess[bytes]) -> str:
    """stderr legible, sin tronar si trae bytes inválidos."""
    return resultado.stderr.decode(errors="replace")


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
    """Imagen que trae el propio programa en /opt/<extraido>/, o None si no trae."""
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


def ejecutar() -> None:
    """Pide un .tar.gz, lo extrae en /opt/ y ofrece ejecutarlo o deshacerlo."""
    global frame_seccion, ruta

    ruta = fd.askopenfilename(
        title="Abrir",
        initialdir="/home",
        filetypes=[(".tar.gz", "*.tar.gz"), ("All", "*.*")]
    )
    if not ruta:
        return

    detectar_extraido()

    resultado = subprocess.run(
        ["pkexec", "tar", "xzf", ruta, "-C", "/opt/"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        etiquetaConfir.config(text="✓ Extracted in /opt/")
        frame_seccion.pack()
        dot_desktop()  # Verificamos si tiene .desktop
    else:
        etiquetaConfir.config(text="✗ Error: " + error_de(resultado))
        frame_seccion.pack_forget()


def abrir() -> None:
    """Ejecuta el binario instalado en /opt/<extraido>/."""
    ejecutable = binario()
    if ejecutable is None:
        etiquetaConfirCond.config(text="✗ No executable found in /opt/" + extraido)
        return

    resultado = subprocess.run(
        [str(ejecutable)],
        cwd=str(ejecutable.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        etiquetaConfirCond.config(text=f"✓ {ejecutable.name} executed!!!")
    else:
        etiquetaConfirCond.config(text="✗ Error: " + error_de(resultado))


def deshacer() -> None:
    """Borra de /opt/ lo que se extrajo y elimina su lanzador .desktop."""
    resultado = subprocess.run(
        ["pkexec", "rm", "-rf", f"/opt/{extraido}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        etiquetaConfirCond.config(text=f"✓ {extraido} deleted")
        (Path.home() / ".local" / "share" / "applications" / f"{extraido}.desktop").unlink(missing_ok=True)
        frame_seccion.pack()
    else:
        etiquetaConfirCond.config(text="✗ Error: " + error_de(resultado))
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

    ventana.mainloop()


if __name__ == "__main__":
    crear_ventana()
#endregion
