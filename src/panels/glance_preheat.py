# -*- coding: utf-8 -*-
# Glance Preheat: direct-manipulation heater control. One horizontal slider
# per heater (amber fill to the live temp, white live tick, cyan target
# handle you drag; release commits, snapped to 5°), a 6-minute temperature
# history graph, and one-tap material profiles. "Hold" is the keep-warm
# standby PRINT_END uses: nozzle 150, bed at the profile temp.

import math
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk
from ks_includes.screen_panel import ScreenPanel
from panels.glance_metrics import px

SNAP = 5            # ° target snap while dragging
OFF_BELOW = 10      # ° dragging under this releases to "off"
HOLD_NOZZLE = 150   # keep-warm nozzle temp (matches PRINT_END)
GRAPH_SECONDS = 360
AMBIENT = 25


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Preheat")
        super().__init__(screen, title)

        specs = [("extruder", "extruder", _("Extruder"), 270, False),
                 ("heater_bed", "heater_bed", _("Bed"), 130, False)]
        # a chamber heater gets a full slider; a bare sensor an inert row
        if self._printer.config_section_exists("heater_generic chamber"):
            specs.append(("heater_generic chamber", "chamber", _("Chamber"),
                          80, False))
        elif self._printer.config_section_exists("temperature_sensor chamber"):
            specs.append(("temperature_sensor chamber", None, _("Chamber"),
                          80, True))

        self.heaters = []
        for device, heater, name, fallback_max, readonly in specs:
            cfg = self._printer.get_config_section(device)
            try:
                max_t = float(cfg["max_temp"])
            except (TypeError, KeyError, ValueError):
                max_t = fallback_max
            self.heaters.append({
                "device": device, "heater": heater, "name": name,
                "max": max_t, "readonly": readonly,
                # nozzle targets never live below 100; cutting the dead range
                # doubles drag resolution (left edge = off, as on Extrude)
                "min": 100.0 if device == "extruder" else 0.0,
                "live": 0.0, "target": 0.0, "pending": None,
            })

        # ---- left: slider rows + history graph ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       hexpand=True, vexpand=True)
        for h in self.heaters:
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            nm = Gtk.Label(label=h["name"], xalign=0, hexpand=True)
            nm.get_style_context().add_class("glance-temp-name")
            h["target_lbl"] = Gtk.Label(label=_("off"), valign=Gtk.Align.END)
            h["target_lbl"].get_style_context().add_class("glance-target-off")
            h["live_lbl"] = Gtk.Label(label="—", valign=Gtk.Align.END)
            cls = "glance-preheat-live" if h["device"] == "extruder" \
                else "glance-preheat-bedlive"
            h["live_lbl"].get_style_context().add_class(cls)
            head.pack_start(nm, True, True, 0)
            head.pack_end(h["live_lbl"], False, False, 0)
            if not h["readonly"]:
                head.pack_end(h["target_lbl"], False, False, 0)

            area = Gtk.DrawingArea(hexpand=True)
            area.set_size_request(-1, px(screen, 16 if h["readonly"] else 64))
            area.connect("draw", self.on_slider_draw, h)
            if not h["readonly"]:
                area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                | Gdk.EventMask.BUTTON_RELEASE_MASK
                                | Gdk.EventMask.BUTTON1_MOTION_MASK)
                area.connect("button-press-event", self.on_slider_press, h)
                area.connect("motion-notify-event", self.on_slider_motion, h)
                area.connect("button-release-event", self.on_slider_release, h)
            h["area"] = area

            left.pack_start(head, False, False, 0)
            left.pack_start(area, False, False, 0)

        self.graph = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.graph.connect("draw", self.on_graph_draw)
        left.pack_end(self.graph, True, True, 0)

        # ---- right: material profiles + verbs ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.get_style_context().add_class("glance-side")
        side.set_hexpand(False)
        side.set_size_request(int(self._screen.width * 0.29), -1)
        head = Gtk.Label(label=_("Profiles"), xalign=0)
        head.get_style_context().add_class("glance-temp-name")
        side.pack_start(head, False, False, 0)

        self.profiles = []
        options = self._config.get_preheat_options() or {}
        for name, opt in options.items():
            if not opt:
                continue
            ext = opt.get("extruder")
            bed = opt.get("bed")
            if ext is None and bed is None:
                continue
            btn = Gtk.Button()
            btn.get_style_context().add_class("glance-profile")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            nm = Gtk.Label(label=name, xalign=0, hexpand=True)
            temps = Gtk.Label(label=f"{ext or 0:.0f} / {bed or 0:.0f}°", xalign=1)
            temps.get_style_context().add_class("glance-profile-temps")
            row.pack_start(nm, True, True, 0)
            row.pack_end(temps, False, False, 0)
            btn.add(row)
            btn.connect("clicked", self.apply_profile, name)
            self.profiles.append({"name": name, "ext": ext or 0, "bed": bed or 0,
                                  "btn": btn})
            side.pack_start(btn, False, False, 0)

        spacer = Gtk.Box(vexpand=True)
        side.pack_start(spacer, True, True, 0)
        cool = Gtk.Button(label=_("Cool down"))
        cool.get_style_context().add_class("glance-cool")
        cool.connect("clicked", self.cool_down)
        self.hold_btn = Gtk.Button(label=_("Hold") + f" {HOLD_NOZZLE} / 110°")
        self.hold_btn.get_style_context().add_class("glance-hold")
        self.hold_btn.connect("clicked", self.hold_warm)
        side.pack_end(self.hold_btn, False, False, 0)
        side.pack_end(cool, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                       vexpand=True, spacing=10)
        main.get_style_context().add_class("glance-move-main")
        main.pack_start(left, True, True, 0)
        main.pack_end(side, False, False, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, px(screen, 32))

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.root, True, True, 0)
        inner.pack_end(self.rail, False, False, 0)
        self.content.pack_start(inner, True, True, 0)
        for w in (self.root, self.rail):
            w.get_style_context().add_class("ph-heat")

    # ---- slider geometry ----------------------------------------------------

    def _track(self, h, area):
        # trough box inside the drawing area: 6px top pad, 34px tall, 24px
        # below for the scale labels. Read-only rows are a bare 10px strip.
        w = area.get_allocated_width()
        s = self._screen
        if h["readonly"]:
            return 2, px(s, 3), w - 4, px(s, 10)
        return 2, px(s, 6), w - 4, px(s, 34)

    def _temp_to_x(self, h, area, t):
        x, _y, w, _hh = self._track(h, area)
        frac = (t - h["min"]) / (h["max"] - h["min"])
        return x + max(0.0, min(1.0, frac)) * w

    def _x_to_temp(self, h, area, px):
        x, _y, w, _hh = self._track(h, area)
        t = h["min"] + (px - x) / w * (h["max"] - h["min"])
        t = min(h["max"], round(t / SNAP) * SNAP)
        if h["min"] > 0 and t <= h["min"] + 2:
            return 0.0
        return max(0.0, t)

    @staticmethod
    def _rounded(cr, x, y, w, hh, r):
        cr.new_path()
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(x + w - r, y + hh - r, r, 0, 0.5 * math.pi)
        cr.arc(x + r, y + hh - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()

    # ---- drawing ------------------------------------------------------------

    def on_slider_draw(self, area, cr, h):
        x, y, w, hh = self._track(h, area)
        self._rounded(cr, x, y, w, hh, min(10, hh // 2))
        cr.set_source_rgb(0.086, 0.094, 0.113)      # #16181d
        cr.fill()

        live_x = self._temp_to_x(h, area, h["live"])
        if h["live"] > (h["min"] if h["min"] > 0 else AMBIENT + 2):
            self._rounded(cr, x, y, max(live_x - x, 20), hh, min(10, hh // 2))
            # sensor-only rows read as instruments, not controls
            cr.set_source_rgba(1.0, 0.69, 0.0, 0.14 if h["readonly"] else 0.32)
            cr.fill()
        if h["readonly"]:
            cr.set_source_rgb(0.482, 0.49, 0.525)    # gray tick, no handle
        else:
            cr.set_source_rgb(0.91, 0.918, 0.929)    # live tick
        pad = 3 if h["readonly"] else 4
        cr.rectangle(live_x - 2, y - pad, 4, hh + 2 * pad)
        cr.fill()
        if h["readonly"]:
            return True                              # no scale, no handle

        target = h["pending"] if h["pending"] is not None else h["target"]
        if target > 0:
            tx = self._temp_to_x(h, area, target)
            self._rounded(cr, tx - 3.5, y - 8, 7, hh + 16, 3.5)
            cr.set_source_rgb(0.2, 0.773, 0.91)      # cyan handle
            cr.fill()

        # scale labels at round temperatures, plus the range end
        cr.select_font_face("Space Grotesk")
        cr.set_font_size(18)
        cr.set_source_rgb(0.353, 0.365, 0.4)
        step = 50
        ticks = [t for t in range(int(h["min"]), int(h["max"]), step)
                 if h["max"] - t > step * 0.5] + [h["max"]]
        for t in ticks:
            label = f"{t:.0f}"
            ext = cr.text_extents(label)
            lx = self._temp_to_x(h, area, t)
            lx = max(x, min(x + w - ext.width, lx - ext.width / 2))
            cr.move_to(lx, y + hh + 22)
            cr.show_text(label)
        return True

    def on_graph_draw(self, area, cr):
        # Mainsail-style: heater power as translucent area fills rising from
        # the plot floor, light fills under the temperature lines, dashed
        # target lines, and a wall-clock axis with faint gridlines
        w = area.get_allocated_width()
        hh = area.get_allocated_height()
        if w < 120 or hh < 80:
            return True
        gutter = 56
        self._rounded(cr, 1, 1, w - 2, hh - 2, 10)
        cr.set_source_rgb(0.067, 0.075, 0.094)       # #111318
        cr.fill_preserve()
        cr.set_source_rgb(0.149, 0.173, 0.212)       # #262c36
        cr.set_line_width(2)
        cr.stroke()

        top_t = max([280.0] + [h["target"] + 20 for h in self.heaters])
        px_w = w - gutter - 16
        top, bottom = 10, hh - 34                    # 24px time axis below

        def ty(t):
            return top + (bottom - top) * (1 - t / top_t)

        def tx(i, n):
            return 14 + px_w * (i / max(n - 1, 1))

        series = []
        for h, rgb, line_a, line_w in (
                (self.heaters[0], (1.0, 0.69, 0.0), 1.0, 4),
                (self.heaters[1], (0.91, 0.918, 0.929), 0.65, 3)):
            temps = self._printer.get_temp_store(h["device"], "temperatures",
                                                 GRAPH_SECONDS)
            if not temps:
                continue
            powers = self._printer.get_temp_store(h["device"], "powers",
                                                  GRAPH_SECONDS)
            series.append((h, temps, powers, rgb, line_a, line_w))

        # heater power: area from the floor, height = duty x plot height
        for h, temps, powers, rgb, _la, _lw in series:
            if not powers:
                continue
            n = len(powers)
            cr.move_to(tx(0, n), bottom)
            for i, p in enumerate(powers):
                cr.line_to(tx(i, n), bottom - max(0.0, min(1.0, p)) * (bottom - top))
            cr.line_to(tx(n - 1, n), bottom)
            cr.close_path()
            cr.set_source_rgba(*rgb, 0.20 if h["device"] == "extruder" else 0.13)
            cr.fill()

        # light fill under each temperature line
        for h, temps, _powers, rgb, _la, _lw in series:
            n = len(temps)
            cr.move_to(tx(0, n), bottom)
            for i, t in enumerate(temps):
                cr.line_to(tx(i, n), ty(max(0.0, min(top_t, t))))
            cr.line_to(tx(n - 1, n), bottom)
            cr.close_path()
            cr.set_source_rgba(*rgb, 0.07)
            cr.fill()

        cr.select_font_face("Space Grotesk")
        cr.set_font_size(17)
        # dashed target lines with right-edge labels (nudged apart when the
        # graph is short enough for two targets to collide)
        label_ys = []
        for h in self.heaters:
            if h["target"] > 0:
                yy = ty(h["target"])
                cr.set_source_rgb(0.227, 0.255, 0.302)
                cr.set_dash((7, 6))
                cr.set_line_width(2)
                cr.move_to(14, yy)
                cr.line_to(14 + px_w, yy)
                cr.stroke()
                cr.set_dash(())
                cr.set_source_rgb(0.482, 0.49, 0.525)
                ly = yy + 6
                while any(abs(ly - prev) < 17 for prev in label_ys):
                    ly += 17
                label_ys.append(ly)
                cr.move_to(18 + px_w, ly)
                cr.show_text(f"{h['target']:.0f}")

        # temperature lines on top
        for h, temps, _powers, rgb, line_a, line_w in series:
            n = len(temps)
            cr.set_source_rgba(*rgb, line_a)
            cr.set_line_width(line_w)
            for i, t in enumerate(temps):
                (cr.move_to if i == 0 else cr.line_to)(
                    tx(i, n), ty(max(0.0, min(top_t, t))))
            cr.stroke()

        cr.set_source_rgb(0.482, 0.49, 0.525)
        cr.move_to(16, 24)
        cr.show_text(_("last 6 min"))

        # wall-clock axis: gridline + HH:MM at even 2-minute marks
        if series:
            n = len(series[0][1])
            now = time.time()
            cr.set_font_size(15)
            ts = (int(now) // 120) * 120
            while now - ts < n - 1:
                x = tx(n - 1 - (now - ts), n)
                if x > 40:
                    cr.set_source_rgba(0.227, 0.255, 0.302, 0.5)
                    cr.set_line_width(1)
                    cr.move_to(x, top)
                    cr.line_to(x, bottom)
                    cr.stroke()
                    label = time.strftime("%H:%M", time.localtime(ts))
                    ext = cr.text_extents(label)
                    cr.set_source_rgb(0.42, 0.43, 0.47)
                    cr.move_to(x - ext.width / 2, hh - 12)
                    cr.show_text(label)
                ts -= 120
        return True

    # ---- input --------------------------------------------------------------

    def on_slider_press(self, area, event, h):
        h["pending"] = self._x_to_temp(h, area, event.x)
        area.queue_draw()
        return True

    def on_slider_motion(self, area, event, h):
        h["pending"] = self._x_to_temp(h, area, event.x)
        area.queue_draw()
        self._show_target(h, h["pending"])
        return True

    def on_slider_release(self, area, event, h):
        t = self._x_to_temp(h, area, event.x)
        h["pending"] = None
        if t < OFF_BELOW:
            t = 0.0
        self._set_target(h["heater"], t)
        return True

    # ---- actions ------------------------------------------------------------

    def _set_target(self, device, t):
        self._screen._ws.klippy.gcode_script(
            f"SET_HEATER_TEMPERATURE HEATER={device} TARGET={t:.0f}")

    def apply_profile(self, widget, name):
        for p in self.profiles:
            if p["name"] == name:
                self._set_target("extruder", p["ext"])
                self._set_target("heater_bed", p["bed"])

    def cool_down(self, widget):
        self._set_target("extruder", 0)
        self._set_target("heater_bed", 0)

    def _hold_bed(self):
        bed = self.heaters[1]["target"]
        if bed > 0:
            return bed
        active = [p for p in self.profiles
                  if abs(self.heaters[0]["target"] - p["ext"]) < 1]
        return active[0]["bed"] if active else 110

    def hold_warm(self, widget):
        self._set_target("extruder", HOLD_NOZZLE)
        self._set_target("heater_bed", self._hold_bed())

    # ---- updates ------------------------------------------------------------

    def activate(self):
        # no glance panel shows the stock temperature graph, so nothing else
        # ever primes the printer's temperature history after a UI restart
        if not self._printer.tempstore:
            self._screen.init_tempstore()

    def _show_target(self, h, target):
        ctx = h["target_lbl"].get_style_context()
        if target > 0:
            h["target_lbl"].set_label(_("target") + f" {target:.0f}°")
            ctx.add_class("glance-target")
            ctx.remove_class("glance-target-off")
        else:
            h["target_lbl"].set_label(_("off"))
            ctx.add_class("glance-target-off")
            ctx.remove_class("glance-target")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        heating_pcts = []
        for h in self.heaters:
            live = self._printer.get_stat(h["device"], "temperature")
            target = self._printer.get_stat(h["device"], "target") or 0
            if live is None or isinstance(live, dict):
                continue
            h["live"] = float(live)
            h["target"] = float(target)
            h["live_lbl"].set_label(f"{h['live']:.0f}°")
            lctx = h["live_lbl"].get_style_context()
            if h["target"] > 0 and h["live"] < h["target"] - 2:
                lctx.add_class("ph-heat")
                if h["target"] > AMBIENT + 1:
                    heating_pcts.append(min(max(
                        (h["live"] - AMBIENT) / (h["target"] - AMBIENT), 0), 1))
            else:
                lctx.remove_class("ph-heat")
            if h["pending"] is None:
                self._show_target(h, h["target"])
            h["area"].queue_draw()

        targets_on = any(h["target"] > 0 for h in self.heaters)
        if heating_pcts:
            self.rail.set_fraction(min(heating_pcts))
        else:
            self.rail.set_fraction(1 if targets_on else 0)

        for p in self.profiles:
            ctx = p["btn"].get_style_context()
            if targets_on and abs(self.heaters[0]["target"] - p["ext"]) < 1 \
                    and abs(self.heaters[1]["target"] - p["bed"]) < 1:
                ctx.add_class("glance-profile-on")
            else:
                ctx.remove_class("glance-profile-on")

        self.hold_btn.set_label(_("Hold") + f" {HOLD_NOZZLE} / {self._hold_bed():.0f}°")
        self.graph.queue_draw()
