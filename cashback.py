import logging

from database import SQL_request
from loyalty_ranks import loyalty_progress


def ensure_cashback_column():
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(users)", fetch="all") or [])
    }
    if "cashback_balance" not in columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN cashback_balance INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )


def cashback_profile_fields(user):
    ensure_cashback_column()
    loyalty = loyalty_progress(user.get("play_time_minutes") or 0)
    return {
        "cashback_balance": int(user.get("cashback_balance") or 0),
        "cashback_percent": loyalty["rank"]["discount"],
    }


def calculate_purchase_cashback(price, user):
    ensure_cashback_column()
    loyalty = loyalty_progress(user.get("play_time_minutes") or 0)
    percent = int(loyalty["rank"]["discount"])
    amount = int(round(float(price) * percent / 100))
    return amount, percent


def credit_purchase_cashback(user_id, price):
    user = SQL_request(
        "SELECT play_time_minutes, cashback_balance FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return 0, 0

    amount, percent = calculate_purchase_cashback(price, user)
    if amount <= 0:
        return 0, percent

    SQL_request(
        """
        UPDATE users
        SET cashback_balance = COALESCE(cashback_balance, 0) + ?
        WHERE id = ?
        """,
        params=(amount, user_id),
        fetch="none",
    )
    return amount, percent


def claim_cashback(user_id):
    ensure_cashback_column()
    user = SQL_request(
        "SELECT balance, cashback_balance FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return None, "Пользователь не найден"

    amount = int(user.get("cashback_balance") or 0)
    if amount <= 0:
        return None, "Нет кешбэка для перевода"

    new_balance = int(user.get("balance") or 0) + amount
    SQL_request(
        """
        UPDATE users
        SET balance = ?, cashback_balance = 0
        WHERE id = ?
        """,
        params=(new_balance, user_id),
        fetch="none",
    )
    return {"claimed": amount, "balance": new_balance, "cashback_balance": 0}, None
