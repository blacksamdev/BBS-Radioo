"""Source radio-browser.info — API REST publique."""

import json
import urllib.request
import urllib.parse


_API_BASE = "https://de1.api.radio-browser.info/json"
_HEADERS  = {"User-Agent": "BBS-radiOO/1.0"}


def _get(path: str, params: dict = None) -> list:
    try:
        url = f"{_API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data if isinstance(data, list) else []
    except Exception:
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
# Sections trending / popular
# Utilise /stations/search qui supporte order, reverse, limit, hidebroken.
# /stations tout seul ne filtre pas correctement.
# ─────────────────────────────

def get_stations_for_section(params: dict) -> list[dict]:
    return _parse_results(_get("/stations/search", params))


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
    """Recherche par genre/tag — utilise tagList pour correspondance exacte."""
    if not tag.strip():
        return []
    return _parse_results(_get("/stations/search", {
        "tagList":    tag.strip().lower(),
        "hidebroken": "true",
        "order":      "votes",
        "reverse":    "true",
        "limit":      str(limit),
    }))
