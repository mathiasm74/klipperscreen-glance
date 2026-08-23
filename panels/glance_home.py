# -*- coding: utf-8 -*-
# Glance Home: the idle screen. Calm READY/WARM/HEATING hero, temps demoted to
# one line, recent gcode files as tap-to-reprint cards, and four doors:
# Preheat / Move / Filament / Menu. Turns amber with a filling rail while
# heaters climb (ambient-referenced, same math as the job screen).

import logging
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango
from ks_includes.screen_panel import ScreenPanel
from ks_includes.KlippyGtk import find_widget

PHASE_CLASSES = ["ph-heat", "ph-prep", "ph-print", "ph-done", "ph-err"]
AMBIENT = 25.0


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        title = title or _("Home")
        super().__init__(screen, title)
        self.mode = None
        self.recent = []

        # hero
        self.big_lbl = Gtk.Label(label=_("READY"), xalign=0, vexpand=True,
                                 valign=Gtk.Align.CENTER)
        self.big_lbl.get_style_context().add_class("glance-big")
        self.big_lbl.get_style_context().add_class("glance-word")
        self.big_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.sub_lbl = Gtk.Label(label="", xalign=0)
        self.sub_lbl.get_style_context().add_class("glance-subline")
        self.sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.temps_lbl = Gtk.Label(label="", xalign=0)
        self.temps_lbl.get_style_context().add_class("glance-temp-name")

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        hero.get_style_context().add_class("glance-hero")
        hero.pack_start(self.big_lbl, True, True, 0)
        hero.pack_start(self.sub_lbl, False, False, 0)
        hero.pack_start(self.temps_lbl, False, False, 6)

        # side column: recent files as print-again cards
        self.cards = []
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.get_style_context().add_class("glance-side")
        side.set_size_request(int(self._screen.width * 0.36), -1)
        side.set_hexpand(False)

        head = Gtk.Label(label=_("Print again"), xalign=0)
        head.get_style_context().add_class("glance-fname")
        side.pack_start(head, False, False, 0)

        # card 0: big with thumbnail; cards 1-2: compact rows
        big_card = Gtk.Button()
        big_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        img = Gtk.Image()
        name0 = Gtk.Label(label="", xalign=0)
        name0.get_style_context().add_class("glance-fname")
        name0.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        # ellipsized labels still request full-text natural width; cap it so
        # long filenames can't widen the column and squeeze the hero
        name0.set_max_width_chars(24)
        time0 = Gtk.Label(label="", xalign=0)
        time0.get_style_context().add_class("glance-temp-name")
        big_box.pack_start(img, True, True, 0)
        big_box.pack_start(name0, False, False, 0)
        big_box.pack_start(time0, False, False, 0)
        big_card.add(big_box)
        big_card.get_style_context().add_class("glance-card")
        big_card.set_vexpand(True)
        big_card.connect("clicked", self.card_clicked, 0)
        side.pack_start(big_card, True, True, 0)
        self.cards.append({"btn": big_card, "img": img, "name": name0, "time": time0})

        for i in (1, 2):
            row = Gtk.Button()
            rbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label="", xalign=0, hexpand=True)
            name.get_style_context().add_class("glance-fname")
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name.set_max_width_chars(18)
            t = Gtk.Label(label="", xalign=1)
            t.get_style_context().add_class("glance-temp-name")
            rbox.pack_start(name, True, True, 0)
            rbox.pack_end(t, False, False, 0)
            row.add(rbox)
            row.get_style_context().add_class("glance-card")
            row.connect("clicked", self.card_clicked, i)
            side.pack_start(row, False, False, 0)
            self.cards.append({"btn": row, "img": None, "name": name, "time": t})

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, cb in ((_("Preheat"), self.open_preheat), (_("Move"), self.open_move),
                          (_("Filament"), self.open_filament), ("• • •", self.open_menu)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("glance-row-btn")
            b.set_hexpand(False)
            b.connect("clicked", cb)
            actions.pack_start(b, True, True, 0)
        side.pack_end(actions, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        main.pack_start(hero, True, True, 0)
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
        self.overlay = Gtk.Overlay()
        self.overlay.add(inner)
        self.backdrop = self._build_confirm()
        self.overlay.add_overlay(self.backdrop)
        self.content.pack_start(self.overlay, True, True, 0)
        self._phase_widgets = (self.big_lbl, self.rail, self.root)
        # the gcode list and per-file metadata arrive asynchronously; refresh
        # the cards whenever the file store changes
        self._files.add_callback(self._files_changed)

    @staticmethod
    def _fmt_short(seconds):
        seconds = max(int(seconds), 0)
        h, m = seconds // 3600, (seconds % 3600) // 60
        if h > 0:
            return f"{h}h {m:02}m"
        return f"{m}m" if m > 0 else "<1m"

    # ---- state ---------------------------------------------------------------

    def _set_mode(self, mode, cls):
        if mode == self.mode:
            return
        self.mode = mode
        for widget in self._phase_widgets:
            ctx = widget.get_style_context()
            for c in PHASE_CLASSES:
                ctx.remove_class(c)
            if cls:
                ctx.add_class(cls)
        self.big_lbl.set_label(mode)

    def _files_changed(self, action=None, item=None):
        GLib.idle_add(self._refresh_recent)

    def process_update(self, action, data):
        if action == "notify_metadata_update":
            self._refresh_recent()
            return
        if action != "notify_status_update":
            return
        nt = self._printer.get_stat("extruder", "temperature")
        ntg = self._printer.get_stat("extruder", "target") or 0
        bt = self._printer.get_stat("heater_bed", "temperature")
        btg = self._printer.get_stat("heater_bed", "target") or 0
        if nt is None or isinstance(nt, dict):
            return
        self.temps_lbl.set_label(
            _("Nozzle") + f" {nt:.0f}°" + (f" / {ntg:.0f}°" if ntg else "") +
            "   ·   " + _("Bed") + f" {bt:.0f}°" + (f" / {btg:.0f}°" if btg else ""))
        if ntg or btg:
            heating = (ntg and nt < ntg - 2) or (btg and bt < btg - 2)
            if heating:
                self._set_mode(_("HEATING"), "ph-heat")
                pcts = []
                for temp, target in ((nt, ntg), (bt, btg)):
                    if target > AMBIENT + 1:
                        pcts.append(min(max((temp - AMBIENT) / (target - AMBIENT), 0), 1))
                self.rail.set_fraction(min(pcts) if pcts else 0)
                self.sub_lbl.set_label(_("heaters climbing"))
            else:
                self._set_mode(_("WARM"), "ph-heat")
                self.rail.set_fraction(1)
                self.sub_lbl.set_label(_("holding temperature"))
        else:
            self._set_mode(_("READY"), None)
            self.rail.set_fraction(0)
            homed = self._printer.get_stat("toolhead", "homed_axes") or ""
            self.sub_lbl.set_label(
                _("heaters off") + ("   ·   " + _("homed") if "xyz" in homed else ""))

    # ---- recent files --------------------------------------------------------

    def activate(self):
        self._refresh_recent()

    def _refresh_recent(self):
        files = [(name, meta) for name, meta in self._files.files.items()
                 if name.lower().endswith(".gcode")]
        files.sort(key=lambda kv: kv[1].get("modified", 0), reverse=True)
        self.recent = [name for name, _m in files[:3]]
        for i, card in enumerate(self.cards):
            if i < len(self.recent):
                name = self.recent[i]
                meta = self._files.files.get(name, {})
                card["name"].set_label(os.path.splitext(name)[0])
                est = meta.get("estimated_time")
                card["time"].set_label(self._fmt_short(est) if est else "")
                if card["img"] is not None:
                    pixbuf = self.get_file_image(name, self._screen.width * 0.30,
                                                 self._screen.height * 0.30)
                    if pixbuf is not None:
                        card["img"].set_from_pixbuf(pixbuf)
                    else:
                        card["img"].clear()
                card["btn"].show()
            else:
                card["btn"].hide()

    def _build_confirm(self):
        # glance-style start-print modal: big preview, name, time, two verbs
        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("glance-backdrop")
        backdrop.connect("button-release-event", self.close_confirm)
        card = Gtk.EventBox()
        card.connect("button-release-event", lambda w, e: True)
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        card.set_size_request(int(self._screen.width * 0.55), -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("glance-sheet")
        card.add(box)
        self.confirm_file = None
        self.confirm_img = Gtk.Image()
        self.confirm_name = Gtk.Label(label="", halign=Gtk.Align.CENTER)
        self.confirm_name.get_style_context().add_class("glance-temp-val")
        self.confirm_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.confirm_time = Gtk.Label(label="", halign=Gtk.Align.CENTER)
        self.confirm_time.get_style_context().add_class("glance-temp-name")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.get_style_context().add_class("glance-row-btn")
        cancel.connect("clicked", self.close_confirm)
        start = Gtk.Button(label=_("Start"))
        start.get_style_context().add_class("glance-row-btn")
        start.get_style_context().add_class("glance-start-btn")
        start.connect("clicked", self.start_confirmed)
        for b in (cancel, start):
            b.set_hexpand(False)
            row.pack_start(b, True, True, 0)
        box.pack_start(self.confirm_img, False, False, 0)
        box.pack_start(self.confirm_name, False, False, 0)
        box.pack_start(self.confirm_time, False, False, 0)
        box.pack_start(row, False, False, 0)
        backdrop.add(card)
        backdrop.show_all()
        backdrop.hide()
        backdrop.set_no_show_all(True)
        return backdrop

    def card_clicked(self, widget, index):
        if index >= len(self.recent):
            return
        self.confirm_file = self.recent[index]
        meta = self._files.files.get(self.confirm_file, {})
        self.confirm_name.set_label(os.path.splitext(self.confirm_file)[0])
        est = meta.get("estimated_time")
        self.confirm_time.set_label(self._fmt_short(est) if est else "")
        pixbuf = self.get_file_image(self.confirm_file, self._screen.width * 0.48,
                                     self._screen.height * 0.42)
        if pixbuf is not None:
            self.confirm_img.set_from_pixbuf(pixbuf)
        else:
            self.confirm_img.clear()
        self.backdrop.show()

    def close_confirm(self, widget=None, event=None):
        self.backdrop.hide()
        return True

    def start_confirmed(self, widget):
        self.backdrop.hide()
        if self.confirm_file:
            self._screen._ws.klippy.print_start(self.confirm_file)

    def deactivate(self):
        self.backdrop.hide()

    # ---- doors ---------------------------------------------------------------

    def open_preheat(self, widget):
        self._screen.show_panel("glance_preheat")

    def open_move(self, widget):
        self._screen.show_panel("glance_move")

    def open_filament(self, widget):
        self._screen.show_panel("glance_extrude")

    def open_menu(self, widget):
        self._screen.show_panel("main_menu", items=self._config.get_menu_items("__main"))
