# BBS radiOO 📡

🇫🇷 [Version française](README.md)

Listen to internet radio by theme — native interface, lightweight audio playback via MPV, local and privacy-respecting. Ad-free with SomaFM and curated stations. Volume control directly in PipeWire.

If you like the project, a ⭐ GitHub star makes a real difference!

---

## Quick install (Flatpak)

### 1. Install MPV

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub io.mpv.Mpv
```

### 2. Add the BBS radiOO repository

```bash
flatpak remote-add --if-not-exists --from bbs-radioo \
  https://blacksamdev.github.io/BBS-Radioo/bbs-radioo.flatpakrepo
```

### 3. Install

```bash
flatpak install bbs-radioo io.github.blacksamdev.Radioo
```

The app will then appear in your desktop menu.

---

## Usage

- **Sections**: *Right now* (most active stations), *Top stations* (most voted), *Ad-free* (SomaFM + curated)
- **Favorites**: click ☆ in the detail panel to save a station
- **Volume**: the slider directly controls the audio stream in PipeWire — visible in system settings
- **Artist / Title**: automatically displayed in the detail panel as soon as the station sends ICY metadata

---

## Update

```bash
flatpak update io.github.blacksamdev.Radioo
```

---

## Technical documentation

### Installation without Flatpak

System dependencies: `mpv`, `python-gobject`, `webkitgtk-6.0`, `pipewire`, `wireplumber`

#### Via Makefile (all distros)

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
make deps      # vérifie les dépendances
make install-user  # installe dans ~/.local (sans sudo)
# ou
sudo make install  # installe dans /usr
```

#### Other distributions

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
PYTHONPATH=src python3 -m bbs_radioo.main
```

### Build from source (Flatpak)

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
flatpak-builder --user --install --force-clean build-dir io.github.blacksamdev.Radioo.json
flatpak run io.github.blacksamdev.Radioo
```

### Debug logs

```bash
BBS_RADIOO_DEBUG=1 flatpak run io.github.blacksamdev.Radioo
tail -f ~/.var/app/io.github.blacksamdev.Radioo/data/bbs-radioo/app.log
```

### Console messages (normal)

These messages appear in the terminal but do not indicate any malfunction:

| Message | Cause | Impact |
|---|---|---|
| `libEGL warning: MESA-LOADER...` | WebKit/Mesa on some GPU configurations | None — fallback renderer active |
| `MESA: error: ZINK: failed to choose pdev` | Zink (OpenGL on Vulkan) unavailable | None — WebKit uses software rendering |
| `Socket IPC: timeout` | MPV (.pls, HLS) takes more than 8s to create the IPC socket | None — normal playback, volume via PipeWire |
| `pw-dump: aucun stream MPV` | MPV not yet registered in PipeWire | None — retried automatically |

---

## Architecture

```
WebKitGTK (BBS radiOO interface)
    │
    ├── Station selection
    │        │
    │        ├── RadioBrowser API  →  trending / top stations
    │        ├── SomaFM API        →  ad-free stations
    │        └── Curated           →  reliable hand-picked selection
    │
    └── MPV  →  audio playback (windowless)
              │
              ├── ICY Metadata  →  artist / title in real time
              │
              └── PipeWire  →  volume control via pw-dump + wpctl
```

---

## License

GPL-3.0 — developed by blacksamdev — in tribute to Samuel Bellamy 🏴‍☠️, the Prince of Pirates, captain of the Whydah.
