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

PRECISE_STEPS = (0.01, 0.1, 1, 10)
SAFE_Z = 15.0
Z_SPEED = 15   # mm/s
XY_SPEED = 150  # mm/s for map taps and precise XY nudges
Z_LINES = (0, 10, 50, 75, 100)
Z_MAP_MAX = 110.0
Z_PAD = 14  # px margin above/below the height-map scale


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Move")
        super().__init__(screen, title)
        self.precise_step = 1.0
        self.speed = XY_SPEED
        self.zdrag = None
        self.map_drag = None
        self.xy_target = None
        self.z_target = None
        self.pos = [0.0, 0.0, 0.0]
        self.homed = ""

        self.max_x = self._axis_max("stepper_x", 300)
        self.max_y = self._axis_max("stepper_y", 300)
        self.max_z = self._axis_max("stepper_z", 280)
        # travel exceeds the physical plate (Y 316 vs 300 on this machine);
        # the map represents the BED, so its center dot is the true bed center
        self.bed_x = min(self.max_x, 300.0)
        self.bed_y = min(self.max_y, 300.0)

        # ---- left: bed map + readouts ----
        self.map = Gtk.DrawingArea()
        self.map.set_size_request(430, 430)
        self.map.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                            | Gdk.EventMask.BUTTON_RELEASE_MASK
                            | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.map.connect("draw", self.on_draw)
        self.map.connect("button-press-event", self.on_map_press)
        self.map.connect("motion-notify-event", self.on_map_motion)
        self.map.connect("button-release-event", self.on_map_release)
        map_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           valign=Gtk.Align.CENTER)
        map_wrap.pack_start(self.map, False, False, 0)
        pos_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14,
                          homogeneous=True)
        self.axis_vals = {}
        # Z lives in the height map; the third cell shows the set travel speed
        for name, key in (("X", "X"), ("Y", "Y")):
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
            cell.get_style_context().add_class("glance-temp-row")
            cell.get_style_context().add_class("ph-prep")
            n = Gtk.Label(label=name, xalign=0)
            n.get_style_context().add_class("glance-temp-name")
            cell.pack_start(n, False, False, 0)
            v = Gtk.Label(label="—", xalign=1, hexpand=True)
            v.get_style_context().add_class("glance-temp-val")
            cell.pack_end(v, True, True, 0)
            pos_row.pack_start(cell, True, True, 0)
            self.axis_vals[key] = v
        vcell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        vcell.get_style_context().add_class("glance-temp-row")
        vcell.get_style_context().add_class("ph-prep")
        vval = Gtk.Label(label=f"{XY_SPEED:g}", xalign=1, hexpand=True)
        vval.get_style_context().add_class("glance-temp-val")
        vunit = Gtk.Label(label="mm/s", xalign=0)
        vunit.get_style_context().add_class("glance-temp-name")
        vcell.pack_end(vunit, False, False, 0)
        vcell.pack_end(vval, True, True, 0)
        pos_row.pack_start(vcell, True, True, 0)
        self.axis_vals["V"] = vval
        map_wrap.pack_start(pos_row, False, False, 0)
        note = Gtk.Label(label=_("taps travel at") + f" Z ≥ {SAFE_Z:.0f}", xalign=0)
        note.get_style_context().add_class("glance-fname")
        map_wrap.pack_start(note, False, False, 0)

        # ---- middle: the Z column ----
        zcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        zcol.get_style_context().add_class("glance-side")  # compact selector sizing
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

        # vertical height map: tap near a line to snap there, or press and
        # drag - Z follows the exact height under your finger on release
        self.zmap = Gtk.DrawingArea()
        self.zmap.set_size_request(190, -1)
        self.zmap.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.zmap.connect("draw", self.on_zmap_draw)
        self.zmap.connect("button-press-event", self.on_zmap_press)
        self.zmap.connect("motion-notify-event", self.on_zmap_motion)
        self.zmap.connect("button-release-event", self.on_zmap_release)
        zcol.pack_start(self.zmap, True, True, 0)

        # ---- right: large verbs ----
        verbs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        verbs.set_size_request(200, -1)
        verbs.set_hexpand(False)
        for label, cb in ((_("Home"), self.home), ("QGL", self.qgl),
                          (_("Motors off"), self.motors_off)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("glance-action")
            b.set_hexpand(False)
            b.connect("clicked", cb)
            verbs.pack_start(b, False, False, 0)

        # precise spans the full right region under both columns
        precise = Gtk.Button(label=_("Precise moves"))
        precise.get_style_context().add_class("glance-action")
        precise.get_style_context().add_class("glance-precise")
        precise.set_hexpand(True)
        precise.connect("clicked", self.open_precise)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        top_row.pack_start(zcol, False, False, 0)
        top_row.pack_end(verbs, False, False, 0)
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.pack_start(top_row, True, True, 0)
        right_box.pack_end(precise, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                       vexpand=True, spacing=16)
        main.get_style_context().add_class("glance-move-main")
        main.pack_start(map_wrap, False, False, 0)
        main.pack_start(right_box, True, True, 0)

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
        speed = Z_SPEED if axis == "Z" else XY_SPEED
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
        for sx, sy in ((0, 0), (self.bed_x, 0), (0, self.bed_y),
                       (self.bed_x, self.bed_y), (self.bed_x / 2, self.bed_y / 2)):
            x = sx / self.bed_x * w
            y = h - sy / self.bed_y * h
            cr.arc(min(max(x, 6), w - 6), min(max(y, 6), h - 6), 4, 0, 6.284)
            cr.fill()
        xy_homed = "x" in self.homed and "y" in self.homed
        if xy_homed and self.map_drag is not None:
            dxp = min(max(self.map_drag["x"], 0), w)
            dyp = min(max(self.map_drag["y"], 0), h)
            cr.set_source_rgba(0.2, 0.77, 0.91, 1)
            cr.set_line_width(2)
            cr.arc(dxp, dyp, 10, 0, 6.284)
            cr.stroke()
            cr.arc(dxp, dyp, 2, 0, 6.284)
            cr.fill()
            bx = dxp / w * self.bed_x
            by = (1 - dyp / h) * self.bed_y
            cr.select_font_face("Space Grotesk")
            cr.set_font_size(22)
            label = f"X {bx:.0f} · Y {by:.0f}"
            ext = cr.text_extents(label)
            # well clear of the fingertip: up and to the side
            tx = dxp + 36 if dxp < w - ext.width - 44 else dxp - ext.width - 36
            ty = dyp - 34 if dyp > 64 else dyp + 52
            cr.move_to(tx, ty)
            cr.show_text(label)
        if xy_homed and self.xy_target is not None and self.map_drag is None:
            tx, ty = self.xy_target
            x = tx / self.bed_x * w
            y = h - ty / self.bed_y * h
            cr.set_source_rgba(0.2, 0.77, 0.91, 0.9)
            cr.set_line_width(2)
            cr.arc(x, y, 10, 0, 6.284)
            cr.stroke()
            cr.arc(x, y, 2, 0, 6.284)
            cr.fill()
        if xy_homed:
            x = min(max(self.pos[0], 0), self.bed_x) / self.bed_x * w
            y = h - min(max(self.pos[1], 0), self.bed_y) / self.bed_y * h
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

    def _map_bed_coords(self, x, y, w, h, snap):
        bx = x / w * self.bed_x
        by = (1 - y / h) * self.bed_y
        if snap:
            snap_r = 15 / w * self.bed_x
            for sx, sy in ((0, 0), (self.bed_x, 0), (0, self.bed_y),
                           (self.bed_x, self.bed_y), (self.bed_x / 2, self.bed_y / 2)):
                if abs(bx - sx) < snap_r and abs(by - sy) < snap_r:
                    return sx, sy
        return (min(max(bx, 0), self.bed_x), min(max(by, 0), self.bed_y))

    def on_map_press(self, da, event):
        # never act while the precise modal is up: on some input paths the
        # press reaches the map through the backdrop
        if self.backdrop.get_visible():
            return True
        if "x" not in self.homed or "y" not in self.homed:
            return True
        self.map_drag = {"x0": event.x, "y0": event.y, "x": event.x, "y": event.y}
        da.queue_draw()
        return True

    def on_map_motion(self, da, event):
        if self.map_drag is not None:
            self.map_drag["x"] = event.x
            self.map_drag["y"] = event.y
            da.queue_draw()
        return True

    def on_map_release(self, da, event):
        if self.map_drag is None:
            return True
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        x0, y0 = self.map_drag["x0"], self.map_drag["y0"]
        self.map_drag = None
        tapped = abs(event.x - x0) <= 8 and abs(event.y - y0) <= 8
        bx, by = self._map_bed_coords(event.x, event.y, w, h, snap=tapped)
        script = "G90\n"
        if self.pos[2] < SAFE_Z and "z" in self.homed:
            script += f"G0 Z{SAFE_Z} F{Z_SPEED * 60}\n"
        script += f"G0 X{bx:.1f} Y{by:.1f} F{self.speed * 60}"
        self._screen._ws.klippy.gcode_script(script)
        self.xy_target = (bx, by)
        da.queue_draw()
        return True

    @staticmethod
    def _mark_selected(widget, buttons):
        for b in buttons:
            b.get_style_context().remove_class("horizontal_togglebuttons_active")
        widget.get_style_context().add_class("horizontal_togglebuttons_active")

    # ---- height map ----------------------------------------------------------

    @staticmethod
    def _zmap_y_to_z(y, h):
        span = max(h - 2 * Z_PAD, 1)
        frac = (h - Z_PAD - y) / span
        return min(max(frac, 0.0), 1.0) * Z_MAP_MAX

    @staticmethod
    def _zmap_z_to_y(z, h):
        span = max(h - 2 * Z_PAD, 1)
        return h - Z_PAD - min(max(z / Z_MAP_MAX, 0.0), 1.0) * span

    def on_zmap_draw(self, da, cr):
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        cr.set_source_rgb(0.063, 0.078, 0.106)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgba(0.23, 0.26, 0.33, 1)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        cr.select_font_face("Space Grotesk")
        cr.set_font_size(19)
        for v in Z_LINES:
            y = self._zmap_z_to_y(v, h)
            label = f"{v:g}"
            ext = cr.text_extents(label)
            cx = w / 2
            gap = ext.width / 2 + 10
            cr.set_source_rgba(1, 1, 1, 0.18)
            cr.move_to(8, y); cr.line_to(cx - gap, y)
            cr.move_to(cx + gap, y); cr.line_to(w - 8, y)
            cr.stroke()
            cr.set_source_rgba(0.55, 0.58, 0.65, 1)
            cr.move_to(cx - ext.width / 2, y + ext.height / 2 - 1)
            cr.show_text(label)
        if "z" not in self.homed:
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            return False
        # current Z marker (clamped into the scale)
        y = self._zmap_z_to_y(self.pos[2], h)
        cr.set_source_rgba(0.25, 0.84, 0.41, 0.9)
        cr.set_line_width(2)
        cr.move_to(4, y); cr.line_to(w - 4, y)
        cr.stroke()
        cr.arc(w - 12, y, 5, 0, 6.284)
        cr.fill()
        # persistent target line while the head is still on its way
        if self.z_target is not None and self.zdrag is None:
            ty = self._zmap_z_to_y(self.z_target, h)
            cr.set_source_rgba(0.2, 0.77, 0.91, 0.9)
            cr.set_line_width(2)
            cr.move_to(4, ty); cr.line_to(w - 4, ty)
            cr.stroke()
        # drag target marker with live value
        if self.zdrag is not None:
            dy = min(max(self.zdrag["y"], Z_PAD), h - Z_PAD)
            dz = self._zmap_y_to_z(dy, h)
            cr.set_source_rgba(0.2, 0.77, 0.91, 1)
            cr.set_line_width(2)
            cr.move_to(4, dy); cr.line_to(w - 4, dy)
            cr.stroke()
            label = f"{dz:.1f}"
            ext = cr.text_extents(label)
            cr.set_font_size(24)
            cr.move_to(10, dy - 8 if dy > 40 else dy + 26)
            cr.show_text(label)
        return False

    def on_zmap_press(self, da, event):
        if self.backdrop.get_visible():
            return True
        if "z" not in self.homed:
            return True
        self.zdrag = {"y0": event.y, "y": event.y}
        da.queue_draw()
        return True

    def on_zmap_motion(self, da, event):
        if self.zdrag is not None:
            self.zdrag["y"] = event.y
            da.queue_draw()
        return True

    def on_zmap_release(self, da, event):
        if self.zdrag is None:
            return True
        h = da.get_allocated_height()
        y0, y = self.zdrag["y0"], event.y
        self.zdrag = None
        z = self._zmap_y_to_z(min(max(y, Z_PAD), h - Z_PAD), h)
        if abs(y - y0) <= 8:
            # a tap: snap to the nearest reference line when close to one
            snap_z = 14 / max(h - 2 * Z_PAD, 1) * Z_MAP_MAX
            for v in Z_LINES:
                if abs(z - v) <= snap_z:
                    z = float(v)
                    break
        z = min(max(z, 0.0), min(Z_MAP_MAX, self.max_z))
        self.z_goto(None, z)
        self.z_target = z
        da.queue_draw()
        return True

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
            self.xy_target = None
            self.z_target = None
            self.map.queue_draw()
            self.zmap.queue_draw()
        # live_position tracks the physical toolhead during moves (the
        # commanded gcode_position teleports to the target immediately);
        # subtract the gcode offset so numbers stay in commanded coordinates
        pos = self._printer.get_stat("motion_report", "live_position")
        origin = self._printer.get_stat("gcode_move", "homing_origin")
        if pos and not isinstance(pos, dict) and origin and not isinstance(origin, dict):
            pos = [float(pos[i]) - float(origin[i]) for i in range(3)]
        else:
            pos = self._printer.get_stat("gcode_move", "gcode_position")
        if pos and not isinstance(pos, dict):
            self.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
            self.z_lbl.set_label(f"{self.pos[2]:.2f}")
            for axis, i in (("X", 0), ("Y", 1)):
                self.axis_vals[axis].set_label(f"{self.pos[i]:.1f}")
            self._update_precise()
            # targets vanish once reached
            if self.xy_target is not None and \
                    abs(self.pos[0] - self.xy_target[0]) < 0.8 and \
                    abs(self.pos[1] - self.xy_target[1]) < 0.8:
                self.xy_target = None
            if self.z_target is not None and abs(self.pos[2] - self.z_target) < 0.3:
                self.z_target = None
            self.map.queue_draw()
            self.zmap.queue_draw()
