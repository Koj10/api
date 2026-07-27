import logging

from config import DEBUG
from database import SQL_request
from phone import normalize_phone
from sms import send_sms
from utils import generate_code

CODE_TTL_MINUTES = 15


def ensure_phone_verification_schema():
    user_columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(users)", fetch="all") or [])
    }
    if "phone_confirmed" not in user_columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN phone_confirmed INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )
        SQL_request(
            "UPDATE users SET phone_confirmed = email_confirmed WHERE email_confirmed = 1",
            fetch="none",
        )

    code_columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(verification_codes)", fetch="all") or [])
    }
    if "phone" not in code_columns:
        SQL_request(
            "ALTER TABLE verification_codes ADD COLUMN phone VARCHAR(20)",
            fetch="none",
        )

    SQL_request(
        "CREATE INDEX IF NOT EXISTS idx_phone_type ON verification_codes (phone, type)",
        fetch="none",
    )

    indexes = SQL_request("PRAGMA index_list(users)", fetch="all") or []
    index_names = {row["name"] for row in indexes}
    if "idx_users_phone_number" not in index_names:
        try:
            SQL_request(
                "CREATE UNIQUE INDEX idx_users_phone_number ON users (phone_number) "
                "WHERE phone_number IS NOT NULL AND phone_number != ''",
                fetch="none",
            )
        except Exception:
            pass


def send_registration_code(phone):
    normalized = normalize_phone(phone)
    if not normalized:
        return False, "Некорректный номер телефона"

    code = generate_code()
    SQL_request(
        """
        INSERT INTO verification_codes (email, phone, code, type)
        VALUES ('', ?, ?, 'register')
        """,
        params=(normalized, code),
        fetch="none",
    )

    text = f"Код подтверждения GameSense: {code}"
    sent, error = send_sms(normalized, text)
    if not sent and DEBUG:
        logging.warning(
            "DEBUG: SMS не отправлено (%s), код для %s: %s",
            error or "неизвестная ошибка",
            normalized,
            code,
        )
        return True, None
    return sent, error
