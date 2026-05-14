"""Stations masquées — exclues de toutes les listes sauf la section Masquées."""

import json
import os

from gi.repository import GLib


_BLACKLIST_FILE = os.path.join(
    GLib.get_user_data_dir(), "bbs-radioo", "blacklist.json"
)


class BlacklistStore:

    def __init__(self):
        self.path = _BLACKLIST_FILE
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

    def add(self, station: dict):
        sid = station.get("id", "")
        if sid:
            self._data[sid] = station
            self._save()

    def remove(self, station_id: str):
        if station_id in self._data:
            del self._data[station_id]
            self._save()

    def is_blacklisted(self, station_id: str) -> bool:
        return station_id in self._data

    def all(self) -> list[dict]:
        return list(self._data.values())

    def toggle(self, station: dict) -> bool:
        """Ajoute ou supprime de la liste noire. Retourne True si ajouté."""
        sid = station.get("id", "")
        if sid in self._data:
            self.remove(sid)
            return False
        self.add(station)
        return True

    def filter(self, stations: list[dict]) -> list[dict]:
        """Filtre une liste de stations en excluant les masquées."""
        return [s for s in stations if not self.is_blacklisted(s.get("id", ""))]
