# Glance — at-a-glance KlipperScreen UI

Custom theme + job status panel for my Voron 2 (1024×600 touchscreen).
One giant phase-colored progress numeral, bottom progress rail, part
thumbnail, live Speed/Flow steppers (extrude controls while paused).
Phases: HEATING (amber) → LEVELING/MESHING (cyan) → PRINTING (green) →
DONE (violet), driven by print_stats + the M117 protocol emitted by
PRINT_START in my Klipper config repo.

## Install (on the printer Pi, KlipperScreen v0.4.x)

```
cp -r styles/glance ~/KlipperScreen/styles/
cp panels/glance_status.py ~/KlipperScreen/panels/
cd ~/KlipperScreen && git apply /path/to/screen.py.patch
# fonts: Anton + Space Grotesk TTFs into ~/.local/share/fonts, fc-cache -f
# KlipperScreen.conf [main]: theme: glance, auto_open_extrude: False
sudo systemctl restart KlipperScreen
```

Deployed live as branch `glance` in the Pi's ~/KlipperScreen repo.
