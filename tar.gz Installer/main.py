import tkinter as tk
from tkinter import filedialog as fd
from tkinter import messagebox

import os
import subprocess
from pathlib import Path

resultado_ok = False
ruta = ""
extraido = ""  # nombre real creado dentro de /opt/

# El logo vive junto a este script, no en el directorio de trabajo
RUTA_LOGO = Path(__file__).resolve().parent / "AppLogo.png"
logo = None  # referencia para que Tkinter no lo recoja (GC)

#region Funciones
def base_extraida():
    """Fallback: "app.tar.gz" -> "app"."""
    nombre = ruta.split("/")[-1]
    for sufijo in (".tar.gz", ".tgz", ".tar"):
        if nombre.endswith(sufijo):
            return nombre[: -len(sufijo)]
    return nombre


def detectar_extraido():
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


def binario():
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


def ejecutar():
    global resultado_ok, frame_seccion, ruta

    ruta = fd.askopenfilename(
        title="Abrir",
        initialdir="/home",
        filetypes=[(".tar.gz", "*.tar.gz"), ("All", "*.*")]
    )
    if not ruta:
        resultado_ok = False
        return

    detectar_extraido()

    resultado = subprocess.run(
        ["pkexec", "tar", "xzf", ruta, "-C", "/opt/"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        etiquetaConfir.config(text="✓ Extracted in /opt/")
        resultado_ok = True
        frame_seccion.pack()
        dotDesktop() #Verificamos si tiene .desktop
    else:
        etiquetaConfir.config(text="✗ Error: " + resultado.stderr.decode(errors="replace"))
        resultado_ok = False
        frame_seccion.pack_forget()

def abrir():
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
        etiquetaConfirCond.config(text="✗ Error: " + resultado.stderr.decode(errors="replace"))

def deshacer():
    global resultado_ok

    resultado = subprocess.run(
        ["pkexec", "rm", "-rf", f"/opt/{extraido}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if resultado.returncode == 0:
        etiquetaConfirCond.config(text=f"✓ {extraido} deleted")
        (Path.home() / ".local" / "share" / "applications" / f"{extraido}.desktop").unlink(missing_ok=True)
        resultado_ok = True
        frame_seccion.pack()
    else:
        etiquetaConfirCond.config(text="✗ Error: " + resultado.stderr.decode(errors="replace"))
        resultado_ok = False
        frame_seccion.pack_forget()

def dotDesktop():
    carpetaApp = Path("/opt") / extraido
    propios = list(carpetaApp.glob("*.desktop")) if carpetaApp.is_dir() else []

    carpetaDestino = Path.home() / ".local" / "share" / "applications"
    carpetaDestino.mkdir(parents=True, exist_ok=True)
    nuestro = carpetaDestino / f"{extraido}.desktop"

    if not propios and not nuestro.exists():
        respuesta = messagebox.askyesno("No .desktop", "There aren't any \".desktop\" file.\nDo you want to create it?")

        if respuesta:
            ejecutable = binario()
            with open(nuestro, "w") as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={extraido}\n"
                    f"Exec={ejecutable if ejecutable else ''}\n"
                    f"Icon={RUTA_LOGO}\n"
                    "Terminal=false\n"
                    "Categories=HelmTool;\n"
                )

#endregion

#region Definición de ventana
ventana = tk.Tk()
ventana.title(".tar.gz INSTALER")
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
#endregion
