#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "       Instalando 3DS-Drop para Linux (ElVichoPo)         "
echo "=========================================================="

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PC_DIR="$REPO_DIR/pc-client"

cd "$PC_DIR"

if [ ! -d "venv" ]; then
    echo "[1/3] Creando entorno virtual Python..."
    python3 -m venv venv
fi

echo "[2/3] Instalando interfaz grafica PySide6..."
./venv/bin/pip install --upgrade pip PySide6 >/dev/null

echo "[3/3] Creando lanzador de aplicacion en el menu del sistema..."
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/3ds-drop.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=3DS-Drop
GenericName=Transferencia de Archivos Nintendo 3DS / 2DS
Comment=Transfiere archivos y carpetas entre tu PC y cualquier consola de la familia Nintendo 2DS / 3DS
Exec=$PC_DIR/venv/bin/python $PC_DIR/main.py
Path=$PC_DIR
Icon=$PC_DIR/icon.png
Terminal=false
Categories=Utility;FileTransfer;Network;Game;
StartupNotify=true
StartupWMClass=3ds-drop
EOF

chmod +x "$HOME/.local/share/applications/3ds-drop.desktop" "$PC_DIR/main.py"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo ""
echo "=========================================================="
echo "  ¡Listo! Ya puedes abrir '3DS-Drop' desde tu menu de     "
echo "  aplicaciones sin usar la terminal.                      "
echo "=========================================================="
