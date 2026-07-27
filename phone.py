import re

PHONE_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(raw):
    """Приводит номер к формату +7XXXXXXXXXX (РФ). Возвращает None, если номер некорректен."""
    if not raw:
        return None

    digits = PHONE_DIGITS_RE.sub("", str(raw).strip())
    if not digits:
        return None

    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("7"):
        pass
    else:
        return None

    if len(digits) != 11 or not digits.startswith("7"):
        return None

    return f"+{digits}"


def mask_phone(phone):
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) < 8:
        return phone or ""
    return f"{normalized[:2]} *** ***-{normalized[-4:-2]}-{normalized[-2:]}"
