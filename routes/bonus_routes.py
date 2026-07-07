from .main_routes import *
from bonus import (
    BONUS_PRIZE_AMOUNTS,
    clear_pending_bonus_claim,
    consume_bonus_card,
    get_pending_bonus_claim,
    roll_bonus_prize,
    set_pending_bonus_claim,
)
import random
import secrets


@api.route("/bonus/prizes", methods=["GET"])
def bonus_prizes():
    return jsonify(
        {
            "prizes": [
                {"amount": amount, "chance": chance}
                for amount, chance in [
                    (100, 45),
                    (150, 30),
                    (200, 18),
                    (300, 6),
                    (500, 1),
                ]
            ]
        }
    ), 200


@api.route("/bonus/open", methods=["POST"])
@auth_decorator()
def bonus_open():
    user = g.user
    pending = get_pending_bonus_claim(user)
    if pending:
        return jsonify(
            {
                "error": "Сначала заберите предыдущий приз",
                "pending_bonus_claim": pending,
            }
        ), 409

    if not consume_bonus_card(user["id"]):
        return jsonify({"error": "Нет бонусных кейсов"}), 400

    prize = roll_bonus_prize()
    claim_id = secrets.token_hex(16)
    win_index = random.randint(36, 44)

    set_pending_bonus_claim(user["id"], claim_id, prize)

    return jsonify(
        {
            "prize": prize,
            "claim_id": claim_id,
            "win_index": win_index,
            "prize_pool": BONUS_PRIZE_AMOUNTS,
        }
    ), 200


@api.route("/bonus/claim", methods=["POST"])
@auth_decorator()
def bonus_claim():
    data = request.get_json(silent=True) or {}
    claim_id = data.get("claim_id")

    if not claim_id:
        return jsonify({"error": "Не указан claim_id"}), 400

    pending = get_pending_bonus_claim(g.user)
    if not pending or pending.get("claim_id") != claim_id:
        return jsonify({"error": "Приз не найден или уже получен"}), 400

    prize = int(pending["amount"])
    current_balance = int(round(float(g.user.get("balance") or 0)))
    new_balance = current_balance + prize

    clear_pending_bonus_claim(g.user["id"])
    SQL_request(
        "UPDATE users SET balance = ? WHERE id = ?",
        params=(new_balance, g.user["id"]),
        fetch="none",
    )

    return jsonify(
        {
            "message": "Приз получен",
            "prize": prize,
            "balance": new_balance,
        }
    ), 200
