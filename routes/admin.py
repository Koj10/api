import sqlite3
import logging
from urllib.parse import unquote

from database import DB_PATH
from date_format import format_date_dmy, parse_date_dmy
from bonus import process_topup_bonus
from .main_routes import *


@api.route("/profile/<int:user_id>", methods=["GET", "POST"])
@auth_decorator("admin", check_self=False)
def user_profile(user_id):
    if request.method == "GET":
        user = SQL_request(
            "SELECT * FROM users WHERE id = ?", params=(user_id,), fetch="one"
        )
        if not user:
            return jsonify({"error": "Пользователь не найден"}), 404

        return jsonify(user), 200

    elif request.method == "POST":
        data = request.get_json()
        new_balance = data.get("balance")
        SQL_request(
            "UPDATE users SET balance = ? WHERE id = ? ",
            params=(new_balance, user_id),
            fetch="none",
        )
        return jsonify({"message": "Баланс обновлён"}), 200


@api.route("/profile/all", methods=["GET"])
@auth_decorator("admin")
def profiles():
    user = SQL_request(
        "SELECT * FROM users WHERE email_confirmed = 1 ORDER BY id DESC",
        fetch="all",
    )
    return jsonify(user), 200


def _revenue_table_columns():
    try:
        return {
            row["name"]
            for row in (SQL_request("PRAGMA table_info(revenue_transactions)", fetch="all") or [])
        }
    except Exception:
        return set()


def _ensure_revenue_transactions_table():
    """На старых БД таблицы могло не быть — иначе INSERT ломает пополнение."""
    SQL_request(
        """CREATE TABLE IF NOT EXISTS revenue_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        admin_id INTEGER REFERENCES users(id),
        amount INTEGER NOT NULL,
        payment_method TEXT,
        kind TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
        fetch="none",
    )
    columns = _revenue_table_columns()
    migrations = [
        ("admin_id", "ALTER TABLE revenue_transactions ADD COLUMN admin_id INTEGER"),
        ("kind", "ALTER TABLE revenue_transactions ADD COLUMN kind TEXT DEFAULT 'topup'"),
        ("payment_method", "ALTER TABLE revenue_transactions ADD COLUMN payment_method TEXT DEFAULT 'cash'"),
        ("created_at", "ALTER TABLE revenue_transactions ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ]
    for column, ddl in migrations:
        if column not in columns:
            try:
                SQL_request(ddl, fetch="none")
            except Exception:
                pass
            columns = _revenue_table_columns()


def _revenue_kind_clause(columns, alias=""):
    prefix = f"{alias}." if alias else ""
    if "kind" in columns:
        return f"{prefix}kind = 'topup' AND "
    return ""


def _get_transactions(rt_filter, pay_filter, params=()):
    _ensure_revenue_transactions_table()
    columns = _revenue_table_columns()
    admin_join = "LEFT JOIN users a ON a.id = rt.admin_id" if "admin_id" in columns else ""
    admin_select = (
        "a.first_name AS admin_first_name, a.last_name AS admin_last_name"
        if "admin_id" in columns
        else "NULL AS admin_first_name, NULL AS admin_last_name"
    )
    kind_select = "rt.kind" if "kind" in columns else "'topup' AS kind"
    pm_select = "rt.payment_method" if "payment_method" in columns else "'cash' AS payment_method"

    manual = []
    try:
        manual = SQL_request(
            f"""
            SELECT
                rt.id,
                rt.created_at,
                rt.amount,
                {kind_select},
                {pm_select},
                u.first_name,
                u.last_name,
                u.email,
                {admin_select}
            FROM revenue_transactions rt
            LEFT JOIN users u ON u.id = rt.user_id
            {admin_join}
            WHERE {rt_filter}
            ORDER BY datetime(rt.created_at) DESC
            LIMIT 500
            """,
            params=params,
            fetch="all",
        ) or []
    except Exception as e:
        logging.warning("_get_transactions manual: %s", e)

    online = []
    if _payments_table_exists():
        try:
            ts_expr = _payments_timestamp_expr() or "created_at"
            rt_cols = _revenue_table_columns()
            online_exists = (
                "rt.payment_method = 'online'"
                if "payment_method" in rt_cols
                else "1 = 0"
            )
            online = SQL_request(
                f"""
                SELECT
                    p.id,
                    {ts_expr} AS created_at,
                    CAST(p.value AS INTEGER) AS amount,
                    'topup' AS kind,
                    'online' AS payment_method,
                    u.first_name,
                    u.last_name,
                    u.email,
                    NULL AS admin_first_name,
                    NULL AS admin_last_name
                FROM payments p
                LEFT JOIN users u ON u.id = p.user_id
                WHERE p.status = 'succeeded' AND {pay_filter}
                  AND NOT EXISTS (
                    SELECT 1 FROM revenue_transactions rt
                    WHERE rt.user_id = p.user_id
                      AND {online_exists}
                      AND rt.amount = CAST(p.value AS INTEGER)
                      AND date(rt.created_at) = date({ts_expr})
                  )
                ORDER BY datetime({ts_expr}) DESC
                LIMIT 500
                """,
                params=params,
                fetch="all",
            ) or []
        except Exception as e:
            logging.warning("_get_transactions online: %s", e)

    items = list(manual)
    items.extend(online)
    try:
        from .coupons import _get_coupon_transactions
        items.extend(_get_coupon_transactions(rt_filter, params))
    except Exception as e:
        logging.warning("_get_transactions coupons: %s", e)
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def _parse_report_dates():
    raw_from = unquote((request.args.get("date_from") or request.args.get("from") or "").strip())
    raw_to = unquote((request.args.get("date_to") or request.args.get("to") or "").strip())
    if not raw_from and not raw_to:
        return None, None
    if not raw_from or not raw_to:
        return None, None
    try:
        return parse_date_dmy(raw_from), parse_date_dmy(raw_to)
    except ValueError:
        return "invalid", "invalid"


def _report_dates_error(date_from, date_to):
    if date_from == "invalid" or date_to == "invalid":
        return jsonify({"error": "Некорректная дата. Формат: ДД/ММ/ГГГГ"}), 400
    return None


def _revenue_cc_aggregate(date_filter_sql, params=()):
    _ensure_revenue_transactions_table()
    columns = _revenue_table_columns()
    kind_clause = _revenue_kind_clause(columns)
    pm_case = (
        "CASE WHEN payment_method = 'cash' THEN amount ELSE 0 END"
        if "payment_method" in columns
        else "CASE WHEN amount > 0 THEN amount ELSE 0 END"
    )
    card_case = (
        "CASE WHEN payment_method = 'card' THEN amount ELSE 0 END"
        if "payment_method" in columns
        else "0"
    )
    try:
        result = SQL_request(
            f"""
            SELECT
                COALESCE(SUM({pm_case}), 0) AS cash,
                COALESCE(SUM({card_case}), 0) AS card
            FROM revenue_transactions
            WHERE {kind_clause}{date_filter_sql}
            """,
            params=params,
            fetch="one",
        )
        return result or {"cash": 0, "card": 0}
    except Exception as e:
        logging.warning("revenue_cc_aggregate: %s", e)
        return {"cash": 0, "card": 0}


def _revenue_online_aggregate(date_filter_sql, params=()):
    if not _payments_table_exists():
        return {"online": 0}
    expr = _payments_timestamp_expr()
    if not expr:
        return {"online": 0}
    pay_filter = date_filter_sql.replace("created_at", expr)
    try:
        result = SQL_request(
            f"""
            SELECT COALESCE(SUM(value), 0) AS online
            FROM payments
            WHERE status = 'succeeded' AND {pay_filter}
            """,
            params=params,
            fetch="one",
        )
        return result or {"online": 0}
    except Exception as e:
        logging.warning("revenue_online_aggregate: %s", e)
        return {"online": 0}


def _payments_table_exists():
    row = SQL_request(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='payments'",
        fetch="one",
    )
    return bool(row)


def _payments_timestamp_expr():
    if not _payments_table_exists():
        return None
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(payments)", fetch="all") or [])
    }
    if "captured_at" in columns and "created_at" in columns:
        return "COALESCE(captured_at, created_at)"
    if "created_at" in columns:
        return "created_at"
    return None


def _payments_date_filter(date_filter_sql):
    expr = _payments_timestamp_expr()
    if not expr:
        return "1 = 0"
    return date_filter_sql.replace("created_at", expr)


def _merge_topup(cc_row, online_row, coupons=0):
    cc_row = cc_row or {}
    online_row = online_row or {}
    cash = int(cc_row.get("cash") or 0)
    card = int(cc_row.get("card") or 0)
    online = int(online_row.get("online") or 0)
    coupons = int(coupons or 0)
    return {
        "cash": cash,
        "card": card,
        "online": online,
        "coupons": coupons,
        "total": cash + card + online + coupons,
    }


def _period_bounds(period):
    if period == "today":
        return "date(created_at) = date('now')", ()
    if period == "week":
        return "datetime(created_at) >= datetime('now', '-7 days')", ()
    if period == "month":
        return "datetime(created_at) >= datetime('now', '-30 days')", ()
    return None, None


def _range_bounds(date_from, date_to):
    return (
        "date(created_at) >= date(?) AND date(created_at) <= date(?)",
        (date_from, date_to),
    )


def _build_period_summary(rt_filter, params=()):
    try:
        from .coupons import _coupon_aggregate
        coupons = _coupon_aggregate(rt_filter, params)
    except Exception:
        coupons = 0
    return _merge_topup(
        _revenue_cc_aggregate(rt_filter, params),
        _revenue_online_aggregate(rt_filter, params),
        coupons,
    )


@api.route("/admin/balance", methods=["POST"])
@auth_decorator("admin")
def admin_balance_adjust():
    _ensure_revenue_transactions_table()
    data = request.get_json(silent=True) or {}

    raw_uid = data.get("user_id")
    try:
        user_id = int(float(raw_uid)) if raw_uid is not None else None
    except (TypeError, ValueError):
        user_id = None
    if user_id is None:
        return jsonify({"error": "Некорректный пользователь"}), 400

    operation = data.get("operation")
    payment_method = data.get("payment_method")

    if operation not in ("add", "subtract"):
        return jsonify({"error": "operation: add или subtract"}), 400

    if operation == "add" and payment_method not in ("cash", "card"):
        return jsonify(
            {"error": "Укажите способ оплаты: наличные (cash) или безнал (card)"}
        ), 400

    # Режим A (основной для админки): amount — дельта в ₽, баланс только из БД.
    # Режим B: new_balance или balance — целевой баланс (без amount в теле).
    # Важно: не смешивать с полем balance=0 из старых клиентов — иначе ломалась логика.
    amount_delta = None
    target_balance = None

    if data.get("amount") is not None and data.get("amount") != "":
        try:
            amount_delta = int(round(float(data.get("amount"))))
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректная сумма"}), 400
        if amount_delta <= 0:
            return jsonify({"error": "Сумма должна быть больше нуля"}), 400
    else:
        target_raw = data.get("new_balance")
        if target_raw is None:
            target_raw = data.get("balance")
        if target_raw is None:
            return jsonify({"error": "Укажите сумму (поле amount)"}), 400
        try:
            target_balance = int(round(float(target_raw)))
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректная итоговая сумма баланса"}), 400
        if target_balance < 0:
            return jsonify({"error": "Итоговый баланс не может быть отрицательным"}), 400

    new_balance = None
    admin_id = getattr(g, "user", {}).get("id")
    topup_for_bonus = 0
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Пользователь не найден"}), 404

        current = int(round(float(row[0] or 0)))

        if target_balance is not None:
            if operation == "add":
                delta = target_balance - current
                if delta < 1:
                    return jsonify(
                        {
                            "error": "Итоговый баланс должен быть больше текущего минимум на 1 ₽"
                        }
                    ), 400
                new_balance = target_balance
                cur.execute(
                    "UPDATE users SET balance = ? WHERE id = ?",
                    (new_balance, user_id),
                )
                cur.execute(
                    """INSERT INTO revenue_transactions (user_id, admin_id, amount, payment_method, kind)
                       VALUES (?, ?, ?, ?, 'topup')""",
                    (user_id, admin_id, delta, payment_method),
                )
                topup_for_bonus = delta
            else:
                if target_balance > current:
                    return jsonify(
                        {"error": "Итоговый баланс не может быть больше текущего при списании"}
                    ), 400
                new_balance = target_balance
                delta = current - new_balance
                if delta < 1:
                    return jsonify(
                        {"error": "Сумма списания должна быть не менее 1 ₽"}
                    ), 400
                cur.execute(
                    "UPDATE users SET balance = ? WHERE id = ?",
                    (new_balance, user_id),
                )
                cur.execute(
                    """INSERT INTO revenue_transactions (user_id, admin_id, amount, payment_method, kind)
                       VALUES (?, ?, ?, 'none', 'withdraw')""",
                    (user_id, admin_id, delta),
                )
        elif operation == "add":
            new_balance = current + amount_delta
            cur.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user_id),
            )
            cur.execute(
                """INSERT INTO revenue_transactions (user_id, admin_id, amount, payment_method, kind)
                   VALUES (?, ?, ?, ?, 'topup')""",
                (user_id, admin_id, amount_delta, payment_method),
            )
            topup_for_bonus = amount_delta
        else:
            new_balance = max(0, current - amount_delta)
            withdrawn = current - new_balance
            cur.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user_id),
            )
            cur.execute(
                """INSERT INTO revenue_transactions (user_id, admin_id, amount, payment_method, kind)
                   VALUES (?, ?, ?, 'none', 'withdraw')""",
                (user_id, admin_id, withdrawn),
            )
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logging.exception("admin_balance_adjust: %s", e)
        return jsonify({"error": "Ошибка базы данных"}), 500
    finally:
        conn.close()

    if topup_for_bonus > 0:
        process_topup_bonus(user_id, topup_for_bonus)

    return jsonify({"message": "Баланс обновлён", "balance": new_balance}), 200


@api.route("/admin/revenue", methods=["GET"])
@auth_decorator("admin")
def admin_revenue():
    try:
        _ensure_revenue_transactions_table()
        date_from, date_to = _parse_report_dates()
        dates_error = _report_dates_error(date_from, date_to)
        if dates_error:
            return dates_error

        if date_from and date_to:
            rt_filter, rt_params = _range_bounds(date_from, date_to)
            pay_filter = _payments_date_filter(rt_filter)
            custom = _build_period_summary(rt_filter, rt_params)
            return jsonify({
                "custom": custom,
                "date_from": format_date_dmy(date_from),
                "date_to": format_date_dmy(date_to),
            }), 200

        today = _build_period_summary(*_period_bounds("today"))
        week = _build_period_summary(*_period_bounds("week"))
        month = _build_period_summary(*_period_bounds("month"))

        return jsonify({"today": today, "week": week, "month": month}), 200
    except Exception as e:
        logging.exception("admin_revenue: %s", e)
        return jsonify({"error": "Ошибка формирования отчёта"}), 500


@api.route("/admin/revenue/report", methods=["GET"])
@auth_decorator("admin")
def admin_revenue_report():
    try:
        _ensure_revenue_transactions_table()
        date_from, date_to = _parse_report_dates()
        dates_error = _report_dates_error(date_from, date_to)
        if dates_error:
            return dates_error
        period = request.args.get("period", "today")
        bootstrap = request.args.get("bootstrap") in ("1", "true", "yes")

        if date_from and date_to:
            rt_filter, params = _range_bounds(date_from, date_to)
            pay_filter = _payments_date_filter(rt_filter)
            summary = _build_period_summary(rt_filter, params)
            transactions = _get_transactions(rt_filter, pay_filter, params)
            return jsonify(
                {
                    "period": "custom",
                    "date_from": format_date_dmy(date_from),
                    "date_to": format_date_dmy(date_to),
                    "summary": summary,
                    "transactions": transactions,
                }
            ), 200

        if bootstrap:
            today = _build_period_summary(*_period_bounds("today"))
            week = _build_period_summary(*_period_bounds("week"))
            month = _build_period_summary(*_period_bounds("month"))
            rt_filter, params = _period_bounds("today")
            pay_filter = _payments_date_filter(rt_filter)
            transactions = _get_transactions(rt_filter, pay_filter, params)
            return jsonify(
                {
                    "period": "today",
                    "today": today,
                    "week": week,
                    "month": month,
                    "summary": today,
                    "transactions": transactions,
                }
            ), 200

        if period not in ("today", "week", "month"):
            period = "today"

        rt_filter, params = _period_bounds(period)
        pay_filter = _payments_date_filter(rt_filter)
        summary = _build_period_summary(rt_filter, params)
        transactions = _get_transactions(rt_filter, pay_filter, params)
        return jsonify(
            {"period": period, "summary": summary, "transactions": transactions}
        ), 200
    except Exception as e:
        logging.exception("admin_revenue_report: %s", e)
        return jsonify({"error": "Ошибка формирования отчёта"}), 500


@api.route("/admin/revenue/transactions", methods=["GET"])
@auth_decorator("admin")
def admin_revenue_transactions():
    try:
        _ensure_revenue_transactions_table()

        period = request.args.get("period")
        date_from, date_to = _parse_report_dates()
        dates_error = _report_dates_error(date_from, date_to)
        if dates_error:
            return dates_error

        if date_from and date_to:
            rt_filter, params = _range_bounds(date_from, date_to)
        elif period in ("today", "week", "month"):
            rt_filter, params = _period_bounds(period)
        else:
            rt_filter, params = _period_bounds("today")

        pay_filter = _payments_date_filter(rt_filter)
        return jsonify(_get_transactions(rt_filter, pay_filter, params)), 200
    except Exception as e:
        logging.exception("admin_revenue_transactions: %s", e)
        return jsonify({"error": "Ошибка загрузки операций"}), 500


@api.route('/time_packages/add', methods=['POST'])
@auth_decorator('admin')
def add_time_package():
    # Проверяем, пришёл ли запрос с форм-данными
    if 'image' not in request.files:
        return jsonify({"error": "Файл изображения обязателен"}), 400

    file = request.files['image']
    data = request.form

    # Проверка наличия обязательных полей
    required_fields = ['name', 'description', 'duration_minutes', 'price', 'time_period']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Отсутствует обязательное поле: {field}"}), 400

    name = data['name']
    description = data.get('description', '')
    duration_minutes = data['duration_minutes']
    price = data['price']
    price_vip = data.get('price_vip', price)
    time_period = data['time_period'].lower()
    is_weekday = data.get('is_weekday', False)
    is_weekend = data.get('is_weekend', False)
    is_active = data.get('is_active', True)
    image_data = file.read()

    try: duration_minutes = int(duration_minutes)
    except: return jsonify({"error": "duration_minutes должен быть положительным целым числом"}), 400

    try: price = float(price)
    except: jsonify({"error": "price должен быть неотрицательным числом"}), 400
    try: price_vip = float(price_vip)
    except: jsonify({"error": "price_vip должен быть неотрицательным числом"}), 400

    # Проверки значений
    if not isinstance(duration_minutes, int) or duration_minutes <= 0:
        return jsonify({"error": "duration_minutes должен быть положительным целым числом"}), 400

    if not isinstance(price, (int, float)) or price < 0:
        return jsonify({"error": "price должен быть неотрицательным числом"}), 400
    if not isinstance(price_vip, (int, float)) or price_vip < 0:
        return jsonify({"error": "price_vip должен быть неотрицательным числом"}), 400

    if time_period not in ['дневной', 'ночной', 'вечерний', 'бесконечный']:
        return jsonify({"error": "time_period должен быть одним из: 'дневной', 'вечерний', 'ночной', 'бесконечный'"}), 400

    # Формируем SQL-запрос
    query = """
    INSERT INTO time_packages (
        name, 
        description, 
        duration_minutes, 
        price, 
        price_vip,
        time_period,  
        is_weekend, 
        is_active,
        image
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    values = (
        name,
        description,
        duration_minutes,
        price,
        price_vip,
        time_period,
        is_weekend,
        is_active,
        image_data
    )

    try:
        SQL_request(query, values, fetch=None)
        return jsonify({
            "message": "Пакет успешно добавлен"
        }), 201
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500