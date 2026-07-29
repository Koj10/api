from flask_cors import CORS

import config


def _cors_origins():
    origins = {
        config.PUBLIC_SITE_URL,
        config.PUBLIC_LANDING_URL,
        config.PUBLIC_API_URL,
        "http://localhost:8100",
        "http://127.0.0.1:8100",
        "https://gamesense-club.ru",
        "https://www.gamesense-club.ru",
        "https://pc.gamesense-club.ru",
        "https://api.gamesense-club.ru",
        "http://localhost:6200",
        "http://127.0.0.1:6200",
    }
    extra = (config.CORS_EXTRA_ORIGINS or "").split(",")
    for item in extra:
        item = item.strip().rstrip("/")
        if item:
            origins.add(item)
    return sorted(origins)


cors = CORS(
    resources={
        r"/*": {
            "origins": _cors_origins(),
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization", "X-API-Key"],
        }
    }
)
