import re

from database import SQL_request, ensure_tag_column

TAG_PATTERN = re.compile(r"^[a-z0-9_]{3,20}$")


def normalize_tag(tag):
    return (tag or "").strip().lower()


def validate_tag(tag):
    if not TAG_PATTERN.match(tag):
        return False, "Тег: от 3 до 20 символов, только латиница, цифры и _"
    return True, None


def tag_exists(tag, exclude_user_id=None):
    ensure_tag_column()
    row = SQL_request(
        "SELECT id FROM users WHERE tag = ?",
        params=(tag,),
        fetch="one",
    )
    if not row:
        return False
    if exclude_user_id is not None and int(row["id"]) == int(exclude_user_id):
        return False
    return True


def generate_default_tag(user_id, first_name):
    base = re.sub(r"[^a-z0-9]", "", (first_name or "user").lower())[:12] or "user"
    tag = f"{base}{user_id}"[:20]
    if len(tag) < 3:
        tag = f"user{user_id}"[:20]
    return tag


def assign_tag_if_missing(user_id, first_name):
    ensure_tag_column()
    user = SQL_request(
        "SELECT tag FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user or user.get("tag"):
        return user.get("tag") if user else None

    candidate = generate_default_tag(user_id, first_name)
    suffix = 0
    while tag_exists(candidate, exclude_user_id=user_id):
        suffix += 1
        candidate = f"{generate_default_tag(user_id, first_name)[:16]}{suffix}"[:20]

    SQL_request(
        "UPDATE users SET tag = ? WHERE id = ?",
        params=(candidate, user_id),
        fetch="none",
    )
    return candidate


def get_user_by_tag(tag):
    ensure_tag_column()
    normalized = normalize_tag(tag)
    if not normalized:
        return None
    return SQL_request(
        """
        SELECT id, first_name, last_name, tag
        FROM users
        WHERE tag = ? AND email_confirmed = 1 AND is_active = 1
        """,
        params=(normalized,),
        fetch="one",
    )


def profile_tag_fields(user):
    ensure_tag_column()
    tag = user.get("tag")
    if not tag:
        tag = assign_tag_if_missing(user["id"], user.get("first_name"))
    birthday = user.get("date_of_birth")
    return {
        "tag": tag,
        "date_of_birth": birthday,
        "birthday_locked": bool(birthday),
    }
