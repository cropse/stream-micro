from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class WindowKind(str, Enum):
    SESSION = "session"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class QuotaWindow:
    kind: WindowKind
    percent_used: float
    reset_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.percent_used <= 100:
            raise ValueError("percent_used must be between 0 and 100")


@dataclass(frozen=True)
class QuotaSnapshot:
    """Ollama usage: the 5-hour and weekly windows, or (newer plans) monthly credits."""

    session: QuotaWindow | None = None
    weekly: QuotaWindow | None = None
    monthly: QuotaWindow | None = None

    def __post_init__(self) -> None:
        if self.monthly is None and (self.session is None or self.weekly is None):
            raise ValueError("a quota snapshot needs session and weekly, or monthly")
