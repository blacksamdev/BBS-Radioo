"""Source SomaFM — API publique JSON."""

import json
import urllib.request


SOMAFM_API = "https://api.somafm.com/channels.json"


def _fetch_channels() -> list[dict]:
    try:
        req = urllib.request.Request(
            SOMAFM_API,
            headers={"User-Agent": "BBS-radiOO/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("channels", [])
    except Exception:
        return []


def _best_stream(playlists: list[dict]) -> str | None:
    """Choisit le meilleur stream (AAC > MP3, qualité la plus haute)."""
    if not playlists:
        return None
    order = {"aac": 0, "aacp": 1, "mp3": 2}

    def sort_key(p):
        try:
            quality = -int(p.get("quality") or 0)
        except (ValueError, TypeError):
            quality = 0
        return (order.get(p.get("format", "mp3"), 99), quality)

    sorted_pl = sorted(playlists, key=sort_key)
    return sorted_pl[0].get("url") if sorted_pl else None


def get_stations_for_themes(theme_ids: list[str], theme_map: dict) -> list[dict]:
    """Retourne les stations SomaFM correspondant aux thèmes sélectionnés.
    Si theme_ids est vide, retourne toutes les stations."""
    channels = _fetch_channels()
    if not channels:
        return []

    wanted_tags: set[str] = set()
    for tid in theme_ids:
        theme = theme_map.get(tid, {})
        wanted_tags.update(t.lower() for t in theme.get("somafm_tags", []))

    results = []
    for ch in channels:
        ch_tags = {t.lower() for t in ch.get("tags", "").split(",")}
        ch_genre = ch.get("genre", "").lower()
        all_ch_tags = ch_tags | {ch_genre}

        if theme_ids and not wanted_tags.intersection(all_ch_tags):
            continue

        stream_url = _best_stream(ch.get("playlists", []))
        if not stream_url:
            continue

        results.append({
            "id": f"somafm-{ch.get('id', '')}",
            "name": ch.get("title", ""),
            "stream_url": stream_url,
            "favicon": ch.get("image", "") or ch.get("thumbnail", ""),
            "homepage": ch.get("homePageUrl", ""),
            "description": ch.get("description", ""),
            "tags": [t.strip() for t in ch.get("tags", "").split(",") if t.strip()],
            "listeners": ch.get("listeners", 0),
            "country": "United States",
            "source": "somafm",
        })

    return results
