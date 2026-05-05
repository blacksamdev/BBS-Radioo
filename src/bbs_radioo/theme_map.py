# Thèmes affichés → tags utilisés pour filtrer les sources

THEMES = [
    {
        "id": "lofi",
        "label": "Lo-fi",
        "emoji": "🎧",
        "somafm_tags": ["lofi", "chillout", "chill"],
        "radiobrowser_tags": ["lofi", "chillhop", "chill"],
    },
    {
        "id": "jazz",
        "label": "Jazz",
        "emoji": "🎷",
        "somafm_tags": ["jazz"],
        "radiobrowser_tags": ["jazz"],
    },
    {
        "id": "classical",
        "label": "Classique",
        "emoji": "🎻",
        "somafm_tags": ["classical", "ambient"],
        "radiobrowser_tags": ["classical", "classic"],
    },
    {
        "id": "ambient",
        "label": "Ambiant",
        "emoji": "🌌",
        "somafm_tags": ["ambient", "space", "drone"],
        "radiobrowser_tags": ["ambient", "drone", "space"],
    },
    {
        "id": "electronic",
        "label": "Electronic",
        "emoji": "🎛️",
        "somafm_tags": ["electronic", "electronica", "techno", "house"],
        "radiobrowser_tags": ["electronic", "techno", "house", "edm"],
    },
    {
        "id": "metal",
        "label": "Metal",
        "emoji": "🤘",
        "somafm_tags": ["metal", "rock"],
        "radiobrowser_tags": ["metal", "heavy metal", "rock"],
    },
    {
        "id": "focus",
        "label": "Focus",
        "emoji": "🧠",
        "somafm_tags": ["ambient", "drone", "chillout"],
        "radiobrowser_tags": ["study", "focus", "work", "ambient"],
    },
    {
        "id": "world",
        "label": "World",
        "emoji": "🌍",
        "somafm_tags": ["world", "reggae", "latin"],
        "radiobrowser_tags": ["world", "world music", "latin", "reggae"],
    },
]

THEME_BY_ID = {t["id"]: t for t in THEMES}
