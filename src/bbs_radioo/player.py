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
            if not self._set_volume_wpctl(self._volume):
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
    # wpctl — volume + metadata
    #
    # Dans `wpctl status`, les STREAMS actifs ont `[vol: X.XX]` dans la ligne.
    # Les CLIENT nodes ont `[version, user, pid]` → c'est le mauvais node.
    # On cherche donc "mpv" ET "vol:" pour trouver le vrai stream audio.
    # ─────────────────────────────

    def _get_wpctl_status(self) -> str:
        try:
            r = subprocess.run(
                ["flatpak-spawn", "--host", "wpctl", "status"],
                capture_output=True, text=True, timeout=5
            )
            return r.stdout
        except Exception as e:
            log_event(f"wpctl status: {e}", level="debug")
            return ""

    def _find_wpctl_stream_id(self) -> tuple[str | None, str | None]:
        """
        Retourne (stream_id, track_title) depuis wpctl status.

        Cherche une ligne contenant "mpv" ET "vol:" — ce sont les STREAMS actifs.
        Exemple de ligne stream :
            ├─  142. mpv-bin: BBS radiOO - Artist - Title   [vol: 1.00]

        Les lignes CLIENT (mpv sans vol:) sont ignorées :
            ├─  85. mpv-bin  [1.4.9, bbs@host, pid:2]
        """
        output = self._get_wpctl_status()
        log_event(f"wpctl: {len(output)} chars", level="debug")

        for line in output.split("\n"):
            # Condition clé : doit contenir "mpv" ET "vol:"
            if re.search(r"mpv", line, re.IGNORECASE) and "vol:" in line:
                # Extraire l'ID (premier nombre dans la ligne)
                id_m = re.search(r"\b(\d+)\b", line)
                if not id_m:
                    continue
                stream_id = id_m.group(1)

                # Extraire le titre depuis le nom du stream
                # Format : "N. mpv-bin: BBS radiOO - Artiste - Titre   [vol: X.XX]"
                title = None
                name_m = re.search(r'BBS radiOO\s*-\s*(.+?)\s*\[vol:', line)
                if name_m:
                    title = name_m.group(1).strip()

                log_event(
                    f"wpctl stream #{stream_id}, title='{title}' — '{line.strip()}'",
                    level="debug"
                )
                return stream_id, title

        log_event("wpctl: aucun stream actif mpv (avec vol:)", level="debug")
        return None, None

    def _set_volume_wpctl(self, volume: int) -> bool:
        stream_id, _ = self._find_wpctl_stream_id()
        if not stream_id:
            return False
        try:
            subprocess.run(
                ["flatpak-spawn", "--host", "wpctl",
                 "set-volume", stream_id, f"{volume}%"],
                capture_output=True, timeout=3
            )
            log_event(f"wpctl: stream #{stream_id} → {volume}%", level="debug")
            return True
        except Exception as e:
            log_event(f"wpctl set-volume: {e}", level="debug")
            return False

    # ─────────────────────────────
    # Metadata
    # ─────────────────────────────

    def _get_track(self) -> str | None:
        """
        Extrait le titre en cours depuis wpctl status (ligne du stream).
        Fallback sur wpctl inspect, puis IPC.
        """
        # 1. Depuis la ligne wpctl status directement
        _, title = self._find_wpctl_stream_id()
        if title:
            return title

        # 2. wpctl inspect du stream (donne media.name complet)
        stream_id, _ = self._find_wpctl_stream_id()
        if stream_id:
            try:
                r = subprocess.run(
                    ["flatpak-spawn", "--host", "wpctl", "inspect", stream_id],
                    capture_output=True, text=True, timeout=3
                )
                for line in r.stdout.split("\n"):
                    if "media.name" in line:
                        m = re.search(r'=\s*"(.+)"', line)
                        if m:
                            val = m.group(1)
                            title_m = re.search(r'BBS radiOO\s*-\s*(.+)', val)
                            if title_m:
                                return title_m.group(1).strip()
                            if "mpv" not in val.lower():
                                return val
            except Exception as e:
                log_event(f"wpctl inspect: {e}", level="debug")

        # 3. IPC MPV via host python3
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
    # IPC via host
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
                    log_event(f"_launch: abandon {station.get('name')} (station changée)", level="debug")
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

            # Appliquer le volume via wpctl après enregistrement PipeWire
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
                # Fix race condition : ne pas écraser _is_playing si une autre station joue
                if self._current_station and self._current_station.get("id") == station_id:
                    self._is_playing = False
                else:
                    log_event(
                        f"_launch end: station changée, _is_playing conservé",
                        level="debug"
                    )
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
