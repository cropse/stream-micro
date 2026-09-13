"""Safe Antigravity CLI cache and configuration primitives."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from .base import AgentSession, PluginHealth, PluginManifest, PluginSnapshot, SessionState

MAX_VISIBLE_SESSIONS = 15
MAX_CACHE_BYTES = 1024 * 1024
DEFAULT_AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"
_ALLOWED_KEYS = frozenset({"enabled", "agy_path", "agy_home", "max_sessions"})
_CONVERSATION_ID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_STATUS_FIELDS = frozenset(
    {"conversation_id", "cwd", "agent_state", "tool_confirmation_pending", "task_count", "quota"}
)
_ACTIVE_AGENT_STATES = frozenset({"thinking", "working", "tool_use", "initializing"})


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

    if max_sessions <= 0:
        return ()
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ()
    if not isinstance(raw, dict):
        return ()

    sessions: list[AntigravityCacheSession] = []
    ordered_entries = sorted(
        raw.items(),
        key=lambda entry: os.path.normcase(os.path.normpath(entry[0]))
        if isinstance(entry[0], str)
        else "",
    )
    for directory, conversation_id in ordered_entries:
        if (
            not isinstance(directory, str)
            or not Path(directory).is_absolute()
            or not Path(directory).is_dir()
            or not isinstance(conversation_id, str)
            or not _CONVERSATION_ID.fullmatch(conversation_id)
        ):
            continue
        sessions.append(AntigravityCacheSession(conversation_id, directory, 0.0))
        if len(sessions) >= max_sessions:
            break
    return tuple(sessions)


def map_status_payload(
    payload: Mapping[str, object],
) -> tuple[SessionState, dict[str, str | float | int | None]]:
    """Validate the documented status hook payload and derive display signals."""

    _status_session_fields(payload)
    quota = payload.get("quota")
    if not isinstance(quota, Mapping):
        raise AntigravityError("antigravity status quota must be a mapping")

    buckets: list[tuple[str, float, str]] = []
    for bucket_id, raw_bucket in quota.items():
        if not isinstance(bucket_id, str) or not bucket_id:
            raise AntigravityError("antigravity quota bucket IDs must be non-empty strings")
        if not isinstance(raw_bucket, Mapping) or set(raw_bucket) != {
            "remaining_fraction",
            "reset_time",
        }:
            raise AntigravityError("antigravity quota buckets are malformed")
        fraction = raw_bucket.get("remaining_fraction")
        reset_time = raw_bucket.get("reset_time")
        if (
            not isinstance(fraction, (int, float))
            or isinstance(fraction, bool)
            or not isfinite(float(fraction))
            or not 0 <= fraction <= 1
        ):
            raise AntigravityError("antigravity quota remaining_fraction must be from 0 to 1")
        if not isinstance(reset_time, str):
            raise AntigravityError("antigravity quota reset_time must be an ISO date")
        try:
            parsed_reset = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AntigravityError("antigravity quota reset_time must be an ISO date") from exc
        if parsed_reset.tzinfo is None:
            raise AntigravityError("antigravity quota reset_time must include a timezone")
        buckets.append(
            (
                bucket_id,
                (1 - float(fraction)) * 100,
                parsed_reset.astimezone(timezone.utc).isoformat(),
            )
        )

    signals: dict[str, str | float | int | None] = {}
    for prefix, (bucket_id, used_percent, reset_at) in zip(
        ("primary", "secondary"), sorted(buckets), strict=False
    ):
        signals[f"{prefix}_percent"] = used_percent
        signals[f"{prefix}_label"] = _quota_label(bucket_id)
        signals[f"{prefix}_reset_at"] = reset_at

    if payload["tool_confirmation_pending"] is True:
        return SessionState.WAITING, signals
    task_count = payload["task_count"]
    if payload["agent_state"] in _ACTIVE_AGENT_STATES or (
        isinstance(task_count, int) and task_count > 0
    ):
        return SessionState.RUNNING, signals
    return SessionState.IDLE, signals


def resolve_agy_command(agy_path: str) -> list[str]:
    """Resolve Windows command shims without enabling a general-purpose shell."""

    if os.name != "nt":
        return [agy_path]
    requested = Path(agy_path).expanduser()
    if requested.suffix:
        resolved = shutil.which(str(requested)) or (
            str(requested) if requested.exists() else agy_path
        )
    else:
        resolved = (
            shutil.which(f"{agy_path}.cmd")
            or shutil.which(f"{agy_path}.exe")
            or shutil.which(agy_path)
            or agy_path
        )
    suffix = Path(resolved).suffix.lower()
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", resolved]
    if suffix == ".ps1":
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", resolved]
    return [resolved]


def launch_antigravity_session(
    conversation_id: str, directory: str | None, *, agy_path: str = "agy"
) -> None:
    """Launch a new terminal attached to one specific Antigravity conversation."""

    if not isinstance(conversation_id, str) or not _CONVERSATION_ID.fullmatch(conversation_id):
        raise AntigravityError("antigravity conversation ID is invalid")
    cwd = directory if isinstance(directory, str) and Path(directory).is_dir() else None
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [*resolve_agy_command(agy_path), "--conversation", conversation_id],
            cwd=cwd,
            shell=False,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise AntigravityError(f"Could not launch Antigravity: {exc}") from exc


def _status_session_fields(payload: Mapping[str, object]) -> tuple[str, str]:
    if set(payload) != _STATUS_FIELDS:
        raise AntigravityError("antigravity status fields are invalid")
    conversation_id = payload.get("conversation_id")
    directory = payload.get("cwd")
    agent_state = payload.get("agent_state")
    confirmation_pending = payload.get("tool_confirmation_pending")
    task_count = payload.get("task_count")
    if not isinstance(conversation_id, str) or not _CONVERSATION_ID.fullmatch(conversation_id):
        raise AntigravityError("antigravity conversation ID is invalid")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise AntigravityError("antigravity status cwd must be an absolute path")
    if not isinstance(agent_state, str) or agent_state not in _ACTIVE_AGENT_STATES | {"idle"}:
        raise AntigravityError("antigravity status agent_state is invalid")
    if not isinstance(confirmation_pending, bool):
        raise AntigravityError("antigravity status tool_confirmation_pending must be a boolean")
    if (
        not isinstance(task_count, int)
        or isinstance(task_count, bool)
        or task_count < 0
    ):
        raise AntigravityError("antigravity status task_count must be non-negative")
    return conversation_id, directory


def _quota_label(bucket_id: str) -> str:
    return "".join(char for char in bucket_id.upper() if char.isascii())[:12]


class AntigravityPlugin:
    """Store the safe Antigravity provider contract for later lifecycle behavior."""

    manifest = PluginManifest(
        id="antigravity",
        name="Antigravity",
        components=("session", "quota"),
        actions=("select_session",),
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        launcher: Callable[[str, str | None], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.validate_config(config)
        self.config = dict(config)
        self._launcher = launcher
        self._clock = clock
        configured_home = self.config.get("agy_home")
        self.home = (
            Path(configured_home).expanduser()
            if isinstance(configured_home, str)
            else DEFAULT_AGY_HOME
        )
        self.cache_path = self.home / "cache" / "last_conversations.json"
        self.max_sessions = int(self.config.get("max_sessions", MAX_VISIBLE_SESSIONS))
        self._live_sessions: dict[str, AntigravityCacheSession] = {}
        self._live_states: dict[str, SessionState] = {}
        self._signals: dict[str, str | float | int | None] = {}
        self._completed: set[str] = set()

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise AntigravityError("antigravity config must be a mapping")
        validate_antigravity_config(config)

    def handle_status(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        """Store only display-safe state from one validated status update."""

        state, signals = map_status_payload(payload)
        conversation_id, directory = _status_session_fields(payload)
        previous = self._live_states.get(conversation_id)
        if state is SessionState.IDLE and previous in (
            SessionState.RUNNING,
            SessionState.WAITING,
            SessionState.COMPLETED,
        ):
            state = SessionState.COMPLETED
            self._completed.add(conversation_id)
        elif state in (SessionState.RUNNING, SessionState.WAITING):
            self._completed.discard(conversation_id)
        self._live_sessions[conversation_id] = AntigravityCacheSession(
            conversation_id, directory, self._clock()
        )
        self._live_states[conversation_id] = state
        self._signals = signals
        self._evict_live_sessions()
        return {"result": "status_updated", "session_id": conversation_id}

    def _evict_live_sessions(self) -> None:
        while len(self._live_sessions) > self.max_sessions:
            evicted_id = min(
                self._live_sessions,
                key=lambda session_id: (
                    self._live_sessions[session_id].updated_at,
                    session_id,
                ),
            )
            self._live_sessions.pop(evicted_id, None)
            self._live_states.pop(evicted_id, None)
            self._completed.discard(evicted_id)

    def snapshot(self) -> PluginSnapshot:
        cache_records = read_last_conversations(self.cache_path, max_sessions=self.max_sessions)
        records = {record.id: record for record in cache_records}
        records.update(self._live_sessions)
        sessions = []
        for record in sorted(records.values(), key=lambda item: item.updated_at, reverse=True):
            state = self._live_states.get(record.id, SessionState.IDLE)
            sessions.append(
                AgentSession(
                    record.id,
                    Path(record.directory.rstrip("/\\")).name or record.directory,
                    state,
                    directory=record.directory,
                )
            )
            if len(sessions) >= self.max_sessions:
                break
        executable = str(self.config.get("agy_path") or "agy")
        command_available = bool(shutil.which(executable)) or (
            Path(executable).expanduser().is_file()
        )
        enabled = bool(self.config.get("enabled", False))
        has_cache = bool(cache_records)
        has_live = bool(self._live_sessions)
        has_quota = bool(self._signals)
        connected = enabled and (has_live or (command_available and has_cache))
        if not enabled:
            detail = "Disabled"
        else:
            detail = "; ".join(
                (
                    f"Cache: {'available' if has_cache else 'unavailable'}",
                    f"live companion: {'available' if has_live else 'unavailable'}",
                    f"quota: {'available' if has_quota else 'unavailable'}",
                )
            )
            if not connected and not command_available:
                detail += "; Antigravity CLI was not found"
        return PluginSnapshot(
            PluginHealth(connected, detail),
            tuple(sessions),
            dict(self._signals),
        )

    def invoke(self, action: str, params: dict[str, Any]) -> dict[str, str]:
        if action != "select_session":
            raise AntigravityError(f"Unsupported Antigravity action: {action}")
        conversation_id = params.get("session_id")
        if not isinstance(conversation_id, str) or not _CONVERSATION_ID.fullmatch(conversation_id):
            raise AntigravityError("antigravity conversation ID is invalid")
        session = next(
            (item for item in self.snapshot().sessions if item.id == conversation_id), None
        )
        if session is None:
            raise AntigravityError(
                "Antigravity conversation no longer exists in the current session list"
            )
        if self._launcher is None:
            agy_path = self.config.get("agy_path") or "agy"
            launch_antigravity_session(conversation_id, session.directory, agy_path=agy_path)
        else:
            directory = session.directory
            self._launcher(
                conversation_id,
                directory if isinstance(directory, str) and Path(directory).is_dir() else None,
            )
        self._completed.discard(conversation_id)
        if (
            conversation_id in self._live_states
            and self._live_states[conversation_id] is SessionState.COMPLETED
        ):
            self._live_states[conversation_id] = SessionState.IDLE
        return {"result": "session_selected_in_antigravity", "session_id": conversation_id}
