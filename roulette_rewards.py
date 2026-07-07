import json
import random
from pathlib import Path

from database import SQL_request

PRIZES_PATH = Path(__file__).resolve().parent / "data" / "prize38.json"


def load_prize_definitions():
    with PRIZES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def pick_prize_index(prizes=None):
    prizes = prizes or load_prize_definitions()
    return random.randrange(len(prizes))


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
        if not package:
            return None

        inventory = user.get("inventory") or {}
        if isinstance(inventory, str):
            inventory = json.loads(inventory)

        time_packages = inventory.get("time_packages") or {}
        product_id = str(package["id"])
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
