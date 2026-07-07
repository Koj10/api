import json
import random
from pathlib import Path

from database import SQL_request, ensure_roulette_pending_column

PRIZES_PATH = Path(__file__).resolve().parent / "data" / "prize38.json"


def load_prize_definitions():
    with PRIZES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def pick_prize_index(prizes=None):
    prizes = prizes or load_prize_definitions()
    return random.randrange(len(prizes))


def parse_pending(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def prize_response(prize_def, prize_index):
    return {
        "prize_index": prize_index,
        "prize_name": prize_def["name"],
        "prize_icon": prize_def.get("icon", ""),
    }


def save_pending_prize(user_id, prize_index, prize_def):
    ensure_roulette_pending_column()
    pending = prize_response(prize_def, prize_index)
    SQL_request(
        "UPDATE users SET roulette_pending = ? WHERE id = ?",
        params=(json.dumps(pending, ensure_ascii=False), user_id),
        fetch="none",
    )
    return pending


def commit_spin(user_id, prize_index, prize_def, new_spin):
    ensure_roulette_pending_column()
    pending = prize_response(prize_def, prize_index)
    SQL_request(
        "UPDATE users SET roulette = ?, roulette_pending = ? WHERE id = ?",
        params=(new_spin, json.dumps(pending, ensure_ascii=False), user_id),
        fetch="none",
    )
    return pending


def clear_pending_prize(user_id):
    ensure_roulette_pending_column()
    SQL_request(
        "UPDATE users SET roulette_pending = NULL WHERE id = ?",
        params=(user_id,),
        fetch="none",
    )


def get_pending_prize(user):
    return parse_pending(user.get("roulette_pending"))


def find_time_package_id(minutes):
    package = SQL_request(
        """
        SELECT id FROM time_packages
        WHERE duration_minutes = ? AND is_active = 1
        ORDER BY id
        LIMIT 1
        """,
        params=(minutes,),
        fetch="one",
    )
    if package:
        return package["id"]

    name_patterns = {
        60: "%1%ЧАС%",
        180: "%3%ЧАС%",
        300: "%5%ЧАС%",
    }
    pattern = name_patterns.get(minutes)
    if not pattern:
        return None

    package = SQL_request(
        """
        SELECT id FROM time_packages
        WHERE UPPER(name) LIKE ? AND is_active = 1
        ORDER BY id
        LIMIT 1
        """,
        params=(pattern,),
        fetch="one",
    )
    return package["id"] if package else None


def grant_roulette_prize(user, prize_def):
    reward_type = prize_def.get("reward_type")
    user_id = user["id"]

    if reward_type == "balance":
        amount = float(prize_def["reward_value"])
        balance = float(user["balance"]) + amount
        SQL_request(
            "UPDATE users SET balance = ? WHERE id = ?",
            params=(balance, user_id),
            fetch="none",
        )
        return {"reward_type": "balance", "amount": amount, "balance": balance}

    if reward_type == "time_package":
        minutes = int(prize_def["reward_minutes"])
        package_id = find_time_package_id(minutes)
        if not package_id:
            return None

        inventory = user.get("inventory") or {}
        if isinstance(inventory, str):
            inventory = json.loads(inventory)

        time_packages = inventory.get("time_packages") or {}
        product_id = str(package_id)
        time_packages[product_id] = int(time_packages.get(product_id, 0)) + 1
        inventory["time_packages"] = time_packages

        SQL_request(
            "UPDATE users SET inventory = ? WHERE id = ?",
            params=(json.dumps(inventory), user_id),
            fetch="none",
        )
        return {
            "reward_type": "time_package",
            "package_id": product_id,
            "minutes": minutes,
            "inventory": inventory,
        }

    return None


def claim_pending_prize(user):
    pending = get_pending_prize(user)
    if not pending:
        return None, "Нет неполученного приза"

    prizes = load_prize_definitions()
    prize_index = int(pending["prize_index"])
    if prize_index < 0 or prize_index >= len(prizes):
        clear_pending_prize(user["id"])
        return None, "Некорректный приз"

    prize_def = prizes[prize_index]
    reward = grant_roulette_prize(user, prize_def)
    if reward is None:
        return None, "Не удалось начислить приз"

    clear_pending_prize(user["id"])
    return {
        **pending,
        "reward": reward,
    }, None
