import os
import re
import json
import subprocess
import socket as _socket
import threading
import time

from gi.repository import GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.updater import Updater

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
            # Contrôle direct du sink input PipeWire/PulseAudio — visible dans les
            # paramètres système KDE et efficace sans IPC socket.
            if not self._set_volume_pactl(self._volume):
                # Fallback : IPC MPV via le host
                self._ipc_set_property_host("volume", self._volume)
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
        self._ipc_command_host("quit")
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
    # Volume PipeWire via pactl
    # ─────────────────────────────

    def _set_volume_pactl(self, volume: int) -> bool:
        """
        Contrôle le volume du flux MPV directement dans PipeWire/PulseAudio.
        Cherche le sink input dont le nom contient 'BBS radiOO'.
        Retourne True si succès.
        """
        try:
            result = subprocess.run(
                ["flatpak-spawn", "--host", "pactl", "list", "sink-inputs"],
                capture_output=True, text=True, timeout=4
            )
            output = result.stdout

            # Trouver le Sink Input # qui contient "BBS radiOO"
            sink_input_id = None
            current_id = None
            for line in output.split("\n"):
                m = re.search(r"Sink Input #(\d+)", line)
                if m:
                    current_id = m.group(1)
                if "BBS radiOO" in line and current_id:
                    sink_input_id = current_id
                    break

            if not sink_input_id:
                log_event("pactl: sink input BBS radiOO non trouvé", level="debug")
                return False

            subprocess.run(
                ["flatpak-spawn", "--host", "pactl",
                 "set-sink-input-volume", sink_input_id, f"{volume}%"],
                capture_output=True, timeout=2
            )
            log_event(f"pactl sink-input #{sink_input_id} → {volume}%", level="debug")
            return True

        except Exception as e:
            log_event(f"pactl volume failed: {e}", level="debug")
            return False

    # ─────────────────────────────
    # IPC MPV via host (contourne isolation Flatpak)
    # ─────────────────────────────

    def _ipc_send_via_host(self, payload: str):
        """Envoie un message JSON IPC au socket MPV en passant par le host."""
        payload_escaped = payload.replace("'", "\\'")
        script = (
            f"import socket as s,sys;"
            f"c=s.socket(s.AF_UNIX,s.SOCK_STREAM);"
            f"c.settimeout(1.0);"
            f"c.connect('{_MPV_IPC_SOCKET}');"
            f"c.sendall(b'{payload_escaped}\\n');"
            f"c.close()"
        )
        try:
            Updater.run_host(["python3", "-c", script], quiet=True)
        except Exception as e:
            log_event(f"IPC host failed: {e}", level="debug")

    def _ipc_command_host(self, *args):
        payload = json.dumps({"command": list(args)})
        self._ipc_send_via_host(payload)

    def _ipc_set_property_host(self, prop: str, value):
        payload = json.dumps({"command": ["set_property", prop, value]})
        self._ipc_send_via_host(payload)

    def _ipc_get_property_host(self, prop: str):
        """Lit une propriété MPV via le host."""
        script = (
            "import socket,json;"
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            "s.settimeout(1.0);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            f"s.sendall(json.dumps({{'command':['get_property','{prop}']}}).encode()+b'\\n');"
            "buf=b'';"
            "b=s.recv(4096);"
            "buf+=b;"
            "s.close();"
            "r=json.loads(buf.split(b'\\n')[0]);"
            "print(r.get('data','') if r.get('error')=='success' else '',end='')"
        )
        try:
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
            title = self._ipc_get_property_host("media-title") or ""
            if isinstance(title, str) and title != last_title:
                last_title = title
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, title)
            time.sleep(_METADATA_POLL_INTERVAL)

    # ─────────────────────────────
    # Internal
    # ─────────────────────────────

    def _stop_current(self):
        self._ipc_command_host("quit")
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
            Updater.run_host(["pkill", "-f", "BBS radiOO"], quiet=True)
        except Exception:
            pass

    def _remove_socket(self):
        try:
            Updater.run_host(["rm", "-f", _MPV_IPC_SOCKET], quiet=True)
        except Exception:
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

            # Attendre que MPV démarre (socket ou 8s timeout)
            deadline = time.monotonic() + 8.0
            socket_found = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self._status("Impossible de se connecter au stream.")
                    log_event(f"MPV exited early for {station.get('name')}")
                    with self._lock:
                        self._is_playing = False
                    return
                result = Updater.run_host(["test", "-S", _MPV_IPC_SOCKET], quiet=True)
                if result.returncode == 0:
                    socket_found = True
                    break
                time.sleep(0.15)

            if socket_found:
                # Appliquer le volume initial via IPC
                self._ipc_set_property_host("volume", self._volume)

            # Courte attente pour que PipeWire enregistre le flux,
            # puis appliquer le volume via pactl aussi
            threading.Timer(1.5, self._apply_pactl_volume_delayed).start()

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

    def _apply_pactl_volume_delayed(self):
        """Applique le volume via pactl après que PipeWire a enregistré le flux."""
        if self._is_playing:
            self._set_volume_pactl(self._volume)

    def _status(self, text: str):
        log_event(text)
        if self.on_status_change:
            GLib.idle_add(self.on_status_change, text)
