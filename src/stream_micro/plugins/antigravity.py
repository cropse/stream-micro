"""Safe Antigravity CLI cache and configuration primitives."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_VISIBLE_SESSIONS = 15
DEFAULT_AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"
_ALLOWED_KEYS = frozenset({"enabled", "agy_path", "agy_home", "max_sessions"})
_CONVERSATION_ID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class AntigravityError(RuntimeError):
    """Raised when Antigravity CLI configuration or data is invalid."""


@dataclass(frozen=True)
class AntigravityCacheSession:
    id: str
    directory: str
    updated_at: float


def validate_antigravity_config(config: dict[str, object]) -> None:
    unknown = sorted(set(config) - _ALLOWED_KEYS)
    if unknown:
        raise AntigravityError(f"Unknown antigravity setting(s): {', '.join(unknown)}")
    enabled = config.get("enabled")
    if "enabled" in config and not isinstance(enabled, bool):
        raise AntigravityError("antigravity.enabled must be a boolean")
    for name in ("agy_path", "agy_home"):
        value = config.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise AntigravityError(f"antigravity.{name} must be a non-empty string or null")
    maximum = config.get("max_sessions", MAX_VISIBLE_SESSIONS)
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 1 <= maximum <= MAX_VISIBLE_SESSIONS
    ):
        raise AntigravityError(
            f"antigravity.max_sessions must be an integer from 1 to {MAX_VISIBLE_SESSIONS}"
        )


def read_last_conversations(
    path: Path, *, max_sessions: int = MAX_VISIBLE_SESSIONS
) -> tuple[AntigravityCacheSession, ...]:
    """Read only the documented Antigravity last-conversations cache."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, dict):
        return ()

    sessions: list[AntigravityCacheSession] = []
    for directory, conversation_id in raw.items():
        if (
            not isinstance(directory, str)
            or not Path(directory).is_absolute()
            or not Path(directory).is_dir()
            or not isinstance(conversation_id, str)
            or not _CONVERSATION_ID.fullmatch(conversation_id)
        ):
            continue
        sessions.append(AntigravityCacheSession(conversation_id, directory, 0.0))
    sessions.sort(key=lambda session: os.path.normcase(os.path.normpath(session.directory)))
    return tuple(sessions[:max_sessions])
