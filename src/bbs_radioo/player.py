import os
import re
import json
import subprocess
import threading
import time

from gi.repository import GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.updater import Updater

_MPV_IPC_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
    "bbs-radioo-mpv.sock"
)

_METADATA_POLL_INTERVAL = 4.0


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
            self._set_volume_pactl(self._volume)
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
    # pactl — volume + metadata
    # ─────────────────────────────

    def _get_pactl_info(self) -> tuple[str | None, str | None]:
        """
        Cherche notre MPV dans pactl list sink-inputs.
        Retourne (sink_input_id, track_title).
        Cherche "BBS radiOO" dans les propriétés du sink.
        """
        try:
            result = subprocess.run(
                ["flatpak-spawn", "--host", "pactl", "list", "sink-inputs"],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout
            log_event(f"pactl: {len(output)} chars, {output.count('Sink Input')} sink(s)", level="debug")

            current_id = None
            found_id = None
            found_title = None

            for line in output.split("\n"):
                # Nouveau sink input
                m = re.search(r"Sink Input #(\d+)", line)
                if m:
                    current_id = m.group(1)

                # Chercher "BBS radiOO" dans n'importe quelle propriété
                if current_id and "BBS radiOO" in line:
                    found_id = current_id
                    # Extraire le titre : "mpv-bin: BBS radiOO - Artiste - Titre"
                    # ou juste "BBS radiOO" si pas encore de metadata
                    title_m = re.search(r'BBS radiOO\s*-\s*(.+?)(?:["\s]*$)', line)
                    if title_m:
                        found_title = title_m.group(1).strip().rstrip('"\'')
                    log_event(f"pactl: found sink #{found_id}, title='{found_title}'", level="debug")
                    break

            # Fallback : chercher par application.name contenant "mpv"
            if not found_id:
                current_id = None
                for line in output.split("\n"):
                    m = re.search(r"Sink Input #(\d+)", line)
                    if m:
                        current_id = m.group(1)
                    if current_id and re.search(r'application\.name\s*=\s*"mpv', line, re.IGNORECASE):
                        found_id = current_id
                        log_event(f"pactl: found mpv sink #{found_id} (fallback)", level="debug")
                        break

            if not found_id:
                log_event("pactl: aucun sink MPV trouvé", level="debug")

            return found_id, found_title

        except Exception as e:
            log_event(f"pactl list failed: {e}", level="debug")
            return None, None

    def _set_volume_pactl(self, volume: int):
        sink_id, _ = self._get_pactl_info()
        if sink_id:
            try:
                subprocess.run(
                    ["flatpak-spawn", "--host", "pactl",
                     "set-sink-input-volume", sink_id, f"{volume}%"],
                    capture_output=True, timeout=3
                )
                log_event(f"pactl: sink #{sink_id} → {volume}%", level="debug")
            except Exception as e:
                log_event(f"pactl set-volume failed: {e}", level="debug")
        else:
            # Fallback IPC
            self._ipc_set_property_host("volume", volume)

    # ─────────────────────────────
    # IPC via host python3
    # ─────────────────────────────

    def _ipc_send_via_host(self, payload: str):
        payload_esc = payload.replace("'", "\\'").replace('"', '\\"')
        script = (
            f"import socket as s;"
            f"c=s.socket(s.AF_UNIX,s.SOCK_STREAM);"
            f"c.settimeout(1.0);"
            f"c.connect('{_MPV_IPC_SOCKET}');"
            f"c.sendall(b\"{payload_esc}\\n\");"
            f"c.close()"
        )
        try:
            Updater.run_host(["python3", "-c", script], quiet=True)
        except Exception as e:
            log_event(f"IPC host send: {e}", level="debug")

    def _ipc_command_host(self, *args):
        self._ipc_send_via_host(json.dumps({"command": list(args)}))

    def _ipc_set_property_host(self, prop: str, value):
        self._ipc_send_via_host(json.dumps({"command": ["set_property", prop, value]}))

    # ─────────────────────────────
    # Metadata polling — pactl primary, IPC fallback
    # ─────────────────────────────

    def _poll_metadata(self):
        self._polling = True
        last_title = ""
        while self._polling and self._is_playing:
            # Essayer pactl d'abord (extrait depuis le nom du sink)
            _, title = self._get_pactl_info()

            # Fallback IPC si pactl ne donne rien
            if not title:
                title = self._ipc_get_property_host("media-title") or ""

            if isinstance(title, str) and title and title != last_title:
                last_title = title
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, title)
            time.sleep(_METADATA_POLL_INTERVAL)

    def _ipc_get_property_host(self, prop: str) -> str | None:
        script = (
            "import socket,json;"
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            "s.settimeout(1);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            f"s.sendall(json.dumps({{'command':['get_property','{prop}']}}).encode()+b'\\n');"
            "d=s.recv(4096);s.close();"
            "r=json.loads(d.split(b'\\n')[0]);"
            "print(r['data'] if r.get('error')=='success' else '',end='')"
        )
        try:
            r = subprocess.run(
                ["flatpak-spawn", "--host", "python3", "-c", script],
                capture_output=True, text=True, timeout=3
            )
            v = r.stdout.strip()
            return v if v else None
        except Exception:
            return None

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

            deadline = time.monotonic() + 8.0
            socket_found = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self._status("Impossible de se connecter au stream.")
                    log_event(f"MPV exited early for {station.get('name')}")
                    with self._lock:
                        self._is_playing = False
                    return
                r = Updater.run_host(["test", "-S", _MPV_IPC_SOCKET], quiet=True)
                if r.returncode == 0:
                    socket_found = True
                    break
                time.sleep(0.15)

            log_event(f"Socket IPC: {'trouvé' if socket_found else 'timeout'}", level="debug")

            if socket_found:
                self._ipc_set_property_host("volume", self._volume)

            # Appliquer aussi via pactl après que PipeWire enregistre le flux
            threading.Timer(2.0, self._apply_volume_delayed).start()

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

    def _apply_volume_delayed(self):
        if self._is_playing:
            self._set_volume_pactl(self._volume)

    def _status(self, text: str):
        log_event(text)
        if self.on_status_change:
            GLib.idle_add(self.on_status_change, text)
