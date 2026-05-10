import os
import json
import socket as _socket
import threading
import time

from gi.repository import GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.updater import Updater

_MPV_IPC_SOCKET = "/tmp/bbs-radioo-mpv.sock"
_METADATA_POLL_INTERVAL = 5.0


class RadioPlayer:

    def __init__(self):
        self._process = None
        self._all_processes: list = []
        self._lock = threading.Lock()
        self._is_playing = False
        self._current_station: dict | None = None
        self._polling = False
        self._volume = 100

        self.on_status_change = None
        self.on_station_change = None
        self.on_metadata_change = None

    # ─────────────────────────────
    # Public
    # ─────────────────────────────

    def play(self, station: dict):
        with self._lock:
            self._polling = False
            self._stop_current()
            self._is_playing = True
            self._current_station = station

        self._status(f"Connexion à {station.get('name', '')}...")
        log_event(f"Play: {station.get('name')} — {station.get('stream_url')}")
        threading.Thread(target=self._launch, args=(station,), daemon=True).start()

    def stop(self):
        self._polling = False
        with self._lock:
            self._stop_current()
            self._is_playing = False
            self._current_station = None
        self._status("Arrêté.")
        if self.on_station_change:
            GLib.idle_add(self.on_station_change, None)
        if self.on_metadata_change:
            GLib.idle_add(self.on_metadata_change, "")

    def set_volume(self, volume: int):
        self._volume = max(0, min(100, volume))
        self._ipc_set_property("volume", self._volume)

    def is_playing(self) -> bool:
        return self._is_playing

    def current_station(self) -> dict | None:
        return self._current_station

    def cleanup(self):
        """Arrêt complet : IPC → terminate wrapper → pkill host → socket."""
        log_event("Player cleanup…")
        self._polling = False

        # 1. Demander à MPV de quitter proprement via IPC
        self._ipc_command("quit")
        time.sleep(0.4)

        # 2. Terminer tous les wrappers flatpak-spawn trackés
        with self._lock:
            for proc in list(self._all_processes):
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            self._all_processes.clear()
            self._process = None

        # 3. pkill côté host — cible le socket IPC (très spécifique)
        self._pkill_host()

        # 4. Nettoyer le socket
        self._remove_socket()

    # ─────────────────────────────
    # IPC
    # ─────────────────────────────

    def _ipc_command(self, *args):
        try:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(_MPV_IPC_SOCKET)
            msg = json.dumps({"command": list(args)}).encode() + b"\n"
            sock.sendall(msg)
            sock.close()
        except Exception:
            pass

    def _ipc_set_property(self, prop: str, value):
        try:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(_MPV_IPC_SOCKET)
            msg = json.dumps({"command": ["set_property", prop, value]}).encode() + b"\n"
            sock.sendall(msg)
            sock.close()
        except Exception:
            pass

    def _ipc_get_property(self, prop: str):
        try:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(_MPV_IPC_SOCKET)
            msg = json.dumps({"command": ["get_property", prop]}).encode() + b"\n"
            sock.sendall(msg)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                buf += chunk
            sock.close()
            resp = json.loads(buf.split(b"\n")[0])
            if resp.get("error") == "success":
                return resp.get("data")
        except Exception:
            pass
        return None

    def _poll_metadata(self):
        self._polling = True
        last_title = ""
        while self._polling and self._is_playing:
            title = self._ipc_get_property("media-title") or ""
            if isinstance(title, str) and title != last_title:
                last_title = title
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, title)
            time.sleep(_METADATA_POLL_INTERVAL)

    # ─────────────────────────────
    # Internal
    # ─────────────────────────────

    def _stop_current(self):
        """Arrête le process courant : IPC → terminate → pkill."""
        self._ipc_command("quit")
        time.sleep(0.3)

        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

        self._pkill_host()
        self._remove_socket()
        self._process = None

    def _pkill_host(self):
        """Tue le MPV côté host en ciblant le socket IPC (spécifique à notre instance)."""
        try:
            Updater.run_host(
                ["pkill", "-f", _MPV_IPC_SOCKET],
                quiet=True,
            )
        except Exception:
            pass
        # Fallback sur le titre de fenêtre
        try:
            Updater.run_host(
                ["pkill", "-f", "BBS radiOO"],
                quiet=True,
            )
        except Exception:
            pass

    def _remove_socket(self):
        try:
            os.remove(_MPV_IPC_SOCKET)
        except OSError:
            pass

    def _launch(self, station: dict):
        try:
            proc = Updater.play_stream(
                station["stream_url"],
                ipc_socket_path=_MPV_IPC_SOCKET,
                volume=self._volume,
            )
            self._process = proc
            with self._lock:
                self._all_processes.append(proc)

            # Attendre que MPV crée le socket IPC (max 8 s)
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self._status("Impossible de se connecter au stream.")
                    log_event(f"MPV exited early for {station.get('name')}")
                    with self._lock:
                        self._is_playing = False
                    return
                if os.path.exists(_MPV_IPC_SOCKET):
                    break
                time.sleep(0.1)

            self._status(f"En écoute : {station.get('name', '')}")
            if self.on_station_change:
                GLib.idle_add(self.on_station_change, station)

            threading.Thread(target=self._poll_metadata, daemon=True).start()

            proc.wait()
            self._polling = False
            with self._lock:
                self._is_playing = False
                if proc in self._all_processes:
                    self._all_processes.remove(proc)

            if (
                self._current_station
                and self._current_station.get("id") == station.get("id")
            ):
                self._status("Stream terminé.")
                if self.on_station_change:
                    GLib.idle_add(self.on_station_change, None)
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, "")

        except Exception as exc:
            log_event(f"Player error: {exc}")
            self._status("Erreur de lecture.")
            self._polling = False
            with self._lock:
                self._is_playing = False

    def _status(self, text: str):
        log_event(text)
        if self.on_status_change:
            GLib.idle_add(self.on_status_change, text)
