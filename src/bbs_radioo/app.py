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
from bbs_radioo.blacklist_store import BlacklistStore
from bbs_radioo.custom_store import CustomStore
from bbs_radioo.theme_map import THEME_BY_ID
from bbs_radioo.sources import curated, somafm, radiobrowser


_UI_HTML = os.path.join(os.path.dirname(__file__), "ui", "index.html")
_SETTINGS_FILE = os.path.join(GLib.get_user_config_dir(), "bbs-radioo", "settings.json")

_WEBKIT_SETTINGS = [
    ("set_enable_javascript",                    True),
    ("set_enable_html5_local_storage",           True),
    ("set_enable_media",                         False),
    ("set_media_playback_requires_user_gesture", True),
    ("set_enable_webgl",                         False),
]


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


class RadiooApp(Gtk.Application):

    def __init__(self, state_dir: str):
        super().__init__(application_id="io.github.blacksamdev.Radioo")
        self.connect("activate", self.on_activate)
        self.state_dir  = state_dir
        self.settings   = _load_settings()
        self.player     = RadioPlayer()
        self.store      = StationStore()
        self.blacklist  = BlacklistStore()
        self.custom     = CustomStore()
        self._window_created = False
        self._shutting_down  = False

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
        log_event("Fenêtre créée.")

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

    def _on_load_changed(self, webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        log_event("Page chargée.")
        vol = self.settings.get("volume", 100)
        self._js(f"document.getElementById('vol').value={vol}; setVol({vol});")
        self._push_favorites_tree()
        self._push_blacklist()
        self._push_custom()
        threading.Thread(target=self._fetch_section, args=("trending",), daemon=True).start()

    # ─────────────────────────────
    # Bridge JS → Python
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

        elif action == "add_to_folder":
            station   = msg.get("station")
            folder_id = msg.get("folder_id")
            if station:
                self.store.add_to_folder(station, folder_id)
                self._push_favorites_tree()

        elif action == "remove_favorite":
            sid = msg.get("station_id", "")
            if sid:
                self.store.remove(sid)
                self._push_favorites_tree()

        elif action == "create_folder":
            name = msg.get("name", "Nouveau dossier")
            self.store.create_folder(name)
            self._push_favorites_tree()

        elif action == "rename_folder":
            self.store.rename_folder(msg.get("folder_id", ""), msg.get("name", ""))
            self._push_favorites_tree()

        elif action == "delete_folder":
            self.store.delete_folder(
                msg.get("folder_id", ""),
                keep_stations=msg.get("keep_stations", True)
            )
            self._push_favorites_tree()

        elif action == "move_station":
            self.store.move_station(
                msg.get("station_id", ""),
                msg.get("from_folder_id"),
                msg.get("to_folder_id"),
            )
            self._push_favorites_tree()

        elif action == "toggle_blacklist":
            station = msg.get("station")
            if station:
                added = self.blacklist.toggle(station)
                self._push_blacklist()
                if added:
                    cur = self.player.current_station()
                    if cur and cur.get("id") == station.get("id"):
                        self.player.stop()

        elif action == "add_custom":
            url  = msg.get("stream_url", "")
            name = msg.get("name", "")
            country = msg.get("country", "")
            if url:
                self.custom.add(name, url, country)
                self._push_custom()

        elif action == "remove_custom":
            self.custom.remove(msg.get("station_id", ""))
            self._push_custom()

        elif action == "load_section":
            section = msg.get("section", "")
            if section:
                threading.Thread(
                    target=self._fetch_section, args=(section,), daemon=True
                ).start()

        elif action == "search":
            query = msg.get("query", "").strip()
            tag   = msg.get("tag",   "").strip()
            if query:
                threading.Thread(target=self._fetch_search, args=(query,), daemon=True).start()
            elif tag:
                threading.Thread(target=self._fetch_tag, args=(tag,), daemon=True).start()

        else:
            log_event(f"Action inconnue: {action}", level="debug")

    # ─────────────────────────────
    # Fetchers
    # ─────────────────────────────

    def _do_play(self, station: dict):
        if not station or not station.get("stream_url"):
            return
        if self.blacklist.is_blacklisted(station.get("id", "")):
            return
        self.player.play(station)

    def _filter(self, stations: list[dict]) -> list[dict]:
        return self.blacklist.filter(stations)

    def _fetch_section(self, section: str):

        if section == "trending":
            try:
                stations = radiobrowser.get_trending()
                log_event(f"Trending: {len(stations)} stations")
            except Exception as exc:
                log_event(f"Erreur trending: {exc}")
                stations = []
            GLib.idle_add(self._push_stations, self._filter(stations))
            return

        if section == "popular":
            try:
                stations = radiobrowser.get_popular()
                log_event(f"Popular: {len(stations)} stations")
            except Exception as exc:
                log_event(f"Erreur popular: {exc}")
                stations = []
            GLib.idle_add(self._push_stations, self._filter(stations))
            return

        if section == "adfree":
            results = []
            try:
                results = curated.get_stations_for_themes([])
            except Exception as exc:
                log_event(f"Erreur curated: {exc}")
            seen = {s["id"] for s in results}
            try:
                for s in somafm.get_stations_for_themes([], THEME_BY_ID):
                    if s["id"] not in seen:
                        seen.add(s["id"])
                        results.append(s)
                log_event(f"Adfree: {len(results)} stations")
            except Exception as exc:
                log_event(f"Erreur somafm: {exc}")
            GLib.idle_add(self._push_stations, self._filter(results))
            return

        if section == "custom":
            GLib.idle_add(self._push_stations, self.custom.all())
            return

        if section == "blacklisted":
            GLib.idle_add(self._push_stations, self.blacklist.all())
            return

    def _fetch_search(self, query: str):
        try:
            results = radiobrowser.search_by_name(query)
            log_event(f"Search '{query}': {len(results)} résultats")
        except Exception as exc:
            log_event(f"Erreur search: {exc}")
            results = []
        GLib.idle_add(self._push_stations, self._filter(results))

    def _fetch_tag(self, tag: str):
        try:
            results = radiobrowser.search_by_tag(tag)
            log_event(f"Tag '{tag}': {len(results)} stations")
        except Exception as exc:
            log_event(f"Erreur tag: {exc}")
            results = []
        GLib.idle_add(self._push_stations, self._filter(results))

    # ─────────────────────────────
    # Bridge Python → JS
    # ─────────────────────────────

    def _js(self, script: str):
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)

    def _push_stations(self, stations: list):
        payload = json.dumps(stations, ensure_ascii=False)
        self._js(f"window.onStationsLoaded({payload})")
        return False

    def _push_favorites_tree(self):
        tree    = self.store.get_tree()
        payload = json.dumps(tree, ensure_ascii=False)
        self._js(f"window.onFavoritesTree({payload})")

    def _push_blacklist(self):
        bl = self.blacklist.all()
        self._js(f"window.onBlacklistLoaded({json.dumps(bl, ensure_ascii=False)})")

    def _push_custom(self):
        c = self.custom.all()
        self._js(f"window.onCustomLoaded({json.dumps(c, ensure_ascii=False)})")

    def _cb_status(self, text: str):
        self._js(f"window.onStatusChange({json.dumps(text)})")

    def _cb_station(self, station):
        self._js(f"window.onStationChange({json.dumps(station, ensure_ascii=False)})")

    def _cb_metadata(self, title: str):
        self._js(f"window.onMetadata({json.dumps(title)})")
