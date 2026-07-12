from database import *
import logging
import sys
from cashback import credit_purchase_cashback
from logging.handlers import RotatingFileHandler
import random
import string
import secrets
from mail import send_email
import json
from datetime import datetime, timedelta
import secrets
import string
import jwt
from config import SECRET_KEY, JWT_ACCESS_EXPIRES_HOURS, ALLOWED_API_KEYS, DEBUG
from paths import log_path

formatter = logging.Formatter('%(levelname)s [%(asctime)s]   %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler = RotatingFileHandler(
    log_path("api.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setFormatter(formatter)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    logger.addHandler(stream_handler)

def generate_code(length=6):
    return ''.join(random.choices(string.digits, k=length))

def generate_token(length=32):
    return secrets.token_hex(length)

def register_send_code(email):
    code = generate_code()
    SQL_request("""
        INSERT INTO verification_codes (email, code, type)
        VALUES (?, ?, 'register')
    """, params=(email, code), fetch='none')

    sent, mail_error = send_email(
        to_email=email,
        subject="Код подтверждения",
        text_body=f"Ваш код: {code}",
        html_body=f"<p>Ваш код: <strong>{code}</strong></p>"
    )
    if not sent and DEBUG:
        logging.warning(
            "DEBUG: письмо не отправлено (%s), код подтверждения для %s: %s",
            mail_error or "неизвестная ошибка",
            email,
            code,
        )
    return sent

def buy_products(user, product_id, type_product, quality, zone="regular"):
    product = SQL_request(f"SELECT * FROM {type_product} WHERE id = ?", params=(product_id,), fetch='one')
    if int(product['is_active']) == 0:
        return {"error":"Товар не доступен к покупке"}, 403

    unit_price = resolve_package_price(product, zone)
    price = unit_price * int(quality)
    if float(user['balance']) < price:
        return {"error":"Недостаточный баланс"}, 402

    balance = float(user['balance']) - price
    inventory = SQL_request("SELECT inventory FROM users WHERE id = ?", params=(user['id'],), fetch='all')[0]
    inventory = (inventory['inventory'])
    product_id = str(product['id'])
    if inventory.get(type_product) is None:
        inventory[type_product] = {}

    if product_id in inventory[type_product]:
        inventory[type_product][product_id] += int(quality)
    else:
        inventory[type_product][product_id] = quality
    inventory = json.dumps(inventory)
    SQL_request("UPDATE users SET inventory = ?, balance = ? WHERE id = ? ", params=(inventory, balance, user['id']), fetch='none')
    SQL_request(
            """INSERT INTO purchases (
                user_id, product, product_id, quality, price, time_buy
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            params=(
                user['id'],
                type_product,
                product_id,
                quality,
                price
            ),
            fetch='none'
        )

    cashback_earned, cashback_percent = credit_purchase_cashback(user['id'], price)
    fresh = SQL_request(
        "SELECT cashback_balance FROM users WHERE id = ?",
        params=(user['id'],),
        fetch="one",
    )

    return {
        "message": "Оплата прошла успешно",
        "cashback_earned": cashback_earned,
        "cashback_percent": cashback_percent,
        "cashback_balance": int(fresh.get("cashback_balance") or 0) if fresh else 0,
    }, 200


def parse_db_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_session_end_at(computer):
    """Единое время окончания сессии — максимум из всех полей."""
    if not computer:
        return None
    candidates = []
    started = parse_db_datetime(computer.get("session_started_at"))
    duration = computer.get("session_duration_minutes")
    if started is not None and duration is not None:
        candidates.append(started + timedelta(minutes=int(duration)))
    ends_at = parse_db_datetime(computer.get("time_active"))
    if ends_at:
        candidates.append(ends_at)
    return max(candidates) if candidates else None


def has_active_session(computer):
    end = get_session_end_at(computer)
    return end is not None and end > datetime.now()


def session_time_base(computer):
    """Точка отсчёта для продления сессии: только активная неистёкшая сессия."""
    end = get_session_end_at(computer)
    if end is None or end <= datetime.now():
        return None
    return end.strftime("%Y-%m-%d %H:%M:%S")


def normalize_computer_for_client(computer, repair=False):
    if not computer:
        return computer

    computer = dict(computer)
    if not has_active_session(computer):
        return computer

    ends_str = get_session_end_at(computer).strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    params = []

    if computer.get("time_active") != ends_str:
        computer["time_active"] = ends_str
        updates.append("time_active = ?")
        params.append(ends_str)

    if computer.get("status") != "занят":
        computer["status"] = "занят"
        if repair:
            updates.append("status = 'занят'")

    if repair and updates:
        params.append(computer["id"])
        SQL_request(
            f"UPDATE computers SET {', '.join(updates)} WHERE id = ?",
            params=tuple(params),
            fetch="none",
        )

    return computer


def get_computer_zone_by_token(pc_token):
    if not pc_token:
        return "regular"
    computer = SQL_request(
        "SELECT zone FROM computers WHERE token = ?",
        (pc_token,),
        fetch="one",
    )
    if not computer:
        return "regular"
    zone = str(computer.get("zone") or "regular").lower()
    return "vip" if zone == "vip" else "regular"


def resolve_package_price(product, zone="regular"):
    if str(zone).lower() == "vip":
        return float(product.get("price_vip") or product.get("price") or 0)
    return float(product.get("price") or 0)


def add_time_to_datetime(old_time_str, time_delta_str):
    dt = parse_db_datetime(old_time_str) or datetime.now()

    hours, minutes = map(int, time_delta_str.split(':'))
    delta = timedelta(hours=hours, minutes=minutes)
    new_dt = dt + delta

    return new_dt.strftime("%Y-%m-%d %H:%M:%S")

def generate_random_token(length=32):
    characters = string.ascii_letters + string.digits + '-_'
    return ''.join(secrets.choice(characters) for _ in range(length))

def generate_pc_token():
    token = jwt.encode({
            'user_id': 'computer',
            'email': generate_random_token(),
            'role': 'developer'
        }, SECRET_KEY, algorithm="HS256")
    return token