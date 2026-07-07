import logging

from database import SQL_request, ensure_topup_bonus_column, ensure_roulette_column

TOPUP_BONUS_THRESHOLD = 2000


def bonus_profile_fields(user):
    ensure_topup_bonus_column()
    ensure_roulette_column()

    progress = int(user.get("topup_bonus_progress") or 0)
    threshold = TOPUP_BONUS_THRESHOLD
    remaining = max(0, threshold - progress)
    roulette_spins = int(user.get("roulette") or 0)

    return {
        "topup_bonus_progress": progress,
        "topup_bonus_threshold": threshold,
        "topup_bonus_remaining": remaining,
        "roulette": roulette_spins,
        "bonus_cards": roulette_spins,
    }


def process_topup_bonus(user_id, amount):
    """Начисляет спин колеса фортуны за каждые 2000 ₽ пополнения."""
    topup_amount = int(round(float(amount)))
    if topup_amount <= 0:
        return 0

    ensure_topup_bonus_column()
    ensure_roulette_column()

    user = SQL_request(
        "SELECT topup_bonus_progress, roulette FROM users WHERE id = ?",
        params=(user_id,),
        fetch="one",
    )
    if not user:
        return 0

    progress = int(user.get("topup_bonus_progress") or 0) + topup_amount
    roulette_spins = int(user.get("roulette") or 0)

    bonuses_granted = 0
    while progress >= TOPUP_BONUS_THRESHOLD:
        progress -= TOPUP_BONUS_THRESHOLD
        bonuses_granted += 1
        roulette_spins += 1

    SQL_request(
        "UPDATE users SET topup_bonus_progress = ?, roulette = ? WHERE id = ?",
        params=(progress, roulette_spins, user_id),
        fetch="none",
    )

    if bonuses_granted:
        logging.info(
            "Пользователю %s начислено спинов колеса фортуны: %s (всего: %s)",
            user_id,
            bonuses_granted,
            roulette_spins,
        )

    return bonuses_granted
