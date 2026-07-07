from datetime import datetime

from .main_routes import *


def _friendship_between(user_a, user_b):
    return SQL_request(
        """
        SELECT * FROM friendships
        WHERE (requester_id = ? AND addressee_id = ?)
           OR (requester_id = ? AND addressee_id = ?)
        """,
        (user_a, user_b, user_b, user_a),
        fetch="one",
    )


def _friendship_status_for(me_id, other_id):
    row = _friendship_between(me_id, other_id)
    if not row:
        return "none"
    if row["status"] == "accepted":
        return "accepted"
    if row["requester_id"] == me_id:
        return "pending_outgoing"
    return "pending_incoming"


def _public_user_row(user):
    return {
        "id": user["id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
    }


def _get_active_user(user_id):
    return SQL_request(
        """
        SELECT id, first_name, last_name
        FROM users
        WHERE id = ? AND email_confirmed = 1 AND is_active = 1
        """,
        (user_id,),
        fetch="one",
    )


@api.route("/friends", methods=["GET"])
@auth_decorator()
def list_friends():
    me_id = g.user["id"]
    rows = SQL_request(
        """
        SELECT
            f.id AS friendship_id,
            f.created_at,
            CASE
                WHEN f.requester_id = ? THEN f.addressee_id
                ELSE f.requester_id
            END AS user_id
        FROM friendships f
        WHERE f.status = 'accepted'
          AND (f.requester_id = ? OR f.addressee_id = ?)
        ORDER BY datetime(f.updated_at) DESC
        """,
        (me_id, me_id, me_id),
        fetch="all",
    ) or []

    friends = []
    for row in rows:
        user = _get_active_user(row["user_id"])
        if not user:
            continue
        friends.append({**_public_user_row(user), "friends_since": row["created_at"]})

    return jsonify(friends), 200


@api.route("/friends/requests", methods=["GET"])
@auth_decorator()
def list_friend_requests():
    me_id = g.user["id"]

    incoming_rows = SQL_request(
        """
        SELECT f.id, f.created_at, u.id AS user_id, u.first_name, u.last_name
        FROM friendships f
        JOIN users u ON u.id = f.requester_id
        WHERE f.addressee_id = ? AND f.status = 'pending'
          AND u.email_confirmed = 1 AND u.is_active = 1
        ORDER BY datetime(f.created_at) DESC
        """,
        (me_id,),
        fetch="all",
    ) or []

    outgoing_rows = SQL_request(
        """
        SELECT f.id, f.created_at, u.id AS user_id, u.first_name, u.last_name
        FROM friendships f
        JOIN users u ON u.id = f.addressee_id
        WHERE f.requester_id = ? AND f.status = 'pending'
          AND u.email_confirmed = 1 AND u.is_active = 1
        ORDER BY datetime(f.created_at) DESC
        """,
        (me_id,),
        fetch="all",
    ) or []

    return jsonify(
        {
            "incoming": [
                {
                    "id": row["user_id"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "requested_at": row["created_at"],
                }
                for row in incoming_rows
            ],
            "outgoing": [
                {
                    "id": row["user_id"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "requested_at": row["created_at"],
                }
                for row in outgoing_rows
            ],
        }
    ), 200


@api.route("/users/search", methods=["GET"])
@auth_decorator()
def search_users():
    me_id = g.user["id"]
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify([]), 200

    like = f"%{query}%"
    users = SQL_request(
        """
        SELECT id, first_name, last_name
        FROM users
        WHERE id != ?
          AND email_confirmed = 1
          AND is_active = 1
          AND (
              first_name LIKE ? COLLATE NOCASE
              OR last_name LIKE ? COLLATE NOCASE
              OR (first_name || ' ' || last_name) LIKE ? COLLATE NOCASE
          )
        ORDER BY last_name, first_name
        LIMIT 20
        """,
        (me_id, like, like, like),
        fetch="all",
    ) or []

    result = []
    for user in users:
        result.append(
            {
                **_public_user_row(user),
                "friendship_status": _friendship_status_for(me_id, user["id"]),
            }
        )
    return jsonify(result), 200


@api.route("/friends/request", methods=["POST"])
@auth_decorator()
def send_friend_request():
    data = request.get_json(silent=True) or {}
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите пользователя"}), 400

    me_id = g.user["id"]
    if target_id == me_id:
        return jsonify({"error": "Нельзя добавить себя в друзья"}), 400

    target = _get_active_user(target_id)
    if not target:
        return jsonify({"error": "Пользователь не найден"}), 404

    existing = _friendship_between(me_id, target_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if existing:
        if existing["status"] == "accepted":
            return jsonify({"error": "Пользователь уже в друзьях"}), 400
        if existing["requester_id"] == me_id:
            return jsonify({"error": "Заявка уже отправлена"}), 400
        if existing["requester_id"] == target_id:
            SQL_request(
                """
                UPDATE friendships
                SET status = 'accepted', updated_at = ?
                WHERE id = ?
                """,
                (now, existing["id"]),
                fetch="none",
            )
            return jsonify({"message": "Заявка принята", "status": "accepted"}), 200
        return jsonify({"error": "Не удалось отправить заявку"}), 400

    SQL_request(
        """
        INSERT INTO friendships (requester_id, addressee_id, status, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?)
        """,
        (me_id, target_id, now, now),
        fetch="none",
    )
    return jsonify({"message": "Заявка отправлена", "status": "pending_outgoing"}), 201


@api.route("/friends/accept", methods=["POST"])
@auth_decorator()
def accept_friend_request():
    data = request.get_json(silent=True) or {}
    try:
        requester_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите пользователя"}), 400

    me_id = g.user["id"]
    row = SQL_request(
        """
        SELECT id FROM friendships
        WHERE requester_id = ? AND addressee_id = ? AND status = 'pending'
        """,
        (requester_id, me_id),
        fetch="one",
    )
    if not row:
        return jsonify({"error": "Заявка не найдена"}), 404

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SQL_request(
        "UPDATE friendships SET status = 'accepted', updated_at = ? WHERE id = ?",
        (now, row["id"]),
        fetch="none",
    )
    return jsonify({"message": "Заявка принята"}), 200


@api.route("/friends/decline", methods=["POST"])
@auth_decorator()
def decline_friend_request():
    data = request.get_json(silent=True) or {}
    try:
        requester_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите пользователя"}), 400

    me_id = g.user["id"]
    SQL_request(
        """
        DELETE FROM friendships
        WHERE requester_id = ? AND addressee_id = ? AND status = 'pending'
        """,
        (requester_id, me_id),
        fetch="none",
    )
    return jsonify({"message": "Заявка отклонена"}), 200


@api.route("/friends/<int:user_id>", methods=["DELETE"])
@auth_decorator(check_self=False)
def remove_friend(user_id):
    me_id = g.user["id"]
    if user_id == me_id:
        return jsonify({"error": "Некорректный запрос"}), 400

    deleted = SQL_request(
        """
        DELETE FROM friendships
        WHERE status IN ('accepted', 'pending')
          AND (
              (requester_id = ? AND addressee_id = ?)
              OR (requester_id = ? AND addressee_id = ?)
          )
        """,
        (me_id, user_id, user_id, me_id),
        fetch="none",
    )
    return jsonify({"message": "Удалено из друзей"}), 200
