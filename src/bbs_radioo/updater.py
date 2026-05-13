import os
import shutil
import subprocess

# Détecte si l'app tourne dans un sandbox Flatpak.
# Hors Flatpak : les commandes sont exécutées directement sur le host.
IN_FLATPAK = os.path.exists("/.flatpak-info")


class Updater:
    """
    Gestion de MPV pour la lecture audio radio.
    Compatible Flatpak (via flatpak-spawn) et installation système directe.
    """

    # ─────────────────────────────
    # Utils
    # ─────────────────────────────

    @staticmethod
    def _host(args: list) -> list:
        """Préfixe la commande avec flatpak-spawn si on est dans un Flatpak."""
        return (["flatpak-spawn", "--host"] + args) if IN_FLATPAK else args

    @staticmethod
    def has_binary(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def run_host(args: list, quiet: bool = False):
        cmd = Updater._host(args)
        if quiet:
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return subprocess.run(cmd)

    @staticmethod
    def popen_host(args: list):
        cmd = Updater._host(args)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ─────────────────────────────
    # MPV
    # ─────────────────────────────

    @staticmethod
    def mpv_available() -> bool:
        if IN_FLATPAK:
            result = Updater.run_host(
                ["flatpak", "info", "io.mpv.Mpv"], quiet=True
            )
        else:
            result = subprocess.run(
                ["which", "mpv"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return result.returncode == 0

    @staticmethod
    def kill_all_streams():
        """Tue tous les process MPV lancés par BBS radiOO."""
        try:
            Updater.run_host(["pkill", "-f", "BBS radiOO"], quiet=True)
        except Exception:
            pass

    @staticmethod
    def play_stream(
        stream_url: str,
        ipc_socket_path: str = None,
        volume: int = 100,
    ):
        """Lance MPV en mode audio pour un stream radio."""
        volume = max(0, min(100, volume))

        if IN_FLATPAK:
            # Flatpak : lance le MPV Flatpak via flatpak run
            cmd = ["flatpak", "run", "io.mpv.Mpv"]
        else:
            # Installation système : MPV directement
            cmd = ["mpv"]

        cmd += [
            "--no-video",
            "--force-window=no",
            f"--volume={volume}",
            "--msg-level=osd/libass=no",
            "--title=BBS radiOO - ${media-title}",
        ]
        if ipc_socket_path:
            cmd.append(f"--input-ipc-server={ipc_socket_path}")
        cmd.append(stream_url)

        if IN_FLATPAK:
            return Updater.popen_host(cmd)
        else:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # ─────────────────────────────
    # Diagnostic
    # ─────────────────────────────

    @staticmethod
    def status() -> dict:
        return {"mpv": Updater.mpv_available()}
