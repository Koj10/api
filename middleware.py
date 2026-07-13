from functools import wraps
import jwt
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import json
import os
from flask import request, jsonify, abort, g
from database import SQL_request
from paths import log_path
import config

# === Настройка логгера для аудита ===
audit_logger = logging.getLogger('audit')
audit_logger.setLevel(logging.INFO)

SECRET_KEY = os.getenv("SECRET_KEY")

# Проверяем, существует ли уже обработчик, чтобы не дублировать
if not audit_logger.handlers:
    audit_handler = RotatingFileHandler(
        log_path("audit.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    audit_formatter = logging.Formatter('%(levelname)s [%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    audit_handler.setFormatter(audit_formatter)
    audit_logger.addHandler(audit_handler)


def auth_decorator(role='bonus', check_self=True):
    """
    Универсальный декоратор для аутентификации и авторизации.
    
    Параметры:
        role (str): Требуемая роль ('admin', 'developer' и т.д.)
        check_self (bool): Проверяет, что пользователь работает только со своими данными
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get('Authorization')
            if not auth_header:
                abort(401, description="JWT токен отсутствует")

            try:
                token = auth_header.split(" ")[1]
                payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])

                # Проверка роли
                if role:
                    user_role = payload.get('role', 'user')
                    allowed_roles = {
                        'developer': ['developer'],
                        'admin': ['admin', 'developer'],
                        'user': ['admin', 'developer', 'user'],
                        'bonus': ['admin', 'developer', 'user', 'bonus']
                    }

                    if user_role not in allowed_roles.get(role, []):
                        abort(403, description=f"Нет прав: требуется роль {role}")

                    if payload.get('user_id') == 'computer':
                        computer = SQL_request("SELECT * FROM computers WHERE token = ?", params=(token,), fetch='one')
                        if computer:
                            user_role = f"{user_role} {computer['id']}"
                        else:
                            abort(401, description="Компьютер не найден")

                    else:
                        try:
                            audit_logger.info(
                                f"{payload.get('email', '?')} ({user_role}) вызвал маршрут {request.path} | IP: {request.remote_addr}"
                            )
                        except Exception:
                            pass

                # Получение данных пользователя
                if check_self or role:
                    user_id = payload.get('user_id')
                    if not user_id:
                        abort(401, description="Неверный токен: отсутствует идентификатор пользователя")

                    if user_id == "computer":
                        computer = SQL_request("SELECT * FROM computers WHERE token = ?", params=(token,), fetch='one')
                        g.computer = computer

                    elif user_id == "password":
                        email = payload.get("email")
                        if not email:
                            abort(404, description="Неверный токен: отсутствует почта")
                        user = SQL_request("SELECT * FROM users WHERE email = ?", params=(email,), fetch='one')
                        if not user:
                            abort(404, description="Пользователь не найден")
                        g.user = user

                    else: 
                        user = SQL_request("SELECT * FROM users WHERE id = ?", params=(user_id,), fetch='one')
                        if not user:
                            abort(404, description="Пользователь не найден")
    
                        g.user = user
    
                        # Проверка, что пользователь может редактировать только себя
                        if check_self and 'user_id' in kwargs and str(kwargs['user_id']) != str(user_id):
                            abort(403, description="Вы можете управлять только своими данными")

            except jwt.ExpiredSignatureError:
                abort(401, description="Срок действия токена истёк")
            except jwt.InvalidTokenError:
                abort(401, description="Неверный токен")

            return func(*args, **kwargs)
        return wrapper
    return decorator


# Маршруты без JWT и без X-API-Key
PUBLIC_ROUTES = frozenset({
    "/",
    "/register",
    "/login",
    "/reset-password",
    "/verify-code/send",
    "/verify-code",
    "/pc/register",
    "/time_packages",
    "/payments/status",
})

PUBLIC_ROUTE_PREFIXES = (
    "/images/",
    "/pc/status/",
)

DEBUG_ONLY_ROUTES = frozenset({
    "/mail-check",
    "/smtp-check",
})


def _is_public_route(path, method, debug):
    if method == "OPTIONS":
        return True
    if path in PUBLIC_ROUTES:
        return True
    if debug and path in DEBUG_ONLY_ROUTES:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_ROUTE_PREFIXES)


# === Middleware для автоматической проверки API-ключа и логирования ===
def setup_middleware(app):
    @app.before_request
    def api_key_and_logging_middleware():
        if _is_public_route(request.path, request.method, config.DEBUG):
            return None

        if request.url_rule and request.url_rule.rule.startswith('/images/'):
            return None

        # Проверка на наличие JWT токена
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(" ", 1)[1].strip()
            if not token:
                return jsonify({"error": "JWT токен отсутствует"}), 401
            try:
                decoded = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
                request.user = decoded
                return None
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Срок действия токена истёк"}), 401
            except jwt.InvalidTokenError:
                return jsonify({"error": "Неверный токен"}), 401

        # Если JWT нет — проверяем API ключ (для десктоп-клиента и внутренних сервисов)
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({"error": "API ключ отсутствует"}), 401

        if api_key not in app.config.get('ALLOWED_API_KEYS', []):
            return jsonify({"error": "Неверный API ключ"}), 403

        request._start_time = datetime.now()
        return None

    @app.after_request
    def log_request_info(response):
        if hasattr(request, '_start_time'):
            elapsed = (datetime.now() - request._start_time).total_seconds() * 1000  # в мс
            logging.info(f"{request.remote_addr} {request.method} {request.path} → {response.status} за {int(elapsed)}ms")
        return response