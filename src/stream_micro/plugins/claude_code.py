"""Claude Code sessions backed by local history, live processes, and hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import AgentSession, PluginHealth, PluginManifest, PluginSnapshot, SessionState
from .claude_sessions import (
    COMPLETED_VISIBLE_SECONDS,
    SESSION_ID,
    BackgroundTaskTracker,
    LiveClaudeProcess,
    ProcessProbe,
    clean_title,
    default_process_probe,
    read_live_processes,
    transcript_entrypoint,
    transcript_state,
)

MAX_VISIBLE_SESSIONS = 15
# Sessions considered before live ones are sorted first and the list is cut.
CANDIDATE_SESSIONS = 40
MAX_HISTORY_LINES = 5000
_SESSION_ID = SESSION_ID
_ALLOWED_KEYS = frozenset(
    {"enabled", "claude_path", "claude_home", "plugin_dir", "max_sessions"}
)


class ClaudeCodeError(RuntimeError):
    """Raised when the Claude Code integration cannot complete an operation."""


@dataclass(frozen=True)
class ClaudeHistorySession:
    id: str
    title: str
    directory: str | None
    updated_at: float


@dataclass
class _LiveSession:
    id: str
    title: str
    directory: str | None
    state: SessionState
    updated_at: float


def validate_claude_code_config(config: dict[str, object]) -> None:
    unknown = sorted(set(config) - _ALLOWED_KEYS)
    if unknown:
        raise ClaudeCodeError(f"Unknown claude_code setting(s): {', '.join(unknown)}")
    enabled = config.get("enabled")
    if "enabled" in config and not isinstance(enabled, bool):
        raise ClaudeCodeError("claude_code.enabled must be a boolean")
    for name in ("claude_path", "claude_home", "plugin_dir"):
        value = config.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ClaudeCodeError(f"claude_code.{name} must be a non-empty string or null")
    maximum = config.get("max_sessions", MAX_VISIBLE_SESSIONS)
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 1 <= maximum <= MAX_VISIBLE_SESSIONS
    ):
        raise ClaudeCodeError(
            f"claude_code.max_sessions must be an integer from 1 to {MAX_VISIBLE_SESSIONS}"
        )


_clean_title = clean_title


def _project_name(directory: str | None) -> str | None:
    if not directory:
        return None
    name = Path(directory).name
    return name or directory


def read_claude_history(
    history_path: Path, *, max_sessions: int = MAX_VISIBLE_SESSIONS
) -> tuple[ClaudeHistorySession, ...]:
    """Read the most recent unique sessions from Claude Code's local history."""

    try:
        with history_path.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=MAX_HISTORY_LINES)
    except OSError:
        return ()

    sessions: dict[str, ClaudeHistorySession] = {}
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, Mapping):
            continue
        session_id = item.get("sessionId")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            continue
        timestamp = item.get("timestamp", 0)
        updated_at = float(timestamp) if isinstance(timestamp, (int, float)) else 0.0
        directory = item.get("project")
        if not isinstance(directory, str) or not directory.strip():
            directory = None
        candidate = ClaudeHistorySession(
            session_id,
            _clean_title(item.get("display")),
            directory,
            updated_at,
        )
        current = sessions.get(session_id)
        if current is None or candidate.updated_at >= current.updated_at:
            sessions[session_id] = candidate
    return tuple(
        sorted(sessions.values(), key=lambda session: session.updated_at, reverse=True)[
            :max_sessions
        ]
    )


def map_claude_hook_state(payload: Mapping[str, object]) -> SessionState | None:
    """Map documented Claude Code lifecycle hook events to Deck states."""

    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return SessionState.RUNNING if payload.get("source") == "compact" else SessionState.IDLE
    if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
        return SessionState.RUNNING
    if event == "PermissionRequest":
        return SessionState.WAITING
    if event == "Notification":
        notification = payload.get("notification_type")
        if notification in ("permission_prompt", "agent_needs_input", "elicitation_dialog"):
            return SessionState.WAITING
        if notification in ("idle_prompt", "agent_completed"):
            return SessionState.COMPLETED
        return None
    if event == "Stop":
        return SessionState.COMPLETED
    if event == "StopFailure":
        return SessionState.ERROR
    if event == "SessionEnd":
        return SessionState.IDLE
    return None


def parse_claude_rate_limits(
    payload: Mapping[str, object],
) -> dict[str, str | float | int | None]:
    """Translate documented Claude Code status-line limits into quota signals."""

    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, Mapping):
        return {}

    signals: dict[str, str | float | int | None] = {}
    for source, prefix, label in (
        ("five_hour", "primary", "5H"),
        ("seven_day", "secondary", "7D"),
    ):
        window = rate_limits.get(source)
        if not isinstance(window, Mapping):
            continue
        used = window.get("used_percentage")
        if isinstance(used, (int, float)) and not isinstance(used, bool) and 0 <= used <= 100:
            signals[f"{prefix}_percent"] = float(used)
            signals[f"{prefix}_label"] = label
        resets_at = window.get("resets_at")
        if (
            isinstance(resets_at, (int, float))
            and not isinstance(resets_at, bool)
            and resets_at > 0
        ):
            try:
                reset = datetime.fromtimestamp(resets_at, tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                continue
            signals[f"{prefix}_reset_at"] = reset
    return signals


def resolve_claude_command(claude_path: str) -> list[str]:
    """Resolve a Claude Code executable or Windows command shim safely."""

    requested = Path(claude_path).expanduser()
    resolved = shutil.which(str(requested))
    if resolved is None and not requested.suffix:
        resolved = shutil.which(f"{claude_path}.cmd") or shutil.which(f"{claude_path}.exe")
    resolved = resolved or (str(requested) if requested.exists() else claude_path)
    suffix = Path(resolved).suffix.lower()
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", resolved]
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-File",
            resolved,
        ]
    return [resolved]


def launch_claude_session(
    session_id: str,
    directory: str | None,
    *,
    claude_path: str = "claude",
    plugin_dir: str | None = None,
) -> None:
    """Resume a Claude Code session in a new Windows console."""

    if os.name != "nt":
        raise ClaudeCodeError("Claude Code Session resume is currently available only on Windows")
    if not _SESSION_ID.fullmatch(session_id):
        raise ClaudeCodeError("Invalid Claude Code Session ID")
    working_directory = directory if directory and Path(directory).is_dir() else None
    plugin_args = ["--plugin-dir", str(Path(plugin_dir).expanduser())] if plugin_dir else []
    try:
        subprocess.Popen(
            [*resolve_claude_command(claude_path), *plugin_args, "--resume", session_id],
            cwd=working_directory,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            shell=False,
        )
    except OSError as exc:
        raise ClaudeCodeError(f"Could not open Claude Code Session: {exc}") from exc


# A registry read can miss a Session for a moment (a file being rewritten).
PROCESS_GRACE_SECONDS = 15.0
_WAIT_WORDS = ("wait", "permission", "input", "approv")


class ClaudeCodePlugin:
    """Expose Claude Code Sessions to Stream Deck session zones."""

    manifest = PluginManifest(
        id="claude_code",
        name="Claude Code",
        components=("session", "quota"),
        actions=("select_session",),
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        launcher: Callable[[str, str | None], None] | None = None,
        process_probe: ProcessProbe | None = default_process_probe(),  # noqa: B008
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.validate_config(config)
        self.config = dict(config)
        self._clock = clock
        self._process_probe = process_probe
        claude_path = self.config.get("claude_path") or "claude"
        plugin_dir = self.config.get("plugin_dir")
        self._launcher = launcher or (
            lambda session_id, directory: launch_claude_session(
                session_id,
                directory,
                claude_path=claude_path,
                plugin_dir=plugin_dir if isinstance(plugin_dir, str) else None,
            )
        )
        configured_home = self.config.get("claude_home")
        self.home = (
            Path(configured_home).expanduser()
            if isinstance(configured_home, str)
            else Path.home() / ".claude"
        )
        self.history_path = self.home / "history.jsonl"
        self.max_sessions = int(self.config.get("max_sessions", MAX_VISIBLE_SESSIONS))
        self._lock = threading.Lock()
        self._live: dict[str, _LiveSession] = {}
        self._history_key: tuple[int, int] | None = None
        self._history: tuple[ClaudeHistorySession, ...] = ()
        self._last_hook_at: float | None = None
        # When the status-line command last reported (seconds), for the setup check.
        self._statusline_at: float | None = None
        self._quota_signals: dict[str, str | float | int | None] = {}
        # Front end that wrote each transcript, once known (it never changes).
        # Per transcript: the file version scanned and the front end found, if any.
        self._entrypoints: dict[Path, tuple[tuple[int, int], str | None]] = {}
        self._transcript_paths: dict[str, Path] = {}
        self._background = BackgroundTaskTracker()
        self._snapshot_lock = threading.Lock()
        # When each Session's process was last seen running.
        self._alive_at: dict[str, float] = {}

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ClaudeCodeError("claude_code config must be a mapping")
        validate_claude_code_config(config)

    def handle_hook(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        session_id = payload.get("session_id")
        event = payload.get("hook_event_name")
        if (
            not isinstance(session_id, str)
            or not _SESSION_ID.fullmatch(session_id)
            or not isinstance(event, str)
        ):
            raise ClaudeCodeError("Claude Code hook requires session_id and hook_event_name")
        state = map_claude_hook_state(payload)
        now = self._clock() * 1000
        directory = payload.get("cwd")
        if not isinstance(directory, str) or not directory.strip():
            directory = None
        title_value = payload.get("session_title")
        if not isinstance(title_value, str) or not title_value.strip():
            title_value = payload.get("prompt")
        with self._lock:
            current = self._live.get(session_id)
            title = (
                _clean_title(title_value)
                if isinstance(title_value, str) and title_value.strip()
                else current.title
                if current is not None
                else "Claude Code session"
            )
            self._live[session_id] = _LiveSession(
                session_id,
                title,
                directory or (current.directory if current else None),
                state or (current.state if current else SessionState.IDLE),
                now,
            )
            self._last_hook_at = now
        return {
            "accepted": True,
            "session_id": session_id,
            "state": state.value if state is not None else None,
        }

    def handle_quota(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        """Accept the account limits exposed to Claude Code status-line commands."""

        session_id = payload.get("session_id")
        if session_id is not None and (
            not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)
        ):
            raise ClaudeCodeError("Claude Code quota update has an invalid session_id")
        signals = parse_claude_rate_limits(payload)
        with self._lock:
            self._statusline_at = self._clock()
        if signals:
            with self._lock:
                self._quota_signals = signals
        return {
            "accepted": True,
            "session_id": session_id if isinstance(session_id, str) else None,
            "quota_updated": bool(signals),
        }

    def setup_activity(self) -> dict[str, float | None]:
        """When the hook and the status line last reached the Bridge (epoch seconds)."""

        with self._lock:
            hook = self._last_hook_at / 1000 if self._last_hook_at is not None else None
            return {"last_hook_at": hook, "last_statusline_at": self._statusline_at}

    def _history_sessions(self) -> tuple[ClaudeHistorySession, ...]:
        try:
            stat = self.history_path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        with self._lock:
            if key == self._history_key:
                return self._history
        history = read_claude_history(self.history_path, max_sessions=CANDIDATE_SESSIONS)
        with self._lock:
            self._history_key = key
            self._history = history
        return history

    def _transcript_path(self, session_id: str) -> Path | None:
        cached = self._transcript_paths.get(session_id)
        if cached is not None and cached.exists():
            return cached
        found = next((self.home / "projects").glob(f"*/{session_id}.jsonl"), None)
        if found is not None:
            self._transcript_paths[session_id] = found
        return found

    def _entrypoint(self, path: Path | None) -> str | None:
        if path is None:
            return None
        cached = self._entrypoints.get(path)
        if cached is not None and cached[1] is not None:
            return cached[1]  # a front end never changes once recorded
        try:
            stat = path.stat()
        except OSError:
            return None
        version = (stat.st_mtime_ns, stat.st_size)
        if cached is not None and cached[0] == version:
            return None  # scanned already and unchanged: still none recorded
        entry = transcript_entrypoint(path)
        self._entrypoints[path] = (version, entry)
        return entry

    def _state(
        self,
        record: _LiveSession,
        hook: _LiveSession | None,
        process: LiveClaudeProcess | None,
        path: Path | None,
        now: float,
    ) -> SessionState:
        """Decide one Session's state; the newest trustworthy evidence wins.

        - transcript: what the last turn record says, as of that record;
        - hook: a lifecycle event, trusted only if sent by the current process;
        - process registry: busy/idle/waiting as of its status update.
        A DONE shows for a limited time; background work turns a finished turn
        into BACKGROUND.
        """

        inferred = transcript_state(path, now) if path is not None else None
        state, evidence_at = inferred if inferred is not None else (record.state, 0.0)
        record.updated_at = max(record.updated_at, evidence_at * 1000)
        process_started = process.started_at if process is not None else 0.0
        exact_wait = False
        if hook is not None:
            hook_at = hook.updated_at / 1000
            # A WAIT hook may land a moment before the record it is about.
            slack = 1.0 if hook.state == SessionState.WAITING else 0.0
            if hook_at >= process_started and hook_at >= evidence_at - slack:
                state, evidence_at = hook.state, hook_at
                exact_wait = state == SessionState.WAITING
        registry_at = (process.status_updated_at or process_started) if process else 0.0
        # The registry is coarse: it may only override a hook's WAIT with a newer report.
        registry_newer = (
            registry_at > evidence_at if exact_wait else registry_at >= evidence_at
        )
        if process is not None and process.status and registry_newer:
            status = process.status.lower()
            if status == "busy":
                state, evidence_at = SessionState.RUNNING, registry_at
            elif any(word in status for word in _WAIT_WORDS):
                state, evidence_at = SessionState.WAITING, registry_at
            elif status == "idle" and state in (SessionState.RUNNING, SessionState.WAITING):
                # A newer idle report ends a turn or an obsolete approval prompt.
                state, evidence_at = SessionState.IDLE, registry_at
        finished = (SessionState.COMPLETED, SessionState.ERROR)
        if state in finished and now - evidence_at >= COMPLETED_VISIBLE_SECONDS:
            state = SessionState.IDLE
        if (
            path is not None
            and state in (SessionState.COMPLETED, SessionState.IDLE)
            and self._background.busy(path, now, process_started)
        ):
            state = SessionState.BACKGROUND
        return state

    def snapshot(self) -> PluginSnapshot:
        with self._snapshot_lock:
            return self._snapshot()

    def _snapshot(self) -> PluginSnapshot:
        history = self._history_sessions()
        processes = read_live_processes(self.home / "sessions", self._process_probe)
        with self._lock:
            live = {session_id: record for session_id, record in self._live.items()}
            last_hook_at = self._last_hook_at
            quota_signals = dict(self._quota_signals)

        merged: dict[str, _LiveSession] = {
            item.id: _LiveSession(
                item.id,
                item.title,
                item.directory,
                SessionState.IDLE,
                item.updated_at,
            )
            for item in history
        }
        for process in (processes or {}).values():
            # Open Sessions not in the recent lists still belong on the Deck.
            if process.session_id not in merged:
                merged[process.session_id] = _LiveSession(
                    process.session_id,
                    _clean_title(process.name),
                    process.cwd,
                    SessionState.IDLE,
                    process.started_at * 1000,
                )
        for session_id, record in live.items():
            previous = merged.get(session_id)
            keep_previous_title = previous is not None and record.title == "Claude Code session"
            merged[session_id] = _LiveSession(
                session_id,
                previous.title if keep_previous_title and previous else record.title,
                record.directory or (previous.directory if previous else None),
                record.state,
                max(record.updated_at, previous.updated_at if previous else 0),
            )
        # The Claude desktop app also runs Claude Code and records its Sessions
        # in the same history; those are not terminal Sessions, so leave them out
        # (resuming one in a console would fork it).  The registry marks live ones;
        # the transcript marks them for good.
        desktop_hosted = {
            process.session_id for process in (processes or {}).values() if process.desktop
        }
        paths = {session_id: self._transcript_path(session_id) for session_id in merged}
        merged = {
            session_id: record
            for session_id, record in merged.items()
            if session_id not in desktop_hosted
            and self._entrypoint(paths[session_id]) != "claude-desktop"
        }

        now = self._clock()
        transcripts: set[Path] = set()
        for session_id, record in merged.items():
            process = processes.get(session_id) if processes is not None else None
            path = paths[session_id]
            if path is not None:
                transcripts.add(path)
            if process is not None:
                self._alive_at[session_id] = now
            recently_alive = now - self._alive_at.get(session_id, float("-inf")) < (
                PROCESS_GRACE_SECONDS
            )
            if processes is not None and process is None and not recently_alive:
                inferred = transcript_state(path, now) if path is not None else None
                if inferred is not None:
                    record.updated_at = max(record.updated_at, inferred[1] * 1000)
                record.state = SessionState.DISCONNECTED  # closed, crashed, or rebooted
                continue
            record.state = self._state(record, live.get(session_id), process, path, now)
        # Keep per-Session caches to the Sessions considered now.
        self._alive_at = {
            session_id: at for session_id, at in self._alive_at.items() if session_id in merged
        }
        self._background.retain(transcripts)
        self._transcript_paths = {
            session_id: path for session_id, path in paths.items() if path is not None
        }
        known = {path for path in paths.values() if path is not None}
        self._entrypoints = {
            path: entry for path, entry in self._entrypoints.items() if path in known
        }

        # Sessions with a running Claude process come first, newest activity next.
        recent = sorted(
            merged.values(),
            key=lambda item: (item.state != SessionState.DISCONNECTED, item.updated_at),
            reverse=True,
        )[: self.max_sessions]
        sessions = tuple(
            AgentSession(
                item.id,
                item.title,
                item.state,
                _project_name(item.directory),
                item.directory,
            )
            for item in recent
        )
        executable = self.config.get("claude_path") or "claude"
        command_available = bool(shutil.which(str(executable))) or Path(str(executable)).exists()
        running = sum(1 for session_id in processes or {} if session_id in merged)
        connected = command_available or bool(merged) or bool(live) or running > 0
        if last_hook_at is not None:
            detail = f"Live hooks connected; {len(sessions)} recent Sessions"
        elif connected:
            detail = f"{len(sessions)} recent Sessions"
            detail += f", {running} open" if processes is not None else "; waiting for live hooks"
        else:
            detail = "Claude Code was not found"
        return PluginSnapshot(
            PluginHealth(connected, detail, str(self.history_path)),
            sessions,
            quota_signals or None,
        )

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        if action != "select_session":
            raise ClaudeCodeError(f"Unsupported Claude Code action: {action}")
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ClaudeCodeError("select_session requires a valid session_id")
        session = next((item for item in self.snapshot().sessions if item.id == session_id), None)
        if session is None:
            raise ClaudeCodeError("Claude Code Session no longer exists")
        self._launcher(session.id, session.directory)
        return {"result": "session_selected_in_claude_code", "session_id": session.id}
