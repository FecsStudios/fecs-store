#!/usr/bin/env bash
# Instala Fecs Store en tu menú de aplicaciones (solo para tu usuario).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/fecs-store.desktop" <<EOT
[Desktop Entry]
Type=Application
Name=Fecs Store
Comment=Tienda de aplicaciones: Flatpak, nativas y web
Exec=python3 $DIR/main.py
Icon=system-software-install
Terminal=false
Categories=System;PackageManager;
EOT
echo "Listo. Busca «Fecs Store» en tu menú de aplicaciones."
