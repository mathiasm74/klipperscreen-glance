# -*- coding: utf-8 -*-
# Glance Move: manual control where coarse motion is spatial and fine motion
# explicit. Tap the bed map and the head goes there (via a safe-Z hop); the Z
# strip has direct destinations (Z 0 solves "get to exactly 0.00"); jog uses a
# 3-step selector whose selected segment is unmistakable at any viewing angle.

import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, Pango
from ks_includes.screen_panel import ScreenPanel

STEPS = (0.1, 1, 10)
SPEEDS = (50, 150, 300)
SAFE_Z = 15.0
Z_SPEED = 15  # mm/s


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Move")
        super().__init__(screen, title)
        self.step = 1.0
        self.speed = 150
        self.pos = [0.0, 0.0, 0.0]
        self.homed = ""
        self.tap_target = None

        # bed size from config
        self.max_x = self._axis_max("stepper_x", 300)
        self.max_y = self._axis_max("stepper_y", 300)
        self.max_z = self._axis_max("stepper_z", 280)

        # ---- bed map ----
        self.map = Gtk.DrawingArea()
        self.map.set_size_request(440, 440)
        self.map.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.map.connect("draw", self.on_draw)
        self.map.connect("button-press-event", self.on_map_tap)
        map_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           valign=Gtk.Align.CENTER)
        map_wrap.pack_start(self.map, False, False, 0)
        pos_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.axis_vals = {}
        for axis in ("X", "Y", "Z"):
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
            cell.get_style_context().add_class("glance-temp-row")
            cell.get_style_context().add_class("ph-prep")
            n = Gtk.Label(label=axis, xalign=0)
            n.get_style_context().add_class("glance-temp-name")
            v = Gtk.Label(label="—", xalign=1, hexpand=True)
            v.get_style_context().add_class("glance-temp-val")
            cell.pack_start(n, False, False, 0)
            cell.pack_end(v, True, True, 0)
            pos_row.pack_start(cell, True, True, 0)
            self.axis_vals[axis] = v
        map_wrap.pack_start(pos_row, False, False, 0)
        self.pos_lbl = Gtk.Label(label=_("taps travel at") + f" Z ≥ {SAFE_Z:.0f}", xalign=0)
        self.pos_lbl.get_style_context().add_class("glance-fname")
        map_wrap.pack_start(self.pos_lbl, False, False, 0)

        # ---- right column ----
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.get_style_context().add_class("glance-side")
        col.set_hexpand(True)

        zrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zt = Gtk.Label(label="Z", xalign=0)
        zt.get_style_context().add_class("glance-temp-name")
        self.z_lbl = Gtk.Label(label="—", xalign=1, hexpand=True)
        self.z_lbl.get_style_context().add_class("glance-temp-val")
        zdown = Gtk.Button(label="▼")
        zup = Gtk.Button(label="▲")
        for b, d in ((zdown, -1), (zup, 1)):
            b.get_style_context().add_class("glance-jog")
            b.set_hexpand(False)
            b.connect("clicked", self.z_jog, d)
        zrow.pack_start(zt, False, False, 0)
        zrow.pack_start(self.z_lbl, True, True, 0)
        zrow.pack_end(zup, False, False, 0)
        zrow.pack_end(zdown, False, False, 0)
        col.pack_start(zrow, False, False, 0)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.z_chips = []
        for label, z in (("Z 0", 0), ("10", 10), ("50", 50), ("100", 100)):
            c = Gtk.Button(label=label)
            c.get_style_context().add_class("glance-row-btn")
            c.set_hexpand(False)
            c.connect("clicked", self.z_goto, z)
            chips.pack_start(c, True, True, 0)
            self.z_chips.append(c)
        col.pack_start(chips, False, False, 0)

        # (the XY jog cross is gone: the tap-to-move map covers coarse XY and
        # taps can be as precise as the finger; the step only drives Z jog now)
        self.step_sel = self._segmented([f"{s:g}" for s in STEPS], self.set_step, 1)
        col.pack_start(self._selrow(_("Z step · mm"), self.step_sel), False, False, 0)
        self.speed_sel = self._segmented([str(s) for s in SPEEDS], self.set_speed, 1)
        col.pack_start(self._selrow(_("Travel · mm/s"), self.speed_sel), False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, cb in ((_("Home"), self.home), ("QGL", self.qgl),
                          (_("Motors off"), self.motors_off)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("glance-row-btn")
            b.set_hexpand(False)
            b.connect("clicked", cb)
            actions.pack_start(b, True, True, 0)
        col.pack_end(actions, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                       vexpand=True, spacing=12)
        main.get_style_context().add_class("glance-hero")
        main.pack_start(map_wrap, False, False, 0)
        main.pack_start(col, True, True, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, 32)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.root, True, True, 0)
        inner.pack_end(self.rail, False, False, 0)
        self.content.add(inner)
        for w in (self.root, self.rail):
            w.get_style_context().add_class("ph-prep")

    def _axis_max(self, section, fallback):
        cfg = self._printer.get_config_section(section)
        try:
            return float(cfg["position_max"])
        except (TypeError, KeyError, ValueError):
            return fallback

    def _segmented(self, labels, cb, active_idx):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        buttons = []
        for i, text in enumerate(labels):
            b = Gtk.Button(label=text)
            ctx = b.get_style_context()
            ctx.add_class("horizontal_togglebuttons")
            if i == active_idx:
                ctx.add_class("horizontal_togglebuttons_active")
            b.connect("clicked", cb, i, buttons)
            box.pack_start(b, True, True, 0)
            buttons.append(b)
        return box

    @staticmethod
    def _selrow(name, selector):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl = Gtk.Label(label=name, xalign=0)
        lbl.get_style_context().add_class("glance-temp-name")
        lbl.set_size_request(150, -1)
        row.pack_start(lbl, False, False, 0)
        row.pack_start(selector, True, True, 0)
        return row

    # ---- drawing -------------------------------------------------------------

    def on_draw(self, da, cr):
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        # surface
        cr.set_source_rgb(0.063, 0.078, 0.106)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # grid every 50mm
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.07)
        step = 50
        for gx in range(0, int(self.max_x) + 1, step):
            x = gx / self.max_x * w
            cr.move_to(x, 0)
            cr.line_to(x, h)
        for gy in range(0, int(self.max_y) + 1, step):
            y = h - gy / self.max_y * h
            cr.move_to(0, y)
            cr.line_to(w, y)
        cr.stroke()
        # border
        cr.set_source_rgba(0.23, 0.26, 0.33, 1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        # snap dots: corners + center
        cr.set_source_rgba(0.2, 0.77, 0.91, 0.5)
        for sx, sy in ((0, 0), (self.max_x, 0), (0, self.max_y),
                       (self.max_x, self.max_y), (self.max_x / 2, self.max_y / 2)):
            x = sx / self.max_x * w
            y = h - sy / self.max_y * h
            cr.arc(min(max(x, 6), w - 6), min(max(y, 6), h - 6), 4, 0, 6.284)
            cr.fill()
        xy_homed = "x" in self.homed and "y" in self.homed
        # tap target ghost
        if self.tap_target and xy_homed:
            tx, ty = self.tap_target
            x = tx / self.max_x * w
            y = h - ty / self.max_y * h
            cr.set_source_rgba(0.2, 0.77, 0.91, 0.8)
            cr.set_line_width(1.5)
            cr.arc(x, y, 10, 0, 6.284)
            cr.stroke()
        # head position
        if xy_homed:
            x = self.pos[0] / self.max_x * w
            y = h - self.pos[1] / self.max_y * h
            cr.set_source_rgba(0.91, 0.92, 0.93, 0.25)
            cr.set_line_width(1)
            cr.move_to(x, 0); cr.line_to(x, h)
            cr.move_to(0, y); cr.line_to(w, y)
            cr.stroke()
            cr.set_source_rgb(0.25, 0.84, 0.41)
            cr.arc(x, y, 7, 0, 6.284)
            cr.fill()
        else:
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            cr.set_source_rgb(0.91, 0.92, 0.93)
            cr.select_font_face("Space Grotesk")
            cr.set_font_size(30)
            text = _("Home first")
            ext = cr.text_extents(text)
            cr.move_to((w - ext.width) / 2, h / 2)
            cr.show_text(text)
        return False

    # ---- interaction ---------------------------------------------------------

    def on_map_tap(self, da, event):
        if "x" not in self.homed or "y" not in self.homed:
            return True
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        bx = event.x / w * self.max_x
        by = (1 - event.y / h) * self.max_y
        # snap when near a snap point (in bed mm; ~15px)
        snap_r = 15 / w * self.max_x
        for sx, sy in ((0, 0), (self.max_x, 0), (0, self.max_y),
                       (self.max_x, self.max_y), (self.max_x / 2, self.max_y / 2)):
            if abs(bx - sx) < snap_r and abs(by - sy) < snap_r:
                bx, by = sx, sy
                break
        bx = min(max(bx, 0), self.max_x)
        by = min(max(by, 0), self.max_y)
        self.tap_target = (bx, by)
        self.map.queue_draw()
        script = "G90\n"
        if self.pos[2] < SAFE_Z and "z" in self.homed:
            script += f"G0 Z{SAFE_Z} F{Z_SPEED * 60}\n"
        script += f"G0 X{bx:.1f} Y{by:.1f} F{self.speed * 60}"
        self._screen._ws.klippy.gcode_script(script)
        return True

    def set_step(self, widget, idx, buttons):
        self.step = STEPS[idx]
        self._mark_selected(widget, buttons)

    def set_speed(self, widget, idx, buttons):
        self.speed = SPEEDS[idx]
        self._mark_selected(widget, buttons)

    @staticmethod
    def _mark_selected(widget, buttons):
        for b in buttons:
            b.get_style_context().remove_class("horizontal_togglebuttons_active")
        widget.get_style_context().add_class("horizontal_togglebuttons_active")

    def z_jog(self, widget, sign):
        if "z" not in self.homed:
            return
        self._screen._ws.klippy.gcode_script(
            f"G91\nG0 Z{self.step * sign:g} F{Z_SPEED * 60}\nG90")

    def z_goto(self, widget, z):
        if "z" not in self.homed:
            return
        self._screen._ws.klippy.gcode_script(f"G90\nG0 Z{z:.1f} F{Z_SPEED * 60}")

    def home(self, widget):
        self._screen._ws.klippy.gcode_script("G28")

    def qgl(self, widget):
        self._screen._ws.klippy.gcode_script("G32")

    def motors_off(self, widget):
        self._screen._ws.klippy.gcode_script("M84")

    # ---- data ----------------------------------------------------------------

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        homed = self._printer.get_stat("toolhead", "homed_axes")
        if isinstance(homed, str) and homed != self.homed:
            self.homed = homed
            self.map.queue_draw()
        pos = self._printer.get_stat("gcode_move", "gcode_position")
        if pos and not isinstance(pos, dict):
            self.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
            self.z_lbl.set_label(f"{self.pos[2]:.2f}")
            for axis, i in (("X", 0), ("Y", 1), ("Z", 2)):
                self.axis_vals[axis].set_label(f"{self.pos[i]:.1f}")
            self.map.queue_draw()
