from database import *
import logging
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
from config import SECRET_KEY, JWT_ACCESS_EXPIRES_HOURS, ALLOWED_API_KEYS
from paths import log_path

formatter = logging.Formatter('%(levelname)s [%(asctime)s]   %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler = RotatingFileHandler(
    log_path("api.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

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

    return send_email(
        to_email=email,
        subject="Код подтверждения",
        text_body=f"Ваш код: {code}",
        html_body=f"<p>Ваш код: <strong>{code}</strong></p>"
    )

def buy_products(user, product_id, type_product, quality):
    product = SQL_request(f"SELECT * FROM {type_product} WHERE id = ?", params=(product_id,), fetch='one')
    if int(product['is_active']) == 0:
        return {"error":"Товар не доступен к покупке"}, 403

    price = float(product['price']) * int(quality)
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


def session_time_base(computer):
    """Точка отсчёта для продления сессии: только активная неистёкшая сессия."""
    if computer.get("status") != "занят":
        return None
    dt = parse_db_datetime(computer.get("time_active"))
    if dt is None or dt <= datetime.now():
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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