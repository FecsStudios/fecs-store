#!/usr/bin/env bash
# Instalador de Fecs Store para cualquier distro Linux (x86_64). Sin root, sin dependencias.
#   Instalar:     curl -fsSL https://github.com/FecsStudios/fecs-store/releases/latest/download/install.sh | bash
#   Desinstalar:  curl -fsSL https://github.com/FecsStudios/fecs-store/releases/latest/download/install.sh | bash -s -- --uninstall
set -euo pipefail
REPO="${FECS_REPO:-FecsStudios/fecs-store}"
DEST="$HOME/.local/share/fecs-store"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
BIN="$HOME/.local/bin"

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$DEST" "$APPS/fecs-store.desktop" "$ICONS/fecs-store.png" "$BIN/fecs-store"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  echo "Fecs Store desinstalada. (Tus ajustes quedan en ~/.config/fecs-store por si la reinstalas.)"
  exit 0
fi

[ "$(uname -m)" = "x86_64" ] || { echo "Por ahora solo hay versión para x86_64 (64 bits)."; exit 1; }
if command -v curl >/dev/null; then
  get()  { curl -fsSL "$1"; }
  save() { curl -fL --progress-bar -o "$2" "$1"; }
elif command -v wget >/dev/null; then
  get()  { wget -qO- "$1"; }
  save() { wget -q --show-progress -O "$2" "$1"; }
else
  echo "Necesitas curl o wget para instalar."; exit 1
fi

echo "Buscando la última versión…"
URL="$(get "https://api.github.com/repos/$REPO/releases/latest" \
      | grep -o '"browser_download_url": *"[^"]*\.AppImage"' | head -n1 | cut -d'"' -f4)"
[ -n "$URL" ] || { echo "No encontré ninguna versión publicada."; exit 1; }

mkdir -p "$DEST" "$APPS" "$ICONS" "$BIN"
save "$URL" "$DEST/Fecs_Store.AppImage.part"
chmod +x "$DEST/Fecs_Store.AppImage.part"
mv -f "$DEST/Fecs_Store.AppImage.part" "$DEST/Fecs_Store.AppImage"

# Ícono (se extrae del propio AppImage)
TMP="$(mktemp -d)"
( cd "$TMP" && APPIMAGE_EXTRACT_AND_RUN=1 "$DEST/Fecs_Store.AppImage" --appimage-extract fecs-store.png >/dev/null 2>&1 || true )
[ -f "$TMP/squashfs-root/fecs-store.png" ] && cp "$TMP/squashfs-root/fecs-store.png" "$ICONS/fecs-store.png"
rm -rf "$TMP"

# Lanzador: si el sistema no tiene FUSE, el AppImage se ejecuta extrayéndose solo (funciona igual)
{
  echo '#!/bin/sh'
  if ! { [ -e /dev/fuse ] && { command -v fusermount3 >/dev/null || command -v fusermount >/dev/null; }; }; then
    echo 'export APPIMAGE_EXTRACT_AND_RUN=1'
  fi
  echo "exec \"$DEST/Fecs_Store.AppImage\" \"\$@\""
} > "$BIN/fecs-store"
chmod +x "$BIN/fecs-store"

cat > "$APPS/fecs-store.desktop" <<EOT
[Desktop Entry]
Type=Application
Name=Fecs Store
Comment=Tienda de aplicaciones: Flatpak, nativas y web
Exec=$BIN/fecs-store
Icon=fecs-store
Terminal=false
Categories=System;PackageManager;
StartupWMClass=fecs-store
EOT
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

echo "Listo. Abre «Fecs Store» desde tu menú de aplicaciones (o escribe: fecs-store)."
echo "Se actualiza sola cuando haya versiones nuevas."
