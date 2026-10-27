#!/usr/bin/env bash
#
# build.sh — compila main.py en un binario de Linux con PyInstaller.
#
# Uso:
#   ./build.sh
#
# Resultado:
#   dist/ulxTarGzInstaller/ulxTarGzInstaller   <- el binario
#   dist/ulxTarGzInstaller/_internal/          <- soportes, incluidos los 2 PNG
#
# Por qué --onedir y NO --onefile:
#   * onefile extrae todo a /tmp/_MEIxxxx y lo BORRA al cerrar la app. El
#     Icon= que se escribe en los .desktop apuntaría a un temporal que ya no
#     existe (probado: el lanzador queda roto nada más cerrar).
#   * onedir deja los ficheros reales en _internal/, que es donde apunta
#     __file__ del programa, y main.py busca los PNG justo ahí. No hay que
#     tocar ni una línea de código.
#
# Los PNG van con --add-data pero NO se incrustan en el ejecutable: quedan
# como ficheros sueltos en _internal/ y se pueden reemplazar sin recompilar.
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

# --- cómo lanzar PyInstaller (sin instalar nada fijo en el sistema) ---
if command -v uv >/dev/null 2>&1; then
  # --no-project: que uv NO intente sincronizar un pyproject.toml del padre
  LANZADOR=(uv run --no-project --with pyinstaller pyinstaller)
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

echo "==> Compilando $NOMBRE (onedir, GUI) con: ${LANZADOR[*]}"

"${LANZADOR[@]}" \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "$NOMBRE" \
  --add-data "AppLogo.png:." \
  --add-data "TarGzLogo.png:." \
  main.py

BINARIO="dist/$NOMBRE/$NOMBRE"
INTERNOS="dist/$NOMBRE/_internal"

# --- comprobaciones finales --------------------------------------------
if [[ ! -x "$BINARIO" ]]; then
  echo "✗ No se generó el binario: $BINARIO" >&2
  exit 1
fi
for png in AppLogo.png TarGzLogo.png; do
  if [[ ! -f "$INTERNOS/$png" ]]; then
    echo "✗ Falta $png en $INTERNOS (no se empaquetó el recurso)" >&2
    exit 1
  fi
done

echo
echo "✓ Binario listo:"
echo "    $BINARIO   ($(du -h "$BINARIO" | cut -f1))"
echo "    PNG sueltos en: $INTERNOS/"
echo
echo "  Para probar:   $BINARIO"
echo "  Para repartir: tar czf $NOMBRE.tar.gz -C dist $NOMBRE"
echo
echo "  Notas:"
echo "   - En la máquina destino hacen falta 'tar' y 'pkexec'."
echo "   - Icon= de los .desktop apunta a $INTERNOS/TarGzLogo.png:"
echo "     no muevas la carpeta después de crear lanzadores."
