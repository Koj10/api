import secrets
import sqlite3
import logging
from datetime import datetime, timedelta

from database import DB_PATH
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
        status TEXT DEFAULT 'used',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used_at DATETIME,
        used_by_user_id INTEGER
    )""",
        fetch="none",
    )


def _generate_coupon_code():
    stamp = datetime.now().strftime("%y%m%d%H%M%S")
    suffix = secrets.token_hex(2).upper()
    return f"ADM-{stamp}-{suffix}"


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


def _activate_package_on_pc(computer, package, user_id=None, db=None):
    """Запускает сессию: счётчик = длительность пакета с момента активации."""
    duration_minutes = int(package["duration_minutes"])
    started_at = datetime.now()
    ends_at = started_at + timedelta(minutes=duration_minutes)
    started_str = started_at.strftime("%Y-%m-%d %H:%M:%S")
    ends_str = ends_at.strftime("%Y-%m-%d %H:%M:%S")

    query = """
        UPDATE computers
        SET status = 'занят',
            session_started_at = ?,
            session_duration_minutes = ?,
            time_active = ?,
            user_active = ?
        WHERE id = ?
    """
    params = (started_str, duration_minutes, ends_str, user_id, computer["id"])
    if db is not None:
        db.execute(query, params)
        if db.rowcount == 0:
            raise ValueError(f"ПК id={computer['id']} не обновлён")
    else:
        SQL_request(query, params=params, fetch="none")

    return {
        "session_started_at": started_str,
        "session_duration_minutes": duration_minutes,
        "time_active": ends_str,
    }


@api.route("/admin/coupons", methods=["GET"])
@auth_decorator("admin")
def list_coupons():
    _ensure_pc_coupons_table()
    limit = min(int(request.args.get("limit", 50)), 200)
    coupons = SQL_request(
        """
        SELECT
            c.id, c.code, c.amount, c.status, c.created_at, c.used_at,
            comp.number_pc, comp.id AS computer_id, comp.time_active,
            tp.name AS package_name, tp.id AS time_package_id,
            a.first_name AS admin_first_name, a.last_name AS admin_last_name
        FROM pc_coupons c
        LEFT JOIN computers comp ON comp.id = c.computer_id
        LEFT JOIN time_packages tp ON tp.id = c.time_package_id
        LEFT JOIN users a ON a.id = c.admin_id
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
        "SELECT * FROM time_packages WHERE id = ?",
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
        session = _activate_package_on_pc(computer, package, user_id=None, db=cur)
        cur.execute(
            """INSERT INTO pc_coupons
               (code, computer_id, time_package_id, admin_id, amount, status, used_at)
               VALUES (?, ?, ?, ?, ?, 'used', CURRENT_TIMESTAMP)""",
            (code, computer_id, time_package_id, admin_id, amount),
        )
        coupon_id = cur.lastrowid
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logging.exception("create_coupon: %s", e)
        return jsonify({"error": "Ошибка базы данных"}), 500
    except Exception as e:
        conn.rollback()
        logging.exception("create_coupon activate: %s", e)
        return jsonify({"error": "Не удалось активировать пакет на ПК"}), 500
    finally:
        conn.close()

    return jsonify(
        {
            "message": "Пакет активирован на ПК",
            "id": coupon_id,
            "code": code,
            "computer_id": computer_id,
            "number_pc": computer.get("number_pc"),
            "package_name": package.get("name"),
            "amount": amount,
            "session_started_at": session["session_started_at"],
            "session_duration_minutes": session["session_duration_minutes"],
            "time_active": session["time_active"],
        }
    ), 201
