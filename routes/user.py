from .main_routes import *
from bonus import bonus_profile_fields, process_topup_bonus
from loyalty_ranks import loyalty_profile_fields
from play_time import ensure_play_time_columns
from user_tags import (
    assign_tag_if_missing,
    normalize_tag,
    profile_tag_fields,
    tag_exists,
    validate_tag,
)
from date_format import parse_date_dmy
import datetime


@api.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    identifier = data.get("identifier")  # Может быть email или телефон
    password = data.get("password")

    if not identifier or not password:
        return jsonify({"error": "Email/телефон и пароль обязательны"}), 400

    # Поиск по email
    user = SQL_request(
        "SELECT * FROM users WHERE email = ?", params=(identifier,), fetch="one"
    )
    if not user and "@" not in identifier:  # Если это не email, попробуем телефон
        user = SQL_request(
            "SELECT * FROM users WHERE phone_number = ?",
            params=(identifier,),
            fetch="one",
        )

    if not user:
        return jsonify({"error": "Пользователь не найден"}), 404

    # Проверяем пароль
    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Неверный пароль"}), 401

    else:
        # Обновляем last_login
        SQL_request(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?",
            params=(user["id"],),
            fetch="none",
        )

        # Генерируем JWT
        token = jwt.encode(
            {
                "user_id": user["id"],
                "email": user["email"],
                "role": user["role"],
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=48),
            },
            SECRET_KEY,
            algorithm="HS256",
        )

        return jsonify({"token": token}), 200


@api.route("/register", methods=["POST"])
def register():
    data = request.get_json()

    required_fields = ["first_name", "last_name", "email", "password"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"error": f"Поле '{field}' обязательно"}), 400

    email = data["email"].strip().lower()
    password = data["password"]

    # Проверяем, существует ли пользователь
    existing_user = SQL_request(
        "SELECT id FROM users WHERE email = ?", params=(email,), fetch="one"
    )
    if existing_user:
        return jsonify({"error": "Пользователь с таким email уже существует"}), 400

    # Хэшируем пароль
    hashed_password = generate_password_hash(password)

    birthday_raw = data.get("date_of_birth")
    birthday_iso = None
    if birthday_raw:
        try:
            birthday_iso = parse_date_dmy(birthday_raw)
        except ValueError:
            return jsonify({"error": "Некорректная дата рождения. Формат: ДД/ММ/ГГГГ"}), 400

    # Подготавливаем данные
    try:
        SQL_request(
            """INSERT INTO users (
                first_name, middle_name, last_name, email, phone_number,
                password_hash, date_of_birth, gender, created_at, cart, inventory, balance, role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), '{}', '{}', 0, "bonus")""",
            params=(
                data.get("first_name"),
                data.get("middle_name"),
                data.get("last_name"),
                email,
                data.get("phone_number"),
                hashed_password,
                birthday_iso,
                data.get("gender", "male"),
            ),
            fetch="none",
        )

        user_id = SQL_request(
            "SELECT id FROM users ORDER BY id DESC LIMIT 1;", fetch="one"
        )["id"]
        assign_tag_if_missing(user_id, data.get("first_name"))

        token = jwt.encode(
            {
                "user_id": user_id,
                "email": email,
                "role": "user",
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=48),
            },
            SECRET_KEY,
            algorithm="HS256",
        )
        return jsonify({"token": token}), 200

    except Exception as e:
        logging.error(f"Ошибка регистрации: {e}")
        return jsonify({"error": "Ошибка регистрации"}), 500


@api.route("/verify-code/send", methods=["POST"])
def send_verify_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ", 1)[1]
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("user_id")
            if user_id not in (None, "computer", "password"):
                user = SQL_request(
                    "SELECT email FROM users WHERE id = ?",
                    params=(user_id,),
                    fetch="one",
                )
                if user:
                    email = user["email"]
        except Exception:
            pass

    if not email or "@" not in email:
        return jsonify({"error": "Некорректный email"}), 400

    user = SQL_request(
        "SELECT id FROM users WHERE email = ?",
        params=(email,),
        fetch="one",
    )
    if not user:
        return jsonify({"error": "Пользователь не найден"}), 404

    if not register_send_code(email):
        return jsonify({"error": "Не удалось отправить письмо. Проверьте настройки почты на сервере"}), 503

    return jsonify({"message": "Код отправлен"}), 200


@api.route("/verify-code", methods=["POST"])
def verify_code():
    data = request.get_json()
    email = data.get("email")
    code = data.get("code")

    if not email or not code:
        return jsonify({"error": "Email и код обязательны"}), 400

    record = SQL_request(
        """
        SELECT * FROM verification_codes
        WHERE email = ? AND code = ? AND type = 'register'
        ORDER BY created_at DESC LIMIT 1
    """,
        params=(email, code),
        fetch="one",
    )

    if not record:
        return jsonify({"error": "Неверный код или истёк срок действия"}), 400

    if record["is_used"]:
        return jsonify({"error": "Этот код уже использован"}), 400

    # Обновляем запись как использованную
    SQL_request(
        """
        UPDATE verification_codes SET is_used = TRUE
        WHERE id = ?
    """,
        params=(record["id"],),
        fetch="none",
    )

    SQL_request(
        """
        UPDATE users SET email_confirmed = TRUE
        WHERE email = ?
    """,
        params=(email,),
        fetch="none",
    )

    return jsonify({"message": "Email подтверждён"}), 200


@api.route("/profile", methods=["GET"])
@auth_decorator()
def profile():
    assign_tag_if_missing(g.user["id"], g.user.get("first_name"))
    ensure_play_time_columns()
    fresh_user = SQL_request(
        "SELECT * FROM users WHERE id = ?",
        params=(g.user["id"],),
        fetch="one",
    )
    return jsonify(
        {
            "id": fresh_user["id"],
            "email": fresh_user["email"],
            "created_at": fresh_user["created_at"],
            "first_name": fresh_user["first_name"],
            "last_name": fresh_user["last_name"],
            "balance": fresh_user["balance"],
            "inventory": fresh_user["inventory"],
            "email_confirmed": fresh_user["email_confirmed"],
            "role": fresh_user["role"],
            "profile_public": bool(fresh_user.get("profile_public")),
            **bonus_profile_fields(fresh_user),
            **profile_tag_fields(fresh_user),
            **loyalty_profile_fields(fresh_user),
        }
    ), 200


@api.route("/profile/settings", methods=["PATCH"])
@auth_decorator()
def update_profile_settings():
    data = request.get_json(silent=True) or {}
    user = SQL_request(
        "SELECT * FROM users WHERE id = ?",
        params=(g.user["id"],),
        fetch="one",
    )
    if not user:
        return jsonify({"error": "Пользователь не найден"}), 404

    updates = []
    params = []

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    if first_name:
        updates.append("first_name = ?")
        params.append(first_name)
    if last_name:
        updates.append("last_name = ?")
        params.append(last_name)

    if "tag" in data:
        tag = normalize_tag(data.get("tag"))
        ok, error = validate_tag(tag)
        if not ok:
            return jsonify({"error": error}), 400
        if tag_exists(tag, exclude_user_id=user["id"]):
            return jsonify({"error": "Этот тег уже занят"}), 400
        updates.append("tag = ?")
        params.append(tag)

    if "date_of_birth" in data:
        if user.get("date_of_birth"):
            return jsonify({"error": "День рождения можно указать только один раз"}), 400
        birthday = (data.get("date_of_birth") or "").strip()
        if not birthday:
            return jsonify({"error": "Укажите дату рождения"}), 400
        try:
            birthday = parse_date_dmy(birthday)
        except ValueError:
            return jsonify({"error": "Некорректная дата рождения. Формат: ДД/ММ/ГГГГ"}), 400
        updates.append("date_of_birth = ?")
        params.append(birthday)

    if "profile_public" in data:
        updates.append("profile_public = ?")
        params.append(1 if data.get("profile_public") else 0)

    if not updates:
        return jsonify({"error": "Нет данных для обновления"}), 400

    params.append(user["id"])
    SQL_request(
        f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
        params=tuple(params),
        fetch="none",
    )

    fresh_user = SQL_request(
        "SELECT * FROM users WHERE id = ?",
        params=(user["id"],),
        fetch="one",
    )
    return jsonify(
        {
            "message": "Профиль обновлён",
            "first_name": fresh_user["first_name"],
            "last_name": fresh_user["last_name"],
            "profile_public": bool(fresh_user.get("profile_public")),
            **profile_tag_fields(fresh_user),
            **loyalty_profile_fields(fresh_user),
        }
    ), 200


@api.route("/activate_product", methods=["POST"])
@auth_decorator()
def activate_product():
    user = g.user
    data = request.get_json()
    id_product = str(data.get("id"))
    type_product = data.get("type")
    quality = data.get("quality")
    token = data.get("token")

    user_id = g.user["id"]

    inventory = SQL_request(
        "SELECT inventory FROM users WHERE id = ?", params=(user["id"],), fetch="one"
    )["inventory"]

    if inventory == {}:
        return jsonify({"error": "Инвентарь пустой"}), 403

    if id_product in inventory[type_product]:
        if int(inventory[type_product][id_product]) >= int(quality):
            inventory[type_product][id_product] -= int(quality)
            inventory = json.dumps(inventory)
            SQL_request(
                "UPDATE users SET inventory = ? WHERE id = ? ",
                params=(inventory, user["id"]),
                fetch="none",
            )
        else:
            return jsonify({"error": "Недостаточное количество"}), 403
    else:
        return jsonify({"error": "У вас нет этого товара"}), 403

    computer = SQL_request(
        "SELECT * FROM computers WHERE token = ?", params=(token,), fetch="one"
    )
    if computer is None:
        return jsonify({"error": "Компьютер для активации, не найден"}), 404
    package = SQL_request(
        f"SELECT * FROM {type_product} WHERE id = ?", (id_product,), fetch="one"
    )

    minutes = int(package["duration_minutes"]) * int(quality)
    hours = minutes // 60
    remaining_minutes = minutes % 60
    formatted_time = f"{hours}:{remaining_minutes:02d}"

    now = datetime.datetime.now()
    started_str = now.strftime("%Y-%m-%d %H:%M:%S")
    base = session_time_base(computer)
    if base:
        time = add_time_to_datetime(base, formatted_time)
        started_at = computer.get("session_started_at") or started_str
        total_duration = int(computer.get("session_duration_minutes") or 0) + minutes
    else:
        time = add_time_to_datetime(started_str, formatted_time)
        started_at = started_str
        total_duration = minutes

    SQL_request(
        """
        UPDATE computers
        SET status = 'занят',
            time_active = ?,
            user_active = ?,
            session_started_at = ?,
            session_duration_minutes = ?
        WHERE token = ?
        """,
        params=(time, user_id, started_at, total_duration, token),
        fetch="none",
    )
    return jsonify({"message": "Успешная активация"}), 200


@api.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email обязателен"}), 400

    user = SQL_request(
        "SELECT email FROM users WHERE email = ?", params=(email,), fetch="one"
    )
    if not user:
        return jsonify({"error": "Email не найден"}), 404

    email = user["email"]
    token = jwt.encode(
        {
            "user_id": "password",
            "email": email,
            "role": "user",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        },
        SECRET_KEY,
        algorithm="HS256",
    )

    sent = send_email(
        to_email=email,
        subject="Восстановление пароля",
        text_body=f"Перейдите по ссылке для восстановления пароля:\nhttps://pc.game-sense.ru/reset-password/{token}",
        html_body=(
            f'<p>Перейдите по <a href="https://pc.game-sense.ru/reset-password/{token}">ссылке</a> '
            f"для восстановления пароля</p>"
            f"<p>https://pc.game-sense.ru/reset-password/{token}</p>"
        ),
    )
    if not sent:
        return jsonify({"error": "Не удалось отправить письмо. Попробуйте позже"}), 503

    return jsonify(
        {"message": "Ссылка для восстановления пароля отправлена на почту"}
    ), 200


@api.route("/new-password", methods=["POST"])
@auth_decorator()
def new_password():
    data = request.get_json()
    password = str(data.get("password"))
    hashed_password = generate_password_hash(password)
    email = g.user["email"]
    try:
        SQL_request(
            "UPDATE users SET password_hash = ? WHERE email = ? ",
            params=(hashed_password, email),
            fetch="none",
        )
        return jsonify({"message": "Пароль изменён"}), 200
    except Exception as e:
        print(e)
        return jsonify({"error": "Не удалось изменить пароль"}), 403


@api.route("/roulette/spin", methods=["GET"])
@auth_decorator()
def roulette_spin():
    user_data = g.user
    user = SQL_request("SELECT * FROM users WHERE id=?", (user_data["id"],), "one")
    if user:
        spin = user.get("roulette") or 0
        activate = False
        if spin > 0:
            activate = True
            spin -= 1
            SQL_request(
                "UPDATE users SET roulette = ? WHERE id = ?",
                params=(spin, user_data["id"]),
                fetch="none",
            )
        return jsonify(
            {
                "spin": activate,
            }
        ), 200
    else:
        return jsonify({"error": "Пользователь не найден"}), 404


@api.route("/roulette", methods=["GET"])
@auth_decorator()
def get_roulette_spins():
    user_data = g.user
    user = SQL_request("SELECT * FROM users WHERE id=?", (user_data["id"],), "one")
    if user:
        spin = user.get("roulette") or 0
        return jsonify(
            {
                "spin": spin,
            }
        ), 200
    else:
        return jsonify({"error": "Пользователь не найден"}), 404


@api.route("/roulette/add", methods=["POST"])
@auth_decorator("admin")
def add_roulette_spins():
    data = request.get_json()
    user_id = data.get("user_id")
    spins_to_add = data.get("spins", 0)

    user = SQL_request("SELECT * FROM users WHERE id=?", (user_id,), "one")
    if not user:
        return jsonify({"error": "Пользователь не найден"}), 404

    spin_count = user.get("roulette") or 0
    new_spin_count = spin_count + spins_to_add
    if new_spin_count <= 0:
        new_spin_count = 0

    try:
        SQL_request(
            "UPDATE users SET roulette = ? WHERE id = ?",
            params=(new_spin_count, user_id),
            fetch="none",
        )
        return jsonify(
            {"message": f"Добавлено {spins_to_add} вращений. Всего: {new_spin_count}"}
        ), 200
    except Exception as e:
        print(e)
        return jsonify({"error": "Не удалось обновить количество вращений"}), 500
