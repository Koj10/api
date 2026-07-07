import json
import logging
import random
import secrets

from database import SQL_request, ensure_topup_bonus_column, ensure_pending_bonus_column

TOPUP_BONUS_THRESHOLD = 2000
BONUS_INVENTORY_TYPE = "bonuses"
BONUS_INVENTORY_KEY = "card"

BONUS_CASE_PRIZES = [
    (100, 45),
    (150, 30),
    (200, 18),
    (300, 6),
    (500, 1),
]
BONUS_PRIZE_AMOUNTS = [amount for amount, _ in BONUS_CASE_PRIZES]


def _normalize_inventory(raw):
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return {}


def bonus_profile_fields(user):
    ensure_topup_bonus_column()
    progress = int(user.get("topup_bonus_progress") or 0)
    threshold = TOPUP_BONUS_THRESHOLD
    remaining = max(0, threshold - progress)

    inventory = _normalize_inventory(user.get("inventory"))
    bonus_cards = int((inventory.get(BONUS_INVENTORY_TYPE) or {}).get(BONUS_INVENTORY_KEY) or 0)
    pending = get_pending_bonus_claim(user)

    fields = {
        "topup_bonus_progress": progress,
        "topup_bonus_threshold": threshold,
        "topup_bonus_remaining": remaining,
        "bonus_cards": bonus_cards,
        "bonus_prize_pool": BONUS_PRIZE_AMOUNTS,
    }
    if pending:
        fields["pending_bonus_claim"] = pending
    return fields


def roll_bonus_prize():
    amounts, weights = zip(*BONUS_CASE_PRIZES)
    return int(random.choices(list(amounts), weights=list(weights), k=1)[0])


def get_bonus_card_count(inventory):
    inventory = _normalize_inventory(inventory)
    return int((inventory.get(BONUS_INVENTORY_TYPE) or {}).get(BONUS_INVENTORY_KEY) or 0)


def consume_bonus_card(user_id):
    user = SQL_request(
        "SELECT inventory FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return False

    inventory = _normalize_inventory(user.get("inventory"))
    count = get_bonus_card_count(inventory)
    if count <= 0:
        return False

    bucket = inventory.setdefault(BONUS_INVENTORY_TYPE, {})
    bucket[BONUS_INVENTORY_KEY] = count - 1
    if bucket[BONUS_INVENTORY_KEY] <= 0:
        bucket.pop(BONUS_INVENTORY_KEY, None)
    if not bucket:
        inventory.pop(BONUS_INVENTORY_TYPE, None)

    SQL_request(
        "UPDATE users SET inventory = ? WHERE id = ?",
        params=(json.dumps(inventory), user_id),
        fetch="none",
    )
    return True


def get_pending_bonus_claim(user):
    ensure_pending_bonus_column()
    raw = user.get("pending_bonus_claim")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("claim_id") and data.get("amount") is not None:
                return {
                    "claim_id": data["claim_id"],
                    "amount": int(data["amount"]),
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


def set_pending_bonus_claim(user_id, claim_id, amount):
    ensure_pending_bonus_column()
    payload = json.dumps({"claim_id": claim_id, "amount": int(amount)})
    SQL_request(
        "UPDATE users SET pending_bonus_claim = ? WHERE id = ?",
        params=(payload, user_id),
        fetch="none",
    )


def clear_pending_bonus_claim(user_id):
    ensure_pending_bonus_column()
    SQL_request(
        "UPDATE users SET pending_bonus_claim = NULL WHERE id = ?",
        params=(user_id,),
        fetch="none",
    )


def process_topup_bonus(user_id, amount):
    """Начисляет карточки бонуса за каждые 2000 ₽ пополнения."""
    topup_amount = int(round(float(amount)))
    if topup_amount <= 0:
        return 0

    ensure_topup_bonus_column()
    user = SQL_request(
        "SELECT inventory, topup_bonus_progress FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return 0

    progress = int(user.get("topup_bonus_progress") or 0) + topup_amount
    inventory = _normalize_inventory(user.get("inventory"))

    bonuses_granted = 0
    while progress >= TOPUP_BONUS_THRESHOLD:
        progress -= TOPUP_BONUS_THRESHOLD
        bonuses_granted += 1
        bucket = inventory.setdefault(BONUS_INVENTORY_TYPE, {})
        bucket[BONUS_INVENTORY_KEY] = int(bucket.get(BONUS_INVENTORY_KEY) or 0) + 1

    SQL_request(
        "UPDATE users SET topup_bonus_progress = ?, inventory = ? WHERE id = ?",
        params=(progress, json.dumps(inventory), user_id),
        fetch="none",
    )

    if bonuses_granted:
        logging.info(
            "Пользователю %s начислено бонусных карточек: %s",
            user_id,
            bonuses_granted,
        )

    return bonuses_granted
