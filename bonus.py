import json
import logging

from database import SQL_request, ensure_topup_bonus_column

TOPUP_BONUS_THRESHOLD = 2000
BONUS_INVENTORY_TYPE = "bonuses"
BONUS_INVENTORY_KEY = "card"


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

    return {
        "topup_bonus_progress": progress,
        "topup_bonus_threshold": threshold,
        "topup_bonus_remaining": remaining,
        "bonus_cards": bonus_cards,
    }


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
