RANKS = [
    {
        "id": "silver",
        "name": "Silver",
        "min_hours": 0,
        "max_hours": 19,
        "discount": 1,
        "color": "#c0c0c0",
    },
    {
        "id": "gold",
        "name": "Gold",
        "min_hours": 20,
        "max_hours": 49,
        "discount": 3,
        "color": "#d4af37",
    },
    {
        "id": "platinum",
        "name": "Platinum",
        "min_hours": 50,
        "max_hours": 199,
        "discount": 5,
        "color": "#8ec5ff",
    },
    {
        "id": "diamond",
        "name": "Diamond",
        "min_hours": 200,
        "max_hours": 399,
        "discount": 10,
        "color": "#7fe7ff",
    },
    {
        "id": "emerald",
        "name": "Emerald",
        "min_hours": 400,
        "max_hours": None,
        "discount": 15,
        "color": "#50fa7b",
    },
]


def hours_from_minutes(total_minutes):
    return round((total_minutes or 0) / 60, 1)


def get_rank_for_hours(hours):
    current = RANKS[0]
    for rank in RANKS:
        max_hours = rank["max_hours"]
        if hours >= rank["min_hours"] and (max_hours is None or hours <= max_hours):
            current = rank
    return current


def get_next_rank(rank):
    for index, item in enumerate(RANKS):
        if item["id"] == rank["id"] and index + 1 < len(RANKS):
            return RANKS[index + 1]
    return None


def rank_public_fields(rank):
    return {
        "id": rank["id"],
        "name": rank["name"],
        "discount": rank["discount"],
        "color": rank["color"],
        "min_hours": rank["min_hours"],
        "max_hours": rank["max_hours"],
    }


def loyalty_progress(total_minutes):
    hours = (total_minutes or 0) / 60
    current = get_rank_for_hours(hours)
    next_rank = get_next_rank(current)

    if not next_rank:
        return {
            "play_time_minutes": int(total_minutes or 0),
            "play_hours": hours_from_minutes(total_minutes),
            "rank": rank_public_fields(current),
            "next_rank": None,
            "progress_percent": 100,
            "hours_to_next": 0,
            "ranks": [rank_public_fields(rank) for rank in RANKS],
        }

    range_start = current["min_hours"]
    range_end = next_rank["min_hours"]
    span = max(range_end - range_start, 1)
    progress_percent = min(100, max(0, round((hours - range_start) / span * 100, 1)))
    hours_to_next = round(max(0, next_rank["min_hours"] - hours), 1)

    return {
        "play_time_minutes": int(total_minutes or 0),
        "play_hours": hours_from_minutes(total_minutes),
        "rank": rank_public_fields(current),
        "next_rank": rank_public_fields(next_rank),
        "progress_percent": progress_percent,
        "hours_to_next": hours_to_next,
        "ranks": [rank_public_fields(rank) for rank in RANKS],
    }


def loyalty_profile_fields(user):
    return loyalty_progress(user.get("play_time_minutes") or 0)
