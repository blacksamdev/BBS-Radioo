import shutil
import subprocess


class Updater:
    """Gestion de MPV pour la lecture audio radio."""

    @staticmethod
    def has_binary(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def run_host(args: list, quiet: bool = False):
        if quiet:
            return subprocess.run(
                ["flatpak-spawn", "--host"] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        return subprocess.run(["flatpak-spawn", "--host"] + args)

    @staticmethod
    def popen_host(args: list):
        return subprocess.Popen(
            ["flatpak-spawn", "--host"] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def mpv_available() -> bool:
        result = Updater.run_host(["flatpak", "info", "io.mpv.Mpv"], quiet=True)
        return result.returncode == 0

    @staticmethod
    def status() -> dict:
        return {"mpv": Updater.mpv_available()}

    @staticmethod
    def play_stream(stream_url: str, ipc_socket_path: str = None):
        """Lance MPV en mode audio pour un stream radio."""
        cmd = [
            "flatpak", "run", "io.mpv.Mpv",
            "--no-video",
            "--force-window=no",
            "--volume=100",
            "--msg-level=osd/libass=no",
            "--title=BBS radiOO - ${media-title}",
        ]
        if ipc_socket_path:
            cmd.append(f"--input-ipc-server={ipc_socket_path}")
        cmd.append(stream_url)
        return Updater.popen_host(cmd)

    @staticmethod
    def stop_stream(process):
        """Arrête un process MPV."""
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
