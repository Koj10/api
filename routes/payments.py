from .main_routes import *
from bonus import process_topup_bonus

import requests
import uuid
import json


@api.route('/payments', methods=['POST'])
@auth_decorator()
def payments():
    try:
        data = request.get_json()
        value = data.get('value')
        email = data.get('email')
    
        idempotence_key = str(uuid.uuid4())
    
        user_id = g.user['id']
        
        payment = Payment.create({
            "amount": {
                "value": float(value),
                "currency": "RUB"
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": "https://pc.game-sense.ru/shop"
            },
            "description": "Пополнение баланса",
            "receipt": {
                "customer": {
                    "full_name": "Сорокин Всеволод Максимович",
                    "inn": "744610594170",
                    "email": email
                },
            "items": [
                {
                    "description": "Пополнение лицевого счета",
                    "quantity": 1.00,
                    "amount": {
                        "value": f"{float(value)}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "payment"
                }
            ]
            },
        })
    
        confirmation_url = payment.confirmation.confirmation_url
    
        SQL_request(
                """INSERT INTO payments (
                    user_id, value, payment_id, created_at, status
                ) VALUES (?, ?, ?, ?, ?)""",
                params=(
                    user_id,
                    value,
                    payment.id,
                    payment.created_at,
                    payment.status
                ),
                fetch='none'
            )
    
        return jsonify(confirmation_url), 200
    
    except Exception as e:
        logging.error(e)
        return jsonify({"error": "Возникла ошибка, при создании платежа", "message": str(e)}), 500

@api.route('/payments/status', methods=['POST'])
def payments_status():
    try:
        event_json = json.loads(request.data)

        notification = WebhookNotification(event_json)
        
        payment = notification.object
        
        payment_id = payment.id
        status = payment.status
        
        logging.info(f"Обработка платежа {payment_id} со статусом {status}")
        
        SQL_request(
            "UPDATE payments SET status = ?, captured_at = ? WHERE payment_id = ?", 
            params=(status, datetime.now(), payment_id), 
            fetch='none'
        )

        if status == 'succeeded':
            payment_data = SQL_request(
                "SELECT * FROM payments WHERE payment_id = ?", 
                params=(payment_id,), 
                fetch='one'
            )

            if payment_data:
                user = SQL_request(
                    "SELECT * FROM users WHERE id = ?", 
                    params=(payment_data["user_id"],), 
                    fetch='one'
                )
                
                if user:
                    current_balance = float(user["balance"]) if user["balance"] else 0.0
                    payment_value = float(payment_data["value"]) if payment_data["value"] else 0.0
                    
                    new_balance = current_balance + payment_value
                    if user["role"] == "bonus":
                        new_balance += 150
                        SQL_request("UPDATE users SET role = ? WHERE id = ?", ("user", payment_data["user_id"]), fetch='none')
                    
                    SQL_request(
                        "UPDATE users SET balance = ? WHERE id = ?", 
                        params=(new_balance, payment_data["user_id"]), 
                        fetch='none'
                    )

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
                    SQL_request(
                        """INSERT INTO revenue_transactions (user_id, amount, payment_method, kind)
                           VALUES (?, ?, 'online', 'topup')""",
                        params=(payment_data["user_id"], int(round(payment_value))),
                        fetch="none",
                    )

                    process_topup_bonus(payment_data["user_id"], payment_value)
                    
                    logging.info(f"Баланс пользователя {payment_data['user_id']} пополнен на {payment_value}")
        
        return jsonify({"message": "Статус оплаты обновлён"}), 200
        
    except json.JSONDecodeError as e:
        logging.error(f"Ошибка парсинга JSON: {str(e)}")
        return jsonify({"error": "Неверный формат JSON"}), 400
        
    except Exception as e:
        logging.error(f"Произошла ошибка при обработке вебхука: {str(e)}")
        return jsonify({"error": f"Произошла ошибка при обработке платежа {str(e)}"}), 500