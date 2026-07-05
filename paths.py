"""Пути к файлам API относительно каталога api/."""
import os

API_ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(API_ROOT, "logs")


def ensure_logs_dir() -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)


def log_path(filename: str) -> str:
    ensure_logs_dir()
    return os.path.join(LOGS_DIR, filename)
