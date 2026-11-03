"""Funding units shared by exchange adapters. No strategy thresholds live here."""

def parse_interval_hours(value):
    """Compatibility parser for CCXT hours/minutes/seconds/milliseconds."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().lower()
            if not value:
                return None
            if value.endswith("h"):
                hours = float(value[:-1])
                return hours if hours > 0 else None
            if value.endswith("m"):
                minutes = float(value[:-1])
                return minutes / 60 if minutes > 0 else None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number >= 3_600_000:
        return number / 3_600_000
    if number >= 3600:
        return number / 3600
    if number > 24:
        return number / 60
    return number
