#!/bin/bash
# Install the Glance UI into a KlipperScreen checkout. Idempotent.
#
#   ./install.sh [--ks-dir DIR] [--width W --height H] [--no-restart]
#
#   --ks-dir      KlipperScreen checkout (default: ~/KlipperScreen)
#   --width/-height  your screen resolution; when it isn't 1024x600 the
#                 stylesheet is scaled proportionally (panels scale at runtime)
#   --no-restart  skip the KlipperScreen service restart
set -e
cd "$(dirname "$0")"

KS="$HOME/KlipperScreen"
WIDTH=1024; HEIGHT=600; RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ks-dir) KS="$2"; shift 2 ;;
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

[ -d "$KS/panels" ] || { echo "error: $KS does not look like a KlipperScreen checkout"; exit 1; }
echo "Installing Glance into $KS (screen ${WIDTH}x${HEIGHT})"

# panels
cp src/panels/glance_*.py "$KS/panels/"

# theme: stylesheet (scaled if not the reference size) + icon symlink
mkdir -p "$KS/styles/glance"
if [ "$WIDTH" = 1024 ] && [ "$HEIGHT" = 600 ]; then
  cp src/styles/glance/style.css "$KS/styles/glance/style.css"
else
  python3 tools/scale_css.py src/styles/glance/style.css "$WIDTH" "$HEIGHT" \
    > "$KS/styles/glance/style.css"
  echo "note: stylesheet scaled for ${WIDTH}x${HEIGHT} (reference is 1024x600);"
  echo "      expect to fine-tune - this is mechanical scaling, not a redesign"
fi
[ -e "$KS/styles/glance/images" ] || ln -s ../z-bolt/images "$KS/styles/glance/images"

# fonts
mkdir -p "$HOME/.local/share/fonts"
cp src/fonts/*.ttf "$HOME/.local/share/fonts/"
fc-cache -f > /dev/null

# the one core patch: route ready/printing states to the glance panels
if git -C "$KS" apply --reverse --check "$(pwd)/src/screen.py.patch" 2>/dev/null; then
  echo "screen.py patch: already applied"
elif git -C "$KS" apply --check "$(pwd)/src/screen.py.patch" 2>/dev/null; then
  git -C "$KS" apply "$(pwd)/src/screen.py.patch"
  echo "screen.py patch: applied"
else
  echo "error: screen.py.patch does not apply cleanly - KlipperScreen has"
  echo "  drifted from the version this patch targets. Open screen.py and"
  echo "  reroute state_ready -> glance_home, state_printing -> glance_status."
  exit 1
fi

# klipper-side contract: stage glance.cfg next to the printer config
CFG_DIR="$HOME/printer_data/config"
if [ -d "$CFG_DIR" ]; then
  cp -n klipper/glance.cfg "$CFG_DIR/glance.cfg" 2>/dev/null \
    && echo "staged $CFG_DIR/glance.cfg" \
    || echo "$CFG_DIR/glance.cfg already exists - left untouched"
  grep -rqs "include glance.cfg" "$CFG_DIR"/printer.cfg \
    || echo "TODO  add to printer.cfg:            [include glance.cfg]"
  grep -rqs "PRINT_START" "$CFG_DIR"/*.cfg \
    || echo "note: no PRINT_START macro found - see the example in glance.cfg"
  grep -rqs "quad_gantry_level" "$CFG_DIR"/*.cfg \
    || echo "note: no quad_gantry_level - the Move screen hides its QGL verb"
fi

cat <<'EOT'
TODO  in KlipperScreen.conf ([main] section):
        theme: glance
        auto_open_extrude: False
      and optionally material profiles for the Preheat screen:
        [preheat PLA]
        extruder: 215
        bed: 60
EOT

if [ "$RESTART" = 1 ]; then
  sudo systemctl restart KlipperScreen 2>/dev/null \
    && echo "KlipperScreen restarted" \
    || echo "TODO  restart KlipperScreen: sudo systemctl restart KlipperScreen"
fi
echo "done"
