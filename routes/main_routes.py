from database import SQL_request
from flask import Blueprint, jsonify, request, abort, g, send_file
from functools import wraps
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import check_password_hash, generate_password_hash
import jwt
import datetime
import logging
from mail import send_email
from middleware import setup_middleware, auth_decorator
import config
from utils import *
import io
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification


SECRET_KEY = config.SECRET_KEY
Configuration.account_id = config.SHOP_ID
Configuration.secret_key = config.CASHBOX_ID
logging.info(f"SHOP_ID: {config.SHOP_ID}, CASHBOX_ID: {config.CASHBOX_ID}")

api = Blueprint('api', __name__)


@api.route('/', methods=['GET'])
def example():
    return jsonify({"message": "API Работает"}), 200

@api.route('/images/<type_product>/<id_product>', methods=['GET'])
def images(type_product, id_product):
    try:
        # Запрос к БД
        result = SQL_request(f"SELECT image FROM {type_product} WHERE id = {id_product}", fetch="one")
        
        if not result or not result["image"]:
            return jsonify({"error": "Изображение не найдено"}), 404

        blob_data = result["image"]

        # Оборачиваем BLOB в файлоподобный объект
        image_stream = io.BytesIO(blob_data)

        # Отправляем изображение как WebP
        return send_file(image_stream, mimetype='image/webp'), 200

    except Exception as e:
        print(e)
        return jsonify({"error": "Ошибка при обработке запроса"}), 500
