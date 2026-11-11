#!/usr/bin/env bash
# Build RomM-Deck-x86_64.AppImage. Needs appimagetool on PATH, or pass its path
# in APPIMAGETOOL (https://github.com/AppImage/appimagetool/releases).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPDIR="$ROOT/build/RomM-Deck.AppDir"
TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
[ -n "$TOOL" ] || { echo "appimagetool not found; set APPIMAGETOOL=/path/to/it" >&2; exit 1; }
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/share/romm-deck"
cp -r "$ROOT/rommdeck" "$ROOT/assets" "$APPDIR/usr/share/romm-deck/"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
chmod -R a+rX "$APPDIR"
install -m755 "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
cp "$ROOT/assets/icon.png" "$APPDIR/romm-deck.png"
cp "$ROOT/assets/icon.png" "$APPDIR/.DirIcon"
chmod -R a+rX "$APPDIR"
cat > "$APPDIR/romm-deck.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=RomM Deck
Comment=Browse and download games from your RomM server
Exec=AppRun
Icon=romm-deck
Categories=Game;
Terminal=false
DESK
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$ROOT/RomM-Deck-x86_64.AppImage"
chmod +x "$ROOT/RomM-Deck-x86_64.AppImage"
python3 "$ROOT/packaging/make-launcher.py" "$ROOT/Install RomM Deck.desktop"
echo "Built $ROOT/RomM-Deck-x86_64.AppImage and the Install RomM Deck.desktop launcher"
