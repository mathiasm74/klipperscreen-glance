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
| ![Home](screenshots/home.png) | ![Move](screenshots/move.png) |
| **Home** — READY/WARM/HEATING hero, icon verb cards, scrollable print-again history | **Move** — tap-to-go bed map, Z height map, one-tap verbs |
| ![Preheat](screenshots/preheat.png) | ![Extrude](screenshots/extrude.png) |
| **Preheat** — draggable heater sliders, Mainsail-style power graph, material profiles | **Extrude** — queueable strokes with rail count badge, cold-gated verbs |

A print walks the job screen through its phases:

| | |
|---|---|
| ![Heating](screenshots/job-heating.png) | ![Leveling](screenshots/job-leveling.png) |
| **Heating** — amber, ambient-referenced percent from the M117 heat bars | **Leveling** — cyan, QGL driven by the heat-soak gate |
| ![Meshing](screenshots/job-meshing.png) | ![Printing](screenshots/job-printing.png) |
| **Meshing** — fresh bed mesh when the soak gate demands one | **Printing** — green percent, time left + ETA, Speed and Z-offset steppers |
| ![Job paused](screenshots/job-paused.png) | ![Job done](screenshots/job-done.png) |
| **Paused** — filament controls swap in; resume/stop verbs | **Done** — violet phase, temps cooling, Save Z |

## Panels

- `glance_home` — idle screen; replaces the main menu
- `glance_status` — job screen; heating/leveling/printing/paused/done phases
- `glance_move` — bed + Z maps with drag targets, precise-move sheet, Z-offset calibration
- `glance_preheat` — heater sliders (nozzle 100–270, left edge = off), 6-min temp+power graph, `[preheat]` profiles, keep-warm Hold
- `glance_extrude` — filament strokes with Klipper-queue-backed repeat taps, Unload/Load macros

## Repo layout

- `src/` — everything that lands on the Pi: the five panels, the theme
  stylesheet, the fonts (OFL-licensed Anton + Space Grotesk), and the one
  2-line `screen.py.patch` that reroutes KlipperScreen's ready/printing
  states to the glance panels (KlipperScreen has no config hook for that)
- `klipper/` — the printer-side contract: `glance.cfg` with the
  `_HEAT_WAIT` heat-narrating macro, filament helpers, soak bookkeeping,
  and the documented M117 phase protocol the job screen parses
- `design/` — the design system: `ds-bundle/` cards + tokens synced to a
  claude.ai/design project, and their sources
- `install.sh`, `tools/` — installer and the stylesheet scaler

## Install (on the printer Pi, KlipperScreen v0.4.x)

```
cd ~ && git clone https://github.com/mathiasm74/klipperscreen-glance.git
cd klipperscreen-glance && ./install.sh
```

The installer copies panels/theme/fonts, applies the patch (and tells you
if upstream KlipperScreen has drifted), stages `glance.cfg` next to your
printer config, and prints the two manual steps: `[include glance.cfg]`
in printer.cfg and `theme: glance` + `auto_open_extrude: False` in
KlipperScreen.conf (plus optional `[preheat NAME]` profiles). For updates
through Moonraker's Update Manager:

```
[update_manager glance]
type: git_repo
path: ~/klipperscreen-glance
origin: https://github.com/mathiasm74/klipperscreen-glance.git
managed_services: KlipperScreen
install_script: install.sh
```

## What degrades gracefully

The panels check the printer's own config at load: no
`UNLOAD_FILAMENT`/`LOAD_FILAMENT` macros → the filament buttons hide; no
`quad_gantry_level` → the Move screen drops its QGL verb (and QGL falls
back to `QUAD_GANTRY_LEVEL` when there's no `G32` macro); no probe → no
Save Z. Without `glance.cfg`'s `_HEAT_WAIT` in your PRINT_START the job
screen still works from print_stats, but loses the amber heating percent
and the leveling/meshing phases — blocking M109/M190 waits freeze all
screen updates, which is half the reason `_HEAT_WAIT` exists.

## Other screen sizes

The layout is designed at 1024×600. Panels route their absolute pixel
sizes through a shared scale (`min(w/1024, h/600)`) at runtime, and
`install.sh --width 800 --height 480` generates a proportionally scaled
stylesheet for e.g. a 5" HDMI display. That's mechanical scaling of one
source design — no forked copies — so expect it to be usable but worth a
fine-tuning pass on real hardware.
