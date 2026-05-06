"""Source radio-browser.info — API REST publique."""

import json
import urllib.request
import urllib.parse


_API_BASE = "https://de1.api.radio-browser.info/json"
_HEADERS = {"User-Agent": "BBS-radiOO/1.0"}
_MAX_PER_TAG = 100


def _get(path: str, params: dict = None) -> list:
    try:
        url = f"{_API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _station_to_dict(s: dict) -> dict | None:
    url = s.get("url_resolved") or s.get("url", "")
    if not url:
        return None
    return {
        "id": f"rb-{s.get('stationuuid', '')}",
        "name": s.get("name", "").strip(),
        "stream_url": url,
        "homepage": s.get("homepage", ""),
        "description": s.get("tags", ""),
        "tags": [t.strip() for t in s.get("tags", "").split(",") if t.strip()],
        "bitrate": s.get("bitrate", 0),
        "codec": s.get("codec", "").lower(),
        "votes": s.get("votes", 0),
        "country": s.get("country", ""),
        "language": s.get("language", ""),
        "source": "radiobrowser",
    }


def get_stations_for_themes(theme_ids: list[str], theme_map: dict) -> list[dict]:
    """Retourne les stations radio-browser pour les thèmes sélectionnés."""
    seen_ids: set[str] = set()
    results = []

    for tid in theme_ids:
        theme = theme_map.get(tid, {})
        for tag in theme.get("radiobrowser_tags", []):
            stations = _get("/stations/bytag/" + urllib.parse.quote(tag), {
                "hidebroken": "true",
                "order": "votes",
                "reverse": "true",
                "limit": str(_MAX_PER_TAG),
                "has_extended_info": "false",
            })
            for s in stations:
                uid = s.get("stationuuid", "")
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                station = _station_to_dict(s)
                if station:
                    results.append(station)

    return results
