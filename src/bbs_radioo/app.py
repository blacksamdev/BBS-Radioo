import json
import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, WebKit, GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.player import RadioPlayer
from bbs_radioo.station_store import StationStore
from bbs_radioo.theme_map import THEME_BY_ID
from bbs_radioo.sources import curated, somafm, radiobrowser


_UI_HTML = os.path.join(os.path.dirname(__file__), "ui", "index.html")
_SETTINGS_FILE = os.path.join(GLib.get_user_config_dir(), "bbs-radioo", "settings.json")

_SECTION_PARAMS = {
    "discover": {"hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": "80"},
    "trending": {"hidebroken": "true", "order": "clicktrend", "reverse": "true", "limit": "80"},
    "popular":  {"hidebroken": "true", "order": "votes",      "reverse": "true", "limit": "80"},
}


# ─────────────────────────────
# Settings
# ─────────────────────────────

def _load_settings() -> dict:
    defaults = {"volume": 100}
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            s = defaults.copy()
            s.update(loaded)
            s["volume"] = max(0, min(100, int(s.get("volume", 100))))
            return s
    except Exception:
        pass
    return defaults


def _save_settings(s: dict):
    try:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


# ─────────────────────────────
# App
# ─────────────────────────────

class RadiooApp(Gtk.Application):

    def __init__(self, state_dir: str):
        super().__init__(application_id="io.github.blacksamdev.Radioo")
        self.connect("activate", self.on_activate)
        self.state_dir = state_dir
        self.settings = _load_settings()
        self.player = RadioPlayer()
        self.store = StationStore()
        self._window_created = False

    # ─────────────────────────────
    # Activation
    # ─────────────────────────────

    def on_activate(self, app):
        if self._window_created:
            self.win.present()
            return
        self._window_created = True

        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_title("BBS radiOO")
        self.win.set_default_size(1160, 700)
        self.win.connect("destroy", self._on_shutdown)

        # ── WebKit bridge ──
        self.cm = WebKit.UserContentManager()
        self.cm.register_script_message_handler("bbsradioo")
        self.cm.connect("script-message-received::bbsradioo", self._on_js_message)

        # ── WebView ──
        self.webview = WebKit.WebView(user_content_manager=self.cm)
        ws = self.webview.get_settings()
        ws.set_enable_javascript(True)
        ws.set_enable_html5_local_storage(True)
        ws.set_enable_media(False)
        ws.set_media_playback_requires_user_gesture(True)
        try:
            ws.set_enable_webgl(False)
        except Exception:
            pass

        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.load_uri(f"file://{_UI_HTML}")

        self.win.set_child(self.webview)
        self.win.present()

        # ── Player callbacks ──
        self.player.on_status_change   = self._cb_status
        self.player.on_station_change  = self._cb_station
        self.player.on_metadata_change = self._cb_metadata

        self.player.set_volume(self.settings.get("volume", 100))

    # ─────────────────────────────
    # WebKit events
    # ─────────────────────────────

    def _on_load_changed(self, webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        vol = self.settings.get("volume", 100)
        self._js(f"document.getElementById('vol').value={vol}; setVol({vol});")
        self._push_favorites()

    # ─────────────────────────────
    # Bridge : JS → Python
    # ─────────────────────────────

    def _on_js_message(self, _manager, message):
        try:
            msg = json.loads(message.to_string())
        except Exception:
            log_event(f"JS message parse error: {message.to_string()}", level="debug")
            return

        action = msg.get("action", "")
        log_event(f"JS→Py: {action}", level="debug")

        if action == "play":
            self._do_play(msg.get("station"))

        elif action == "stop":
            self.player.stop()

        elif action == "set_volume":
            vol = max(0, min(100, int(msg.get("volume", 100))))
            self.player.set_volume(vol)
            self.settings["volume"] = vol
            _save_settings(self.settings)

        elif action == "toggle_favorite":
            station = msg.get("station")
            if station:
                self.store.toggle(station)
                self._push_favorites()

        elif action == "load_genre":
            genre = msg.get("genre", "")
            if genre:
                threading.Thread(
                    target=self._fetch_genre,
                    args=(genre,),
                    daemon=True,
                ).start()

        elif action == "load_section":
            section = msg.get("section", "")
            if section:
                threading.Thread(
                    target=self._fetch_section,
                    args=(section,),
                    daemon=True,
                ).start()

        else:
            log_event(f"Action inconnue: {action}", level="debug")

    # ─────────────────────────────
    # Station fetchers (threads)
    # ─────────────────────────────

    def _do_play(self, station: dict):
        if not station or not station.get("stream_url"):
            log_event(f"Pas de stream_url pour {station.get('name', '?') if station else '?'}", level="debug")
            return
        self.player.play(station)

    def _fetch_genre(self, genre: str):
        """Fusionne curated + SomaFM + RadioBrowser pour un genre donné."""
        try:
            theme_ids = [genre]

            # 1. Curated en premier — sources fiables, pas de pub
            results = curated.get_stations_for_themes(theme_ids)
            seen_ids = {s["id"] for s in results}

            # 2. SomaFM
            for s in somafm.get_stations_for_themes(theme_ids, THEME_BY_ID):
                if s["id"] not in seen_ids:
                    seen_ids.add(s["id"])
                    results.append(s)

            # 3. RadioBrowser
            for s in radiobrowser.get_stations_for_themes(theme_ids, THEME_BY_ID):
                if s["id"] not in seen_ids:
                    seen_ids.add(s["id"])
                    results.append(s)

        except Exception as exc:
            log_event(f"Erreur fetch genre {genre}: {exc}")
            results = []

        GLib.idle_add(self._push_stations, results)

    def _fetch_section(self, section: str):
        """Sections Découvrir / Tendances / Populaires — RadioBrowser uniquement."""
        params = _SECTION_PARAMS.get(section, _SECTION_PARAMS["discover"])
        try:
            raw = radiobrowser._get("/stations", params)
        except Exception as exc:
            log_event(f"Erreur fetch section {section}: {exc}")
            raw = []

        seen, stations = set(), []
        for s in raw:
            uid = s.get("stationuuid", "")
            if uid in seen:
                continue
            seen.add(uid)
            d = radiobrowser._station_to_dict(s)
            if d:
                stations.append(d)

        GLib.idle_add(self._push_stations, stations)

    # ─────────────────────────────
    # Bridge : Python → JS
    # ─────────────────────────────

    def _js(self, script: str):
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)

    def _push_stations(self, stations: list):
        payload = json.dumps(stations, ensure_ascii=False)
        self._js(f"window.onStationsLoaded({payload})")
        return False  # stoppe GLib.idle_add

    def _push_favorites(self):
        favs = self.store.all()
        payload = json.dumps(favs, ensure_ascii=False)
        self._js(f"window.onFavoritesLoaded({payload})")

    # ─────────────────────────────
    # Player callbacks → JS
    # ─────────────────────────────

    def _cb_status(self, text: str):
        self._js(f"window.onStatusChange({json.dumps(text)})")

    def _cb_station(self, station):
        self._js(f"window.onStationChange({json.dumps(station, ensure_ascii=False)})")

    def _cb_metadata(self, title: str):
        self._js(f"window.onMetadata({json.dumps(title)})")

    # ─────────────────────────────
    # Shutdown
    # ─────────────────────────────

    def _on_shutdown(self, _win):
        self.player.cleanup()
        self.quit()
