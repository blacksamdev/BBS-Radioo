import json
import os
import threading
import traceback

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
    "trending": {"hidebroken": "true", "order": "clicktrend", "reverse": "true", "limit": "80"},
    "popular":  {"hidebroken": "true", "order": "votes",      "reverse": "true", "limit": "80"},
}

_WEBKIT_SETTINGS = [
    ("set_enable_javascript",                    True),
    ("set_enable_html5_local_storage",           True),
    ("set_enable_media",                         False),
    ("set_media_playback_requires_user_gesture", True),
    ("set_enable_webgl",                         False),
]


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
        self._shutting_down = False

    def on_activate(self, app):
        try:
            self._build_window(app)
        except Exception:
            err = traceback.format_exc()
            log_event(f"ERREUR on_activate:\n{err}")
            print(f"ERREUR on_activate:\n{err}")
            self.quit()

    def _build_window(self, app):
        if self._window_created:
            self.win.present()
            return
        self._window_created = True

        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_title("BBS radiOO")
        self.win.set_default_size(1160, 700)
        self.win.connect("close-request", self._on_close_request)

        self.cm = WebKit.UserContentManager()
        try:
            self.cm.register_script_message_handler("bbsradioo", None)
        except TypeError:
            self.cm.register_script_message_handler("bbsradioo")
        self.cm.connect("script-message-received::bbsradioo", self._on_js_message)

        self.webview = WebKit.WebView(user_content_manager=self.cm)
        ws = self.webview.get_settings()
        for method, value in _WEBKIT_SETTINGS:
            try:
                getattr(ws, method)(value)
            except Exception as e:
                log_event(f"WebKit setting {method}: {e}", level="debug")

        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.load_uri(f"file://{_UI_HTML}")

        self.win.set_child(self.webview)
        self.win.present()

        self.player.on_status_change   = self._cb_status
        self.player.on_station_change  = self._cb_station
        self.player.on_metadata_change = self._cb_metadata
        self.player.set_volume(self.settings.get("volume", 100))
        log_event("Fenêtre créée, WebView chargée.")

    # ─────────────────────────────
    # Shutdown
    # ─────────────────────────────

    def _on_close_request(self, _win) -> bool:
        if self._shutting_down:
            return False
        self._shutting_down = True
        log_event("Shutdown.")
        try:
            self.player.cleanup()
        except Exception as exc:
            log_event(f"Cleanup error: {exc}")
        os._exit(0)
        return True

    # ─────────────────────────────
    # WebKit events
    # ─────────────────────────────

    def _on_load_changed(self, webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        log_event("Page chargée.")
        vol = self.settings.get("volume", 100)
        self._js(f"document.getElementById('vol').value={vol}; setVol({vol});")
        self._push_favorites()
        # Section par défaut au démarrage
        threading.Thread(target=self._fetch_section, args=("trending",), daemon=True).start()

    # ─────────────────────────────
    # Bridge : JS → Python
    # ─────────────────────────────

    def _on_js_message(self, _manager, message):
        try:
            msg = json.loads(message.to_string())
        except Exception:
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
                threading.Thread(target=self._fetch_genre, args=(genre,), daemon=True).start()
        elif action == "load_section":
            section = msg.get("section", "")
            if section:
                threading.Thread(target=self._fetch_section, args=(section,), daemon=True).start()
        else:
            log_event(f"Action inconnue: {action}", level="debug")

    # ─────────────────────────────
    # Fetchers
    # ─────────────────────────────

    def _do_play(self, station: dict):
        if not station or not station.get("stream_url"):
            return
        self.player.play(station)

    def _fetch_genre(self, genre: str):
        try:
            theme_ids = [genre]
            results   = curated.get_stations_for_themes(theme_ids)
            seen_ids  = {s["id"] for s in results}
            for s in somafm.get_stations_for_themes(theme_ids, THEME_BY_ID):
                if s["id"] not in seen_ids:
                    seen_ids.add(s["id"]); results.append(s)
            for s in radiobrowser.get_stations_for_themes(theme_ids, THEME_BY_ID):
                if s["id"] not in seen_ids:
                    seen_ids.add(s["id"]); results.append(s)
        except Exception as exc:
            log_event(f"Erreur fetch genre {genre}: {exc}")
            results = []
        GLib.idle_add(self._push_stations, results)

    def _fetch_section(self, section: str):
        # Section "adfree" = curated + SomaFM uniquement, garanti sans pub
        if section == "adfree":
            try:
                results  = curated.get_stations_for_themes([])  # toutes les curated
                seen_ids = {s["id"] for s in results}
                # Toutes les stations SomaFM (tous genres)
                all_themes = list(THEME_BY_ID.keys())
                seen_soma: set[str] = set()
                for tid in all_themes:
                    for s in somafm.get_stations_for_themes([tid], THEME_BY_ID):
                        if s["id"] not in seen_ids and s["id"] not in seen_soma:
                            seen_soma.add(s["id"])
                            results.append(s)
            except Exception as exc:
                log_event(f"Erreur fetch adfree: {exc}")
                results = []
            GLib.idle_add(self._push_stations, results)
            return

        params = _SECTION_PARAMS.get(section, _SECTION_PARAMS["popular"])
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
        return False

    def _push_favorites(self):
        favs    = self.store.all()
        payload = json.dumps(favs, ensure_ascii=False)
        self._js(f"window.onFavoritesLoaded({payload})")

    def _cb_status(self, text: str):
        self._js(f"window.onStatusChange({json.dumps(text)})")

    def _cb_station(self, station):
        self._js(f"window.onStationChange({json.dumps(station, ensure_ascii=False)})")

    def _cb_metadata(self, title: str):
        self._js(f"window.onMetadata({json.dumps(title)})")
