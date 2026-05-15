import os
import re
import json
import subprocess
import threading
import time

from gi.repository import GLib

from bbs_radioo.logging_utils import log_event
from bbs_radioo.updater import Updater, IN_FLATPAK

_MPV_IPC_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
    "bbs-radioo-mpv.sock"
)

_METADATA_POLL_INTERVAL = 4.0
_RECONNECT_DELAY        = 5.0
_RECONNECT_MAX          = 3


def _run_host(args: list, **kwargs) -> subprocess.CompletedProcess:
    cmd = (["flatpak-spawn", "--host"] + args) if IN_FLATPAK else args
    return subprocess.run(cmd, **kwargs)


def _is_valid_title(title: str) -> bool:
    """
    Filtre les métadonnées invalides que certains streams envoient :
    - URL / query string     ('ouuku85n3nje?origine=fluxradios')
    - Nombre pur             ('9999999', '128000')
    - Décimal                ('971.755')
    - Nombres avec espaces   ('971 755')
    - Toutes parties nombres ('971755 - 971755')  ← cas Chérie FM
    - Trop court             (< 3 chars)
    """
    if not title or len(title.strip()) < 3:
        return False
    t = title.strip()
    # URL ou query string
    if any(c in t for c in ('/', '?', '://')):
        return False
    # Nombre pur (avec ou sans espaces/points)
    if re.match(r'^[\d\s.]+$', t):
        return False
    # Toutes les parties séparées par " - " sont des nombres
    # ex: "971755 - 971755" → invalide
    parts = [p.strip() for p in t.split(' - ') if p.strip()]
    if parts and all(re.match(r'^[\d\s.]+$', p) for p in parts):
        return False
    return True


class RadioPlayer:

    def __init__(self):
        self._process = None
        self._all_processes: list = []
        self._lock = threading.Lock()
        self._is_playing = False
        self._current_station: dict | None = None
        self._polling = False
        self._volume = 100
        self._user_stopped = False

        self.on_status_change   = None
        self.on_station_change  = None
        self.on_metadata_change = None

    # ─────────────────────────────
    # Public
    # ─────────────────────────────

    def play(self, station: dict):
        with self._lock:
            self._polling = False
            self._user_stopped = False
            self._stop_current()
            self._is_playing = True
            self._current_station = station
        self._status(f"Connexion à {station.get('name', '')}...")
        log_event(f"Play: {station.get('name')} — {station.get('stream_url')}")
        threading.Thread(target=self._launch, args=(station, 0), daemon=True).start()

    def stop(self):
        self._polling = False
        self._user_stopped = True
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
            if not self._set_volume_pw(self._volume):
                self._ipc_set_property_host("volume", self._volume)
        else:
            log_event(f"Volume sauvegardé → {self._volume}", level="debug")

    def is_playing(self) -> bool:
        return self._is_playing

    def current_station(self) -> dict | None:
        return self._current_station

    def cleanup(self):
        log_event("Player cleanup…")
        self._polling = False
        self._user_stopped = True
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
    # PipeWire — volume
    # ─────────────────────────────

    def _find_stream_pw(self) -> tuple[str | None, str | None]:
        result = self._find_stream_pwdump()
        if result[0]:
            return result
        return self._find_stream_wpctl()

    def _find_stream_pwdump(self) -> tuple[str | None, str | None]:
        try:
            r = _run_host(["pw-dump"], capture_output=True, text=True, timeout=6)
            if not r.stdout.strip():
                return None, None
            nodes = json.loads(r.stdout)
            for node in nodes:
                if "Node" not in node.get("type", ""):
                    continue
                props       = node.get("info", {}).get("props", {})
                node_name   = props.get("node.name", "")
                media_class = props.get("media.class", "")
                app_name    = props.get("application.name", "")
                media_name  = props.get("media.name", "")
                is_mpv = (
                    "Stream/Output/Audio" in media_class and (
                        "BBS radiOO" in node_name or "BBS radiOO" in media_name or
                        "mpv" in node_name.lower() or "mpv" in app_name.lower()
                    )
                )
                if not is_mpv:
                    continue
                node_id = str(node.get("id", ""))
                title = None
                for src in [node_name, media_name]:
                    m = re.search(r'BBS radiOO\s*-\s*(.+)', src)
                    if m:
                        candidate = m.group(1).strip()
                        if _is_valid_title(candidate):
                            title = candidate
                        break
                return node_id, title
        except Exception as e:
            log_event(f"pw-dump: {e}", level="debug")
        return None, None

    def _find_stream_wpctl(self) -> tuple[str | None, str | None]:
        try:
            r = _run_host(["wpctl", "status"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "BBS radiOO" in line or (
                    re.search(r"mpv", line, re.IGNORECASE) and "vol:" in line
                ):
                    id_m = re.search(r"\b(\d+)\b", line)
                    if not id_m:
                        continue
                    sid = id_m.group(1)
                    title_m = re.search(r'BBS radiOO\s*-\s*(.+?)(?:\s*[\[|]|$)', line)
                    if title_m:
                        candidate = title_m.group(1).strip()
                        title = candidate if _is_valid_title(candidate) else None
                    else:
                        title = None
                    return sid, title
        except Exception as e:
            log_event(f"wpctl: {e}", level="debug")
        return None, None

    def _set_volume_pw(self, volume: int) -> bool:
        node_id, _ = self._find_stream_pw()
        if not node_id:
            return False
        try:
            _run_host(["wpctl", "set-volume", node_id, f"{volume}%"],
                      capture_output=True, timeout=3)
            log_event(f"wpctl set-volume #{node_id} → {volume}%", level="debug")
            return True
        except Exception as e:
            log_event(f"wpctl set-volume: {e}", level="debug")
            return False

    # ─────────────────────────────
    # Metadata
    # ─────────────────────────────

    def _get_track(self) -> str | None:
        _, title = self._find_stream_pw()
        if title:
            return title
        raw = self._ipc_get_property("media-title")
        if raw and _is_valid_title(raw):
            return raw
        return None

    def _ipc_get_property(self, prop: str) -> str | None:
        script = (
            "import socket,json;"
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            "s.settimeout(1);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            f"s.sendall(json.dumps({{'command':['get_property','{prop}']}}).encode()+b'\\n');"
            "d=s.recv(4096);s.close();"
            "r=json.loads(d.split(b'\\n')[0]);"
            "print(r.get('data','') if r.get('error')=='success' else '',end='')"
        )
        try:
            r = _run_host(["python3", "-c", script],
                          capture_output=True, text=True, timeout=3)
            v = r.stdout.strip()
            return v if v else None
        except Exception:
            return None

    def _probe_stream_info(self) -> dict:
        info = {}
        try:
            bitrate = self._ipc_get_property("audio-bitrate")
            if bitrate:
                try:
                    info["bitrate"] = max(1, int(float(bitrate) / 1000))
                except ValueError:
                    pass
            codec = self._ipc_get_property("audio-codec-name")
            if codec and codec.strip():
                info["codec"] = codec.strip().lower()
        except Exception:
            pass
        return info

    def _poll_metadata(self, station_id: str):
        self._polling = True
        last_title = ""
        probed = False
        probe_at = time.monotonic() + 3.0

        while self._polling and self._is_playing:
            if (
                self._current_station is None or
                self._current_station.get("id") != station_id
            ):
                break

            # Probe stream info pour stations custom
            if not probed and time.monotonic() >= probe_at:
                probed = True
                if self._current_station and self._current_station.get("source") == "custom":
                    info = self._probe_stream_info()
                    if info and self._current_station:
                        updated = {**self._current_station, **info}
                        self._current_station = updated
                        if self.on_station_change:
                            GLib.idle_add(self.on_station_change, updated)

            title = self._get_track() or ""
            if title and title != last_title:
                last_title = title
                log_event(f"Metadata: '{title}'")
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, title)
            time.sleep(_METADATA_POLL_INTERVAL)

    # ─────────────────────────────
    # IPC MPV
    # ─────────────────────────────

    def _ipc_send_via_host(self, payload: str):
        safe = payload.replace("\\", "\\\\").replace("'", "\\'")
        script = (
            f"import socket as s;"
            f"c=s.socket(s.AF_UNIX,s.SOCK_STREAM);"
            f"c.settimeout(1);"
            f"c.connect('{_MPV_IPC_SOCKET}');"
            f"c.sendall(b'{safe}\\n');"
            f"c.close()"
        )
        try:
            _run_host(["python3", "-c", script],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log_event(f"IPC send: {e}", level="debug")

    def _ipc_command_host(self, *args):
        self._ipc_send_via_host(json.dumps({"command": list(args)}))

    def _ipc_set_property_host(self, prop: str, value):
        self._ipc_send_via_host(json.dumps({"command": ["set_property", prop, value]}))

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

    def _launch(self, station: dict, attempt: int):
        station_id = station.get("id", "")
        try:
            if attempt > 0:
                log_event(f"Reconnexion {attempt}/{_RECONNECT_MAX} — {station.get('name')}")
                self._status(f"Reconnexion… ({attempt}/{_RECONNECT_MAX})")

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
                if self._current_station and self._current_station.get("id") != station_id:
                    return
                if proc.poll() is not None:
                    self._status("Impossible de se connecter au stream.")
                    log_event(f"MPV exited early for {station.get('name')}")
                    with self._lock:
                        if self._current_station and self._current_station.get("id") == station_id:
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

            threading.Timer(2.0, lambda: self._apply_volume_if_current(station_id)).start()

            if attempt == 0:
                self._status(f"En écoute : {station.get('name', '')}")
            if self.on_station_change:
                GLib.idle_add(self.on_station_change, station)

            threading.Thread(
                target=self._poll_metadata, args=(station_id,), daemon=True
            ).start()

            proc.wait()
            self._polling = False

            with self._lock:
                if proc in self._all_processes:
                    self._all_processes.remove(proc)
                is_ours = (
                    self._current_station and
                    self._current_station.get("id") == station_id
                )
                if not is_ours:
                    return
                if self._user_stopped:
                    self._is_playing = False
                    self._status("Arrêté.")
                    if self.on_station_change:
                        GLib.idle_add(self.on_station_change, None)
                    if self.on_metadata_change:
                        GLib.idle_add(self.on_metadata_change, "")
                    return

            # Reconnexion automatique
            if attempt < _RECONNECT_MAX:
                log_event(f"Stream perdu — reconnexion dans {_RECONNECT_DELAY}s")
                self._status(f"Connexion perdue. Reconnexion dans {int(_RECONNECT_DELAY)}s…")
                time.sleep(_RECONNECT_DELAY)
                if (
                    not self._user_stopped and
                    self._current_station and
                    self._current_station.get("id") == station_id
                ):
                    self._launch(station, attempt + 1)
            else:
                self._status("Stream terminé.")
                with self._lock:
                    self._is_playing = False
                if self.on_station_change:
                    GLib.idle_add(self.on_station_change, None)
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, "")

        except Exception as exc:
            log_event(f"Player error: {exc}")
            self._status("Erreur de lecture.")
            with self._lock:
                if self._current_station and self._current_station.get("id") == station_id:
                    self._is_playing = False

    def _apply_volume_if_current(self, station_id: str):
        if (
            self._is_playing and
            self._current_station and
            self._current_station.get("id") == station_id
        ):
            self.set_volume(self._volume)

    def _status(self, text: str):
        log_event(text)
        if self.on_status_change:
            GLib.idle_add(self.on_status_change, text)
