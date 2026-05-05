import random
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gio

from bbs_radioo.logging_utils import log_event
from bbs_radioo.player import RadioPlayer
from bbs_radioo.station_store import StationStore
from bbs_radioo.theme_map import THEMES, THEME_BY_ID
from bbs_radioo.sources import curated, somafm, radiobrowser


class RadiooApp(Gtk.Application):

    def __init__(self, state_dir: str):
        super().__init__(application_id="io.github.blacksamdev.Radioo")
        self.connect("activate", self.on_activate)
        self.state_dir = state_dir
        self.player = RadioPlayer()
        self.store = StationStore()
        self._selected_themes: set[str] = set()
        self._stations: list[dict] = []
        self._loading = False

    # ─────────────────────────────
    # app init
    # ─────────────────────────────

    def on_activate(self, app):
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_title("BBS radiOO")
        self.win.set_default_size(900, 620)
        self.win.connect("destroy", self._on_shutdown)

        self.player.on_status_change = self._set_status
        self.player.on_station_change = self._on_station_change
        self.player.on_metadata_change = self._on_metadata_change

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.append(self._build_header())
        vbox.append(self._build_theme_bar())
        vbox.append(self._build_main_area())
        vbox.append(self._build_statusbar())

        self.win.set_child(vbox)
        self.win.present()

    # ─────────────────────────────
    # UI construction
    # ─────────────────────────────

    def _build_header(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(12)
        header.set_margin_end(12)

        title = Gtk.Label(label="BBS radiOO")
        title.set_hexpand(True)
        title.set_xalign(0)

        self.btn_random = Gtk.Button(label="🎲 Aléatoire")
        self.btn_random.connect("clicked", self._on_random)
        self.btn_random.set_sensitive(False)

        self.btn_stop = Gtk.Button(label="⏹ Stop")
        self.btn_stop.connect("clicked", self._on_stop)
        self.btn_stop.set_sensitive(False)

        btn_favorites = Gtk.ToggleButton(label="★ Favoris")
        btn_favorites.connect("toggled", self._on_favorites_toggled)
        self._btn_favorites = btn_favorites

        header.append(title)
        header.append(self.btn_random)
        header.append(self.btn_stop)
        header.append(btn_favorites)
        return header

    def _build_theme_bar(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_min_content_height(56)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(12)
        bar.set_margin_end(12)

        self._theme_buttons: dict[str, Gtk.ToggleButton] = {}
        for theme in THEMES:
            btn = Gtk.ToggleButton(label=f"{theme['emoji']} {theme['label']}")
            btn.connect("toggled", self._on_theme_toggled, theme["id"])
            bar.append(btn)
            self._theme_buttons[theme["id"]] = btn

        scroll.set_child(bar)
        return scroll

    def _build_main_area(self):
        self._stack = Gtk.Stack()

        # Vue stations
        self._station_scroll = Gtk.ScrolledWindow()
        self._station_scroll.set_vexpand(True)
        self._station_list = Gtk.ListBox()
        self._station_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._station_scroll.set_child(self._station_list)
        self._stack.add_named(self._station_scroll, "stations")

        # Vue chargement
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_vexpand(True)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(48, 48)
        spinner_box.append(self._spinner)
        spinner_box.append(Gtk.Label(label="Chargement des stations..."))
        self._stack.add_named(spinner_box, "loading")

        # Vue vide
        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_halign(Gtk.Align.CENTER)
        empty_box.set_vexpand(True)
        empty_lbl = Gtk.Label(label="Sélectionne un ou plusieurs thèmes pour découvrir des stations.")
        empty_lbl.set_wrap(True)
        empty_box.append(empty_lbl)
        self._stack.add_named(empty_box, "empty")

        self._stack.set_visible_child_name("empty")
        return self._stack

    def _build_statusbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.set_margin_start(12)
        bar.set_margin_end(12)

        self._now_playing_label = Gtk.Label(label="")
        self._now_playing_label.set_hexpand(True)
        self._now_playing_label.set_xalign(0)
        self._now_playing_label.set_ellipsize(3)

        self._track_label = Gtk.Label(label="")
        self._track_label.set_hexpand(True)
        self._track_label.set_xalign(0)
        self._track_label.set_ellipsize(3)
        ctx = self._track_label.get_style_context()
        ctx.add_class("dim-label")

        self._status_label = Gtk.Label(label="Prêt.")
        self._status_label.set_xalign(1)

        bar.append(self._now_playing_label)
        bar.append(self._track_label)
        bar.append(self._status_label)
        return bar

    # ─────────────────────────────
    # theme selection
    # ─────────────────────────────

    def _on_theme_toggled(self, btn, theme_id):
        if btn.get_active():
            self._selected_themes.add(theme_id)
        else:
            self._selected_themes.discard(theme_id)

        if self._btn_favorites.get_active():
            self._btn_favorites.set_active(False)
            return

        self._load_stations()

    def _load_stations(self):
        if not self._selected_themes:
            self._stack.set_visible_child_name("empty")
            self._stations = []
            self.btn_random.set_sensitive(False)
            return

        self._stack.set_visible_child_name("loading")
        self._spinner.start()
        self._loading = True

        theme_ids = list(self._selected_themes)
        threading.Thread(
            target=self._fetch_stations,
            args=(theme_ids,),
            daemon=True
        ).start()

    def _fetch_stations(self, theme_ids: list[str]):
        stations = []

        # Curated (synchrone, instantané)
        stations += curated.get_stations_for_themes(theme_ids)

        # SomaFM
        stations += somafm.get_stations_for_themes(theme_ids, THEME_BY_ID)

        # radio-browser
        stations += radiobrowser.get_stations_for_themes(theme_ids, THEME_BY_ID)

        # Dédoublonnage par id
        seen = set()
        unique = []
        for s in stations:
            if s["id"] not in seen:
                seen.add(s["id"])
                unique.append(s)

        GLib.idle_add(self._on_stations_loaded, unique)

    def _on_stations_loaded(self, stations: list[dict]):
        self._stations = stations
        self._spinner.stop()
        self._loading = False
        self._populate_station_list(stations)
        self.btn_random.set_sensitive(bool(stations))
        self._stack.set_visible_child_name("stations")
        self._set_status(f"{len(stations)} station(s) trouvée(s).")
        return False

    # ─────────────────────────────
    # station list
    # ─────────────────────────────

    def _populate_station_list(self, stations: list[dict]):
        while child := self._station_list.get_first_child():
            self._station_list.remove(child)

        if not stations:
            lbl = Gtk.Label(label="Aucune station trouvée pour ces thèmes.")
            lbl.set_margin_top(24)
            self._station_list.append(lbl)
            return

        for station in stations:
            row = self._build_station_row(station)
            self._station_list.append(row)

    def _build_station_row(self, station: dict) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        row.set_margin_start(12)
        row.set_margin_end(12)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_hexpand(True)

        name_lbl = Gtk.Label(label=station.get("name", ""))
        name_lbl.set_xalign(0)
        name_lbl.set_ellipsize(3)

        desc = station.get("description", "") or ", ".join(station.get("tags", []))
        desc_lbl = Gtk.Label(label=desc[:80])
        desc_lbl.set_xalign(0)
        desc_lbl.set_ellipsize(3)
        ctx = desc_lbl.get_style_context()
        ctx.add_class("dim-label")

        info.append(name_lbl)
        info.append(desc_lbl)

        is_fav = self.store.is_favorite(station["id"])
        fav_btn = Gtk.ToggleButton(label="★")
        fav_btn.set_active(is_fav)
        fav_btn.connect("toggled", self._on_fav_toggled, station)

        play_btn = Gtk.Button(label="▶")
        play_btn.connect("clicked", self._on_play_station, station)

        row.append(info)
        row.append(fav_btn)
        row.append(play_btn)
        return row

    # ─────────────────────────────
    # playback
    # ─────────────────────────────

    def _on_play_station(self, _btn, station: dict):
        self.player.play(station)
        self.btn_stop.set_sensitive(True)

    def _on_random(self, _btn):
        if not self._stations:
            return
        station = random.choice(self._stations)
        log_event(f"Random pick: {station.get('name')}")
        self.player.play(station)
        self.btn_stop.set_sensitive(True)

    def _on_stop(self, _btn):
        self.player.stop()
        self.btn_stop.set_sensitive(False)

    def _on_station_change(self, station: dict | None):
        if station:
            self._now_playing_label.set_text(f"▶ {station.get('name', '')}")
        else:
            self._now_playing_label.set_text("")
            self._track_label.set_text("")

    def _on_metadata_change(self, title: str):
        self._track_label.set_text(title)

    # ─────────────────────────────
    # favorites
    # ─────────────────────────────

    def _on_fav_toggled(self, btn, station: dict):
        added = self.store.toggle(station)
        btn.set_active(added)

    def _on_favorites_toggled(self, btn):
        if btn.get_active():
            # Désélectionner les thèmes visuellement
            for b in self._theme_buttons.values():
                b.handler_block_by_func(self._on_theme_toggled)
                b.set_active(False)
                b.handler_unblock_by_func(self._on_theme_toggled)
            favs = self.store.all()
            self._stations = favs
            self._populate_station_list(favs)
            self.btn_random.set_sensitive(bool(favs))
            self._stack.set_visible_child_name("stations")
            self._set_status(f"{len(favs)} favori(s).")
        else:
            self._load_stations()

    # ─────────────────────────────
    # misc
    # ─────────────────────────────

    def _set_status(self, text: str):
        self._status_label.set_text(text)

    def _on_shutdown(self, _win):
        self.player.cleanup()
