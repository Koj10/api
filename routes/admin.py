import sqlite3
import logging

from database import DB_PATH
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


def _ensure_revenue_transactions_table():
    """На старых БД таблицы могло не быть — иначе INSERT ломает пополнение."""
    SQL_request(
        """CREATE TABLE IF NOT EXISTS revenue_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        admin_id INTEGER REFERENCES users(id),
        amount INTEGER NOT NULL,
        payment_method TEXT CHECK(payment_method IN ('cash', 'card', 'online', 'none')),
        kind TEXT CHECK(kind IN ('topup', 'withdraw')) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
        fetch="none",
    )
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(revenue_transactions)", fetch="all") or [])
    }
    if "admin_id" not in columns:
        try:
            SQL_request(
                "ALTER TABLE revenue_transactions ADD COLUMN admin_id INTEGER REFERENCES users(id)",
                fetch="none",
            )
        except Exception:
            pass


def _get_transactions(rt_filter, pay_filter, params=()):
    _ensure_revenue_transactions_table()
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(revenue_transactions)", fetch="all") or [])
    }
    admin_join = "LEFT JOIN users a ON a.id = rt.admin_id" if "admin_id" in columns else ""
    admin_select = (
        "a.first_name AS admin_first_name, a.last_name AS admin_last_name"
        if "admin_id" in columns
        else "NULL AS admin_first_name, NULL AS admin_last_name"
    )

    manual = SQL_request(
        f"""
        SELECT
            rt.id,
            rt.created_at,
            rt.amount,
            rt.kind,
            rt.payment_method,
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

    online = SQL_request(
        f"""
        SELECT
            p.id,
            COALESCE(p.captured_at, p.created_at) AS created_at,
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
              AND rt.payment_method = 'online'
              AND rt.amount = CAST(p.value AS INTEGER)
              AND date(rt.created_at) = date(COALESCE(p.captured_at, p.created_at))
          )
        ORDER BY datetime(COALESCE(p.captured_at, p.created_at)) DESC
        LIMIT 500
        """,
        params=params,
        fetch="all",
    ) or []

    items = list(manual)
    items.extend(online)
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def _parse_report_dates():
    date_from = request.args.get("date_from") or request.args.get("from")
    date_to = request.args.get("date_to") or request.args.get("to")
    return date_from, date_to


def _revenue_cc_aggregate(date_filter_sql, params=()):
    _ensure_revenue_transactions_table()
    return SQL_request(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN payment_method = 'cash' THEN amount ELSE 0 END), 0) AS cash,
            COALESCE(SUM(CASE WHEN payment_method = 'card' THEN amount ELSE 0 END), 0) AS card
        FROM revenue_transactions
        WHERE kind = 'topup' AND {date_filter_sql}
        """,
        params=params,
        fetch="one",
    )


def _revenue_online_aggregate(date_filter_sql, params=()):
    return SQL_request(
        f"""
        SELECT COALESCE(SUM(value), 0) AS online
        FROM payments
        WHERE status = 'succeeded' AND {date_filter_sql}
        """,
        params=params,
        fetch="one",
    )


def _merge_topup(cc_row, online_row):
    cash = int(cc_row.get("cash") or 0)
    card = int(cc_row.get("card") or 0)
    online = int(online_row.get("online") or 0)
    return {
        "cash": cash,
        "card": card,
        "online": online,
        "total": cash + card + online,
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


def _payments_date_filter(date_filter_sql):
    return date_filter_sql.replace("created_at", "COALESCE(captured_at, created_at)")


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

    return jsonify({"message": "Баланс обновлён", "balance": new_balance}), 200


@api.route("/admin/revenue", methods=["GET"])
@auth_decorator("admin")
def admin_revenue():
    try:
        _ensure_revenue_transactions_table()
        date_from, date_to = _parse_report_dates()

        if date_from and date_to:
            rt_filter, rt_params = _range_bounds(date_from, date_to)
            pay_filter = _payments_date_filter(rt_filter)
            custom = _merge_topup(
                _revenue_cc_aggregate(rt_filter, rt_params),
                _revenue_online_aggregate(pay_filter, rt_params),
            )
            return jsonify({"custom": custom, "date_from": date_from, "date_to": date_to}), 200

        today = _merge_topup(
            _revenue_cc_aggregate(*_period_bounds("today")),
            _revenue_online_aggregate(_payments_date_filter(_period_bounds("today")[0])),
        )
        week = _merge_topup(
            _revenue_cc_aggregate(*_period_bounds("week")),
            _revenue_online_aggregate(_payments_date_filter(_period_bounds("week")[0])),
        )
        month = _merge_topup(
            _revenue_cc_aggregate(*_period_bounds("month")),
            _revenue_online_aggregate(_payments_date_filter(_period_bounds("month")[0])),
        )

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
        period = request.args.get("period", "today")
        bootstrap = request.args.get("bootstrap") in ("1", "true", "yes")

        if date_from and date_to:
            rt_filter, params = _range_bounds(date_from, date_to)
            pay_filter = _payments_date_filter(rt_filter)
            summary = _merge_topup(
                _revenue_cc_aggregate(rt_filter, params),
                _revenue_online_aggregate(pay_filter, params),
            )
            transactions = _get_transactions(rt_filter, pay_filter, params)
            return jsonify(
                {
                    "period": "custom",
                    "date_from": date_from,
                    "date_to": date_to,
                    "summary": summary,
                    "transactions": transactions,
                }
            ), 200

        if bootstrap:
            today = _merge_topup(
                _revenue_cc_aggregate(*_period_bounds("today")),
                _revenue_online_aggregate(_payments_date_filter(_period_bounds("today")[0])),
            )
            week = _merge_topup(
                _revenue_cc_aggregate(*_period_bounds("week")),
                _revenue_online_aggregate(_payments_date_filter(_period_bounds("week")[0])),
            )
            month = _merge_topup(
                _revenue_cc_aggregate(*_period_bounds("month")),
                _revenue_online_aggregate(_payments_date_filter(_period_bounds("month")[0])),
            )
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
        summary = _merge_topup(
            _revenue_cc_aggregate(rt_filter, params),
            _revenue_online_aggregate(pay_filter, params),
        )
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
    time_period = data['time_period'].lower()
    is_weekday = data.get('is_weekday', False)
    is_weekend = data.get('is_weekend', False)
    is_active = data.get('is_active', True)
    image_data = file.read()

    try: duration_minutes = int(duration_minutes)
    except: return jsonify({"error": "duration_minutes должен быть положительным целым числом"}), 400

    try: price = float(price)
    except: jsonify({"error": "price должен быть неотрицательным числом"}), 400

    # Проверки значений
    if not isinstance(duration_minutes, int) or duration_minutes <= 0:
        return jsonify({"error": "duration_minutes должен быть положительным целым числом"}), 400

    if not isinstance(price, (int, float)) or price < 0:
        return jsonify({"error": "price должен быть неотрицательным числом"}), 400

    if time_period not in ['дневной', 'ночной', 'вечерний', 'бесконечный']:
        return jsonify({"error": "time_period должен быть одним из: 'дневной', 'вечерний', 'ночной', 'бесконечный'"}), 400

    # Формируем SQL-запрос
    query = """
    INSERT INTO time_packages (
        name, 
        description, 
        duration_minutes, 
        price, 
        time_period,  
        is_weekend, 
        is_active,
        image
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    values = (
        name,
        description,
        duration_minutes,
        price,
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