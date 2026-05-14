"""Source radio-browser.info — API REST publique.

Utilise plusieurs serveurs en cascade : si de1 est down,
on essaie nl1, at1, etc. automatiquement.
"""

import json
import urllib.request
import urllib.parse

from bbs_radioo.logging_utils import log_event


# Serveurs RadioBrowser publics — essayés dans l'ordre
_API_SERVERS = [
    "https://de1.api.radio-browser.info/json",
    "https://nl1.api.radio-browser.info/json",
    "https://at1.api.radio-browser.info/json",
    "https://fi1.api.radio-browser.info/json",
]
_HEADERS = {"User-Agent": "BBS-radiOO/1.0"}


def _get(path: str, params: dict = None) -> list:
    """Essaie chaque serveur jusqu'à obtenir une réponse non vide."""
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    for base in _API_SERVERS:
        url = f"{base}{path}{qs}"
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                if isinstance(data, list) and len(data) > 0:
                    log_event(f"RadioBrowser OK: {base} — {len(data)} résultats", level="debug")
                    return data
                # Liste vide → tenter le serveur suivant
                log_event(f"RadioBrowser vide: {base}{path}", level="debug")
        except Exception as e:
            log_event(f"RadioBrowser erreur {base}: {e}", level="debug")
    log_event(f"RadioBrowser: tous les serveurs ont échoué pour {path}", level="debug")
    return []


def _parse_bitrate(value) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _station_to_dict(s: dict) -> dict | None:
    url = s.get("url_resolved") or s.get("url", "")
    if not url:
        return None
    return {
        "id":          f"rb-{s.get('stationuuid', '')}",
        "name":        s.get("name", "").strip(),
        "stream_url":  url,
        "favicon":     s.get("favicon", ""),
        "homepage":    s.get("homepage", ""),
        "description": s.get("tags", ""),
        "tags":        [t.strip() for t in s.get("tags", "").split(",") if t.strip()],
        "bitrate":     _parse_bitrate(s.get("bitrate")),
        "codec":       s.get("codec", "").lower(),
        "votes":       s.get("votes", 0),
        "country":     s.get("country", ""),
        "language":    s.get("language", ""),
        "source":      "radiobrowser",
    }


def _parse_results(raw: list) -> list[dict]:
    seen, results = set(), []
    for s in raw:
        uid = s.get("stationuuid", "")
        if uid in seen:
            continue
        seen.add(uid)
        d = _station_to_dict(s)
        if d:
            results.append(d)
    return results


# ─────────────────────────────
# Sections
# ─────────────────────────────

def get_trending(limit: int = 80) -> list[dict]:
    """Stations les plus cliquées."""
    return _parse_results(_get("/stations", {
        "hidebroken": "true",
        "order":      "clickcount",
        "reverse":    "true",
        "limit":      str(limit),
    }))


def get_popular(limit: int = 80) -> list[dict]:
    """Stations les plus votées."""
    return _parse_results(_get("/stations", {
        "hidebroken": "true",
        "order":      "votes",
        "reverse":    "true",
        "limit":      str(limit),
    }))


# ─────────────────────────────
# Recherche
# ─────────────────────────────

def search_by_name(query: str, limit: int = 40) -> list[dict]:
    if not query.strip():
        return []
    return _parse_results(_get("/stations/search", {
        "name":       query.strip(),
        "hidebroken": "true",
        "order":      "votes",
        "reverse":    "true",
        "limit":      str(limit),
    }))


def search_by_tag(tag: str, limit: int = 60) -> list[dict]:
    """Endpoint dédié /stations/bytag/."""
    if not tag.strip():
        return []
    encoded = urllib.parse.quote(tag.strip().lower())
    return _parse_results(_get(f"/stations/bytag/{encoded}", {
        "hidebroken": "true",
        "order":      "votes",
        "reverse":    "true",
        "limit":      str(limit),
    }))
