"""Stations curatées manuellement — sans pub, fiables."""

CURATED_STATIONS = [
    {
        "id": "lofi-girl",
        "name": "Lofi Girl",
        "stream_url": "https://stream.lofirecords.com/lofirecords_radio.mp3",
        "homepage": "https://lofigirl.com",
        "theme_ids": ["lofi", "focus"],
        "tags": ["lofi", "chillhop", "study"],
        "bitrate": 128,
        "codec": "mp3",
        "source": "curated",
    },
    {
        "id": "france-musique",
        "name": "France Musique",
        "stream_url": "https://icecast.radiofrance.fr/francemusique-hifi.aac",
        "homepage": "https://www.radiofrance.fr/francemusique",
        "theme_ids": ["classical"],
        "tags": ["classical", "opera", "contemporary"],
        "bitrate": 192,
        "codec": "aac",
        "source": "curated",
    },
    {
        "id": "jazz24",
        "name": "Jazz24",
        "stream_url": "https://live.wostreaming.net/manifest/ppm-jazz24aac256-ibc1.m3u8",
        "homepage": "https://www.jazz24.org",
        "theme_ids": ["jazz"],
        "tags": ["jazz", "smooth jazz"],
        "bitrate": 256,
        "codec": "aac",
        "source": "curated",
    },
    {
        "id": "wbgo",
        "name": "WBGO Jazz 88.3",
        "stream_url": "https://wbgo.streamguys1.com/wbgo128",
        "homepage": "https://www.wbgo.org",
        "theme_ids": ["jazz"],
        "tags": ["jazz", "bebop", "blues"],
        "bitrate": 128,
        "codec": "mp3",
        "source": "curated",
    },
    {
        "id": "chillhop",
        "name": "Chillhop Music",
        "stream_url": "https://streams.fluxfm.de/Chillhop/mp3-128/streams.fluxfm.de/",
        "homepage": "https://chillhop.com",
        "theme_ids": ["lofi", "focus"],
        "tags": ["lofi", "chillhop", "hip-hop"],
        "bitrate": 128,
        "codec": "mp3",
        "source": "curated",
    },
]


def get_stations_for_themes(theme_ids: list[str]) -> list[dict]:
    if not theme_ids:
        return CURATED_STATIONS[:]
    return [
        s for s in CURATED_STATIONS
        if any(t in s["theme_ids"] for t in theme_ids)
    ]
