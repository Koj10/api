"""Пути к файлам API относительно каталога api/."""
import os

from dotenv import load_dotenv

API_ROOT = os.path.dirname(os.path.abspath(__file__))
_ENV_LOADED = False


def load_app_env() -> None:
    """Загружает api/.env; переменные из окружения (Docker env_file) имеют приоритет."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_dotenv(os.path.join(API_ROOT, ".env"), override=False)
    _ENV_LOADED = True
LOGS_DIR = os.path.join(API_ROOT, "logs")


def ensure_logs_dir() -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)


def log_path(filename: str) -> str:
    ensure_logs_dir()
    return os.path.join(LOGS_DIR, filename)
