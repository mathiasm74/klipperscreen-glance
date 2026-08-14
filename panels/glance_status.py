# -*- coding: utf-8 -*-
# Glance: at-a-glance job status panel for KlipperScreen.
# One giant phase-colored numeral, a bottom progress rail, the part thumbnail,
# nozzle/bed readouts and three fixed controls. The stock job_status panel
# stays available behind the "•••" button for all the detailed knobs.
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
        self.thumb_dialog = None
        self.thumb_loaded = False

        # hero column
        self.phase_lbl = Gtk.Label(label="", xalign=0)
        self.phase_lbl.get_style_context().add_class("glance-phase")
        self.big_lbl = Gtk.Label(label="—", xalign=0, vexpand=True, valign=Gtk.Align.CENTER)
        self.big_lbl.get_style_context().add_class("glance-big")
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
        self.thumb_btn.set_hexpand(True)
        self.thumb_btn.set_vexpand(True)
        self.thumb_btn.connect("clicked", self.show_fullscreen_thumbnail)

        self.fname_lbl = Gtk.Label(label="", xalign=0)
        self.fname_lbl.get_style_context().add_class("glance-fname")
        self.fname_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)

        self.noz_row, self.noz_val = self._temp_row(_("Nozzle"))
        self.bed_row, self.bed_val = self._temp_row(_("Bed"))

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
        side.pack_start(self.thumb_btn, True, True, 0)
        side.pack_start(self.fname_lbl, False, False, 0)
        side.pack_start(self.noz_row, False, False, 0)
        side.pack_start(self.bed_row, False, False, 0)
        side.pack_end(actions, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        main.pack_start(hero, True, True, 0)
        main.pack_start(side, False, False, 0)

        self.rail = Gtk.ProgressBar(hexpand=True)
        self.rail.get_style_context().add_class("glance-rail")
        self.rail.set_size_request(-1, 32)  # keep the rail on-screen even if the hero is tight

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.get_style_context().add_class("glance-root")
        self.root.pack_start(main, True, True, 0)
        self.root.pack_end(self.rail, False, False, 0)
        self.content.add(self.root)

    @staticmethod
    def _temp_row(name):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("glance-temp-row")
        name_lbl = Gtk.Label(label=name, xalign=0, hexpand=True)
        name_lbl.get_style_context().add_class("glance-temp-name")
        val_lbl = Gtk.Label(label="—", xalign=1)
        val_lbl.get_style_context().add_class("glance-temp-val")
        row.pack_start(name_lbl, True, True, 0)
        row.pack_end(val_lbl, False, False, 0)
        return row, val_lbl

    @staticmethod
    def _fmt_short(seconds):
        seconds = max(int(seconds), 0)
        h, m = seconds // 3600, (seconds % 3600) // 60
        if h > 0:
            return f"{h}h {m:02}m"
        return f"{m}m" if m > 0 else _("less than a minute")

    # ---- phase / view management -------------------------------------------

    def set_phase(self, phase, label, cls):
        if self.phase == phase:
            return
        self.phase = phase
        self.phase_lbl.set_label(label)
        for widget in (self.big_lbl, self.rail, self.root):
            ctx = widget.get_style_context()
            for c in PHASE_CLASSES:
                ctx.remove_class(c)
            ctx.add_class(cls)
        for row in (self.noz_row, self.bed_row):
            ctx = row.get_style_context()
            for c in PHASE_CLASSES:
                ctx.remove_class(c)
            ctx.add_class(cls)
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
        self.btn_pause.set_visible(not paused)
        self.btn_resume.set_visible(paused)
        self.btn_stop.set_visible(not job_over)
        self.btn_close.set_visible(job_over)
        self.btn_pause.set_sensitive(self.state == "printing")
        self.btn_stop.set_sensitive(self.state in ("printing", "paused"))

    def refresh_view(self):
        if self.state != "printing":
            return
        m = HEAT_RE.search(self.msg) if self.msg else None
        if m:
            self.set_phase("heat", _("HEATING"), "ph-heat")
            pct = int(m.group(1))
            self._set_big(f"{pct}%")
            target = f" / {m.group(4)}°" if m.group(4) else "°"
            self.sub_lbl.set_label(f"{m.group(2)} {m.group(3)}{target}")
            self.rail.set_fraction(pct / 1000)  # heat phase = first 10% of the rail
        elif self.msg.startswith("Leveling"):
            self.set_phase("prep", _("PREPARING"), "ph-prep")
            self._set_big(_("LEVELING"), word=True)
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
            return
        if action == "notify_metadata_update" and data.get("filename") == self.filename:
            self.get_file_metadata(response=True)
            return
        if action != "notify_status_update":
            return

        self._update_temps()
        if "display_status" in data and "message" in data["display_status"]:
            self.msg = data["display_status"]["message"] or ""
        if "print_stats" in data:
            ps = data["print_stats"]
            if "filename" in ps:
                self.update_filename(ps["filename"])
            if "state" in ps:
                self.set_job_state(ps["state"], ps.get("message", ""))
        self.refresh_view()

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
            self.sub_lbl.set_label(os.path.splitext(self.filename)[0])
        elif state == "complete":
            self.set_phase("done", _("COMPLETE"), "ph-done")
            self._set_big(_("DONE"), word=True)
            total = self._printer.get_stat("print_stats", "total_duration") or 0
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
        last_time = self.file_metadata.get("last_time", 0)
        slicer_time = self.file_metadata.get("estimated_time", 0)
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
            progress = min(max(print_duration / estimated, 0), 1)
            remaining = max(estimated - print_duration, 0)
            eta = (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")
            self.sub_lbl.set_label(
                f"{self._fmt_short(remaining)} " + _("left") + f"  ·  " + _("done") + f" {eta}"
            )
        else:
            self.sub_lbl.set_label(_("elapsed") + f" {self._fmt_short(print_duration)}")
        self.progress = progress
        self._set_big(f"{trunc(progress * 100)}%")
        self.rail.set_fraction(progress)

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
        width = self.thumb_btn.get_allocated_width()
        height = self.thumb_btn.get_allocated_height()
        if width <= 1 or height <= 1:
            width = self._screen.width * 0.28
            height = self._screen.height * 0.34
        pixbuf = self.get_file_image(self.filename, width, height)
        if pixbuf is None:
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

    def pause(self, widget):
        self.btn_pause.set_sensitive(False)
        self._screen._send_action(widget, "printer.print.pause", {})

    def resume(self, widget):
        self._screen._send_action(widget, "printer.print.resume", {})

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
        self._screen._ws.klippy.print_cancel()

    def open_details(self, widget):
        self._screen.show_panel("job_status")

    def close_panel(self, widget=None):
        self._screen.state_ready(wait=False)

    def deactivate(self):
        if self.pulse_timeout is not None:
            GLib.source_remove(self.pulse_timeout)
            self.pulse_timeout = None

    def activate(self):
        self.thumb_loaded = False
        if self.phase == "prep" and self.pulse_timeout is None:
            self.pulse_timeout = GLib.timeout_add(180, self._pulse)
