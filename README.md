# BBS radiOO 📡

🇬🇧 [English version](README.en.md)

Écoutez la radio internet par thèmes — interface native, lecture audio légère via MPV, locale et respectueuse de votre vie privée. Sans publicité avec SomaFM et les stations curatées. Contrôle du volume directement dans PipeWire.

Si le projet vous plaît, une ⭐ GitHub fait vraiment la différence !

---

## Installation rapide (Flatpak)

### 1. Installer MPV

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub io.mpv.Mpv
```

### 2. Ajouter le dépôt BBS radiOO

```bash
flatpak remote-add --if-not-exists --from bbs-radioo \
  https://blacksamdev.github.io/BBS-Radioo/bbs-radioo.flatpakrepo
```

### 3. Installer

```bash
flatpak install bbs-radioo io.github.blacksamdev.Radioo
```

L'application apparaît ensuite dans le menu de votre bureau.

---

## Utilisation

- **Sections** : *En ce moment* (stations les plus actives), *Top stations* (les plus votées), *Sans pub* (SomaFM + curatées)
- **Favoris** : cliquer sur ☆ dans le panneau de détail pour sauvegarder une station
- **Volume** : le slider contrôle directement le flux audio dans PipeWire — visible dans les paramètres système
- **Artiste / Titre** : affiché automatiquement dans le panneau de détail dès que la station envoie les métadonnées ICY

---

## Mise à jour

```bash
flatpak update io.github.blacksamdev.Radioo
```

---

## Documentation technique

### Installation sans Flatpak

Dépendances système : `mpv`, `python-gobject`, `webkitgtk-6.0`, `pipewire`, `wireplumber`

#### Via Makefile (toutes distros)

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
make deps      # vérifie les dépendances
make install-user  # installe dans ~/.local (sans sudo)
# ou
sudo make install  # installe dans /usr
```

#### Autres distributions

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
PYTHONPATH=src python3 -m bbs_radioo.main
```

### Build depuis les sources (Flatpak)

```bash
git clone https://github.com/blacksamdev/BBS-Radioo.git
cd BBS-Radioo
flatpak-builder --user --install --force-clean build-dir io.github.blacksamdev.Radioo.json
flatpak run io.github.blacksamdev.Radioo
```

### Logs de debug

```bash
BBS_RADIOO_DEBUG=1 flatpak run io.github.blacksamdev.Radioo
tail -f ~/.var/app/io.github.blacksamdev.Radioo/data/bbs-radioo/app.log
```

### Messages dans la console (normaux)

Ces messages apparaissent dans le terminal mais n'indiquent aucun dysfonctionnement :

| Message | Cause | Impact |
|---|---|---|
| `libEGL warning: MESA-LOADER...` | WebKit/Mesa sur certaines configurations GPU | Aucun — rendu de secours actif |
| `MESA: error: ZINK: failed to choose pdev` | Zink (OpenGL sur Vulkan) non disponible | Aucun — WebKit utilise le rendu logiciel |
| `Socket IPC: timeout` | MPV (.pls, HLS) met plus de 8s à créer le socket IPC | Aucun — lecture normale, volume via PipeWire |
| `pw-dump: aucun stream MPV` | MPV pas encore enregistré dans PipeWire | Aucun — réessayé automatiquement |

---

## Architecture

```
WebKitGTK (interface BBS radiOO)
    │
    ├── Sélection d'une station
    │        │
    │        ├── RadioBrowser API  →  stations tendances / populaires
    │        ├── SomaFM API        →  stations sans publicité
    │        └── Curated           →  sélection manuelle fiable
    │
    └── MPV  →  lecture audio (sans fenêtre)
              │
              ├── ICY Metadata  →  artiste / titre en temps réel
              │
              └── PipeWire  →  contrôle volume via pw-dump + wpctl
```

---

## Licence

GPL-3.0 — développé par blacksamdev — en hommage à Samuel Bellamy 🏴‍☠️, le Prince des Pirates, capitaine du Whydah.
