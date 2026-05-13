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
    # Trouver le stream MPV dans PipeWire
    # Méthode 1 : pw-dump (JSON structuré — le plus fiable)
    # Méthode 2 : wpctl status (fallback avec log du format brut)
    # ─────────────────────────────

    def _find_stream_pw(self) -> tuple[str | None, str | None]:
        """
        Retourne (node_id, track_title) du stream MPV actif.
        Essaie pw-dump d'abord, puis wpctl.
        """
        result = self._find_stream_pwdump()
        if result[0]:
            return result
        return self._find_stream_wpctl()

    def _find_stream_pwdump(self) -> tuple[str | None, str | None]:
        """
        Utilise pw-dump (JSON PipeWire) pour trouver le stream MPV.
        Cherche un node de type Stream/Output/Audio avec "BBS radiOO" ou "mpv".
        """
        try:
            result = subprocess.run(
                ["flatpak-spawn", "--host", "pw-dump"],
                capture_output=True, text=True, timeout=6
            )
            if not result.stdout.strip():
                log_event("pw-dump: sortie vide", level="debug")
                return None, None

            nodes = json.loads(result.stdout)
            for node in nodes:
                if "Node" not in node.get("type", ""):
                    continue
                info  = node.get("info", {})
                props = info.get("props", {})

                node_name    = props.get("node.name", "")
                media_class  = props.get("media.class", "")
                app_name     = props.get("application.name", "")
                media_name   = props.get("media.name", "")

                is_mpv_stream = (
                    "Stream/Output/Audio" in media_class and (
                        "BBS radiOO" in node_name or
                        "BBS radiOO" in media_name or
                        "mpv" in node_name.lower() or
                        "mpv" in app_name.lower()
                    )
                )
                if not is_mpv_stream:
                    continue

                node_id = str(node.get("id", ""))

                # Extraire le titre depuis le nom du node ou media.name
                title = None
                for source in [node_name, media_name]:
                    m = re.search(r'BBS radiOO\s*-\s*(.+)', source)
                    if m:
                        title = m.group(1).strip()
                        break

                log_event(
                    f"pw-dump: node #{node_id} class={media_class} "
                    f"name='{node_name}' title='{title}'",
                    level="debug"
                )
                return node_id, title

            log_event("pw-dump: aucun stream MPV trouvé", level="debug")

        except json.JSONDecodeError as e:
            log_event(f"pw-dump JSON error: {e}", level="debug")
        except Exception as e:
            log_event(f"pw-dump: {e}", level="debug")

        return None, None

    def _find_stream_wpctl(self) -> tuple[str | None, str | None]:
        """
        Fallback wpctl status. Log le contenu brut pour diagnostiquer le format.
        """
        try:
            result = subprocess.run(
                ["flatpak-spawn", "--host", "wpctl", "status"],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout

            # Log toutes les lignes contenant mpv ou BBS pour diagnostiquer
            relevant = [l.strip() for l in output.split("\n")
                        if l.strip() and ("mpv" in l.lower() or "BBS" in l)]
            log_event(f"wpctl mpv/BBS lines: {relevant[:8]}", level="debug")

            for line in output.split("\n"):
                # Lignes de streams : contiennent "BBS radiOO" OU ("mpv" + "vol:")
                if "BBS radiOO" in line or (
                    re.search(r"mpv", line, re.IGNORECASE) and "vol:" in line
                ):
                    id_m = re.search(r"\b(\d+)\b", line)
                    if not id_m:
                        continue
                    sid = id_m.group(1)
                    title_m = re.search(r'BBS radiOO\s*-\s*(.+?)(?:\s*[\[|]|$)', line)
                    title = title_m.group(1).strip() if title_m else None
                    log_event(f"wpctl: stream #{sid} title='{title}'", level="debug")
                    return sid, title

        except Exception as e:
            log_event(f"wpctl: {e}", level="debug")

        return None, None

    # ─────────────────────────────
    # Volume
    # ─────────────────────────────

    def _set_volume_pw(self, volume: int) -> bool:
        """Cherche le stream puis applique le volume via wpctl set-volume."""
        node_id, _ = self._find_stream_pw()
        if not node_id:
            return False
        try:
            subprocess.run(
                ["flatpak-spawn", "--host", "wpctl",
                 "set-volume", node_id, f"{volume}%"],
                capture_output=True, timeout=3
            )
            log_event(f"wpctl set-volume #{node_id} → {volume}%", level="debug")
            return True
        except Exception as e:
            log_event(f"wpctl set-volume: {e}", level="debug")
            return False

    # ─────────────────────────────
    # Metadata
    # ─────────────────────────────

    def _get_track(self) -> str | None:
        """Titre en cours : pw-dump → wpctl → IPC."""
        _, title = self._find_stream_pw()
        if title:
            return title
        return self._get_track_from_ipc()

    def _get_track_from_ipc(self) -> str | None:
        script = (
            "import socket,json;"
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            "s.settimeout(1);"
            f"s.connect('{_MPV_IPC_SOCKET}');"
            "s.sendall(json.dumps({'command':['get_property','media-title']}).encode()+b'\\n');"
            "d=s.recv(4096);s.close();"
            "r=json.loads(d.split(b'\\n')[0]);"
            "print(r.get('data','') if r.get('error')=='success' else '',end='')"
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

    def _poll_metadata(self, station_id: str):
        self._polling = True
        last_title = ""
        while self._polling and self._is_playing:
            if (
                self._current_station is None or
                self._current_station.get("id") != station_id
            ):
                break
            title = self._get_track() or ""
            if title and title != last_title:
                last_title = title
                log_event(f"Metadata: '{title}'")
                if self.on_metadata_change:
                    GLib.idle_add(self.on_metadata_change, title)
            time.sleep(_METADATA_POLL_INTERVAL)

    # ─────────────────────────────
    # IPC via host python3
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
            Updater.run_host(["python3", "-c", script], quiet=True)
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

    def _launch(self, station: dict):
        station_id = station.get("id", "")
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
                if self._current_station and self._current_station.get("id") != station_id:
                    log_event(f"_launch: abandon {station.get('name')}", level="debug")
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

            # Volume via PipeWire après enregistrement du flux (délai 2s)
            threading.Timer(2.0, lambda: self._apply_volume_if_current(station_id)).start()

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
                if self._current_station and self._current_station.get("id") == station_id:
                    self._is_playing = False
                else:
                    log_event("_launch end: station changée, _is_playing conservé", level="debug")
                    return

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
