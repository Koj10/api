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

# Временно: IP вместо домена (landing :6200, site :8100, api :6001)
PUBLIC_LANDING_URL = os.getenv("PUBLIC_LANDING_URL", "http://77.91.100.153:6200").rstrip("/")
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "http://77.91.100.153:8100").rstrip("/")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "http://77.91.100.153:6001").rstrip("/")