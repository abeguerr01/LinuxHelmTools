#!/usr/bin/env bash
#
# build.sh — compila main.py en un binario de Linux con PyInstaller.
#
# Uso:
#   ./build.sh
#
# Resultado:
#   dist/ulxTarGzInstaller   <- UN único binario autocontenido: dentro va
#                                python, tkinter y los 2 PNG
#
# Por qué --onefile y NO --onedir:
#   LinuxHelm (la app contenedora) despliega SOLO el ejecutable en
#   ~/.config/ulx/LinuxHelm/tools/ (plano, +x) y BORRA el resto: con
#   --onedir las bibliotecas quedaban en _internal/ y el binario arrancado
#   desde ahí moría con "libpython3.14.so.1.0: cannot open shared object
#   file" (código 255). Con --onefile el binario va solo y funciona en
#   cualquier sitio (la app, la terminal, copiado a otro ordenador).
#
# El motivo por el que antes se evitaba --onefile ya está resuelto: el
# Icon= de los .desktop que crea la herramienta apuntaba a los PNG
# embebidos, que en --onefile viven en un temporal que se borra al cerrar
# (el lanzador quedaba roto). Ahora main.py copia el logo una vez a
# ~/.local/share/icons/ y el .desktop usa esa copia fija
# (ver icono_permanente()).
#
# Los PNG van con --add-data y quedan embebidos en el binario.
set -euo pipefail

cd "$(dirname "$0")"   # el script funciona desde cualquier sitio

NOMBRE="ulxTarGzInstaller"

# --- comprobaciones previas -------------------------------------------
for fichero in main.py AppLogo.png TarGzLogo.png; do
  if [[ ! -f "$fichero" ]]; then
    echo "✗ Falta $fichero en $(pwd)" >&2
    exit 1
  fi
done

# --- intérprete: el del SISTEMA, no el de uv ---------------------------------
# El Python de uv trae un Tk 9 sin fontconfig/Xft: no resuelve 'Arial' y todo
# sale con la fuente monoespaciada 'fixed'. El Tk 8.6 del sistema sí (Liberation
# Sans), así que el binario queda idéntico a como se ve main.py.
PYTHON=$(command -v python3)
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo "✗ $PYTHON no tiene tkinter (paquete python3-tk): no se puede compilar" >&2
  exit 1
fi

# --- cómo lanzar PyInstaller (sin instalar nada fijo en el sistema) ---
if command -v uv >/dev/null 2>&1; then
  # --no-project: que uv NO intente sincronizar un pyproject.toml del padre
  LANZADOR=(uv run --no-project --python "$PYTHON" --with pyinstaller pyinstaller)
elif python3 -c "import PyInstaller" >/dev/null 2>&1; then
  LANZADOR=(python3 -m PyInstaller)
elif command -v pyinstaller >/dev/null 2>&1; then
  LANZADOR=(pyinstaller)
else
  echo "✗ No hay PyInstaller. Instálalo con una de estas órdenes:" >&2
  echo "      pipx install pyinstaller" >&2
  echo "      uv tool install pyinstaller" >&2
  echo "      pip install --user pyinstaller" >&2
  exit 1
fi

echo "==> Compilando $NOMBRE (onefile, GUI) con: ${LANZADOR[*]}"

"${LANZADOR[@]}" \
  --noconfirm \
  --clean \
  --windowed \
  --onefile \
  --name "$NOMBRE" \
  --add-data "AppLogo.png:." \
  --add-data "TarGzLogo.png:." \
  main.py

BINARIO="dist/$NOMBRE"

# --- comprobaciones finales --------------------------------------------
if [[ ! -x "$BINARIO" ]]; then
  echo "✗ No se generó el binario: $BINARIO" >&2
  exit 1
fi
if [[ -d "dist/$NOMBRE/_internal" ]]; then
  echo "✗ Se generó un _internal/: ¿se ha compilado con --onedir por error?" >&2
  exit 1
fi

echo
echo "✓ Binario listo (autocontenido, un solo fichero):"
echo "    $BINARIO   ($(du -h "$BINARIO" | cut -f1))"
echo
echo "  Para probar:   $BINARIO"
echo "  Para repartir: tar czf $NOMBRE.tar.gz -C dist $NOMBRE"
echo
echo "  Notas:"
echo "   - En la máquina destino hacen falta 'tar' y 'pkexec'."
echo "   - El Icon= de los .desktop que crea la herramienta apunta a"
echo "     ~/.local/share/icons/$NOMBRE.png (copia permanente del logo)."
