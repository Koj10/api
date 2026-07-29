import os

from paths import load_app_env

load_app_env()

VERSION = "test"
SECRET_KEY = os.getenv("SECRET_KEY")
JWT_ACCESS_EXPIRES_HOURS = int(os.getenv("JWT_ACCESS_EXPIRES_HOURS", "24"))
DEBUG = os.getenv("DEBUG", "False").lower() in ["true", "1"]
ALLOWED_API_KEYS = [key.strip() for key in os.getenv("ALLOWED_API_KEYS", "").split(",") if key.strip()]
required_env_vars = ["SECRET_KEY"]
SHOP_ID = os.getenv("SHOP_ID")
CASHBOX_ID = os.getenv("CASHBOX_ID")

PUBLIC_LANDING_URL = os.getenv("PUBLIC_LANDING_URL", "https://gamesense-club.ru").rstrip("/")
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "https://pc.gamesense-club.ru").rstrip("/")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "https://api.gamesense-club.ru").rstrip("/")
CORS_EXTRA_ORIGINS = os.getenv("CORS_EXTRA_ORIGINS", "")
