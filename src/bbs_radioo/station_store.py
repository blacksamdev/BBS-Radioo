"""
Gestion des favoris avec arborescence de dossiers.

Structure JSON persistée :
{
  "version": 2,
  "folders": [
    {"id": "f-abc123", "name": "Jazz", "stations": ["rb-1", "rb-2"]}
  ],
  "stations": {
    "rb-1": { ... dict station complet ... }
  },
  "unfiled": ["rb-3"]   // favoris pas dans un dossier
}
"""

import json
import os
import uuid

from gi.repository import GLib


_STORE_FILE = os.path.join(
    GLib.get_user_data_dir(), "bbs-radioo", "favorites.json"
)


class StationStore:

    def __init__(self):
        self.path = _STORE_FILE
        self._stations: dict[str, dict] = {}   # id → station dict
        self._folders: list[dict]         = []  # [{id, name, stations:[id...]}]
        self._unfiled: list[str]          = []  # ids sans dossier
        self._load()

    # ─────────────────────────────
    # Persistance
    # ─────────────────────────────

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return

            # Migration v1 (liste plate) → v2 (dossiers)
            if data.get("version") != 2:
                old = data if isinstance(data, dict) and "stations" not in data else {}
                # Ancien format : dict {id: station_dict}
                for sid, st in old.items():
                    if isinstance(st, dict) and st.get("id"):
                        self._stations[sid] = st
                        self._unfiled.append(sid)
                self._save()
                return

            self._stations = data.get("stations", {})
            self._folders  = data.get("folders",  [])
            self._unfiled  = data.get("unfiled",  [])

        except Exception:
            self._stations = {}
            self._folders  = []
            self._unfiled  = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "version":  2,
                    "stations": self._stations,
                    "folders":  self._folders,
                    "unfiled":  self._unfiled,
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ─────────────────────────────
    # Requêtes
    # ─────────────────────────────

    def is_favorite(self, station_id: str) -> bool:
        return station_id in self._stations

    def has_folders(self) -> bool:
        return bool(self._folders)

    def get_folders(self) -> list[dict]:
        """Retourne les dossiers avec leur nombre de stations."""
        return [
            {**f, "count": len(f.get("stations", []))}
            for f in self._folders
        ]

    def get_folder(self, folder_id: str) -> dict | None:
        return next((f for f in self._folders if f["id"] == folder_id), None)

    def get_folder_stations(self, folder_id: str) -> list[dict]:
        folder = self.get_folder(folder_id)
        if not folder:
            return []
        return [self._stations[sid] for sid in folder["stations"] if sid in self._stations]

    def get_unfiled_stations(self) -> list[dict]:
        return [self._stations[sid] for sid in self._unfiled if sid in self._stations]

    def all(self) -> list[dict]:
        """Toutes les stations favorites (tous dossiers + sans dossier)."""
        return list(self._stations.values())

    def get_tree(self) -> dict:
        """
        Retourne l'arborescence complète pour le JS :
        {
          folders: [{id, name, count, stations:[...]}],
          unfiled: [...stations...]
        }
        """
        return {
            "folders": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "stations": self.get_folder_stations(f["id"]),
                }
                for f in self._folders
            ],
            "unfiled": self.get_unfiled_stations(),
        }

    # ─────────────────────────────
    # Favoris — ajout / suppression
    # ─────────────────────────────

    def add_to_folder(self, station: dict, folder_id: str | None = None) -> bool:
        """
        Ajoute la station aux favoris dans le dossier indiqué,
        ou dans "sans dossier" si folder_id est None.
        Retourne True si ajouté, False si déjà présent dans ce dossier.
        """
        sid = station.get("id", "")
        if not sid:
            return False

        # Sauvegarder le dict complet
        self._stations[sid] = station

        if folder_id is None:
            if sid not in self._unfiled:
                self._unfiled.append(sid)
        else:
            folder = self.get_folder(folder_id)
            if folder and sid not in folder["stations"]:
                folder["stations"].append(sid)

        self._save()
        return True

    def remove(self, station_id: str):
        """Supprime la station de TOUS les dossiers et des favoris."""
        self._stations.pop(station_id, None)
        self._unfiled = [s for s in self._unfiled if s != station_id]
        for f in self._folders:
            f["stations"] = [s for s in f["stations"] if s != station_id]
        self._save()

    def toggle_unfiled(self, station: dict) -> bool:
        """Toggle dans 'sans dossier'. Retourne True si ajouté."""
        sid = station.get("id", "")
        if self.is_favorite(sid):
            self.remove(sid)
            return False
        self.add_to_folder(station, None)
        return True

    # ─────────────────────────────
    # Dossiers — CRUD
    # ─────────────────────────────

    def create_folder(self, name: str) -> dict:
        """Crée un nouveau dossier. Retourne le dict dossier."""
        folder = {
            "id": f"f-{uuid.uuid4().hex[:8]}",
            "name": name.strip() or "Nouveau dossier",
            "stations": [],
        }
        self._folders.append(folder)
        self._save()
        return folder

    def rename_folder(self, folder_id: str, name: str) -> bool:
        folder = self.get_folder(folder_id)
        if not folder:
            return False
        folder["name"] = name.strip() or folder["name"]
        self._save()
        return True

    def delete_folder(self, folder_id: str, keep_stations: bool = True):
        """
        Supprime le dossier.
        Si keep_stations=True, les stations vont dans "sans dossier".
        Si keep_stations=False, les stations sont supprimées des favoris.
        """
        folder = self.get_folder(folder_id)
        if not folder:
            return

        if keep_stations:
            for sid in folder["stations"]:
                if sid not in self._unfiled:
                    self._unfiled.append(sid)
        else:
            for sid in folder["stations"]:
                self._stations.pop(sid, None)
                self._unfiled = [s for s in self._unfiled if s != sid]

        self._folders = [f for f in self._folders if f["id"] != folder_id]
        self._save()

    def move_station(
        self,
        station_id: str,
        from_folder_id: str | None,
        to_folder_id: str | None,
    ) -> bool:
        """
        Déplace une station d'un dossier à un autre.
        None = "sans dossier".
        """
        if station_id not in self._stations:
            return False

        # Retirer de la source
        if from_folder_id is None:
            self._unfiled = [s for s in self._unfiled if s != station_id]
        else:
            folder = self.get_folder(from_folder_id)
            if folder:
                folder["stations"] = [s for s in folder["stations"] if s != station_id]

        # Ajouter à la destination
        if to_folder_id is None:
            if station_id not in self._unfiled:
                self._unfiled.append(station_id)
        else:
            folder = self.get_folder(to_folder_id)
            if folder and station_id not in folder["stations"]:
                folder["stations"].append(station_id)

        self._save()
        return True
