import os
import sys
from gi.repository import GLib

from bbs_radioo.app import RadiooApp
from bbs_radioo.updater import Updater


def check_dependencies():
    status = Updater.status()
    missing = []

    if not status.get("mpv", False):
        missing.append("MPV Flatpak manquant : flatpak install flathub io.mpv.Mpv")

    if missing:
        print("\n=== Dépendances manquantes ===\n")
        for dep in missing:
            print(" -", dep)
        print("\nInstalle les dépendances puis relance l'application.\n")
        sys.exit(1)


def main():
    GLib.set_prgname("bbs-radioo")
    GLib.set_application_name("BBS radiOO")
    print("radiOO starting...")
    check_dependencies()

    state_dir = os.path.join(GLib.get_user_data_dir(), "bbs-radioo")
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass

    app = RadiooApp(state_dir)
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
