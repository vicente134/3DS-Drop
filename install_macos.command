#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "       Instalando 3DS-Drop para macOS (ElVichoPo)         "
echo "=========================================================="

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PC_DIR="$REPO_DIR/pc-client"
APP_BUNDLE="$HOME/Applications/3DS-Drop.app"

cd "$PC_DIR"

if [ ! -d "venv" ]; then
    echo "[1/3] Creando entorno virtual Python..."
    python3 -m venv venv
fi

echo "[2/3] Instalando interfaz grafica PySide6..."
./venv/bin/pip install --upgrade pip PySide6 >/dev/null

echo "[3/3] Creando aplicacion nativa 3DS-Drop.app en ~/Applications..."
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>3DS-Drop</string>
    <key>CFBundleIdentifier</key>
    <string>com.elvichopo.3dsdrop</string>
    <key>CFBundleName</key>
    <string>3DS-Drop</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
</dict>
</plist>
EOF

cat > "$APP_BUNDLE/Contents/MacOS/3DS-Drop" <<EOF
#!/usr/bin/env bash
cd "$PC_DIR"
exec "$PC_DIR/venv/bin/python" "$PC_DIR/main.py"
EOF

chmod +x "$APP_BUNDLE/Contents/MacOS/3DS-Drop"

echo ""
echo "=========================================================="
echo "  ¡Listo! 3DS-Drop.app fue instalado en ~/Applications.   "
echo "  Abriendo 3DS-Drop...                                    "
echo "=========================================================="
open "$APP_BUNDLE"
