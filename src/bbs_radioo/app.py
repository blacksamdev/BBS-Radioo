import random
import threading
import urllib.request
import tempfile
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, GdkPixbuf

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
        self._current_station: dict | None = None

    # ─────────────────────────────
    # app init
    # ─────────────────────────────

    def on_activate(self, app):
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_title("BBS radiOO")
        self.win.set_default_size(1100, 660)
        self.win.connect("destroy", self._on_shutdown)

        self.player.on_status_change = self._set_status
        self.player.on_station_change = self._on_station_change
        self.player.on_metadata_change = self._on_metadata_change

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.append(self._build_header())

        # Paned : 2/3 gauche (onglets + liste) | 1/3 droite (panneau player)
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_vexpand(True)
        self._paned.set_position(720)
        self._paned.set_start_child(self._build_left_panel())
        self._paned.set_end_child(self._build_right_panel())
        vbox.append(self._paned)
        vbox.append(self._build_statusbar())

        self.win.set_child(vbox)
        self.win.present()

    # ─────────────────────────────
    # header
    # ─────────────────────────────

    def _build_header(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(12)
        header.set_margin_end(12)

        title = Gtk.Label(label="BBS radiOO 🎯")
        title.set_hexpand(True)
        title.set_xalign(0)

        self.btn_random = Gtk.Button(label="🎲 Aléatoire")
        self.btn_random.connect("clicked", self._on_random)
        self.btn_random.set_sensitive(False)

        header.append(title)
        header.append(self.btn_random)
        return header

    # ─────────────────────────────
    # left panel : tabs
    # ─────────────────────────────

    def _build_left_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._notebook = Gtk.Notebook()
        self._notebook.set_vexpand(True)

        # Onglet Favoris
        self._fav_list = Gtk.ListBox()
        self._fav_list.set_selection_mode(Gtk.SelectionMode.NONE)
        fav_scroll = Gtk.ScrolledWindow()
        fav_scroll.set_vexpand(True)
        fav_scroll.set_child(self._fav_list)
        self._notebook.append_page(fav_scroll, Gtk.Label(label="★ Favoris"))

        # Onglet Thèmes
        themes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        themes_box.append(self._build_theme_bar())
        themes_box.append(self._build_station_stack())
        self._notebook.append_page(themes_box, Gtk.Label(label="🎛 Thèmes"))

        # Onglet Recherche
        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        search_box.set_margin_top(8)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Nom de station, genre...")
        self._search_entry.connect("activate", self._on_search)
        self._search_entry.connect("search-changed", self._on_search_changed)
        search_box.append(self._search_entry)
        self._search_list = Gtk.ListBox()
        self._search_list.set_selection_mode(Gtk.SelectionMode.NONE)
        search_scroll = Gtk.ScrolledWindow()
        search_scroll.set_vexpand(True)
        search_scroll.set_child(self._search_list)
        search_box.append(search_scroll)
        self._notebook.append_page(search_box, Gtk.Label(label="🔍 Recherche"))

        self._notebook.connect("switch-page", self._on_tab_changed)
        box.append(self._notebook)
        return box

    def _build_theme_bar(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_min_content_height(52)

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

    def _build_station_stack(self):
        self._stack = Gtk.Stack()

        self._station_scroll = Gtk.ScrolledWindow()
        self._station_scroll.set_vexpand(True)
        self._station_list = Gtk.ListBox()
        self._station_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._station_scroll.set_child(self._station_list)
        self._stack.add_named(self._station_scroll, "stations")

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_vexpand(True)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(48, 48)
        spinner_box.append(self._spinner)
        spinner_box.append(Gtk.Label(label="Chargement des stations..."))
        self._stack.add_named(spinner_box, "loading")

        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_halign(Gtk.Align.CENTER)
        empty_box.set_vexpand(True)
        empty_lbl = Gtk.Label(label="Sélectionne un ou plusieurs thèmes.")
        empty_lbl.set_wrap(True)
        empty_box.append(empty_lbl)
        self._stack.add_named(empty_box, "empty")

        self._stack.set_visible_child_name("empty")
        return self._stack

    # ─────────────────────────────
    # right panel : player
    # ─────────────────────────────

    def _build_right_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(300, -1)

        # Image station
        self._station_image = Gtk.Image()
        self._station_image.set_pixel_size(96)
        self._station_image.set_from_icon_name("audio-x-generic")
        box.append(self._station_image)

        # Nom station
        self._panel_station_name = Gtk.Label(label="Aucune station")
        self._panel_station_name.set_wrap(True)
        self._panel_station_name.set_justify(Gtk.Justification.CENTER)
        self._panel_station_name.set_halign(Gtk.Align.CENTER)
        ctx = self._panel_station_name.get_style_context()
        ctx.add_class("title-4")
        box.append(self._panel_station_name)

        # Métadonnées (titre en cours)
        self._panel_track = Gtk.Label(label="")
        self._panel_track.set_wrap(True)
        self._panel_track.set_justify(Gtk.Justification.CENTER)
        self._panel_track.set_halign(Gtk.Align.CENTER)
        self._panel_track.set_ellipsize(3)
        ctx2 = self._panel_track.get_style_context()
        ctx2.add_class("dim-label")
        box.append(self._panel_track)

        # Séparateur
        box.append(Gtk.Separator())

        # Volume
        vol_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vol_box.set_halign(Gtk.Align.FILL)
        vol_lbl = Gtk.Label(label="🔊")
        vol_adj = Gtk.Adjustment(value=100, lower=0, upper=100,
                                 step_increment=5, page_increment=10, page_size=0)
        self._vol_slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                     adjustment=vol_adj)
        self._vol_slider.set_draw_value(False)
        self._vol_slider.set_hexpand(True)
        self._vol_slider.connect("value-changed", self._on_volume_changed)
        self._vol_pct_label = Gtk.Label(label="100%")
        self._vol_pct_label.set_width_chars(4)
        vol_box.append(vol_lbl)
        vol_box.append(self._vol_slider)
        vol_box.append(self._vol_pct_label)
        box.append(vol_box)

        # Boutons Play / Stop
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        self._panel_play_btn = Gtk.Button(label="▶ Lire")
        self._panel_play_btn.set_sensitive(False)
        self._panel_play_btn.connect("clicked", self._on_panel_play)

        self._panel_stop_btn = Gtk.Button(label="⏹ Stop")
        self._panel_stop_btn.set_sensitive(False)
        self._panel_stop_btn.connect("clicked", self._on_stop)

        btn_box.append(self._panel_play_btn)
        btn_box.append(self._panel_stop_btn)
        box.append(btn_box)

        # Bouton Favori
        self._panel_fav_btn = Gtk.ToggleButton(label="★ Ajouter aux favoris")
        self._panel_fav_btn.set_halign(Gtk.Align.CENTER)
        self._panel_fav_btn.set_sensitive(False)
        self._panel_fav_btn.connect("toggled", self._on_panel_fav_toggled)
        box.append(self._panel_fav_btn)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)

        # Statut
        self._panel_status = Gtk.Label(label="Prêt.")
        self._panel_status.set_wrap(True)
        self._panel_status.set_justify(Gtk.Justification.CENTER)
        self._panel_status.set_halign(Gtk.Align.CENTER)
        ctx3 = self._panel_status.get_style_context()
        ctx3.add_class("dim-label")
        box.append(self._panel_status)

        return box

    # ─────────────────────────────
    # statusbar (bas)
    # ─────────────────────────────

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

        self._status_label = Gtk.Label(label="")
        self._status_label.set_xalign(1)

        bar.append(self._now_playing_label)
        bar.append(self._status_label)
        return bar

    # ─────────────────────────────
    # tabs
    # ─────────────────────────────

    def _on_tab_changed(self, notebook, page, page_num):
        if page_num == 0:
            self._refresh_favorites_list()

    # ─────────────────────────────
    # theme selection
    # ─────────────────────────────

    def _on_theme_toggled(self, btn, theme_id):
        if btn.get_active():
            self._selected_themes.add(theme_id)
        else:
            self._selected_themes.discard(theme_id)
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
        threading.Thread(target=self._fetch_stations, args=(theme_ids,), daemon=True).start()

    def _fetch_stations(self, theme_ids: list[str]):
        stations = []
        stations += curated.get_stations_for_themes(theme_ids)
        try:
            stations += somafm.get_stations_for_themes(theme_ids, THEME_BY_ID)
        except Exception:
            pass
        try:
            stations += radiobrowser.get_stations_for_themes(theme_ids, THEME_BY_ID)
        except Exception:
            pass

        seen = set()
        unique = []
        for s in stations:
            sid = s.get("id", "")
            if sid and sid not in seen:
                seen.add(sid)
                unique.append(s)

        GLib.idle_add(self._on_stations_loaded, unique)

    def _on_stations_loaded(self, stations: list[dict]):
        self._stations = stations
        self._spinner.stop()
        self._loading = False
        self._populate_station_list(stations, self._station_list)
        self.btn_random.set_sensitive(bool(stations))
        self._stack.set_visible_child_name("stations")
        self._set_status(f"{len(stations)} station(s) trouvée(s).")
        return False

    # ─────────────────────────────
    # station list (shared)
    # ─────────────────────────────

    def _populate_station_list(self, stations: list[dict], listbox: Gtk.ListBox):
        while child := listbox.get_first_child():
            listbox.remove(child)

        if not stations:
            lbl = Gtk.Label(label="Aucune station.")
            lbl.set_margin_top(24)
            listbox.append(lbl)
            return

        for station in stations:
            row = self._build_station_row(station)
            listbox.append(row)

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
        desc_lbl.get_style_context().add_class("dim-label")

        info.append(name_lbl)
        info.append(desc_lbl)

        play_btn = Gtk.Button(label="▶")
        play_btn.connect("clicked", self._on_play_station, station)

        row.append(info)
        row.append(play_btn)
        return row

    # ─────────────────────────────
    # favorites tab
    # ─────────────────────────────

    def _refresh_favorites_list(self):
        self._populate_station_list(self.store.all(), self._fav_list)

    # ─────────────────────────────
    # search tab
    # ─────────────────────────────

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if not query:
            while child := self._search_list.get_first_child():
                self._search_list.remove(child)

    def _on_search(self, entry):
        query = entry.get_text().strip()
        if not query:
            return
        while child := self._search_list.get_first_child():
            self._search_list.remove(child)
        lbl = Gtk.Label(label="Recherche en cours...")
        self._search_list.append(lbl)
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query: str):
        import urllib.parse
        from bbs_radioo.sources.radiobrowser import _get
        results = _get("/stations/byname/" + urllib.parse.quote(query), {
            "hidebroken": "true",
            "order": "votes",
            "reverse": "true",
            "limit": "50",
        })
        stations = []
        seen = set()
        for s in results:
            uid = s.get("stationuuid", "")
            url = s.get("url_resolved") or s.get("url", "")
            if not url or uid in seen:
                continue
            seen.add(uid)
            stations.append({
                "id": f"rb-{uid}",
                "name": s.get("name", "").strip(),
                "stream_url": url,
                "homepage": s.get("homepage", ""),
                "description": s.get("tags", ""),
                "tags": [t.strip() for t in s.get("tags", "").split(",") if t.strip()],
                "favicon": s.get("favicon", ""),
                "source": "radiobrowser",
            })
        GLib.idle_add(self._on_search_results, stations)

    def _on_search_results(self, stations: list[dict]):
        self._populate_station_list(stations, self._search_list)
        return False

    # ─────────────────────────────
    # playback
    # ─────────────────────────────

    def _on_play_station(self, _btn, station: dict):
        self._current_station = station
        self._update_right_panel(station)
        self.player.play(station)

    def _on_panel_play(self, _btn):
        if self._current_station:
            self.player.play(self._current_station)

    def _on_stop(self, _btn):
        self.player.stop()

    def _on_random(self, _btn):
        if not self._stations:
            return
        station = random.choice(self._stations)
        log_event(f"Random pick: {station.get('name')}")
        self._current_station = station
        self._update_right_panel(station)
        self.player.play(station)

    def _on_volume_changed(self, scale):
        volume = int(scale.get_value())
        self._vol_pct_label.set_text(f"{volume}%")
        self.player.set_volume(volume)

    # ─────────────────────────────
    # right panel update
    # ─────────────────────────────

    def _update_right_panel(self, station: dict):
        self._panel_station_name.set_text(station.get("name", ""))
        self._panel_track.set_text("")
        self._panel_play_btn.set_sensitive(True)
        self._panel_stop_btn.set_sensitive(True)
        self._panel_fav_btn.set_sensitive(True)
        self._panel_fav_btn.handler_block_by_func(self._on_panel_fav_toggled)
        self._panel_fav_btn.set_active(self.store.is_favorite(station.get("id", "")))
        self._panel_fav_btn.handler_unblock_by_func(self._on_panel_fav_toggled)
        # Charger l'image en arrière-plan
        favicon_url = station.get("favicon", "")
        if favicon_url:
            threading.Thread(target=self._load_station_image, args=(favicon_url,), daemon=True).start()
        else:
            self._station_image.set_from_icon_name("audio-x-generic")

    def _load_station_image(self, url: str):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BBS-radiOO/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            fd, tmp = tempfile.mkstemp(suffix=".img")
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(data)
            GLib.idle_add(self._set_station_image, tmp)
        except Exception:
            GLib.idle_add(self._station_image.set_from_icon_name, "audio-x-generic")

    def _set_station_image(self, path: str):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 96, 96, True)
            self._station_image.set_from_pixbuf(pixbuf)
        except Exception:
            self._station_image.set_from_icon_name("audio-x-generic")
        try:
            os.remove(path)
        except OSError:
            pass
        return False

    # ─────────────────────────────
    # station callbacks
    # ─────────────────────────────

    def _on_station_change(self, station: dict | None):
        if station:
            self._now_playing_label.set_text(f"▶ {station.get('name', '')}")
            self._panel_stop_btn.set_sensitive(True)
        else:
            self._now_playing_label.set_text("")
            self._panel_track.set_text("")
            self._panel_stop_btn.set_sensitive(False)

    def _on_metadata_change(self, title: str):
        self._panel_track.set_text(title)
        station_name = self._current_station.get("name", "") if self._current_station else ""
        if title:
            self._now_playing_label.set_text(f"▶ {station_name} — {title}")

    # ─────────────────────────────
    # favorites
    # ─────────────────────────────

    def _on_panel_fav_toggled(self, btn):
        if not self._current_station:
            return
        added = self.store.toggle(self._current_station)
        btn.set_active(added)
        btn.set_label("★ Retirer des favoris" if added else "★ Ajouter aux favoris")

    # ─────────────────────────────
    # misc
    # ─────────────────────────────

    def _set_status(self, text: str):
        self._status_label.set_text(text)
        self._panel_status.set_text(text)

    def _on_shutdown(self, _win):
        self.player.cleanup()
