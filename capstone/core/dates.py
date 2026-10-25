"""Loss-date parsing. One strict parser: a bad date is an explicit error, never a guess."""

import re
from datetime import date, datetime

OPEN_END = date(9999, 12, 31)


class BadDate(ValueError):
    pass


_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%B %d, %Y")


def parse_date(value):
    """Accept ISO and a few common spellings; raise BadDate otherwise."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value or "").strip())
    if not text:
        raise BadDate("loss date is empty")
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise BadDate(f"unparseable loss date {value!r}; use YYYY-MM-DD")


def parse_end(value):
    return OPEN_END if value in (None, "") else parse_date(value)


def in_range(day, start, end):
    return parse_date(start) <= day <= parse_end(end)
