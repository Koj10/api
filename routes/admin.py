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
        amount INTEGER NOT NULL,
        payment_method TEXT CHECK(payment_method IN ('cash', 'card', 'none')),
        kind TEXT CHECK(kind IN ('topup', 'withdraw')) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
        fetch="none",
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
                    """INSERT INTO revenue_transactions (user_id, amount, payment_method, kind)
                       VALUES (?, ?, ?, 'topup')""",
                    (user_id, delta, payment_method),
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
                    """INSERT INTO revenue_transactions (user_id, amount, payment_method, kind)
                       VALUES (?, ?, 'none', 'withdraw')""",
                    (user_id, delta),
                )
        elif operation == "add":
            new_balance = current + amount_delta
            cur.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user_id),
            )
            cur.execute(
                """INSERT INTO revenue_transactions (user_id, amount, payment_method, kind)
                   VALUES (?, ?, ?, 'topup')""",
                (user_id, amount_delta, payment_method),
            )
        else:
            new_balance = max(0, current - amount_delta)
            withdrawn = current - new_balance
            cur.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user_id),
            )
            cur.execute(
                """INSERT INTO revenue_transactions (user_id, amount, payment_method, kind)
                   VALUES (?, ?, 'none', 'withdraw')""",
                (user_id, withdrawn),
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
    pay_today = SQL_request(
        """
        SELECT COALESCE(SUM(value), 0) AS online
        FROM payments
        WHERE status = 'succeeded' AND date(COALESCE(captured_at, created_at)) = date('now')
        """,
        fetch="one",
    )
    pay_week = SQL_request(
        """
        SELECT COALESCE(SUM(value), 0) AS online
        FROM payments
        WHERE status = 'succeeded'
          AND datetime(COALESCE(captured_at, created_at)) >= datetime('now', '-7 days')
        """,
        fetch="one",
    )
    pay_month = SQL_request(
        """
        SELECT COALESCE(SUM(value), 0) AS online
        FROM payments
        WHERE status = 'succeeded'
          AND datetime(COALESCE(captured_at, created_at)) >= datetime('now', '-30 days')
        """,
        fetch="one",
    )

    def merge_topup(base, online_row):
        o = int(online_row.get("online") or 0)
        cc_total = base["cash"] + base["card"]
        return {
            "cash": base["cash"],
            "card": base["card"],
            "online": o,
            "total": cc_total + o,
        }

    cc_today = SQL_request(
        """
        SELECT
            COALESCE(SUM(CASE WHEN payment_method = 'cash' THEN amount ELSE 0 END), 0) AS cash,
            COALESCE(SUM(CASE WHEN payment_method = 'card' THEN amount ELSE 0 END), 0) AS card
        FROM revenue_transactions
        WHERE kind = 'topup' AND date(created_at) = date('now')
        """,
        fetch="one",
    )
    cc_week = SQL_request(
        """
        SELECT
            COALESCE(SUM(CASE WHEN payment_method = 'cash' THEN amount ELSE 0 END), 0) AS cash,
            COALESCE(SUM(CASE WHEN payment_method = 'card' THEN amount ELSE 0 END), 0) AS card
        FROM revenue_transactions
        WHERE kind = 'topup' AND datetime(created_at) >= datetime('now', '-7 days')
        """,
        fetch="one",
    )
    cc_month = SQL_request(
        """
        SELECT
            COALESCE(SUM(CASE WHEN payment_method = 'cash' THEN amount ELSE 0 END), 0) AS cash,
            COALESCE(SUM(CASE WHEN payment_method = 'card' THEN amount ELSE 0 END), 0) AS card
        FROM revenue_transactions
        WHERE kind = 'topup' AND datetime(created_at) >= datetime('now', '-30 days')
        """,
        fetch="one",
    )

    return (
        jsonify(
            {
                "today": merge_topup(
                    {
                        "cash": int(cc_today.get("cash") or 0),
                        "card": int(cc_today.get("card") or 0),
                    },
                    pay_today,
                ),
                "week": merge_topup(
                    {
                        "cash": int(cc_week.get("cash") or 0),
                        "card": int(cc_week.get("card") or 0),
                    },
                    pay_week,
                ),
                "month": merge_topup(
                    {
                        "cash": int(cc_month.get("cash") or 0),
                        "card": int(cc_month.get("card") or 0),
                    },
                    pay_month,
                ),
            }
        ),
        200,
    )


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