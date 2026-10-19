"""Remember the last quota a provider saw, for when its source is unreachable.

The Bridge passes each provider a cache path next to its configuration file
(``<config dir>/.state``), so separate configurations never share numbers.
Each cache also records which source produced it (for example the proxy URL),
and is ignored once that source changes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .quota_freshness import age_quota_signals

QuotaSignals = dict[str, str | float | int | bool | None]


def load_cache(path: Path | None, source: str) -> tuple[dict, float] | None:
    """Return ``(payload, saved_at)``, or None when absent, malformed, or foreign."""

    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source") != source:
        return None
    payload, saved_at = data.get("payload"), data.get("saved_at")
    if (
        not isinstance(payload, dict)
        or not payload
        or isinstance(saved_at, bool)
        or not isinstance(saved_at, (int, float))
        or not math.isfinite(saved_at)
    ):
        return None
    return payload, float(saved_at)


def save_cache(path: Path | None, source: str, payload: dict, saved_at: float) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"source": source, "saved_at": saved_at, "payload": payload}),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass  # The cache only helps while the live source is unreachable.


def stale_signals(signals: QuotaSignals, saved_at: float, now: float) -> QuotaSignals:
    """Last-seen quota from an unreachable source: always marked stale.

    Windows that must have reset since are dropped (unknown), never shown as
    refilled: other clients may have used quota in the meantime.
    """

    return age_quota_signals(signals, saved_at, now, stale=True)


def finite_percent(value: object) -> float | None:
    """A 0-100 percentage, or None for missing, boolean, NaN, or infinite input."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(min(max(value, 0), 100))
