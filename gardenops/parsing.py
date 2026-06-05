"""Shared parsing helpers for bool and int coercion."""


def parse_bool(raw: str) -> bool:
    """Parse a string to bool. Empty/falsy strings return False."""
    value = raw.strip().lower()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise ValueError(f"Invalid boolean value: {raw}")


def parse_optional_bool(raw: str) -> bool | None:
    """Parse a string to bool or None if empty."""
    value = raw.strip().lower()
    if value == "":
        return None
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw}")


def parse_bool_flag(raw: object) -> bool:
    """Coerce an arbitrary value to bool (for JSON/form flags)."""
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    return value in {"1", "true", "yes", "on"}
