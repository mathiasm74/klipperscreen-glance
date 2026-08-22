# Glance — design conventions

Glance is the at-a-glance dark UI for a Voron 2 3D-printer touchscreen (1024×600, viewed at arm's length or across a room). This project is a **visual reference**, not a component bundle: the shipped artifacts are `styles.css` (design tokens as CSS custom properties), `fonts/` (Anton, Space Grotesk), and per-screen preview cards. Build new screens by composing plain HTML/CSS with these tokens — there are no JS components to import.

## Rules that make something look like Glance

- **Dark, always.** Page on `var(--bg)` #0b0d10; controls on `var(--surface)` #16181d with 10px radius; cards on `var(--card)` with 1px `var(--card-border)` and 14px radius; modals are a `var(--sheet)` card (18px radius, 2px border) centered over `var(--backdrop)`.
- **One phase color rules the screen.** The machine phase picks one of `--ph-heat` (amber: heating/soaking/cooling/paused), `--ph-prep` (cyan: homing/QGL/meshing/moves), `--ph-print` (green), `--ph-done` (violet), `--ph-err` (red). That single color paints the screen frame (5px border, top/left/right), the hero number/word, phase-tinted row underlines, and the bottom progress rail (solid fill over a 25%-alpha trough). Idle screens use `--dim` for the frame.
- **The frame is the layout.** Content sits inside the phase-colored frame; the 32px progress rail is the frame's bottom edge — never a floating bar.
- **One display element per screen.** Exactly one number or word gets Anton (`var(--font-display)`): 160px hero percent, 88px hero word (READY/PAUSED/DONE/ERROR), or 58px live readout. Everything else is Space Grotesk (`var(--font-ui)`): 30px values, 26px sublines/verbs, 24px labels in `--dim`, 20px filenames in `--dim`.
- **Hero left, utility right.** Screens split into a hero column (phase word small and dim on top, huge Anton figure, one subline in `--text-soft`) and a 45%-wide side column (thumbnail/cards, label-value rows with 3px bottom borders, then action buttons).
- **Selected state is loud.** Toggled/selected controls fill solid `--selected-bg` cyan with `--selected-fg` dark bold text, one type size up. Primary "go" verbs get green **text** on a normal dark button, never a green fill.
- **Touch targets:** buttons ≥ 42px tall (68px for primary job actions); tappable maps beat button grids — a bed map you tap is the Glance way to move a tool head.

## Where the truth lives

Read `styles.css` for every token before styling. Each screen's composition is in `components/Screens/*/(JobStatus|Home|Move).html` — self-contained recreations whose inline CSS shows the exact layout recipe (frame/rail shell, hero column, side column, row patterns).

## Minimal screen skeleton

```html
<div class="stage"><!-- 1024x600, background: var(--bg) -->
  <div class="titlebar">…</div>              <!-- 44px, dim -->
  <div class="trow">
    <div class="action-bar">…</div>          <!-- 88px icon rail + red STOP -->
    <div class="panel">
      <div class="frame ph-print">           <!-- 5px phase border, no bottom -->
        <div class="hero">…</div>
        <div class="side">…</div>            <!-- width: 45% -->
      </div>
      <div class="rail ph-print"><div class="fill" style="width:67%"></div></div>
    </div>
  </div>
</div>
```
