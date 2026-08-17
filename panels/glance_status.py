# -*- coding: utf-8 -*-
# Glance: at-a-glance job status panel for KlipperScreen.
# One giant phase-colored numeral, a bottom progress rail, the part thumbnail,
# nozzle/bed readouts and three fixed controls. The "•••" button opens a
# glance-styled details sheet: Z babystepping, live stats, and doors into the
# stock fine-tune/extrude/settings panels.
# Phase is derived from print_stats plus the M117 messages this printer's
# PRINT_START emits (_HEAT_WAIT progress bars, "Leveling gantry",
# "Scanning bed mesh").

import logging
import os
import re
from datetime import datetime, timedelta
from math import trunc

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango
from ks_includes.screen_panel import ScreenPanel
from ks_includes.KlippyGtk import find_widget

# matches the M117 lines from _HEAT_WAIT: "██░░ 23% Bed 45/110" or "... 100% Bed 110"
HEAT_RE = re.compile(r"(\d+)%\s+(Bed|Nozzle)\s+(\d+)(?:/(\d+))?")

PHASE_CLASSES = ["ph-heat", "ph-prep", "ph-print", "ph-done", "ph-err"]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or _("Job Status")
        super().__init__(screen, title)
        self.state = "standby"
        self.phase = None
        self.filename = ""
        self.file_metadata = {}
        self.progress = 0.0
        self.msg = ""
        self.pulse_timeout = None
        self.cool_from = None
        self.probe_count = 0
        self.speed_pct = 100
        self.flow_pct = 100
        self._busy_buttons = set()
        self.sheet_labels = {}
        self.pending_zoff = None
        self.thumb_dialog = None
        self.thumb_loaded = False

        # hero column
        self.phase_lbl = Gtk.Label(label="", xalign=0)
        self.phase_lbl.get_style_context().add_class("glance-phase")
        self.big_lbl = Gtk.Label(label="—", xalign=0, vexpand=True, valign=Gtk.Align.CENTER)
        self.big_lbl.get_style_context().add_class("glance-big")
        # digits have different widths in Anton; without ellipsize the label's
        # natural width changes per digit and nudges the side column around
        self.big_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.sub_lbl = Gtk.Label(label="", xalign=0)
        self.sub_lbl.get_style_context().add_class("glance-subline")
        self.sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        hero.get_style_context().add_class("glance-hero")
        hero.pack_start(self.phase_lbl, False, False, 0)
        hero.pack_start(self.big_lbl, True, True, 0)
        hero.pack_start(self.sub_lbl, False, False, 0)

        # side column
        self.thumb_btn = self._gtk.Button("file")
        self.thumb_btn.get_style_context().add_class("glance-thumb")
        self.thumb_btn.set_hexpand(False)
        self.thumb_btn.set_vexpand(True)
        self.thumb_btn.connect("clicked", self.show_fullscreen_thumbnail)

        self.fname_lbl = Gtk.Label(label="", xalign=0)
        self.fname_lbl.get_style_context().add_class("glance-fname")
        self.fname_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)

        self.noz_row, self.noz_val = self._temp_row(_("Nozzle"))
        self.bed_row, self.bed_val = self._temp_row(_("Bed"))
        self.speed_row, self.speed_val = self._factor_row(_("Speed"), self.adjust_speed)
        self.flow_row, self.flow_val = self._factor_row(_("Flow"), self.adjust_flow)
        # z-offset row: arrows bracket the hundredths digit (the 0.01 step),
        # and the buttons are the matching up/down arrows
        self.zoff_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.zoff_row.get_style_context().add_class("glance-temp-row")
        self.zoff_row.get_style_context().add_class("glance-zoff-row")
        zname = Gtk.Label(label=_("Z offset"), xalign=0, hexpand=True)
        zname.get_style_context().add_class("glance-temp-name")
        self.zoff_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.zoff_box.set_valign(Gtk.Align.CENTER)
        self.zoff_prefix = Gtk.Label(label="+0.")
        self.zoff_prefix.get_style_context().add_class("glance-temp-val")
        self.zoff_box.pack_start(self.zoff_prefix, False, False, 0)
        self.zoff_digits = []
        for i in range(3):
            d = Gtk.Label(label="0")
            d.get_style_context().add_class("glance-temp-val")
            if i == 1:
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                up, down = Gtk.Label(label="▲"), Gtk.Label(label="▼")
                for a in (up, down):
                    a.get_style_context().add_class("glance-z-arrow")
                col.pack_start(up, False, False, 0)
                col.pack_start(d, False, False, 0)
                col.pack_start(down, False, False, 0)
                self.zoff_box.pack_start(col, False, False, 0)
            else:
                self.zoff_box.pack_start(d, False, False, 0)
            self.zoff_digits.append(d)
        zdown = Gtk.Button(label="↓")
        zup = Gtk.Button(label="↑")
        for b, sign in ((zdown, -1), (zup, 1)):
            b.get_style_context().add_class("glance-step")
            b.set_hexpand(False)
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", self.adjust_zoffset_row, sign)
        self.zoff_row.pack_start(zname, True, True, 0)
        self.zoff_row.pack_end(zup, False, False, 0)
        self.zoff_row.pack_end(self.zoff_box, False, False, 6)
        self.zoff_row.pack_end(zdown, False, False, 0)
        # paused-state replacements for the factor rows (same footprint):
        # quick inline extrude/retract, and a door into the full extrude panel
        self.btn_unload = Gtk.Button(label=_("Unload"))
        self.btn_load = Gtk.Button(label=_("Load"))
        self.ext_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for b in (self.btn_unload, self.btn_load):
            b.get_style_context().add_class("glance-row-btn")
            b.set_hexpand(False)
            self.ext_row.pack_start(b, True, True, 0)
        self.btn_unload.connect("clicked", self.filament_action, "UNLOAD_FILAMENT")
        self.btn_load.connect("clicked", self.filament_action, "LOAD_FILAMENT")
        self.ext_more = Gtk.Button(label=_("Filament / extrude panel"))
        self.ext_more.get_style_context().add_class("glance-row-btn")
        self.ext_more.set_hexpand(False)
        self.ext_more.connect("clicked", self.open_extrude)

        self.btn_pause = self._gtk.Button("pause", None, None, scale=0.9)
        self.btn_resume = self._gtk.Button("resume", None, None, scale=0.9)
        self.btn_stop = self._gtk.Button("stop", None, None, scale=0.9)
        self.btn_close = self._gtk.Button("complete", None, None, scale=0.9)
        self.btn_more = Gtk.Button(label="• • •")
        for b in (self.btn_pause, self.btn_resume, self.btn_stop, self.btn_close, self.btn_more):
            b.get_style_context().add_class("glance-action")
            b.set_hexpand(True)
        # state-dependent buttons manage their own visibility; show_all() must not undo it
        for b in (self.btn_pause, self.btn_resume, self.btn_stop, self.btn_close):
            b.set_no_show_all(True)
        self.btn_pause.show()
        self.btn_stop.show()
        self.btn_pause.connect("clicked", self.pause)
        self.btn_resume.connect("clicked", self.resume)
        self.btn_stop.connect("clicked", self.cancel)
        self.btn_close.connect("clicked", self.close_panel)
        self.btn_more.connect("clicked", self.open_details)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.pack_start(self.btn_pause, True, True, 0)
        actions.pack_start(self.btn_resume, True, True, 0)
        actions.pack_start(self.btn_stop, True, True, 0)
        actions.pack_start(self.btn_close, True, True, 0)
        actions.pack_start(self.btn_more, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.get_style_context().add_class("glance-side")
        side.set_size_request(int(self._screen.width * 0.34), -1)
        # children with hexpand (buttons) would propagate expand up and get this
        # column centered in a variable-width cell, wobbling with the hero digits;
        # explicit hexpand=False stops the propagation so pack_end pins it right
        side.set_hexpand(False)
        side.pack_start(self.thumb_btn, True, True, 0)
        side.pack_start(self.fname_lbl, False, False, 0)
        side.pack_start(self.noz_row, False, False, 0)
        side.pack_start(self.bed_row, False, False, 0)
        side.pack_start(self.speed_row, False, False, 0)
        side.pack_start(self.zoff_row, False, False, 0)
        side.pack_start(self.ext_row, False, False, 0)
        side.pack_start(self.ext_more, False, False, 0)
        side.pack_end(actions, False, False, 0)
        # attach_panel's show_all() would briefly reveal every row before
        # _show_buttons hides the wrong ones, reflowing the layout (visible in
        # slow-mo as the hero jumping). Pre-show the subtrees once, then seal
        # them so only row-level set_visible controls them.
        for row in (self.noz_row, self.bed_row, self.speed_row, self.zoff_row,
                    self.flow_row, self.ext_row, self.ext_more):
            row.show_all()
            row.set_no_show_all(True)
        self._show_buttons()

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        main.pack_start(hero, True, True, 0)
        # pack_end anchors the side column to the right edge; packed after the
        # hero it rides the hero label's natural width, which changes per digit
        main.pack_end(side, False, False, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, 32)  # keep the rail on-screen even if the hero is tight

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        # the rail lives OUTSIDE the bordered box: the side keylines terminate
        # into it, so the rail itself closes the frame (as in the mockup)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.root, True, True, 0)
        inner.pack_end(self.rail, False, False, 0)
        self.overlay = Gtk.Overlay()
        self.overlay.add(inner)
        self.backdrop = self._build_sheet()
        self.overlay.add_overlay(self.backdrop)
        self.content.pack_start(self.overlay, True, True, 0)
        self._phase_widgets = (self.big_lbl, self.rail, self.root, self.sheet_box)

    @staticmethod
    def _temp_row(name):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("glance-temp-row")
        name_lbl = Gtk.Label(label=name, xalign=0, hexpand=True)
        name_lbl.get_style_context().add_class("glance-temp-name")
        val_lbl = Gtk.Label(label="—", xalign=1)
        val_lbl.get_style_context().add_class("glance-temp-val")
        val_lbl.set_hexpand(False)
        row.pack_start(name_lbl, True, True, 0)
        row.pack_end(val_lbl, False, False, 0)
        return row, val_lbl

    def _factor_row(self, name, adjust_cb):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.get_style_context().add_class("glance-temp-row")
        name_lbl = Gtk.Label(label=name, xalign=0, hexpand=True)
        name_lbl.get_style_context().add_class("glance-temp-name")
        val_lbl = Gtk.Label(label="100%")
        val_lbl.get_style_context().add_class("glance-temp-val")
        # fixed width so changing digit counts can't nudge the buttons around
        val_lbl.set_size_request(96, -1)
        minus = Gtk.Button(label="−")
        plus = Gtk.Button(label="+")
        for w in (minus, plus, val_lbl):
            w.set_hexpand(False)  # never let expand propagate (column wobble)
        for b in (minus, plus):
            b.get_style_context().add_class("glance-step")
        minus.connect("clicked", adjust_cb, -1)
        plus.connect("clicked", adjust_cb, +1)
        row.pack_start(name_lbl, True, True, 0)
        row.pack_end(plus, False, False, 0)
        row.pack_end(val_lbl, False, False, 0)
        row.pack_end(minus, False, False, 0)
        return row, val_lbl

    @staticmethod
    def _fmt_short(seconds):
        seconds = max(int(seconds), 0)
        h, m = seconds // 3600, (seconds % 3600) // 60
        if h > 0:
            return f"{h}h {m:02}m"
        return f"{m}m" if m > 0 else f"{seconds}s"

    # ---- phase / view management -------------------------------------------

    def set_phase(self, phase, label, cls):
        if self.phase == phase:
            return
        self.phase = phase
        self.phase_lbl.set_label(label)
        for widget in self._phase_widgets:
            ctx = widget.get_style_context()
            for c in PHASE_CLASSES:
                ctx.remove_class(c)
            ctx.add_class(cls)
        for row in (self.noz_row, self.bed_row, self.speed_row, self.zoff_row):
            ctx = row.get_style_context()
            for c in PHASE_CLASSES:
                ctx.remove_class(c)
            ctx.add_class(cls)
        if phase == "prep":
            self.probe_count = 0  # fresh count per prep phase (guard above
            # means this only runs on the transition into prep)
        if phase == "prep" and self.pulse_timeout is None:
            self.pulse_timeout = GLib.timeout_add(180, self._pulse)
        elif phase != "prep" and self.pulse_timeout is not None:
            GLib.source_remove(self.pulse_timeout)
            self.pulse_timeout = None
        self._show_buttons()

    def _set_big(self, text, word=False):
        ctx = self.big_lbl.get_style_context()
        if word:
            ctx.add_class("glance-word")
        else:
            ctx.remove_class("glance-word")
        self.big_lbl.set_label(text)

    def _pulse(self):
        if self.phase != "prep":
            self.pulse_timeout = None
            return False
        self.rail.pulse()
        return True

    def _show_buttons(self):
        job_over = self.state in ("complete", "cancelled", "error", "standby")
        paused = self.state == "paused"
        running = self.state == "printing" and self.phase == "print"
        self.btn_pause.set_visible(not paused)
        self.btn_resume.set_visible(paused)
        self.btn_stop.set_visible(not job_over)
        self.btn_close.set_visible(job_over)
        # pause can't act until PRINT_START releases the gcode queue, so
        # don't offer it before the print phase - cancel is the real option.
        # busy buttons stay insensitive until their state transition lands.
        self.btn_pause.set_sensitive(running and self.btn_pause not in self._busy_buttons)
        self.btn_resume.set_sensitive(paused and self.btn_resume not in self._busy_buttons)
        self.btn_stop.set_sensitive(self.state in ("printing", "paused"))
        # speed/flow only matter while gcode is actually running; during
        # heat/level/mesh/cool the rows just add noise, so hide them
        temps = not running and not paused
        for w, vis in ((self.noz_row, temps), (self.bed_row, temps),
                       (self.speed_row, running), (self.zoff_row, running),
                       (self.ext_row, paused), (self.ext_more, paused)):
            w.set_visible(vis)

    def refresh_view(self):
        if self.state != "printing":
            return
        m = HEAT_RE.search(self.msg) if self.msg else None
        if m:
            # the M117 only tells us which heater gates and its target; temps and
            # percent are computed live so they stay in sync with the temp column
            name = m.group(2)
            dev = "extruder" if name == "Nozzle" else "heater_bed"
            live = self._printer.get_stat(dev, "temperature")
            target = float(m.group(4)) if m.group(4) else float(self._printer.get_stat(dev, "target") or 0)
            if live is None or isinstance(live, dict):
                live = float(m.group(3))
            ambient = 25.0
            if live > target + 1:
                # overshoot cooldown (e.g. nozzle back down to tap temp): count the
                # live temp down instead of showing a meaningless percent
                self.set_phase("cool", _("COOLING"), "ph-heat")
                if self.cool_from is None or live > self.cool_from:
                    self.cool_from = live
                span = max(self.cool_from - target, 1.0)
                self._set_big(f"{live:.0f}°")
                self.sub_lbl.set_label(f"{name} → {target:.0f}°")
                self.rail.set_fraction(min(max((self.cool_from - live) / span, 0.0), 0.99))
            else:
                self.cool_from = None
                self.set_phase("heat", _("HEATING"), "ph-heat")
                # heating: measure from ambient so a warm start begins at 60-odd %, not 0
                pct = (live - ambient) / (target - ambient) * 100 if target > ambient + 1 else 99
                pct = min(max(int(pct), 0), 99)
                self._set_big(f"{pct}%")
                self.sub_lbl.set_label(f"{name} {live:.0f} / {target:.0f}°")
                self.rail.set_fraction(pct / 100)  # rail always agrees with the hero number
        elif self.msg.startswith("Leveling"):
            self.set_phase("prep", _("PREPARING"), "ph-prep")
            self._set_big(_("LEVELING"), word=True)
            if self.probe_count:
                pass_n = (self.probe_count - 1) // 4 + 1
                point = (self.probe_count - 1) % 4 + 1
                self.sub_lbl.set_label(f"QGL  ·  " + _("pass") + f" {pass_n}  ·  {point}/4")
            else:
                self.sub_lbl.set_label(_("Quad gantry level"))
        elif self.msg.startswith("Scanning"):
            self.set_phase("prep", _("PREPARING"), "ph-prep")
            self._set_big(_("MESHING"), word=True)
            self.sub_lbl.set_label(_("Scanning bed mesh"))
        elif self.msg.startswith("Stabilizing"):
            self.set_phase("heat", _("HEATING"), "ph-heat")
            self.sub_lbl.set_label(_("Stabilizing temps"))
        else:
            self.set_phase("print", _("PRINTING"), "ph-print")
            self.update_time_left()

    # ---- data handling ------------------------------------------------------

    def process_update(self, action, data):
        if action == "notify_gcode_response":
            if "action:cancel" in data:
                self.set_job_state("cancelled")
            elif "action:paused" in data:
                self.set_job_state("paused")
            elif "action:resumed" in data:
                self.set_job_state("printing")
            elif ("probe: at" in data or "probe at" in data) and self.phase == "prep":
                # QGL probes 4 corners per pass, retrying until in tolerance
                self.probe_count += 1
                self.refresh_view()
            return
        if action == "notify_metadata_update" and data.get("filename") == self.filename:
            # a re-upload under the SAME name (Cura re-slice) refreshes
            # metadata; the cached thumbnail must be invalidated too
            self.thumb_loaded = False
            self.get_file_metadata(response=True)
            return
        if action != "notify_status_update":
            return

        self._update_temps()
        if "gcode_move" in data:
            gm = data["gcode_move"]
            if "speed_factor" in gm:
                self.speed_pct = round(float(gm["speed_factor"]) * 100)
                self.speed_val.set_label(f"{self.speed_pct}%")
            if "extrude_factor" in gm:
                self.flow_pct = round(float(gm["extrude_factor"]) * 100)
                self.flow_val.set_label(f"{self.flow_pct}%")
            if "homing_origin" in gm:
                live = float(gm["homing_origin"][2])
                if self.pending_zoff is not None:
                    if abs(live - self.pending_zoff) < 0.0005:
                        self.pending_zoff = None
                        self.zoff_box.get_style_context().remove_class("glance-busy")
                        self._show_zoff(live)
                else:
                    self._show_zoff(live)
        if "display_status" in data and "message" in data["display_status"]:
            self.msg = data["display_status"]["message"] or ""
        if "print_stats" in data:
            ps = data["print_stats"]
            if "filename" in ps:
                self.update_filename(ps["filename"])
            if "state" in ps:
                self.set_job_state(ps["state"], ps.get("message", ""))
        self.refresh_view()
        self._show_buttons()
        self._update_sheet()

    def _update_temps(self):
        for dev, lbl in (("extruder", self.noz_val), ("heater_bed", self.bed_val)):
            temp = self._printer.get_stat(dev, "temperature")
            target = self._printer.get_stat(dev, "target")
            if temp is None or isinstance(temp, dict):
                continue
            text = f"{temp:.0f}°"
            if target:
                text += f" / {target:.0f}°"
            lbl.set_label(text)

    def set_job_state(self, state, msg=""):
        if state == self.state:
            return
        logging.debug(f"glance_status: job state {self.state} -> {state}")
        self.state = state
        if state == "printing":
            self._screen.screensaver.close()
            self.phase = None  # force refresh_view to re-derive the phase
        elif state == "paused":
            self.set_phase("pause", _("PAUSED"), "ph-heat")
            self._set_big(_("PAUSED"), word=True)
            # filename is already in the side column; show elapsed time instead
            dur = float(self._printer.get_stat("print_stats", "print_duration") or 0)
            self.sub_lbl.set_label(_("elapsed") + f" {self._fmt_short(dur)}")
        elif state == "complete":
            self.set_phase("done", _("COMPLETE"), "ph-done")
            self._set_big(_("DONE"), word=True)
            total = (self._printer.get_stat("print_stats", "total_duration")
                     or self._printer.get_stat("print_stats", "print_duration") or 0)
            self.sub_lbl.set_label(_("finished in") + f" {self._fmt_short(total)}")
            self.rail.set_fraction(1)
            self._add_timeout(self._config.get_main_config().getint("job_complete_timeout", 0))
        elif state in ("cancelled", "cancelling"):
            self.set_phase("cancel", _("CANCELLED"), "ph-err")
            self._set_big(_("STOPPED"), word=True)
            self.sub_lbl.set_label(os.path.splitext(self.filename)[0])
            self._add_timeout(self._config.get_main_config().getint("job_cancelled_timeout", 0))
        elif state == "error":
            self.set_phase("error", _("ERROR"), "ph-err")
            self._set_big(_("ERROR"), word=True)
            self.sub_lbl.set_label(msg or "")
            self._screen.show_popup_message(msg)
        # the requested transition arrived (or a new one did): busy is over
        for b in (self.btn_pause, self.btn_resume):
            self._set_busy(b, False)
        # queued offset taps die with the job state (e.g. cancel flushes them)
        self.pending_zoff = None
        self.zoff_box.get_style_context().remove_class("glance-busy")
        self.content.show_all()
        self._show_buttons()

    def _add_timeout(self, timeout):
        self._screen.screensaver.close()
        if timeout != 0:
            GLib.timeout_add_seconds(timeout, self.close_panel)

    def update_time_left(self):
        progress = (
            max(self._printer.get_stat("virtual_sdcard", "file_position")
                - self.file_metadata["gcode_start_byte"], 0)
            / (self.file_metadata["gcode_end_byte"] - self.file_metadata["gcode_start_byte"])
        ) if "gcode_start_byte" in self.file_metadata \
            else (self._printer.get_stat("virtual_sdcard", "progress") or 0)

        print_duration = float(self._printer.get_stat("print_stats", "print_duration") or 0)
        # reference estimates assume 100% speed; scale by sqrt(factor) so the
        # ETA reacts immediately to the Speed stepper (sqrt because accel and
        # min-layer-times keep real prints from scaling linearly). The measured
        # pace below self-corrects over time on its own.
        speed_adj = (max(self.speed_pct, 10) / 100) ** 0.5
        last_time = self.file_metadata.get("last_time", 0) / speed_adj
        slicer_time = self.file_metadata.get("estimated_time", 0) / speed_adj
        file_time = (print_duration / progress) if progress > 0 else 0

        # blend like stock "auto": trust history/slicer early, measured pace late
        if progress <= 0.3 or file_time == 0:
            estimated = last_time or slicer_time or file_time
        else:
            weight_file = progress - 0.3
            weight_ref = (1.3 - progress) if (last_time or slicer_time) else 0
            estimated = (
                (last_time or slicer_time) * weight_ref + file_time * weight_file
            ) / (weight_ref + weight_file)

        if estimated > 1:
            remaining = max(estimated - print_duration, 0)
            if remaining < 60:
                # no ETA clock here: the full line overflows and shifts the layout
                self.sub_lbl.set_label(_("less than a minute left"))
            else:
                eta = (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")
                self.sub_lbl.set_label(
                    f"{self._fmt_short(remaining)} " + _("left") + "  ·  " + _("done") + f" {eta}"
                )
        else:
            self.sub_lbl.set_label(_("elapsed") + f" {self._fmt_short(print_duration)}")
        # hero and rail track FILE progress, not elapsed/estimated time: file
        # position is monotonic and reaches 100 exactly when the job ends,
        # while a time-based percent overruns whenever a speed-factor change
        # (or any estimate error) makes the estimate shorter than reality
        self.progress = min(max(progress, 0), 1)
        self._set_big(f"{trunc(self.progress * 100)}%")
        self.rail.set_fraction(self.progress)

    # ---- file / thumbnail ---------------------------------------------------

    def update_filename(self, filename):
        if not filename or filename == self.filename:
            return
        self.filename = filename
        self.fname_lbl.set_label(os.path.splitext(filename)[0])
        self.thumb_loaded = False
        self.get_file_metadata()

    def get_file_metadata(self, response=False):
        if self._files.file_metadata_exists(self.filename):
            self.file_metadata = self._files.get_file_info(self.filename)
            if self.file_metadata.get("job_id"):
                history = self._screen.apiclient.send_request(
                    f"server/history/job?uid={self.file_metadata['job_id']}")
                if history and history["job"]["status"] == "completed" \
                        and history["job"]["print_duration"]:
                    self.file_metadata["last_time"] = history["job"]["print_duration"]
        elif not response:
            self._files.request_metadata(self.filename)
        self.show_file_thumbnail()

    def show_file_thumbnail(self):
        if self.thumb_loaded:
            return
        # cap the pixbuf so its minimum size can never ratchet the side
        # column past the screen height (which shoves the rail off-screen)
        width = min(self.thumb_btn.get_allocated_width() or 0, self._screen.width * 0.28)
        height = min(self.thumb_btn.get_allocated_height() or 0, self._screen.height * 0.30)
        if width <= 1 or height <= 1:
            width = self._screen.width * 0.28
            height = self._screen.height * 0.30
        pixbuf = self.get_file_image(self.filename, width, height)
        if pixbuf is None:
            # no thumbnail for this file: don't keep showing the previous print's part
            if image := find_widget(self.thumb_btn, Gtk.Image):
                image.clear()
            return
        if image := find_widget(self.thumb_btn, Gtk.Image):
            image.set_from_pixbuf(pixbuf)
            self.thumb_loaded = True

    def show_fullscreen_thumbnail(self, widget):
        pixbuf = self.get_file_image(self.filename, self._screen.width * .9, self._screen.height * .75)
        if pixbuf is None:
            return
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_vexpand(True)
        self.thumb_dialog = self._gtk.Dialog(self.filename, None, image, self.close_dialog)

    def close_dialog(self, dialog=None, response_id=None):
        self._gtk.remove_dialog(dialog)
        self.thumb_dialog = None

    # ---- actions ------------------------------------------------------------

    def filament_action(self, widget, macro):
        # gcode runs immediately while paused (queue is idle between file lines)
        self._screen._ws.klippy.gcode_script(macro)

    def open_extrude(self, widget):
        self._screen.show_panel("extrude")

    def adjust_speed(self, widget, direction):
        self.speed_pct = min(max(self.speed_pct + 5 * direction, 10), 300)
        self.speed_val.set_label(f"{self.speed_pct}%")
        self._screen._ws.klippy.gcode_script(f"M220 S{self.speed_pct}")

    def adjust_flow(self, widget, direction):
        self.flow_pct = min(max(self.flow_pct + 1 * direction, 50), 200)
        self.flow_val.set_label(f"{self.flow_pct}%")
        self._screen._ws.klippy.gcode_script(f"M221 S{self.flow_pct}")

    def _set_busy(self, button, busy):
        # GTK3 won't scale GtkSpinner's drawn animation no matter what the
        # node's CSS min-size says, so busy-state is a whole-button pulse.
        # Busy persists until the job state actually transitions (cleared in
        # set_job_state); _show_buttons must not touch it, or the every-second
        # status updates wipe the pulse before it is ever seen.
        ctx = button.get_style_context()
        if busy:
            self._busy_buttons.add(button)
            button.set_sensitive(False)
            ctx.add_class("glance-busy")
            GLib.timeout_add_seconds(20, self._busy_failsafe, button)
        else:
            self._busy_buttons.discard(button)
            ctx.remove_class("glance-busy")

    def _busy_failsafe(self, button):
        # a pause/resume request that never produced a state change would
        # otherwise leave the button dead forever
        if button in self._busy_buttons:
            self._set_busy(button, False)
            self._show_buttons()
        return False

    def pause(self, widget):
        self._set_busy(self.btn_pause, True)
        self._screen._ws.klippy.print_pause()

    def resume(self, widget):
        self._set_busy(self.btn_resume, True)
        self._screen._ws.klippy.print_resume()

    def cancel(self, widget):
        buttons = [
            {"name": _("Cancel Print"), "response": Gtk.ResponseType.OK, "style": "dialog-error"},
            {"name": _("Go Back"), "response": Gtk.ResponseType.CANCEL, "style": "dialog-info"},
        ]
        label = Gtk.Label(hexpand=True, vexpand=True, wrap=True)
        label.set_markup(_("Are you sure you wish to cancel this print?"))
        self._gtk.Dialog(_("Cancel"), buttons, label, self.cancel_confirm)

    def cancel_confirm(self, dialog, response_id):
        self._gtk.remove_dialog(dialog)
        if response_id != Gtk.ResponseType.OK:
            return
        self.btn_stop.set_sensitive(False)
        if self.phase in ("heat", "cool", "prep"):
            # PRINT_START holds the gcode queue through its heat waits and
            # leveling, so a queued CANCEL_PRINT - and even Moonraker's
            # firmware_restart, which is a queued script too - only acts after
            # the queue frees up, minutes later. The emergency-stop webhook is
            # the one call that bypasses the queue; follow it with a firmware
            # restart so Klipper comes straight back instead of sitting in
            # the shutdown screen.
            self._screen._ws.klippy.emergency_stop()
            GLib.timeout_add_seconds(2, self._fw_restart_once)
            self.set_job_state("cancelled")
        else:
            self._screen._ws.klippy.print_cancel()

    def _fw_restart_once(self):
        self._screen._ws.klippy.restart_firmware()
        return False  # single-shot timeout

    def _build_sheet(self):
        # a real modal: dimmed backdrop over the whole panel, bordered card
        # centered on it. Tap outside the card (or Close) to dismiss.
        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("glance-backdrop")
        backdrop.connect("button-release-event", self.close_sheet)
        card = Gtk.EventBox()
        card.connect("button-release-event", lambda w, e: True)  # eat card taps
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        card.set_size_request(int(self._screen.width * 0.66), -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.get_style_context().add_class("glance-sheet")
        card.add(box)

        # flow moved here from the job screen (z-offset took its slot)
        box.pack_start(self.flow_row, False, False, 0)

        grid = Gtk.Grid(column_homogeneous=True, row_spacing=10, column_spacing=32)
        stats = [("layer", _("Layer")), ("z", "Z"), ("fila", _("Filament")),
                 ("flow", _("Flow")), ("fan", _("Fan")), ("vel", _("Velocity"))]
        for i, (key, name) in enumerate(stats):
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            n = Gtk.Label(label=name, xalign=0, hexpand=True)
            n.get_style_context().add_class("glance-temp-name")
            v = Gtk.Label(label="—", xalign=1)
            v.get_style_context().add_class("glance-temp-val")
            self.sheet_labels[key] = v
            cell.pack_start(n, True, True, 0)
            cell.pack_end(v, False, False, 0)
            grid.attach(cell, i % 2, i // 2, 1, 1)
        box.pack_start(grid, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        settings = Gtk.Button(label=_("Settings"))
        settings.connect("clicked", self.sheet_settings)
        close = Gtk.Button(label=_("Close"))
        close.connect("clicked", self.close_sheet)
        for b in (settings, close):
            b.get_style_context().add_class("glance-row-btn")
            row.pack_start(b, True, True, 0)
        box.pack_start(row, False, False, 0)
        self.sheet_box = box

        backdrop.add(card)
        # pre-render then seal, so attach_panel's show_all can't reveal it
        backdrop.show_all()
        backdrop.hide()
        backdrop.set_no_show_all(True)
        return backdrop

    def open_details(self, widget):
        self.backdrop.show()
        self._update_sheet()

    def close_sheet(self, widget=None, event=None):
        self.backdrop.hide()
        return True

    def sheet_settings(self, widget):
        self.close_sheet()
        self._screen._go_to_submenu(widget, "")

    def _show_zoff(self, value):
        s = f"{value:+.3f}"
        self.zoff_prefix.set_label(s[:-3])
        for i, d in enumerate(self.zoff_digits):
            d.set_label(s[-3 + i])

    def adjust_zoffset_row(self, widget, direction):
        # optimistic: during PRINT_START's heating waits the gcode queue is
        # blocked, so the command applies later - show the target value now,
        # pulsing until gcode_move confirms it landed
        delta = 0.01 * direction
        offset = self._printer.get_stat("gcode_move", "homing_origin")
        cur = self.pending_zoff if self.pending_zoff is not None else (
            float(offset[2]) if offset and not isinstance(offset, dict) else 0.0)
        self.pending_zoff = round(cur + delta, 3)
        self._show_zoff(self.pending_zoff)
        self.zoff_box.get_style_context().add_class("glance-busy")
        self._screen._ws.klippy.gcode_script(
            f"SET_GCODE_OFFSET Z_ADJUST={delta:+.3f} MOVE=1")

    def _update_sheet(self):
        if not self.backdrop.get_visible():
            return
        gs = self._printer.get_stat

        pos = gs("gcode_move", "gcode_position")
        if pos:
            self.sheet_labels["z"].set_label(f"{float(pos[2]):.2f} mm")
        info = gs("print_stats", "info") or {}
        cur, tot = info.get("current_layer"), info.get("total_layer")
        if (cur is None or not tot) and pos:
            # Cura doesn't send SET_PRINT_STATS_INFO; derive from Z + metadata
            lh = self.file_metadata.get("layer_height")
            flh = self.file_metadata.get("first_layer_height") or lh
            oh = self.file_metadata.get("object_height")
            if lh:
                z = float(pos[2])
                cur = max(int((z - flh) / lh + 1.5), 1) if z >= flh else 1
                tot = self.file_metadata.get("layer_count") or                     (max(int((oh - flh) / lh + 1.5), 1) if oh else None)
        self.sheet_labels["layer"].set_label(
            f"{cur} / {tot}" if cur is not None and tot else "—")
        used = gs("print_stats", "filament_used")
        if used is not None and not isinstance(used, dict):
            self.sheet_labels["fila"].set_label(f"{float(used) / 1000:.1f} m")
        ev = gs("motion_report", "live_extruder_velocity")
        if ev is not None and not isinstance(ev, dict):
            # 1.75mm filament cross-section
            self.sheet_labels["flow"].set_label(f"{max(2.405 * float(ev), 0):.1f} mm³/s")
        v = gs("motion_report", "live_velocity")
        if v is not None and not isinstance(v, dict):
            self.sheet_labels["vel"].set_label(f"{float(v):.0f} mm/s")
        fan = self._printer.get_fan_speed("fan")
        if fan is not None:
            self.sheet_labels["fan"].set_label(f"{float(fan) * 100:.0f}%")

    def close_panel(self, widget=None):
        self._screen.state_ready(wait=False)

    def deactivate(self):
        self.backdrop.hide()
        if self.pulse_timeout is not None:
            GLib.source_remove(self.pulse_timeout)
            self.pulse_timeout = None

    def activate(self):
        # do NOT reset thumb_loaded here: reloading the thumbnail on every
        # re-attach momentarily collapses the side column's natural size and
        # reflows the whole layout - the hero visibly jumps. The widget keeps
        # its pixbuf across panel switches; update_filename resets the flag
        # when a reload is genuinely needed.
        if self.phase == "prep" and self.pulse_timeout is None:
            self.pulse_timeout = GLib.timeout_add(180, self._pulse)
