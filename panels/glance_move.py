# -*- coding: utf-8 -*-
# Glance Move: manual control where coarse motion is spatial and fine motion
# explicit. Three columns: the tap-to-move bed map, the Z strip (big live Z,
# direct destinations, fine jog), and large single-purpose verbs. A "Precise"
# modal offers exact per-axis nudging at fine steps.

import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk
from ks_includes.screen_panel import ScreenPanel

STEPS = (0.1, 1, 10)
PRECISE_STEPS = (0.01, 0.1, 1, 10)
SPEEDS = (50, 150, 300)
SAFE_Z = 15.0
Z_SPEED = 15  # mm/s


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Move")
        super().__init__(screen, title)
        self.step = 1.0
        self.precise_step = 1.0
        self.speed = 150
        self.pos = [0.0, 0.0, 0.0]
        self.homed = ""

        self.max_x = self._axis_max("stepper_x", 300)
        self.max_y = self._axis_max("stepper_y", 300)
        self.max_z = self._axis_max("stepper_z", 280)

        # ---- left: bed map + readouts ----
        self.map = Gtk.DrawingArea()
        self.map.set_size_request(430, 430)
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
        note = Gtk.Label(label=_("taps travel at") + f" Z ≥ {SAFE_Z:.0f}", xalign=0)
        note.get_style_context().add_class("glance-fname")
        map_wrap.pack_start(note, False, False, 0)

        # ---- middle: the Z column ----
        zcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        zcol.set_size_request(206, -1)
        zcol.set_hexpand(False)

        zhero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zhero.get_style_context().add_class("glance-temp-row")
        zhero.get_style_context().add_class("ph-prep")
        zt = Gtk.Label(label="Z", xalign=0, valign=Gtk.Align.END)
        zt.get_style_context().add_class("glance-temp-name")
        self.z_lbl = Gtk.Label(label="—", xalign=1, hexpand=True)
        self.z_lbl.get_style_context().add_class("glance-z-val")
        zhero.pack_start(zt, False, False, 0)
        zhero.pack_end(self.z_lbl, True, True, 0)
        zcol.pack_start(zhero, False, False, 0)

        chipgrid = Gtk.Grid(row_spacing=8, column_spacing=8,
                            column_homogeneous=True, row_homogeneous=True)
        for i, (label, z) in enumerate((("Z 0", 0), ("10", 10), ("50", 50), ("100", 100))):
            c = Gtk.Button(label=label)
            c.get_style_context().add_class("glance-row-btn")
            c.set_hexpand(False)
            c.connect("clicked", self.z_goto, z)
            chipgrid.attach(c, i % 2, i // 2, 1, 1)
        zcol.pack_start(chipgrid, False, False, 0)

        jogrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for glyph, d in (("▼", -1), ("▲", 1)):
            b = Gtk.Button(label=glyph)
            b.get_style_context().add_class("glance-jog")
            b.set_hexpand(True)
            b.connect("clicked", self.z_jog, d)
            jogrow.pack_start(b, True, True, 0)
        zcol.pack_start(jogrow, False, False, 0)

        steplbl = Gtk.Label(label=_("Z step · mm"), xalign=0)
        steplbl.get_style_context().add_class("glance-temp-name")
        zcol.pack_start(steplbl, False, False, 0)
        self.step_sel = self._segmented([f"{s:g}" for s in STEPS], self.set_step, 1)
        zcol.pack_start(self.step_sel, False, False, 0)

        spdlbl = Gtk.Label(label=_("Travel · mm/s"), xalign=0)
        spdlbl.get_style_context().add_class("glance-temp-name")
        self.speed_sel = self._segmented([str(s) for s in SPEEDS], self.set_speed, 1)
        zcol.pack_end(self.speed_sel, False, False, 0)
        zcol.pack_end(spdlbl, False, False, 6)

        # ---- right: large verbs ----
        verbs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        verbs.set_size_request(200, -1)
        verbs.set_hexpand(False)
        for label, cb in ((_("Home"), self.home), ("QGL", self.qgl),
                          (_("Motors off"), self.motors_off),
                          (_("Precise…"), self.open_precise)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("glance-action")
            b.set_hexpand(False)
            b.connect("clicked", cb)
            verbs.pack_start(b, True, True, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                       vexpand=True, spacing=16)
        main.get_style_context().add_class("glance-hero")
        main.pack_start(map_wrap, False, False, 0)
        main.pack_start(zcol, False, False, 0)
        main.pack_end(verbs, False, False, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, 32)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.root, True, True, 0)
        inner.pack_end(self.rail, False, False, 0)
        self.overlay = Gtk.Overlay()
        self.overlay.add(inner)
        self.backdrop = self._build_precise()
        self.overlay.add_overlay(self.backdrop)
        self.content.pack_start(self.overlay, True, True, 0)
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

    # ---- precise-move modal --------------------------------------------------

    def _build_precise(self):
        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("glance-backdrop")
        backdrop.connect("button-release-event", self.close_precise)
        card = Gtk.EventBox()
        card.connect("button-release-event", lambda w, e: True)
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        card.set_size_request(int(self._screen.width * 0.6), -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.get_style_context().add_class("glance-sheet")
        box.get_style_context().add_class("ph-prep")
        card.add(box)

        self.precise_vals = {}
        for axis in ("X", "Y", "Z"):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.get_style_context().add_class("glance-temp-row")
            n = Gtk.Label(label=axis, xalign=0, hexpand=True)
            n.get_style_context().add_class("glance-temp-name")
            v = Gtk.Label(label="—")
            v.get_style_context().add_class("glance-temp-val")
            v.set_size_request(170, -1)
            self.precise_vals[axis] = v
            minus = Gtk.Button(label="−")
            plus = Gtk.Button(label="+")
            for b, sign in ((minus, -1), (plus, 1)):
                b.get_style_context().add_class("glance-z-step")
                b.set_hexpand(False)
                b.set_valign(Gtk.Align.CENTER)
                b.connect("clicked", self.precise_jog, axis, sign)
            row.pack_start(n, True, True, 0)
            row.pack_end(plus, False, False, 0)
            row.pack_end(v, False, False, 0)
            row.pack_end(minus, False, False, 0)
            box.pack_start(row, False, False, 0)

        steprow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        slbl = Gtk.Label(label=_("Step · mm"), xalign=0)
        slbl.get_style_context().add_class("glance-temp-name")
        slbl.set_size_request(140, -1)
        self.precise_sel = self._segmented(
            [f"{s:g}" for s in PRECISE_STEPS], self.set_precise_step, 2)
        steprow.pack_start(slbl, False, False, 0)
        steprow.pack_start(self.precise_sel, True, True, 0)
        box.pack_start(steprow, False, False, 0)

        close = Gtk.Button(label=_("Close"))
        close.get_style_context().add_class("glance-row-btn")
        close.connect("clicked", self.close_precise)
        box.pack_start(close, False, False, 0)

        backdrop.add(card)
        backdrop.show_all()
        backdrop.hide()
        backdrop.set_no_show_all(True)
        return backdrop

    def open_precise(self, widget):
        self.backdrop.show()
        self._update_precise(force=True)

    def close_precise(self, widget=None, event=None):
        self.backdrop.hide()
        return True

    def set_precise_step(self, widget, idx, buttons):
        self.precise_step = PRECISE_STEPS[idx]
        self._mark_selected(widget, buttons)

    def precise_jog(self, widget, axis, sign):
        if axis.lower() not in self.homed:
            return
        speed = Z_SPEED if axis == "Z" else self.speed
        self._screen._ws.klippy.gcode_script(
            f"G91\nG0 {axis}{self.precise_step * sign:g} F{speed * 60}\nG90")

    def _update_precise(self, force=False):
        if not force and not self.backdrop.get_visible():
            return
        for axis, i in (("X", 0), ("Y", 1), ("Z", 2)):
            self.precise_vals[axis].set_label(f"{self.pos[i]:.2f}")

    # ---- drawing -------------------------------------------------------------

    def on_draw(self, da, cr):
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        cr.set_source_rgb(0.063, 0.078, 0.106)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # grid: six equal cells per axis - symmetric for any bed size
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.07)
        for i in range(1, 6):
            x = i / 6 * w
            cr.move_to(x, 0)
            cr.line_to(x, h)
            y = i / 6 * h
            cr.move_to(0, y)
            cr.line_to(w, y)
        cr.stroke()
        cr.set_source_rgba(0.23, 0.26, 0.33, 1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        cr.set_source_rgba(0.2, 0.77, 0.91, 0.5)
        for sx, sy in ((0, 0), (self.max_x, 0), (0, self.max_y),
                       (self.max_x, self.max_y), (self.max_x / 2, self.max_y / 2)):
            x = sx / self.max_x * w
            y = h - sy / self.max_y * h
            cr.arc(min(max(x, 6), w - 6), min(max(y, 6), h - 6), 4, 0, 6.284)
            cr.fill()
        xy_homed = "x" in self.homed and "y" in self.homed
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
        snap_r = 15 / w * self.max_x
        for sx, sy in ((0, 0), (self.max_x, 0), (0, self.max_y),
                       (self.max_x, self.max_y), (self.max_x / 2, self.max_y / 2)):
            if abs(bx - sx) < snap_r and abs(by - sy) < snap_r:
                bx, by = sx, sy
                break
        bx = min(max(bx, 0), self.max_x)
        by = min(max(by, 0), self.max_y)
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

    def deactivate(self):
        self.backdrop.hide()

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
            self._update_precise()
            self.map.queue_draw()
