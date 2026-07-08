import datetime
import re

DMY_PATTERN = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
ISO_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _valid_date(year, month, day):
    try:
        datetime.date(year, month, day)
        return True
    except ValueError:
        return False


def parse_date_dmy(value):
    """Parse DD/MM/YYYY (also . or -) or YYYY-MM-DD into ISO date string."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty")

    match = DMY_PATTERN.match(raw)
    if match:
        day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if not _valid_date(year, month, day):
            raise ValueError("invalid")
        return f"{year:04d}-{month:02d}-{day:02d}"

    match = ISO_PATTERN.match(raw)
    if match:
        year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if not _valid_date(year, month, day):
            raise ValueError("invalid")
        return f"{year:04d}-{month:02d}-{day:02d}"

    raise ValueError("format")


def format_date_dmy(value):
    """Format ISO or DMY input as DD/MM/YYYY for display."""
    if not value:
        return None
    try:
        iso = parse_date_dmy(value)
    except ValueError:
        return value
    year, month, day = iso.split("-")
    return f"{int(day):02d}/{int(month):02d}/{year}"
