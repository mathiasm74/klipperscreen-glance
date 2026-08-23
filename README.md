# Glance — at-a-glance KlipperScreen UI

Custom theme + panels for my Voron 2 (1024×600 touchscreen). One idea
throughout: each screen shows a single huge phase-colored figure you can
read from across the room, framed by a border whose bottom edge is the
progress rail. Phases: HEATING (amber) → LEVELING/MESHING (cyan) →
PRINTING (green) → DONE (violet) → error (red), driven by print_stats
plus the M117 protocol emitted by PRINT_START in my Klipper config repo.

## Screens

| | |
|---|---|
| ![Home](screenshots/home.png) | ![Job paused](screenshots/job-paused.png) |
| **Home** — READY/WARM/HEATING hero, icon verb cards, scrollable print-again history | **Job status (paused)** — filament controls swap in; resume/stop verbs |
| ![Job done](screenshots/job-done.png) | ![Move](screenshots/move.png) |
| **Job status (done)** — violet phase, temps cooling, Save Z | **Move** — tap-to-go bed map, Z height map, one-tap verbs |
| ![Preheat](screenshots/preheat.png) | ![Extrude](screenshots/extrude.png) |
| **Preheat** — draggable heater sliders, Mainsail-style power graph, material profiles | **Extrude** — queueable strokes with rail count badge, cold-gated verbs |

## Panels

- `glance_home` — idle screen; replaces the main menu
- `glance_status` — job screen; heating/leveling/printing/paused/done phases
- `glance_move` — bed + Z maps with drag targets, precise-move sheet, Z-offset calibration
- `glance_preheat` — heater sliders (nozzle 100–270, left edge = off), 6-min temp+power graph, `[preheat]` profiles, keep-warm Hold
- `glance_extrude` — filament strokes with Klipper-queue-backed repeat taps, Unload/Load macros

## Install (on the printer Pi, KlipperScreen v0.4.x)

```
cp -r styles/glance ~/KlipperScreen/styles/
cp panels/glance_*.py ~/KlipperScreen/panels/
cd ~/KlipperScreen && git apply /path/to/screen.py.patch
# fonts: Anton + Space Grotesk TTFs into ~/.local/share/fonts, fc-cache -f
# KlipperScreen.conf [main]: theme: glance, auto_open_extrude: False
# optional: [preheat NAME] sections feed the Preheat profiles
sudo systemctl restart KlipperScreen
```

Deployed live as branch `glance` in the Pi's ~/KlipperScreen repo. A
design-system companion (tokens, type, per-screen cards) lives in the
`ds-bundle/` directory, synced to a claude.ai/design project.
