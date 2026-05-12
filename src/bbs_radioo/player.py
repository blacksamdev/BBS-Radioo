import os
import json
import socket as _socket
import threading
import time

from gi.repository import GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.updater import Updater

# Socket dans XDG_RUNTIME_DIR (/run/user/1000) — partagé host/Flatpak en théorie.
# En pratique on utilise flatpak-spawn pour les commandes IPC depuis le host.
_MPV_IPC_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
    "bbs-radioo-mpv.sock"
)

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
        if self._is_playing:
            self._ipc_set_property("volume", self._volume)
            log_event(f"Volume → {self._volume}", level="debug")
        else:
            log_event(f"Volume sauvegardé → {self._volume}", level="debug")

    def is_playing(self) -> bool:
        return self._is_playing

    def current_station(self) -> dict | None:
        return self._current_station

    def cleanup(self):
        log_event("Player cleanup…")
        self._polling = False
        self._ipc_command("quit")
        time.sleep(0.4)
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
        self._pkill_host()
        self._remove_socket()

    # ─────────────────────────────
    # IPC — exécuté côté HOST via flatpak-spawn
    # Le socket MPV est sur le host. Depuis l'intérieur du Flatpak,
    # on ne peut pas s'y connecter directement (isolation /tmp ou runtime).
    # On passe par flatpak-spawn --host python3 -c "..." pour accéder
    # au socket depuis le même contexte que MPV.
    # ─────────────────────────────

    def _ipc_send_via_host(self, payload: str):
        """Envoie un message JSON IPC au socket MPV en passant par le host."""
        escaped = payload.replace("'", "\\'")
        script = (
            f"import socket,sys;"
            f"s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            f"s.settimeout(1.0);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            f"s.sendall(b'{escaped}\\n');"
            f"s.close()"
        )
        try:
            Updater.run_host(["python3", "-c", script], quiet=True)
        except Exception as e:
            log_event(f"IPC host send failed: {e}", level="debug")

    def _ipc_command(self, *args):
        payload = json.dumps({"command": list(args)})
        self._ipc_send_via_host(payload)

    def _ipc_set_property(self, prop: str, value):
        payload = json.dumps({"command": ["set_property", prop, value]})
        self._ipc_send_via_host(payload)

    def _ipc_get_property(self, prop: str):
        """Lit une propriété MPV via le host — retourne la valeur ou None."""
        script = (
            f"import socket,json,sys;"
            f"s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            f"s.settimeout(1.0);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            f"msg=json.dumps({{'command':['get_property','{prop}']}}).encode()+b'\\n';"
            f"s.sendall(msg);"
            f"buf=b'';"
            f"[buf:=buf+c for c in iter(lambda:s.recv(256),b'') if b'\\n' not in buf];"
            f"s.close();"
            f"resp=json.loads(buf.split(b'\\n')[0]);"
            f"print(resp.get('data','') if resp.get('error')=='success' else '',end='')"
        )
        try:
            import subprocess
            result = subprocess.run(
                ["flatpak-spawn", "--host", "python3", "-c", script],
                capture_output=True, text=True, timeout=3
            )
            val = result.stdout.strip()
            return val if val else None
        except Exception:
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
        self._ipc_command("quit")
        time.sleep(0.3)
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._pkill_host()
        self._remove_socket()
        self._process = None

    def _pkill_host(self):
        try:
            Updater.run_host(["pkill", "-f", _MPV_IPC_SOCKET], quiet=True)
        except Exception:
            pass
        try:
            Updater.run_host(["pkill", "-f", "BBS radiOO"], quiet=True)
        except Exception:
            pass

    def _remove_socket(self):
        try:
            # Supprimer le socket depuis le host
            Updater.run_host(["rm", "-f", _MPV_IPC_SOCKET], quiet=True)
        except Exception:
            pass
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

            # Attendre que MPV crée le socket IPC côté host (max 8 s)
            # On vérifie via flatpak-spawn pour ne pas souffrir de l'isolation
            deadline = time.monotonic() + 8.0
            socket_found = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self._status("Impossible de se connecter au stream.")
                    log_event(f"MPV exited early for {station.get('name')}")
                    with self._lock:
                        self._is_playing = False
                    return
                # Vérifier l'existence du socket côté host
                result = Updater.run_host(
                    ["test", "-S", _MPV_IPC_SOCKET], quiet=True
                )
                if result.returncode == 0:
                    socket_found = True
                    break
                time.sleep(0.15)

            if not socket_found:
                log_event(
                    f"Socket IPC introuvable (timeout) pour {station.get('name')}",
                    level="debug"
                )
            else:
                # Appliquer le volume dès que le socket est prêt
                self._ipc_set_property("volume", self._volume)
                log_event(f"Socket IPC trouvé — volume={self._volume}", level="debug")

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
