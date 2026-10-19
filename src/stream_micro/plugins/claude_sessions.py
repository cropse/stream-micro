"""Local evidence about Claude Code Sessions: transcripts and live processes.

Everything here only reads local files that Claude Code already writes; nothing
is sent anywhere and no credentials are touched.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..timeparse import parse_timestamp
from .base import SessionState

SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")

TRANSCRIPT_TAIL_BYTES = 256 * 1024
RUNNING_STALE_SECONDS = 600
COMPLETED_VISIBLE_SECONDS = 1800
SUBAGENT_PENDING_SECONDS = 7200
# A registration without procStart must have been made this close to the
# process start, or the PID may belong to another program now.
REGISTRATION_SLACK_SECONDS = 300
# ...and the process may not have started after the registration (beyond
# clock rounding): one that did is another program that got the PID.
REGISTRATION_LATE_SECONDS = 5
_FILETIME_EPOCH = 116_444_736_000_000_000  # 1601-01-01 to 1970-01-01, 100 ns ticks


def clean_title(value: object) -> str:
    if not isinstance(value, str):
        return "Claude Code session"
    normalized = " ".join(value.split())
    if not normalized:
        return "Claude Code session"
    return normalized[:80]


# --------------------------------------------------------------------------
# Transcripts (~/.claude/projects/<project>/<session>.jsonl)


def read_tail(path: Path) -> tuple[float, str] | None:
    try:
        modified = path.stat().st_mtime
        with path.open("rb") as stream:
            stream.seek(max(0, stream.seek(0, os.SEEK_END) - TRANSCRIPT_TAIL_BYTES))
            return modified, stream.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _record_time(record: Mapping[object, object]) -> float | None:
    parsed = parse_timestamp(record.get("timestamp"))
    return parsed.timestamp() if parsed is not None else None


def transcript_state(path: Path, now: float) -> tuple[SessionState, float] | None:
    """Infer a Session's state from the tail of its live JSONL transcript.

    Claude Code appends every prompt, tool call, and tool result as it happens,
    so the newest user/assistant record tells whether a turn is in flight.
    Returns the state and the time of that record (epoch seconds).  Claude
    keeps rewriting title metadata after a turn ends, so file mtime is only a
    fallback for records without a timestamp.
    """

    last = _last_turn_record(path)
    if last is None:
        return None
    record, modified = last
    activity = _record_time(record) or modified
    age = now - activity
    message = record.get("message")
    message = message if isinstance(message, Mapping) else {}
    if record.get("isApiErrorMessage") is True:
        recent = age < COMPLETED_VISIBLE_SECONDS
        return (SessionState.ERROR if recent else SessionState.IDLE), activity
    if record.get("type") == "assistant" and message.get("stop_reason") in (
        "end_turn",
        "stop_sequence",
        "max_tokens",
    ):
        done = age < COMPLETED_VISIBLE_SECONDS
        return (SessionState.COMPLETED if done else SessionState.IDLE), activity
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    if isinstance(content, list):
        text = " ".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))
    if "[Request interrupted" in text:
        return SessionState.IDLE, activity
    running = age < RUNNING_STALE_SECONDS
    return (SessionState.RUNNING if running else SessionState.IDLE), activity


_TURN_CACHE: OrderedDict[Path, tuple[tuple[int, int], tuple[Mapping, float] | None]] = OrderedDict()
_TURN_CACHE_SIZE = 128
_TURN_LOCK = threading.Lock()


def _last_turn_record(path: Path) -> tuple[Mapping, float] | None:
    """The newest user/assistant record of the Session itself, and the file's mtime.

    Parsed again only when the file changed; the caller still judges its age.
    """

    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    with _TURN_LOCK:
        cached = _TURN_CACHE.get(path)
        if cached is not None and cached[0] == key:
            _TURN_CACHE.move_to_end(path)
            return cached[1]
    found: tuple[Mapping, float] | None = None
    read = read_tail(path)
    if read is None:
        return None  # a passing read error: try again next time, cache nothing
    modified, tail = read
    for line in reversed(tail.splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, Mapping) or record.get("type") not in ("user", "assistant"):
            continue
        if record.get("isSidechain") is True:
            continue  # a subagent's turn, not the Session's own
        found = (record, modified)
        break
    with _TURN_LOCK:
        _TURN_CACHE[path] = (key, found)
        _TURN_CACHE.move_to_end(path)
        while len(_TURN_CACHE) > _TURN_CACHE_SIZE:
            _TURN_CACHE.popitem(last=False)
    return found


_ENTRYPOINT_SCAN_BYTES = 4 * 1024 * 1024


def transcript_entrypoint(path: Path) -> str | None:
    """Which Claude Code front end wrote a transcript ("cli", "claude-desktop", ...).

    Claude Code records it on every turn record; the first one is enough.
    """

    try:
        with path.open("rb") as stream:
            scanned = 0
            for raw in stream:
                scanned += len(raw)
                if b'"entrypoint"' in raw:
                    try:
                        record = json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        record = None
                    value = record.get("entrypoint") if isinstance(record, Mapping) else None
                    if isinstance(value, str) and value:
                        return value
                if scanned >= _ENTRYPOINT_SCAN_BYTES:
                    break
    except OSError:
        return None
    return None


# --------------------------------------------------------------------------
# Background work a finished turn is still waiting for

_TASK_ID = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
_BACKGROUND_ID = re.compile(r"running in background with ID: ([A-Za-z0-9_-]+)")
# Background shell commands that hand work to another coding agent.  Other
# background commands (dev servers, watchers) are ignored: they never finish.
EXTERNAL_AGENT_COMMAND = re.compile(r"\bcodex\b|\bopencode\b|\bagy\b|\bgemini\b", re.IGNORECASE)
_SCAN_MARKERS = ("<task-notification>", "run_in_background", "running in background with ID")


@dataclass(frozen=True)
class BackgroundTasks:
    """Background work recorded in one main transcript."""

    notified: dict[str, float]
    external_launches: dict[str, float]

    def external_pending(self, since: float) -> bool:
        return any(
            launched >= since and self.notified.get(task_id, 0.0) < launched
            for task_id, launched in self.external_launches.items()
        )


def _external_tool_id(part: Mapping[object, object]) -> str | None:
    tool_input = part.get("input")
    if not isinstance(tool_input, Mapping) or not tool_input.get("run_in_background"):
        return None
    command = tool_input.get("command")
    if isinstance(command, str) and EXTERNAL_AGENT_COMMAND.search(command):
        return str(part.get("id"))
    return None


class _BackgroundScan:
    """Background work found so far in one transcript, and how far it was read."""

    def __init__(self) -> None:
        self.offset = 0
        self.notified: dict[str, float] = {}
        self.external_tools: set[str] = set()
        self.launches: dict[str, float] = {}

    def feed(self, line: str) -> None:
        if not any(marker in line for marker in _SCAN_MARKERS):
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(record, Mapping):
            return
        when = _record_time(record)
        if "<task-notification>" in line and when is not None:
            for task_id in _TASK_ID.findall(line):
                self.notified[task_id] = max(self.notified.get(task_id, 0.0), when)
        message = record.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        for part in content if isinstance(content, list) else ():
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "tool_use":
                tool_id = _external_tool_id(part)
                if tool_id is not None:
                    self.external_tools.add(tool_id)
            elif (
                part.get("type") == "tool_result"
                and when is not None
                and str(part.get("tool_use_id")) in self.external_tools
            ):
                found = _BACKGROUND_ID.search(json.dumps(part.get("content")))
                if found:
                    self.launches[found.group(1)] = when

    def read_more(self, transcript: Path) -> bool:
        """Read complete lines appended since the last call; False if unreadable."""

        try:
            with transcript.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
        except OSError:
            return False
        complete = data.rfind(b"\n") + 1  # a line still being written waits
        for line in data[:complete].decode("utf-8", errors="replace").splitlines():
            self.feed(line)
        self.offset += complete
        return True

    def result(self) -> BackgroundTasks:
        return BackgroundTasks(dict(self.notified), dict(self.launches))


def scan_background_tasks(transcript: Path) -> BackgroundTasks:
    """Collect completion notices and external-agent launches from a transcript."""

    scan = _BackgroundScan()
    return scan.result() if scan.read_more(transcript) else BackgroundTasks({}, {})


class BackgroundTaskTracker:
    """Tell whether a finished turn still waits for background work.

    Completion notices can be megabytes back in a transcript, so each file is
    read once and then only what was appended; a file that shrank is read
    again from the start.  Entries for Sessions no longer shown are dropped.
    """

    def __init__(self) -> None:
        self._scans: dict[Path, _BackgroundScan] = {}

    def tasks(self, transcript: Path) -> BackgroundTasks:
        try:
            size = transcript.stat().st_size
        except OSError:
            return BackgroundTasks({}, {})
        scan = self._scans.get(transcript)
        if scan is None or size < scan.offset:
            scan = self._scans[transcript] = _BackgroundScan()
        if size > scan.offset and not scan.read_more(transcript):
            return BackgroundTasks({}, {})
        return scan.result()

    def retain(self, transcripts: set[Path]) -> None:
        self._scans = {path: scan for path, scan in self._scans.items() if path in transcripts}

    def subagents_active(self, transcript: Path, now: float, since: float = 0.0) -> bool:
        """Background subagents still working for this Session.

        Each subagent writes a live transcript under
        ``<session>/subagents/agent-<id>.jsonl`` with a ``.meta.json`` beside
        it.  A background subagent is finished once a completion notice newer
        than its last write reaches the main transcript; writing again after
        that (resumed through SendMessage) makes it active again.  Work last
        written before ``since`` (the current Claude process start) died with
        an earlier process.  Foreground subagents keep the main turn running.
        """

        folder = transcript.with_suffix("") / "subagents"
        pending: list[tuple[str, float]] = []
        try:
            for path in folder.glob("agent-*.jsonl"):
                modified = path.stat().st_mtime
                if now - modified >= SUBAGENT_PENDING_SECONDS or modified < since:
                    continue
                try:
                    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(meta, Mapping) and meta.get("requestShape") == "background":
                    pending.append((path.stem.removeprefix("agent-"), modified))
        except OSError:
            return False
        if not pending:
            return False
        notified = self.tasks(transcript).notified
        return any(notified.get(agent_id, 0.0) + 1 < modified for agent_id, modified in pending)

    def busy(self, transcript: Path, now: float, since: float = 0.0) -> bool:
        """Background subagents or external agents (Codex, opencode, ...) pending."""

        return self.subagents_active(transcript, now, since) or self.tasks(
            transcript
        ).external_pending(max(since, now - SUBAGENT_PENDING_SECONDS))


# --------------------------------------------------------------------------
# Live Claude processes (~/.claude/sessions/<pid>.json)


@dataclass(frozen=True)
class LiveClaudeProcess:
    """A running Claude Code process registered under ``~/.claude/sessions``."""

    pid: int
    session_id: str
    status: str | None
    started_at: float
    status_updated_at: float = 0.0
    name: str | None = None
    cwd: str | None = None
    # Hosted by the Claude desktop app rather than a terminal.
    desktop: bool = False


ProcessProbe = Callable[[int], int | None]


def _windows_process_start(pid: int) -> int | None:
    """Creation time (FILETIME ticks) of a running process, or None if it is gone."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    filetime = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, filetime, filetime, filetime, filetime)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != still_active:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        created = times[0]
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def default_process_probe() -> ProcessProbe | None:
    """How to check a registered PID on this platform; None when unknown."""

    return _windows_process_start if os.name == "nt" else None


def _epoch_ms(value: object) -> float:
    """Epoch seconds from a registry time in milliseconds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return 0.0
    return float(value) / 1000


def _filetime_seconds(ticks: int) -> float:
    return (ticks - _FILETIME_EPOCH) / 10_000_000


def read_live_processes(
    sessions_dir: Path, probe: ProcessProbe | None
) -> dict[str, LiveClaudeProcess] | None:
    """Return live Claude processes by Session ID.

    A registration counts only while its PID is running *and*, when Claude
    recorded it, the process start time still matches; a reused PID (after a
    crash or reboot) is not mistaken for the Session, whatever program it is
    (``claude.exe``, or ``node.exe`` for an npm install).  Returns None when
    there is no registry or no way to check processes on this platform, so
    callers fall back to transcript evidence instead of marking every Session
    closed.
    """

    if probe is None or not sessions_dir.is_dir():
        return None
    live: dict[str, LiveClaudeProcess] = {}
    verified = 0
    other_unit = 0
    for path in sessions_dir.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(item, Mapping):
            continue
        pid, session_id = item.get("pid"), item.get("sessionId")
        if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(session_id, str):
            continue
        if not SESSION_ID.fullmatch(session_id):
            continue
        started = probe(pid)
        if started is None:
            continue
        recorded = item.get("procStart")
        recorded_start = (
            str(recorded)
            if isinstance(recorded, (str, int)) and not isinstance(recorded, bool)
            else ""
        )
        if recorded_start.isdigit():
            if len(recorded_start) != len(str(started)):
                other_unit += 1  # not comparable: another unit than the process table
                continue
            if int(recorded_start) != started:
                continue  # The PID now belongs to another process.
            verified += 1
        else:
            # Without procStart, the registration must date from this process.
            registered = _epoch_ms(item.get("startedAt"))
            created = _filetime_seconds(started)
            if not registered or not (
                registered - REGISTRATION_SLACK_SECONDS
                <= created
                <= registered + REGISTRATION_LATE_SECONDS
            ):
                continue
        status, name, cwd = item.get("status"), item.get("name"), item.get("cwd")
        process = LiveClaudeProcess(
            pid,
            session_id,
            status if isinstance(status, str) else None,
            _epoch_ms(item.get("startedAt")),
            _epoch_ms(item.get("statusUpdatedAt")),
            name if isinstance(name, str) else None,
            cwd if isinstance(cwd, str) else None,
            item.get("entrypoint") == "claude-desktop"
            or isinstance(item.get("hostSessionId"), str),
        )
        current = live.get(session_id)
        if current is None or process.started_at > current.started_at:
            live[session_id] = process
    if other_unit and not verified:
        # No record could be checked, all in another unit: Claude Code changed
        # how it records processes. Trust none of it rather than show every
        # open Session as OFF; transcripts and hooks still work. A single odd
        # record next to verified ones is just skipped.
        return None
    return live
