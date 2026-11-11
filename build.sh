#!/usr/bin/env bash
# Build the AppImage. appimagetool is downloaded on demand (with confirmation).
set -euo pipefail
cd "$(dirname "$0")"

if command -v appimagetool >/dev/null 2>&1; then
  TOOL="$(command -v appimagetool)"
else
  TOOL="$PWD/build/appimagetool"
fi

if [ ! -x "$TOOL" ]; then
  echo "appimagetool is required to build the AppImage but was not found."
  read -r -p "Download it to $TOOL now? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted: appimagetool not installed."; exit 1 ;;
  esac
  mkdir -p build
  curl -fL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi

APPIMAGETOOL="$TOOL" ./packaging/build-appimage.sh

# Offer to clean up a locally downloaded appimagetool (never touches one from PATH).
if [ "$TOOL" = "$PWD/build/appimagetool" ]; then
  read -r -p "Remove the downloaded appimagetool ($TOOL)? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) rm -f "$TOOL"; echo "Removed appimagetool." ;;
    *) echo "Keeping appimagetool." ;;
  esac
fi

# KDE only runs a .desktop launcher on a click when it sits on the Desktop, so offer to put both files there.
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ] && [ "$(readlink -f "$DESKTOP_DIR")" != "$(readlink -f "$PWD")" ]; then
  read -r -p "Copy the AppImage and installer launcher to $DESKTOP_DIR? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS])
      cp -p RomM-Deck-x86_64.AppImage "Install RomM Deck.desktop" "$DESKTOP_DIR/"
      echo "Copied. Double-click \"Install RomM Deck\" on the Desktop to install." ;;
    *) echo "Not copied." ;;
  esac
fi
