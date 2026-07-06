import secrets
import sqlite3
import logging

from database import DB_PATH
from utils import add_time_to_datetime
from .main_routes import *


def _ensure_pc_coupons_table():
    SQL_request(
        """CREATE TABLE IF NOT EXISTS pc_coupons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        computer_id INTEGER NOT NULL,
        time_package_id INTEGER NOT NULL,
        admin_id INTEGER,
        amount INTEGER NOT NULL DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used_at DATETIME,
        used_by_user_id INTEGER
    )""",
        fetch="none",
    )


def _generate_coupon_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        suffix = "".join(secrets.choice(alphabet) for _ in range(6))
        code = f"GS-{suffix}"
        exists = SQL_request(
            "SELECT id FROM pc_coupons WHERE code = ?", (code,), fetch="one"
        )
        if not exists:
            return code
    return f"GS-{secrets.token_hex(3).upper()}"


def _coupon_date_filter(date_filter_sql):
    return date_filter_sql.replace("created_at", "c.created_at")


def _get_coupon_transactions(date_filter_sql, params=()):
    _ensure_pc_coupons_table()
    coupon_filter = _coupon_date_filter(date_filter_sql)
    try:
        return SQL_request(
            f"""
            SELECT
                c.id,
                c.created_at,
                c.amount,
                'coupon' AS kind,
                'coupon' AS payment_method,
                u.first_name,
                u.last_name,
                u.email,
                a.first_name AS admin_first_name,
                a.last_name AS admin_last_name,
                comp.number_pc,
                tp.name AS package_name,
                c.code,
                c.status
            FROM pc_coupons c
            LEFT JOIN computers comp ON comp.id = c.computer_id
            LEFT JOIN time_packages tp ON tp.id = c.time_package_id
            LEFT JOIN users a ON a.id = c.admin_id
            LEFT JOIN users u ON u.id = c.used_by_user_id
            WHERE {coupon_filter}
            ORDER BY datetime(c.created_at) DESC
            LIMIT 500
            """,
            params=params,
            fetch="all",
        ) or []
    except Exception as e:
        logging.warning("_get_coupon_transactions: %s", e)
        return []


def _coupon_aggregate(date_filter_sql, params=()):
    _ensure_pc_coupons_table()
    coupon_filter = _coupon_date_filter(date_filter_sql)
    try:
        row = SQL_request(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS coupons
            FROM pc_coupons c
            WHERE {coupon_filter}
            """,
            params=params,
            fetch="one",
        )
        return int(row.get("coupons") or 0) if row else 0
    except Exception as e:
        logging.warning("_coupon_aggregate: %s", e)
        return 0


def _activate_package_on_pc(computer, package, user_id, conn=None):
    minutes = int(package["duration_minutes"])
    hours = minutes // 60
    remaining_minutes = minutes % 60
    formatted_time = f"{hours}:{remaining_minutes:02d}"
    time = add_time_to_datetime(computer.get("time_active"), formatted_time)
    query = (
        "UPDATE computers SET status = 'занят', time_active = ?, user_active = ? WHERE id = ?"
    )
    params = (time, user_id, computer["id"])
    if conn:
        conn.execute(query, params)
    else:
        SQL_request(query, params=params, fetch="none")


@api.route("/admin/coupons", methods=["GET"])
@auth_decorator("admin")
def list_coupons():
    _ensure_pc_coupons_table()
    limit = min(int(request.args.get("limit", 50)), 200)
    coupons = SQL_request(
        """
        SELECT
            c.id, c.code, c.amount, c.status, c.created_at, c.used_at,
            comp.number_pc, comp.id AS computer_id,
            tp.name AS package_name, tp.id AS time_package_id,
            a.first_name AS admin_first_name, a.last_name AS admin_last_name,
            u.first_name AS used_first_name, u.last_name AS used_last_name
        FROM pc_coupons c
        LEFT JOIN computers comp ON comp.id = c.computer_id
        LEFT JOIN time_packages tp ON tp.id = c.time_package_id
        LEFT JOIN users a ON a.id = c.admin_id
        LEFT JOIN users u ON u.id = c.used_by_user_id
        ORDER BY datetime(c.created_at) DESC
        LIMIT ?
        """,
        (limit,),
        fetch="all",
    )
    return jsonify(coupons or []), 200


@api.route("/admin/coupons", methods=["POST"])
@auth_decorator("admin")
def create_coupon():
    _ensure_pc_coupons_table()
    data = request.get_json(silent=True) or {}

    try:
        computer_id = int(data.get("computer_id"))
        time_package_id = int(data.get("time_package_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Выберите компьютер и пакет"}), 400

    computer = SQL_request(
        "SELECT * FROM computers WHERE id = ? AND number_pc IS NOT NULL",
        (computer_id,),
        fetch="one",
    )
    if not computer:
        return jsonify({"error": "Компьютер не найден"}), 404

    package = SQL_request(
        "SELECT id, name, price, duration_minutes, is_active FROM time_packages WHERE id = ?",
        (time_package_id,),
        fetch="one",
    )
    if not package:
        return jsonify({"error": "Пакет не найден"}), 404
    if int(package.get("is_active") or 0) == 2:
        return jsonify({"error": "Пакет недоступен"}), 400

    admin_id = getattr(g, "user", {}).get("id")
    code = _generate_coupon_code()
    amount = int(round(float(package.get("price") or 0)))

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO pc_coupons
               (code, computer_id, time_package_id, admin_id, amount, status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (code, computer_id, time_package_id, admin_id, amount),
        )
        coupon_id = cur.lastrowid
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logging.exception("create_coupon: %s", e)
        return jsonify({"error": "Ошибка базы данных"}), 500
    finally:
        conn.close()

    return jsonify(
        {
            "message": "Купон выдан",
            "id": coupon_id,
            "code": code,
            "computer_id": computer_id,
            "number_pc": computer.get("number_pc"),
            "package_name": package.get("name"),
            "amount": amount,
        }
    ), 201


@api.route("/coupons/redeem", methods=["POST"])
@auth_decorator()
def redeem_coupon():
    _ensure_pc_coupons_table()
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip().upper()
    token = data.get("token")

    if not code:
        return jsonify({"error": "Введите код купона"}), 400
    if not token:
        return jsonify({"error": "Активация возможна только с компьютера клуба"}), 400

    coupon = SQL_request(
        "SELECT * FROM pc_coupons WHERE code = ? AND status = 'active'",
        (code,),
        fetch="one",
    )
    if not coupon:
        return jsonify({"error": "Купон не найден или уже использован"}), 404

    computer = SQL_request(
        "SELECT * FROM computers WHERE token = ?", (token,), fetch="one"
    )
    if not computer:
        return jsonify({"error": "Компьютер не найден"}), 404
    if int(computer["id"]) != int(coupon["computer_id"]):
        target = SQL_request(
            "SELECT number_pc FROM computers WHERE id = ?",
            (coupon["computer_id"],),
            fetch="one",
        )
        pc_label = target.get("number_pc") if target else coupon["computer_id"]
        return jsonify({"error": f"Купон действует только на ПК №{pc_label}"}), 403

    package = SQL_request(
        "SELECT * FROM time_packages WHERE id = ?",
        (coupon["time_package_id"],),
        fetch="one",
    )
    if not package:
        return jsonify({"error": "Пакет купона не найден"}), 404

    user_id = g.user["id"]
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        _activate_package_on_pc(computer, package, user_id, conn=conn)
        cur.execute(
            """UPDATE pc_coupons
               SET status = 'used', used_at = CURRENT_TIMESTAMP, used_by_user_id = ?
               WHERE id = ? AND status = 'active'""",
            (user_id, coupon["id"]),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Купон уже использован"}), 409
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.exception("redeem_coupon: %s", e)
        return jsonify({"error": "Не удалось активировать купон"}), 500
    finally:
        conn.close()

    return jsonify(
        {
            "message": "Купон активирован",
            "package": package.get("name"),
            "number_pc": computer.get("number_pc"),
        }
    ), 200
