"""Stockage des stations favorites."""

import json
import os

from gi.repository import GLib


_FAVORITES_FILE = os.path.join(GLib.get_user_data_dir(), "bbs-radioo", "favorites.json")


class StationStore:

    def __init__(self):
        self.path = _FAVORITES_FILE
        self._favorites: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._favorites = loaded
        except Exception:
            self._favorites = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._favorites, f, indent=2)
        except Exception:
            pass

    def add(self, station: dict):
        sid = station.get("id", "")
        if sid:
            self._favorites[sid] = station
            self._save()

    def remove(self, station_id: str):
        if station_id in self._favorites:
            del self._favorites[station_id]
            self._save()

    def is_favorite(self, station_id: str) -> bool:
        return station_id in self._favorites

    def all(self) -> list[dict]:
        return list(self._favorites.values())

    def toggle(self, station: dict) -> bool:
        """Ajoute ou supprime des favoris. Retourne True si ajouté."""
        sid = station.get("id", "")
        if sid in self._favorites:
            self.remove(sid)
            return False
        self.add(station)
        return True
