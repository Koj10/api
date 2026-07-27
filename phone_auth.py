from database import SQL_request
from phone import mask_phone, normalize_phone


def resolve_phone_from_request(data, jwt_payload=None):
    phone = normalize_phone((data or {}).get("phone"))
    if phone:
        return phone

    user_id = (jwt_payload or {}).get("user_id")
    if user_id in (None, "computer", "password"):
        return None

    user = SQL_request(
        "SELECT phone_number FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return None
    return normalize_phone(user.get("phone_number"))


def user_by_phone(phone):
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return SQL_request(
        "SELECT * FROM users WHERE phone_number = ?",
        params=(normalized,),
        fetch="one",
    )


def phone_profile_fields(user):
    phone = user.get("phone_number")
    return {
        "phone_number": phone,
        "phone_masked": mask_phone(phone),
        "phone_confirmed": bool(user.get("phone_confirmed")),
    }
