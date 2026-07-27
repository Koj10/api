from paths import load_app_env

load_app_env()

from flask import Flask, jsonify
from extensions import cors
from routes.main_routes import *
import config
import os
import logging
from utils import *
from config import SECRET_KEY, JWT_ACCESS_EXPIRES_HOURS, ALLOWED_API_KEYS

# Проверка переменных окружения (DB_PATH необязателен — см. database.py)
for var in config.required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"Переменная окружения {var} не задана в .env")


def create_app():
    app = Flask(__name__)

    # Регистрация расширений
    cors.init_app(app)

    @app.errorhandler(500)
    def handle_internal_error(error):
        logging.exception("Internal server error: %s", error)
        return jsonify({"error": "Внутренняя ошибка сервера"}), 500

    # Регистрация blueprint'ов
    app.register_blueprint(api)

    # Настройка конфигов
    app.config["ALLOWED_API_KEYS"] = ALLOWED_API_KEYS
    app.config["SECRET_KEY"] = SECRET_KEY
    setup_middleware(app)

    return app


app = create_app()

if __name__ == "__main__":
    logging.info("Сервер запущен")
    app.run(port=5006, debug=config.DEBUG, host="0.0.0.0")
