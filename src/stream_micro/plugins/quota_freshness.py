"""How long quota numbers stay trustworthy after they were read."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone

from ..timeparse import parse_timestamp

QuotaSignals = dict[str, str | float | int | bool | None]

# Numbers older than this are shown as "last seen" (grey) rather than current.
STALE_AFTER_SECONDS = 15 * 60
_WINDOW = re.compile(r"^(\d+(?:\.\d+)?)\s*([HD])$")


def _window_seconds(label: object) -> float | None:
    """Length of a window from its label ("5H", "7D"), when the label says so."""

    if label == "WEEK":
        return 7 * 86400
    match = _WINDOW.match(str(label or "").upper())
    if not match:
        return None
    return float(match.group(1)) * (3600 if match.group(2) == "H" else 86400)


def age_quota_signals(
    signals: Mapping[str, object], read_at: float, now: float, *, stale: bool = False
) -> QuotaSignals:
    """Drop windows that must have reset since ``read_at``; mark old numbers stale.

    A window is dropped (its value is unknown) when its reset time has passed,
    or, without a reset time, when a whole window length has passed since it
    was read.  Nothing is ever shown as refilled.
    """

    shown: QuotaSignals = {key: value for key, value in signals.items()}  # type: ignore[misc]
    for prefix in ("primary", "secondary"):
        reset = parse_timestamp(shown.get(f"{prefix}_reset_at"))
        length = _window_seconds(shown.get(f"{prefix}_label"))
        expired = (
            reset.timestamp() <= now
            if reset is not None
            else length is not None and now - read_at >= length
        )
        if expired and f"{prefix}_percent" in shown:
            for field in ("percent", "label", "reset_at"):
                shown.pop(f"{prefix}_{field}", None)
    if stale or now - read_at >= STALE_AFTER_SECONDS:
        shown["stale"] = True
        shown["seen_at"] = datetime.fromtimestamp(read_at, tz=timezone.utc).isoformat()
    return shown
