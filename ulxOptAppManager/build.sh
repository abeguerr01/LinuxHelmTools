#!/usr/bin/env bash
#
# build.sh — compila main.py en un binario de Linux con PyInstaller.
#
# Uso:
#   ./build.sh
#
# Resultado:
#   dist/ulxOptAppManager   <- UN único binario autocontenido: dentro va
#                                python, tkinter y los recursos (si los hay).
#
# Se usa --onefile por el mismo motivo que ulxTarGzInstaller: la app
# contenedora (LinuxHelm) despliega SOLO el ejecutable y borra el resto, así
# que el binario tiene que ir solo. Los PNG, si existen, se embeben con
# --add-data y main.py los lee de sys._MEIPASS (ver DIR_RECURSOS).
#
# Los recursos son OPCIONALES en esta herramienta: si no hay AppLogo.png,
# la app arranca igual sin logo. Solo se embebe lo que exista.
set -euo pipefail

cd "$(dirname "$0")"   # el script funciona desde cualquier sitio

NOMBRE="ulxOptAppManager"

# --- comprobaciones previas -------------------------------------------
if [[ ! -f main.py ]]; then
  echo "✗ Falta main.py en $(pwd)" >&2
  exit 1
fi

# --- intérprete: el del SISTEMA, no el de uv ---------------------------------
# El Python de uv trae un Tk 9 sin fontconfig/Xft: no resuelve 'Arial' y todo
# sale con la fuente monoespaciada 'fixed'. El Tk 8.6 del sistema sí (Liberation
# Sans), así que el binario queda idéntico a como se ve main.py.
PYTHON=$(command -v python3)
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo "✗ $PYTHON no tiene tkinter (paquete python3-tk): no se puede compilar" >&2
  exit 1
fi

# --- recursos gráficos (opcionales: solo si existen) ----------------------
ADD_DATA=()
for png in AppLogo.png; do
  if [[ -f "$png" ]]; then
    ADD_DATA+=(--add-data "$png:.")
    echo "==> Se embebe el recurso: $png"
  else
    echo "==> No hay $png: se compila sin logo (opcional)."
  fi
done

# --- cómo lanzar PyInstaller (sin instalar nada fijo en el sistema) ---
if command -v uv >/dev/null 2>&1; then
  # --no-project: que uv NO intente sincronizar un pyproject.toml del padre
  LANZADOR=(uv run --no-project --python "$PYTHON" --with pyinstaller pyinstaller)
elif "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  LANZADOR=("$PYTHON" -m PyInstaller)
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
  ${ADD_DATA[@]+"${ADD_DATA[@]}"} \
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
echo "   - Solo LEE /opt: no necesita 'pkexec' ni permisos de root."
echo "   - En la máquina destino solo hace falta un Linux gráfico con glibc >= 2.17."
