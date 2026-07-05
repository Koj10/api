import os

VERSION = "test"
SECRET_KEY = os.getenv("SECRET_KEY")
JWT_ACCESS_EXPIRES_HOURS = int(os.getenv("JWT_ACCESS_EXPIRES_HOURS", "24"))
DEBUG = os.getenv("DEBUG", "False").lower() in ["true", "1"]
ALLOWED_API_KEYS = [key.strip() for key in os.getenv("ALLOWED_API_KEYS", "").split(",") if key.strip()]
required_env_vars = ["SECRET_KEY"]
SHOP_ID = os.getenv("SHOP_ID")
CASHBOX_ID = os.getenv("CASHBOX_ID")