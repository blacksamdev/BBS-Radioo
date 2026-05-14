"""Stations ajoutées manuellement par l'utilisateur via URL."""

import json
import os
import uuid

from gi.repository import GLib


_CUSTOM_FILE = os.path.join(
    GLib.get_user_data_dir(), "bbs-radioo", "custom_stations.json"
)


class CustomStore:

    def __init__(self):
        self.path = _CUSTOM_FILE
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded
        except Exception:
            self._data = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def add(self, name: str, stream_url: str, country: str = "") -> dict:
        """Crée et sauvegarde une station personnalisée. Retourne le dict station."""
        sid = f"custom-{uuid.uuid4().hex[:8]}"
        station = {
            "id": sid,
            "name": name.strip() or stream_url,
            "stream_url": stream_url.strip(),
            "favicon": "",
            "homepage": "",
            "description": "Station personnalisée",
            "tags": [],
            "bitrate": 0,
            "codec": "",
            "country": country.strip(),
            "source": "custom",
        }
        self._data[sid] = station
        self._save()
        return station

    def remove(self, station_id: str):
        if station_id in self._data:
            del self._data[station_id]
            self._save()

    def all(self) -> list[dict]:
        return list(self._data.values())

    def get(self, station_id: str) -> dict | None:
        return self._data.get(station_id)
