# -*- coding: utf-8 -*-
# Glance Extrude: filament control. Hero nozzle temp with a Preheat-style
# temperature slider (amber fill to live, white tick, cyan target handle),
# big Retract/Extrude verbs echoing the selected distance, segmented
# distance/speed chips, and Unload/Load macro buttons. The bottom rail
# animates the stroke while filament moves. Everything that pushes
# filament is gated on can_extrude (Klipper refuses cold extrusion).

import math

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk
from ks_includes.screen_panel import ScreenPanel

SNAP = 5            # ° target snap while dragging
OFF_BELOW = 10      # ° dragging under this releases to "off"
AMBIENT = 25
DISTANCES = (5, 10, 15, 25)   # mm
SPEEDS = (1, 2, 5, 25)        # mm/s
RAIL_TICK_MS = 80


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Extrude")
        super().__init__(screen, title)
        self.distance = 10
        self.speed = 5
        self.live = 0.0
        self.target = 0.0
        self.pending = None
        self.can_extrude = False
        self.stroke_timer = None
        self.stroke_ticks = 0
        self.stroke_total = 0

        cfg = self._printer.get_config_section("extruder")
        try:
            self.max_temp = float(cfg["max_temp"])
        except (TypeError, KeyError, ValueError):
            self.max_temp = 270
        # can_extrude isn't in KlipperScreen's live status subscription, so it
        # freezes at panel-load value; derive it from the live temp instead
        try:
            self.min_extrude_temp = float(cfg["min_extrude_temp"])
        except (TypeError, KeyError, ValueError):
            self.min_extrude_temp = 170.0

        # ---- left: hero temp + temperature slider ----
        phase = Gtk.Label(label=_("EXTRUDER"), xalign=0)
        phase.get_style_context().add_class("glance-phase")
        self.hero = Gtk.Label(label="—", xalign=0, vexpand=True)
        self.hero.get_style_context().add_class("glance-big")
        self.hero.get_style_context().add_class("ph-prep")
        self.sub_lbl = Gtk.Label(label="", xalign=0)
        self.sub_lbl.get_style_context().add_class("glance-subline")

        shead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        snm = Gtk.Label(label=_("Nozzle"), xalign=0, hexpand=True)
        snm.get_style_context().add_class("glance-temp-name")
        self.target_lbl = Gtk.Label(label=_("off"), valign=Gtk.Align.END)
        self.target_lbl.get_style_context().add_class("glance-target-off")
        shead.pack_start(snm, True, True, 0)
        shead.pack_end(self.target_lbl, False, False, 0)

        self.slider = Gtk.DrawingArea(hexpand=True)
        self.slider.set_size_request(-1, 64)
        self.slider.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.slider.connect("draw", self.on_slider_draw)
        self.slider.connect("button-press-event", self.on_slider_press)
        self.slider.connect("motion-notify-event", self.on_slider_motion)
        self.slider.connect("button-release-event", self.on_slider_release)

        hero_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           hexpand=True, vexpand=True)
        hero_col.get_style_context().add_class("glance-hero")
        hero_col.pack_start(phase, False, False, 0)
        hero_col.pack_start(self.hero, True, True, 0)
        hero_col.pack_start(self.sub_lbl, False, False, 0)
        hero_col.pack_start(shead, False, False, 6)
        hero_col.pack_start(self.slider, False, False, 0)

        # ---- right: verbs + chips + load/unload ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.get_style_context().add_class("glance-side")
        side.set_hexpand(False)
        side.set_size_request(int(self._screen.width * 0.40), -1)

        self.btn_retract = self._verb_card(_("Retract"), False)
        self.btn_extrude = self._verb_card(_("Extrude"), True)
        self.btn_retract["btn"].connect("clicked", self.stroke, -1)
        self.btn_extrude["btn"].connect("clicked", self.stroke, +1)
        verbs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # top of the cards sits level with the bottom of the EXTRUDER title
        verbs.set_margin_top(32)
        verbs.pack_start(self.btn_retract["btn"], True, True, 0)
        verbs.pack_start(self.btn_extrude["btn"], True, True, 0)
        side.pack_start(verbs, False, False, 0)

        dlbl = Gtk.Label(label=_("Distance mm"), xalign=0)
        dlbl.get_style_context().add_class("glance-temp-name")
        side.pack_start(dlbl, False, False, 0)
        self.dist_btns = self._segmented(DISTANCES, self.set_distance,
                                         DISTANCES.index(self.distance))
        side.pack_start(self.dist_btns["box"], False, False, 0)

        slbl = Gtk.Label(label=_("Speed mm/s"), xalign=0)
        slbl.get_style_context().add_class("glance-temp-name")
        side.pack_start(slbl, False, False, 0)
        self.speed_btns = self._segmented(SPEEDS, self.set_speed,
                                          SPEEDS.index(self.speed))
        side.pack_start(self.speed_btns["box"], False, False, 0)

        side.pack_start(Gtk.Box(vexpand=True), True, True, 0)

        self.btn_unload = Gtk.Button(label=_("Unload"))
        self.btn_load = Gtk.Button(label=_("Load"))
        lu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for b, macro in ((self.btn_unload, "UNLOAD_FILAMENT"),
                         (self.btn_load, "LOAD_FILAMENT")):
            b.get_style_context().add_class("glance-ext-lu")
            b.connect("clicked", self.run_macro, macro)
            lu.pack_start(b, True, True, 0)
        side.pack_end(lu, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                       vexpand=True)
        main.pack_start(hero_col, True, True, 0)
        main.pack_end(side, False, False, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, 32)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.root, True, True, 0)
        inner.pack_end(self.rail, False, False, 0)
        self.content.pack_start(inner, True, True, 0)
        for w in (self.root, self.rail):
            w.get_style_context().add_class("ph-prep")

    # ---- widget builders ----------------------------------------------------

    def _verb_card(self, name, is_go):
        btn = Gtk.Button()
        ctx = btn.get_style_context()
        ctx.add_class("glance-ext-verb")
        if is_go:
            ctx.add_class("glance-ext-go")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      valign=Gtk.Align.CENTER)
        nm = Gtk.Label(label=name)
        sub = Gtk.Label(label=f"{self.distance} mm")
        sub.get_style_context().add_class("glance-ext-sub")
        box.pack_start(nm, False, False, 0)
        box.pack_start(sub, False, False, 0)
        btn.add(box)
        return {"btn": btn, "sub": sub}

    def _segmented(self, values, cb, active_idx):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        buttons = []
        for i, v in enumerate(values):
            b = Gtk.Button(label=str(v))
            ctx = b.get_style_context()
            ctx.add_class("horizontal_togglebuttons")
            if i == active_idx:
                ctx.add_class("horizontal_togglebuttons_active")
            b.connect("clicked", cb, v, buttons)
            b.set_hexpand(True)
            buttons.append(b)
            box.pack_start(b, True, True, 0)
        return {"box": box, "buttons": buttons}

    @staticmethod
    def _select(buttons, widget):
        for b in buttons:
            ctx = b.get_style_context()
            if b is widget:
                ctx.add_class("horizontal_togglebuttons_active")
            else:
                ctx.remove_class("horizontal_togglebuttons_active")

    # ---- slider (same grammar as the preheat panel) -------------------------

    def _track(self):
        w = self.slider.get_allocated_width()
        return 2, 6, w - 4, 34

    def _temp_to_x(self, t):
        x, _y, w, _hh = self._track()
        return x + max(0.0, min(1.0, t / self.max_temp)) * w

    def _x_to_temp(self, px):
        x, _y, w, _hh = self._track()
        t = (px - x) / w * self.max_temp
        return max(0.0, min(self.max_temp, round(t / SNAP) * SNAP))

    @staticmethod
    def _rounded(cr, x, y, w, hh, r):
        cr.new_path()
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(x + w - r, y + hh - r, r, 0, 0.5 * math.pi)
        cr.arc(x + r, y + hh - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()

    def on_slider_draw(self, area, cr):
        x, y, w, hh = self._track()
        self._rounded(cr, x, y, w, hh, 10)
        cr.set_source_rgb(0.086, 0.094, 0.113)
        cr.fill()
        live_x = self._temp_to_x(self.live)
        if self.live > AMBIENT + 2:
            self._rounded(cr, x, y, max(live_x - x, 20), hh, 10)
            cr.set_source_rgba(1.0, 0.69, 0.0, 0.32)
            cr.fill()
        cr.set_source_rgb(0.91, 0.918, 0.929)
        cr.rectangle(live_x - 2, y - 4, 4, hh + 8)
        cr.fill()
        target = self.pending if self.pending is not None else self.target
        if target > 0:
            tx = self._temp_to_x(target)
            self._rounded(cr, tx - 3.5, y - 8, 7, hh + 16, 3.5)
            cr.set_source_rgb(0.2, 0.773, 0.91)
            cr.fill()
        cr.select_font_face("Space Grotesk")
        cr.set_font_size(18)
        cr.set_source_rgb(0.353, 0.365, 0.4)
        step = 100 if self.max_temp > 200 else 50
        ticks = [t for t in range(0, int(self.max_temp), step)
                 if self.max_temp - t > step * 0.35] + [self.max_temp]
        for t in ticks:
            label = f"{t:.0f}"
            ext = cr.text_extents(label)
            lx = max(x, min(x + w - ext.width,
                            self._temp_to_x(t) - ext.width / 2))
            cr.move_to(lx, y + hh + 22)
            cr.show_text(label)
        return True

    def on_slider_press(self, area, event):
        self.pending = self._x_to_temp(event.x)
        area.queue_draw()
        return True

    def on_slider_motion(self, area, event):
        self.pending = self._x_to_temp(event.x)
        self._show_target(self.pending)
        area.queue_draw()
        return True

    def on_slider_release(self, area, event):
        t = self._x_to_temp(event.x)
        self.pending = None
        if t < OFF_BELOW:
            t = 0.0
        self._screen._ws.klippy.gcode_script(
            f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={t:.0f}")
        return True

    # ---- actions ------------------------------------------------------------

    def set_distance(self, widget, value, buttons):
        self.distance = value
        self._select(buttons, widget)
        for card in (self.btn_retract, self.btn_extrude):
            card["sub"].set_label(f"{value} mm")

    def set_speed(self, widget, value, buttons):
        self.speed = value
        self._select(buttons, widget)

    def stroke(self, widget, sign):
        if not self.can_extrude or self.stroke_timer:
            return
        self._screen._ws.klippy.gcode_script(
            f"M83\nG1 E{sign * self.distance} F{self.speed * 60}")
        # the rail shows the stroke: fill over the move's real duration
        self.stroke_total = max(1, int(self.distance / self.speed * 1000
                                       / RAIL_TICK_MS))
        self.stroke_ticks = 0
        self._gate_verbs(False)
        self.stroke_timer = GLib.timeout_add(RAIL_TICK_MS, self._stroke_tick)

    def _stroke_tick(self):
        self.stroke_ticks += 1
        if self.stroke_ticks >= self.stroke_total:
            self.rail.set_fraction(0)
            self.stroke_timer = None
            self._gate_verbs(self.can_extrude)
            return False
        self.rail.set_fraction(self.stroke_ticks / self.stroke_total)
        return True

    def run_macro(self, widget, macro):
        if not self.can_extrude:
            return
        self._screen._ws.klippy.gcode_script(macro)

    def _gate_verbs(self, on):
        for b in (self.btn_retract["btn"], self.btn_extrude["btn"],
                  self.btn_unload, self.btn_load):
            b.set_sensitive(on)

    # ---- updates ------------------------------------------------------------

    def _show_target(self, target):
        ctx = self.target_lbl.get_style_context()
        if target > 0:
            self.target_lbl.set_label(_("target") + f" {target:.0f}°")
            ctx.add_class("glance-target")
            ctx.remove_class("glance-target-off")
        else:
            self.target_lbl.set_label(_("off"))
            ctx.add_class("glance-target-off")
            ctx.remove_class("glance-target")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        live = self._printer.get_stat("extruder", "temperature")
        if live is None or isinstance(live, dict):
            return
        self.live = float(live)
        self.target = float(self._printer.get_stat("extruder", "target") or 0)
        self.can_extrude = self.live >= self.min_extrude_temp
        bed = self._printer.get_stat("heater_bed", "temperature")

        self.hero.set_label(f"{self.live:.0f}°")
        hctx = self.hero.get_style_context()
        heating = self.target > 0 and self.live < self.target - 2
        if heating:
            hctx.add_class("ph-heat")
            hctx.remove_class("ph-prep")
            self.sub_lbl.set_label(
                _("heating") + f"  ·  {self.live:.0f} / {self.target:.0f}°")
        else:
            hctx.add_class("ph-prep")
            hctx.remove_class("ph-heat")
            if self.can_extrude:
                bed_txt = f"  ·  " + _("bed") + f" {bed:.0f}°" \
                    if bed is not None and not isinstance(bed, dict) else ""
                self.sub_lbl.set_label(_("ready to move filament") + bed_txt)
            else:
                self.sub_lbl.set_label(_("nozzle cold — heat to move filament"))
        if self.pending is None:
            self._show_target(self.target)
        if not self.stroke_timer:
            self._gate_verbs(self.can_extrude)
        self.slider.queue_draw()

    def deactivate(self):
        if self.stroke_timer:
            GLib.source_remove(self.stroke_timer)
            self.stroke_timer = None
            self.rail.set_fraction(0)
