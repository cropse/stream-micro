"""Timestamp parsing shared by providers and key renderers."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware datetime, or return None.

    Accepts a trailing ``Z`` (Python 3.10's ``fromisoformat`` does not) and
    treats naive values as UTC, which is how providers report reset times.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def epoch_to_iso(value: object) -> str | None:
    """Convert positive epoch seconds into an ISO-8601 UTC string."""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
