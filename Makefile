PREFIX     ?= /usr
LIBDIR     ?= $(PREFIX)/lib/bbs-radioo
BINDIR     ?= $(PREFIX)/bin
DATADIR    ?= $(PREFIX)/share
APPDIR     ?= $(DATADIR)/applications
ICONDIR    ?= $(DATADIR)/icons/hicolor/scalable/apps

USER_LIBDIR ?= $(HOME)/.local/lib/bbs-radioo
USER_BINDIR ?= $(HOME)/.local/bin
USER_APPDIR ?= $(HOME)/.local/share/applications
USER_ICONDIR?= $(HOME)/.local/share/icons/hicolor/scalable/apps

SRC        := src/bbs_radioo
SOURCES_SRC:= $(SRC)/sources

.PHONY: all install install-user uninstall uninstall-user deps check

all:
	@echo "Cibles disponibles :"
	@echo "  make deps          — vérifie les dépendances système"
	@echo "  make install-user  — installe dans ~/.local  (sans sudo)"
	@echo "  make install       — installe dans /usr       (sudo requis)"
	@echo "  make uninstall-user"
	@echo "  make uninstall"

# ── Vérification des dépendances ──────────────────────────────────────────────

deps:
	@echo "==> Vérification des dépendances..."
	@which mpv          >/dev/null 2>&1 && echo "  [OK] mpv"          || echo "  [KO] mpv manquant"
	@which wpctl        >/dev/null 2>&1 && echo "  [OK] wpctl"        || echo "  [KO] wireplumber manquant"
	@which pw-dump      >/dev/null 2>&1 && echo "  [OK] pw-dump"      || echo "  [KO] pipewire-utils manquant"
	@python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" \
	                    >/dev/null 2>&1 && echo "  [OK] python-gobject (GTK4)" \
	                                    || echo "  [KO] python-gobject manquant"
	@python3 -c "import gi; gi.require_version('WebKit','6.0'); from gi.repository import WebKit" \
	                    >/dev/null 2>&1 && echo "  [OK] webkitgtk-6.0" \
	                                    || echo "  [KO] webkitgtk-6.0 manquant"
	@echo "==> Terminé."

# ── Installation utilisateur (~/.local) ───────────────────────────────────────

install-user:
	@echo "==> Installation dans $(USER_LIBDIR)..."
	install -dm755 $(USER_LIBDIR)/bbs_radioo/sources
	install -dm755 $(USER_LIBDIR)/bbs_radioo/ui

	# Package Python
	install -Dm644 $(SRC)/__init__.py       $(USER_LIBDIR)/bbs_radioo/__init__.py
	install -Dm644 $(SRC)/app.py            $(USER_LIBDIR)/bbs_radioo/app.py
	install -Dm644 $(SRC)/main.py           $(USER_LIBDIR)/bbs_radioo/main.py
	install -Dm644 $(SRC)/player.py         $(USER_LIBDIR)/bbs_radioo/player.py
	install -Dm644 $(SRC)/updater.py        $(USER_LIBDIR)/bbs_radioo/updater.py
	install -Dm644 $(SRC)/logging_utils.py  $(USER_LIBDIR)/bbs_radioo/logging_utils.py
	install -Dm644 $(SRC)/theme_map.py      $(USER_LIBDIR)/bbs_radioo/theme_map.py
	install -Dm644 $(SRC)/station_store.py  $(USER_LIBDIR)/bbs_radioo/station_store.py

	# Sources
	install -Dm644 $(SOURCES_SRC)/__init__.py    $(USER_LIBDIR)/bbs_radioo/sources/__init__.py
	install -Dm644 $(SOURCES_SRC)/curated.py     $(USER_LIBDIR)/bbs_radioo/sources/curated.py
	install -Dm644 $(SOURCES_SRC)/somafm.py      $(USER_LIBDIR)/bbs_radioo/sources/somafm.py
	install -Dm644 $(SOURCES_SRC)/radiobrowser.py $(USER_LIBDIR)/bbs_radioo/sources/radiobrowser.py

	# Interface HTML
	install -Dm644 $(SRC)/ui/index.html     $(USER_LIBDIR)/bbs_radioo/ui/index.html

	# Script de lancement
	install -dm755 $(USER_BINDIR)
	@printf '#!/bin/bash\nexport PYTHONPATH=$(USER_LIBDIR)\nexec python3 -m bbs_radioo.main "$$@"\n' \
	    > $(USER_BINDIR)/bbs-radioo
	chmod 755 $(USER_BINDIR)/bbs-radioo

	# Fichier .desktop et icône
	install -dm755 $(USER_APPDIR) $(USER_ICONDIR)
	install -Dm644 data/io.github.blacksamdev.Radioo.desktop $(USER_APPDIR)/io.github.blacksamdev.Radioo.desktop
	install -Dm644 data/io.github.blacksamdev.Radioo.svg     $(USER_ICONDIR)/io.github.blacksamdev.Radioo.svg

	@echo "==> Installé. Lance avec : bbs-radioo"
	@echo "    (assure-toi que $(USER_BINDIR) est dans ton PATH)"

# ── Installation système (/usr) ───────────────────────────────────────────────

install:
	@echo "==> Installation dans $(LIBDIR)..."
	install -dm755 $(DESTDIR)$(LIBDIR)/bbs_radioo/sources
	install -dm755 $(DESTDIR)$(LIBDIR)/bbs_radioo/ui

	install -Dm644 $(SRC)/__init__.py       $(DESTDIR)$(LIBDIR)/bbs_radioo/__init__.py
	install -Dm644 $(SRC)/app.py            $(DESTDIR)$(LIBDIR)/bbs_radioo/app.py
	install -Dm644 $(SRC)/main.py           $(DESTDIR)$(LIBDIR)/bbs_radioo/main.py
	install -Dm644 $(SRC)/player.py         $(DESTDIR)$(LIBDIR)/bbs_radioo/player.py
	install -Dm644 $(SRC)/updater.py        $(DESTDIR)$(LIBDIR)/bbs_radioo/updater.py
	install -Dm644 $(SRC)/logging_utils.py  $(DESTDIR)$(LIBDIR)/bbs_radioo/logging_utils.py
	install -Dm644 $(SRC)/theme_map.py      $(DESTDIR)$(LIBDIR)/bbs_radioo/theme_map.py
	install -Dm644 $(SRC)/station_store.py  $(DESTDIR)$(LIBDIR)/bbs_radioo/station_store.py

	install -Dm644 $(SOURCES_SRC)/__init__.py    $(DESTDIR)$(LIBDIR)/bbs_radioo/sources/__init__.py
	install -Dm644 $(SOURCES_SRC)/curated.py     $(DESTDIR)$(LIBDIR)/bbs_radioo/sources/curated.py
	install -Dm644 $(SOURCES_SRC)/somafm.py      $(DESTDIR)$(LIBDIR)/bbs_radioo/sources/somafm.py
	install -Dm644 $(SOURCES_SRC)/radiobrowser.py $(DESTDIR)$(LIBDIR)/bbs_radioo/sources/radiobrowser.py

	install -Dm644 $(SRC)/ui/index.html     $(DESTDIR)$(LIBDIR)/bbs_radioo/ui/index.html

	install -dm755 $(DESTDIR)$(BINDIR)
	@printf '#!/bin/bash\nexport PYTHONPATH=$(LIBDIR)\nexec python3 -m bbs_radioo.main "$$@"\n' \
	    > $(DESTDIR)$(BINDIR)/bbs-radioo
	chmod 755 $(DESTDIR)$(BINDIR)/bbs-radioo

	install -Dm644 data/io.github.blacksamdev.Radioo.desktop \
	    $(DESTDIR)$(APPDIR)/io.github.blacksamdev.Radioo.desktop
	install -Dm644 data/io.github.blacksamdev.Radioo.svg \
	    $(DESTDIR)$(ICONDIR)/io.github.blacksamdev.Radioo.svg

	@echo "==> Installé. Lance avec : bbs-radioo"

# ── Désinstallation ───────────────────────────────────────────────────────────

uninstall-user:
	rm -rf  $(USER_LIBDIR)
	rm -f   $(USER_BINDIR)/bbs-radioo
	rm -f   $(USER_APPDIR)/io.github.blacksamdev.Radioo.desktop
	rm -f   $(USER_ICONDIR)/io.github.blacksamdev.Radioo.svg
	@echo "==> Désinstallé."

uninstall:
	rm -rf  $(DESTDIR)$(LIBDIR)
	rm -f   $(DESTDIR)$(BINDIR)/bbs-radioo
	rm -f   $(DESTDIR)$(APPDIR)/io.github.blacksamdev.Radioo.desktop
	rm -f   $(DESTDIR)$(ICONDIR)/io.github.blacksamdev.Radioo.svg
	@echo "==> Désinstallé."
