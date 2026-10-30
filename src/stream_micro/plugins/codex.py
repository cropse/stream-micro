"""Codex App Server JSON-RPC primitives and status mapping."""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from typing import IO, Any
from urllib.parse import quote

from .. import __version__
from ..process_launch import background_process_options
from ..windows_focus import WindowFocusError, focus_app_windows
from .base import AgentSession, PluginHealth, PluginManifest, PluginSnapshot, SessionState

LOGGER = logging.getLogger(__name__)

JsonObject = dict[str, object]
QuotaSignals = dict[str, str | float | None]
MAX_VISIBLE_THREADS = 15
APP_SERVER_REFRESH_SECONDS = 2.0
ROLLOUT_REFRESH_SECONDS = 0.25


@dataclass(frozen=True)
class ThreadNotification:
    """One display-relevant Codex thread event."""

    kind: str
    thread_id: str
    state: SessionState | None = None
    thread: Mapping[object, object] | None = None
    title: str | None = None


class CodexError(RuntimeError):
    """Raised when the Codex App Server cannot complete an operation."""


def thread_deep_link(thread_id: str) -> str:
    """Build the ChatGPT desktop app's documented local-thread deep link."""

    normalized = thread_id.strip()
    if not normalized:
        raise CodexError("Codex thread ID is required")
    return f"codex://threads/{quote(normalized, safe='')}"


def select_codex_thread(thread_id: str) -> None:
    """Open an existing local Codex chat in the ChatGPT desktop app."""

    if os.name != "nt":
        raise CodexError("Codex desktop chat switching is available only on Windows")
    try:
        os.startfile(thread_deep_link(thread_id))
    except OSError as exc:
        raise CodexError(
            "Could not open the Codex chat. Install or start the ChatGPT desktop app first."
        ) from exc
    # The deep link switches the chat inside the app but Windows may deny the
    # app's own focus attempt (foreground lock). Force the window front.
    with contextlib.suppress(WindowFocusError):
        focus_app_windows(("ChatGPT.exe",))


def validate_codex_config(config: dict[str, object]) -> None:
    """Validate optional Codex executable and home-directory settings."""

    enabled = config.get("enabled")
    if "enabled" in config and not isinstance(enabled, bool):
        raise CodexError("codex.enabled must be a boolean")
    for name in ("codex_path", "codex_home"):
        value = config.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise CodexError(f"codex.{name} must be a non-empty string or null")


def resolve_codex_command(codex_path: str) -> list[str]:
    """Resolve Windows command shims without enabling a general-purpose shell."""

    if os.name != "nt":
        return [codex_path]

    requested = Path(codex_path).expanduser()
    resolved: str | None
    if requested.suffix:
        resolved = shutil.which(str(requested)) or (str(requested) if requested.exists() else None)
    else:
        # A .cmd shim is directly launchable through COMSPEC, while WindowsApps
        # may expose a codex.exe path that ordinary CreateProcess cannot open.
        resolved = (
            shutil.which(f"{codex_path}.cmd")
            or shutil.which(f"{codex_path}.exe")
            or shutil.which(codex_path)
        )
    resolved = resolved or codex_path
    if Path(resolved).suffix.lower() in (".cmd", ".bat"):
        npm_root = Path(resolved).parent
        native_candidates = sorted(
            npm_root.glob(
                "node_modules/@openai/codex/node_modules/@openai/"
                "codex-win32-*/vendor/*/bin/codex.exe"
            )
        )
        if native_candidates:
            return [str(native_candidates[0])]
    suffix = Path(resolved).suffix.lower()
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", resolved]
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            resolved,
        ]
    return [resolved]


class CodexAppServerClient:
    """Thread-safe JSON-RPC client for ``codex app-server`` stdio."""

    def __init__(self, codex_path: str, codex_home: str | None = None) -> None:
        self.codex_path = codex_path
        self.codex_home = codex_home
        self._process: subprocess.Popen[str] | None = None
        self._stdin: IO[str] | None = None
        self._stdout: IO[str] | None = None
        self._next_request_id = 1
        self._notifications: Queue[JsonObject | None] = Queue()
        self._responses: dict[int, Queue[JsonObject | None]] = {}
        self._response_backlog: dict[int, JsonObject] = {}
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._transport_eof = threading.Event()

    def start(self) -> None:
        if self.is_alive():
            return
        if self._process is not None:
            self.close()

        environment = os.environ.copy()
        if self.codex_home is not None:
            environment["CODEX_HOME"] = self.codex_home

        try:
            process = subprocess.Popen(
                [*resolve_codex_command(self.codex_path), "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=environment,
                shell=False,
                **background_process_options(),
            )
        except OSError as exc:
            raise CodexError(f"Could not start Codex App Server: {exc}") from exc

        self._process = process
        if process.stdin is None or process.stdout is None:
            self.close()
            raise CodexError("Codex App Server did not provide stdio pipes")

        self._stdin = process.stdin
        self._stdout = process.stdout
        self._next_request_id = 1
        self._notifications = Queue()
        self._transport_eof.clear()
        with self._state_lock:
            self._responses.clear()
            self._response_backlog.clear()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="codex-app-server-transport",
            daemon=True,
        )
        self._reader.start()

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def initialize(self) -> JsonObject:
        result = self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "stream-micro",
                    "title": "Stream Micro Bridge",
                    "version": __version__,
                },
            },
        )
        return result if isinstance(result, dict) else {}

    def send_initialized(self) -> None:
        self._notify("initialized", {})

    def list_threads(self) -> list[JsonObject]:
        result = self._request(
            "thread/list",
            {
                "limit": MAX_VISIBLE_THREADS,
                "sortKey": "recency_at",
                "sortDirection": "desc",
                "sourceKinds": ["cli", "vscode", "appServer"],
            },
        )
        if isinstance(result, list):
            threads = result
        elif isinstance(result, dict) and isinstance(result.get("data"), list):
            threads = result["data"]
        elif isinstance(result, dict) and isinstance(result.get("threads"), list):
            # Compatibility with older pre-v2 app-server builds.
            threads = result["threads"]
        else:
            LOGGER.warning("Codex thread/list returned an unexpected result: %r", result)
            return []
        return [item for item in threads if isinstance(item, dict)]

    def read_rate_limits(self) -> JsonObject:
        """Return the current ChatGPT rate-limit windows, if available."""

        result = self._request("account/rateLimits/read", {})
        return result if isinstance(result, dict) else {}

    def read_line(self) -> JsonObject | None:
        """Read one app-server notification dispatched by the transport reader."""

        return self._notifications.get()

    def _reader_loop(self) -> None:
        stdout = self._stdout
        if stdout is None:
            self._transport_closed()
            return
        while True:
            line = stdout.readline()
            if not line:
                self._transport_closed()
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                LOGGER.warning("Skipping malformed Codex JSON-RPC line: %s", exc)
                continue
            if not isinstance(message, dict):
                LOGGER.warning("Skipping non-object Codex JSON-RPC message: %r", message)
                continue
            request_id = message.get("id")
            if isinstance(request_id, int):
                with self._state_lock:
                    waiter = self._responses.get(request_id)
                    if waiter is None:
                        self._response_backlog[request_id] = message
                if waiter is not None:
                    waiter.put(message)
            else:
                self._notifications.put(message)

    def _transport_closed(self) -> None:
        self._transport_eof.set()
        self._notifications.put(None)
        with self._state_lock:
            waiters = tuple(self._responses.values())
        for waiter in waiters:
            waiter.put(None)

    def read_message(self) -> JsonObject | None:
        """Backward-compatible alias for :meth:`read_line`."""

        return self.read_line()

    def close(self) -> None:
        process = self._process
        if process is None:
            return

        stdin = self._stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError as exc:
                LOGGER.debug("Could not close Codex stdin: %s", exc)

        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired) as exc:
                LOGGER.debug("Codex App Server did not terminate cleanly: %s", exc)
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired) as kill_exc:
                    LOGGER.warning("Could not kill Codex App Server: %s", kill_exc)
        self._process = None
        self._stdin = None
        self._stdout = None
        self._notifications.put(None)
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2)
        with self._state_lock:
            self._responses.clear()
            self._response_backlog.clear()

    def __enter__(self) -> CodexAppServerClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _request(self, method: str, params: JsonObject) -> object:
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            waiter: Queue[JsonObject | None] = Queue()
            self._responses[request_id] = waiter
            pending = self._response_backlog.pop(request_id, None)
        if pending is not None:
            waiter.put(pending)
        elif self._transport_eof.is_set():
            waiter.put(None)
        try:
            self._write_message(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            try:
                message = waiter.get(timeout=15)
            except Empty as exc:
                raise CodexError(f"Codex {method} timed out") from exc
            if message is None:
                raise CodexError(f"Codex App Server closed while waiting for {method}")
            error = message.get("error")
            if error is not None:
                raise CodexError(f"Codex {method} failed: {error}")
            if "result" not in message:
                raise CodexError(f"Codex {method} returned a malformed response")
            return message["result"]
        finally:
            with self._state_lock:
                self._responses.pop(request_id, None)

    def _notify(self, method: str, params: JsonObject) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_message(self, message: JsonObject) -> None:
        _, stdin, _ = self._require_started()
        try:
            with self._write_lock:
                stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexError(f"Could not write to Codex App Server: {exc}") from exc

    def _require_started(self) -> tuple[subprocess.Popen[str], IO[str], IO[str]]:
        process = self._process
        stdin = self._stdin
        stdout = self._stdout
        if process is None or stdin is None or stdout is None or process.poll() is not None:
            raise CodexError("Codex App Server has not been started or is no longer running")
        return process, stdin, stdout


def map_thread_status(status: object) -> SessionState:
    """Map a Codex thread status object to the bridge's defensive state model."""

    if not isinstance(status, Mapping):
        return SessionState.IDLE
    status_type = status.get("type")
    if status_type in ("notLoaded", "idle"):
        return SessionState.IDLE
    if status_type == "systemError":
        return SessionState.ERROR
    if status_type != "active":
        return SessionState.IDLE

    flags = status.get("activeFlags")
    if not isinstance(flags, list):
        flags = status.get("active_flags")
    if isinstance(flags, list) and any(_is_waiting_flag(flag) for flag in flags):
        return SessionState.WAITING
    return SessionState.RUNNING


@dataclass
class _RolloutCursor:
    offset: int = 0
    lifecycle: str | None = None
    initialized: bool = False
    pending_input_calls: set[str] | None = None


class CodexRolloutMonitor:
    """Read cross-process turn lifecycle events from Codex JSONL rollouts."""

    def __init__(self) -> None:
        self._cursors: dict[Path, _RolloutCursor] = {}

    def poll(self, thread_id: str, path_value: str) -> ThreadNotification | None:
        path = Path(path_value)
        cursor = self._cursors.setdefault(path, _RolloutCursor())
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size < cursor.offset:
            cursor.offset = 0
            cursor.lifecycle = None
            cursor.initialized = False
            cursor.pending_input_calls = None

        latest = cursor.lifecycle
        try:
            with path.open("rb") as stream:
                stream.seek(cursor.offset)
                while True:
                    line_start = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        stream.seek(line_start)
                        break
                    event = _rollout_status_event(line)
                    if event is None:
                        continue
                    event_type, call_id = event
                    if cursor.pending_input_calls is None:
                        cursor.pending_input_calls = set()
                    if event_type == "waiting_input" and call_id is not None:
                        cursor.pending_input_calls.add(call_id)
                        latest = event_type
                    elif event_type == "input_received" and call_id in cursor.pending_input_calls:
                        cursor.pending_input_calls.discard(call_id)
                        latest = "task_started"
                    elif event_type in ("task_started", "task_complete", "turn_aborted"):
                        if event_type in ("task_complete", "turn_aborted"):
                            cursor.pending_input_calls.clear()
                        latest = event_type
                cursor.offset = stream.tell()
        except OSError:
            return None

        previous = cursor.lifecycle
        was_initialized = cursor.initialized
        cursor.lifecycle = latest
        cursor.initialized = True
        if not was_initialized:
            if latest == "task_started":
                return ThreadNotification("status", thread_id, SessionState.RUNNING)
            if latest == "waiting_input":
                return ThreadNotification("status", thread_id, SessionState.WAITING)
            return None
        if latest == previous:
            return None
        if latest == "task_started":
            return ThreadNotification("status", thread_id, SessionState.RUNNING)
        if latest == "waiting_input":
            return ThreadNotification("status", thread_id, SessionState.WAITING)
        if latest == "task_complete":
            return ThreadNotification("status", thread_id, SessionState.COMPLETED)
        if latest == "turn_aborted":
            return ThreadNotification("aborted", thread_id, SessionState.IDLE)
        return None

    def retain(self, paths: set[str]) -> None:
        normalized = {Path(path) for path in paths}
        self._cursors = {
            path: cursor for path, cursor in self._cursors.items() if path in normalized
        }


def _rollout_status_event(line: bytes) -> tuple[str, str | None] | None:
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        return None
    record_type = value.get("type")
    event = payload.get("type")
    if record_type == "event_msg" and event in (
        "task_started",
        "task_complete",
        "turn_aborted",
    ):
        return str(event), None
    call_id = payload.get("call_id")
    normalized_call_id = call_id if isinstance(call_id, str) else None
    if (
        record_type == "response_item"
        and event == "function_call"
        and payload.get("name") == "request_user_input"
    ):
        return "waiting_input", normalized_call_id
    if record_type == "response_item" and event == "function_call_output":
        return "input_received", normalized_call_id
    return None


def parse_notification(message: object) -> ThreadNotification | None:
    """Return a display-relevant event from a Codex JSON-RPC notification."""

    if not isinstance(message, Mapping) or "id" in message:
        return None
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, Mapping):
        return None

    if method == "thread/started":
        thread = params.get("thread")
        if not isinstance(thread, Mapping):
            return None
        thread_id = _thread_id(thread)
        if thread_id is None:
            return None
        return ThreadNotification(
            "upsert",
            thread_id,
            map_thread_status(thread.get("status")),
            thread=thread,
        )

    thread_id = _thread_id(params)
    if thread_id is None:
        return None

    if method == "thread/status/changed":
        status = params.get("status")
        if not isinstance(status, Mapping):
            return None
        return ThreadNotification("status", thread_id, map_thread_status(status))
    if method == "turn/started":
        return ThreadNotification("status", thread_id, SessionState.RUNNING)
    if method == "turn/completed":
        turn = params.get("turn")
        turn_status = turn.get("status") if isinstance(turn, Mapping) else None
        state = {
            "completed": SessionState.COMPLETED,
            "failed": SessionState.ERROR,
            "interrupted": SessionState.IDLE,
        }.get(turn_status if isinstance(turn_status, str) else "")
        return ThreadNotification("status", thread_id, state) if state is not None else None
    if method == "thread/name/updated":
        title = params.get("threadName")
        return ThreadNotification(
            "title",
            thread_id,
            title=title if isinstance(title, str) and title.strip() else "Untitled thread",
        )
    if method in ("thread/archived", "thread/deleted"):
        return ThreadNotification("remove", thread_id)
    return None


def parse_rate_limit_notification(message: object) -> QuotaSignals | None:
    """Return quota-display signals from a rate-limit update notification."""

    if not isinstance(message, Mapping) or "id" in message:
        return None
    if message.get("method") != "account/rateLimits/updated":
        return None
    params = message.get("params")
    if not isinstance(params, Mapping):
        return None
    return rate_limit_signals(params.get("rateLimits"))


def rate_limit_signals(rate_limits: object) -> QuotaSignals:
    """Translate one App Server rate-limit record into renderer-safe signals."""

    if not isinstance(rate_limits, Mapping):
        return {}

    signals: QuotaSignals = {}
    _add_quota_window(signals, "primary", rate_limits.get("primary"))
    _add_quota_window(signals, "secondary", rate_limits.get("secondary"))
    return signals


def _add_quota_window(signals: QuotaSignals, prefix: str, value: object) -> None:
    if not isinstance(value, Mapping):
        return
    used_percent = value.get("usedPercent")
    if isinstance(used_percent, (int, float)) and not isinstance(used_percent, bool):
        percent = float(used_percent)
        if isfinite(percent) and 0 <= percent <= 100:
            signals[f"{prefix}_percent"] = percent

    duration = value.get("windowDurationMins")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        minutes = int(duration)
        if minutes > 0 and minutes == duration:
            signals[f"{prefix}_label"] = _quota_window_label(minutes)

    resets_at = value.get("resetsAt")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        timestamp = float(resets_at)
        if isfinite(timestamp):
            with contextlib.suppress(OverflowError, OSError, ValueError):
                signals[f"{prefix}_reset_at"] = datetime.fromtimestamp(
                    timestamp, tz=timezone.utc
                ).isoformat()


def _quota_window_label(minutes: int) -> str:
    if minutes == 7 * 24 * 60:
        return "WEEK"
    if minutes % 60 == 0:
        return f"{minutes // 60}H"
    return f"{minutes}M"


def _thread_id(value: Mapping[object, object]) -> str | None:
    for key in ("threadId", "thread_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _is_waiting_flag(flag: object) -> bool:
    if isinstance(flag, str):
        normalized = flag.lower().replace("_", "").replace("-", "")
    elif isinstance(flag, Mapping):
        value = flag.get("type") or flag.get("name")
        if not isinstance(value, str):
            return False
        normalized = value.lower().replace("_", "").replace("-", "")
    else:
        return False
    return "waiting" in normalized or "approval" in normalized or "input" in normalized


class CodexPlugin:
    """Codex App Server adapter backed by a cached listener state."""

    manifest = PluginManifest(
        id="codex",
        name="Codex",
        components=("session", "quota"),
        actions=("select_session",),
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client_factory: Callable[[str, str | None], CodexAppServerClient] = CodexAppServerClient,
        thread_selector: Callable[[str], None] = select_codex_thread,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.validate_config(config)
        self.config = dict(config)
        self._client_factory = client_factory
        self._thread_selector = thread_selector
        self._sleep = sleep
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cache: tuple[AgentSession, ...] = ()
        self._quota_signals: QuotaSignals = {}
        self._recently_active: set[str] = set()
        self._rollout_paths: dict[str, str] = {}
        self._rollout_monitor = CodexRolloutMonitor()
        self._listener: threading.Thread | None = None
        self._rollout_watcher: threading.Thread | None = None
        self._client: CodexAppServerClient | None = None
        self._connected = False
        self._last_error: str | None = None

        if self.config.get("enabled", False):
            self._listener = threading.Thread(
                target=self._listen,
                name="codex-app-server-listener",
                daemon=True,
            )
            self._listener.start()
            self._rollout_watcher = threading.Thread(
                target=self._watch_rollouts,
                name="codex-rollout-watcher",
                daemon=True,
            )
            self._rollout_watcher.start()
            atexit.register(self.close)

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise CodexError("codex config must be a mapping")
        validate_codex_config(config)

    def _listen(self) -> None:
        delay = 1
        while not self._stop.is_set():
            client: CodexAppServerClient | None = None
            try:
                codex_path = self.config.get("codex_path") or "codex"
                codex_home = self.config.get("codex_home")
                client = self._client_factory(codex_path, codex_home)
                with self._lock:
                    self._client = client
                    self._connected = False
                    self._last_error = None

                client.start()
                client.initialize()
                client.send_initialized()
                self._replace_cache(client.list_threads())
                self._replace_quota_signals(client)
                with self._lock:
                    self._connected = True
                delay = 1
                refresher = threading.Thread(
                    target=self._refresh_metadata,
                    args=(client,),
                    name="codex-app-server-refresh",
                    daemon=True,
                )
                refresher.start()
                self._read_loop(client)
                if not self._stop.is_set():
                    raise CodexError("Codex App Server closed its output stream")
            except (CodexError, OSError) as exc:
                self._record_error(str(exc))
            except Exception as exc:
                self._record_error(f"Unexpected Codex listener failure: {exc}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except (CodexError, OSError) as exc:
                        self._record_error(f"Could not close Codex App Server: {exc}")
                with self._lock:
                    if self._client is client:
                        self._client = None
                        self._connected = False

            if not self._stop.is_set():
                self._sleep(delay)
                delay = min(delay * 2, 30)

    def _refresh_metadata(self, client: CodexAppServerClient) -> None:
        """Refresh cross-process chat discovery and quota on the live connection."""

        while not self._stop.wait(APP_SERVER_REFRESH_SECONDS):
            with self._lock:
                if self._client is not client or not self._connected:
                    return
            try:
                self._replace_cache(client.list_threads())
                self._replace_quota_signals(client)
            except (CodexError, OSError) as exc:
                self._record_error(f"Could not refresh Codex metadata: {exc}")
                return

    def _watch_rollouts(self) -> None:
        """Follow desktop/CLI turns that belong to other Codex processes."""

        while not self._stop.is_set():
            with self._lock:
                paths = dict(self._rollout_paths)
            self._rollout_monitor.retain(set(paths.values()))
            for thread_id, path in paths.items():
                notification = self._rollout_monitor.poll(thread_id, path)
                if notification is not None:
                    self._apply_notification(notification)
            self._stop.wait(ROLLOUT_REFRESH_SECONDS)

    def _read_loop(self, client: CodexAppServerClient) -> None:
        while not self._stop.is_set():
            message = client.read_line()
            if message is None:
                return
            notification = parse_notification(message)
            if notification is not None:
                self._apply_notification(notification)
                continue
            quota_signals = parse_rate_limit_notification(message)
            if quota_signals is not None:
                self._merge_quota_signals(quota_signals)

    def _replace_quota_signals(self, source: CodexAppServerClient | QuotaSignals) -> None:
        if isinstance(source, dict):
            signals = source
        else:
            try:
                signals = rate_limit_signals(source.read_rate_limits().get("rateLimits"))
            except CodexError as exc:
                LOGGER.info("Codex App Server does not provide rate limits: %s", exc)
                signals = {}
        with self._lock:
            self._quota_signals = signals

    def _merge_quota_signals(self, signals: QuotaSignals) -> None:
        """Merge sparse rolling updates without clearing unchanged windows."""

        with self._lock:
            self._quota_signals = {**self._quota_signals, **signals}

    def _replace_cache(self, threads: list[JsonObject]) -> None:
        sessions: list[AgentSession] = []
        active: set[str] = set()
        rollout_paths: dict[str, str] = {}
        with self._lock:
            previous = {session.id: session for session in self._cache}
        for thread in threads:
            session = self._session_from_thread(thread)
            if session is None:
                continue
            old_session = previous.get(session.id)
            if (
                session.state is SessionState.IDLE
                and old_session is not None
                and old_session.state
                in (SessionState.RUNNING, SessionState.WAITING, SessionState.COMPLETED)
            ):
                session = AgentSession(
                    session.id,
                    session.title,
                    old_session.state,
                    session.project,
                    session.directory,
                )
            sessions.append(session)
            path_value = thread.get("path", thread.get("rolloutPath"))
            if isinstance(path_value, str) and path_value:
                rollout_paths[session.id] = path_value
            if session.state in (SessionState.RUNNING, SessionState.WAITING):
                active.add(session.id)
            if len(sessions) == MAX_VISIBLE_THREADS:
                break
        with self._lock:
            self._cache = tuple(sessions)
            self._rollout_paths = rollout_paths
            self._recently_active &= {session.id for session in sessions}
            self._recently_active |= active

    @staticmethod
    def _session_from_thread(thread: JsonObject) -> AgentSession | None:
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        name = thread.get("name")
        preview = thread.get("preview")
        legacy_title = thread.get("title")
        title = next(
            (
                candidate.strip()
                for candidate in (name, legacy_title, preview)
                if isinstance(candidate, str) and candidate.strip()
            ),
            "Untitled thread",
        )
        directory_value = thread.get("cwd", thread.get("directory"))
        directory = directory_value if isinstance(directory_value, str) else None
        project_value = thread.get("project")
        project = project_value if isinstance(project_value, str) else None
        if project is None and directory:
            project = Path(directory.rstrip("/\\")).name or directory
        return AgentSession(
            id=thread_id,
            title=title,
            state=map_thread_status(thread.get("status")),
            project=project,
            directory=directory,
        )

    def _apply_notification(self, notification: ThreadNotification) -> None:
        with self._lock:
            sessions = list(self._cache)
            thread_id = notification.thread_id
            index = next((i for i, session in enumerate(sessions) if session.id == thread_id), None)

            if notification.kind == "remove":
                self._cache = tuple(session for session in sessions if session.id != thread_id)
                self._rollout_paths.pop(thread_id, None)
                self._recently_active.discard(thread_id)
                return

            if notification.kind == "upsert" and notification.thread is not None:
                raw_thread: JsonObject = {
                    key: value for key, value in notification.thread.items() if isinstance(key, str)
                }
                session = self._session_from_thread(raw_thread)
                if session is None:
                    return
                if index is not None:
                    sessions.pop(index)
                sessions.insert(0, session)
                if session.state in (SessionState.RUNNING, SessionState.WAITING):
                    self._recently_active.add(thread_id)
                self._cache = tuple(sessions[:MAX_VISIBLE_THREADS])
                self._recently_active &= {cached.id for cached in self._cache}
                return

            if index is None:
                session = AgentSession(thread_id, "Untitled thread", SessionState.IDLE)
            else:
                session = sessions.pop(index)

            if notification.kind == "title":
                updated = AgentSession(
                    session.id,
                    notification.title or "Untitled thread",
                    session.state,
                    session.project,
                    session.directory,
                )
                if index is None:
                    sessions.insert(0, updated)
                else:
                    sessions.insert(index, updated)
                self._cache = tuple(sessions[:MAX_VISIBLE_THREADS])
                return

            state = notification.state
            if state is None:
                return

            force_idle = notification.kind == "aborted"
            if force_idle:
                self._recently_active.discard(thread_id)
                state = SessionState.IDLE

            if state in (SessionState.RUNNING, SessionState.WAITING):
                self._recently_active.add(thread_id)
            elif (
                not force_idle and state is SessionState.IDLE and thread_id in self._recently_active
            ):
                state = SessionState.COMPLETED
                self._recently_active.discard(thread_id)
            elif (
                not force_idle
                and state is SessionState.IDLE
                and session.state is SessionState.COMPLETED
            ):
                state = SessionState.COMPLETED
            elif state is SessionState.ERROR or state is SessionState.COMPLETED:
                self._recently_active.discard(thread_id)

            updated = AgentSession(
                id=session.id,
                title=session.title,
                state=state,
                project=session.project,
                directory=session.directory,
            )
            # Activity changes recency, so keep the most recently active chat first.
            sessions.insert(0, updated)
            self._cache = tuple(sessions[:MAX_VISIBLE_THREADS])
            self._recently_active &= {cached.id for cached in self._cache}

    def mark_viewed(self, thread_id: str) -> None:
        """Clear the unread-completed indicator after opening a chat."""

        with self._lock:
            self._cache = tuple(
                AgentSession(
                    session.id,
                    session.title,
                    SessionState.IDLE
                    if session.id == thread_id and session.state is SessionState.COMPLETED
                    else session.state,
                    session.project,
                    session.directory,
                )
                for session in self._cache
            )

    def _record_error(self, detail: str) -> None:
        with self._lock:
            self._last_error = detail or "Codex app-server failed"

    def snapshot(self) -> PluginSnapshot:
        if not self.config.get("enabled", False):
            return PluginSnapshot(PluginHealth(False, "Disabled"))
        with self._lock:
            client = self._client
            connected = self._connected
            cache = self._cache
            quota_signals = dict(self._quota_signals)
            last_error = self._last_error
        alive = False
        if client is not None and connected:
            try:
                alive = bool(client.is_alive())
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                alive = False
        if not alive:
            return PluginSnapshot(
                PluginHealth(False, last_error or "Codex app-server not running"),
                cache,
                quota_signals,
            )
        return PluginSnapshot(PluginHealth(True, "Connected", "stdio"), cache, quota_signals)

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        if action != "select_session":
            raise CodexError(f"Unsupported Codex action: {action}")
        thread_id = params.get("session_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise CodexError("session_id is required")
        with self._lock:
            known = any(session.id == thread_id for session in self._cache)
        if not known:
            raise CodexError("Codex thread no longer exists in the current session list")
        self._thread_selector(thread_id)
        self.mark_viewed(thread_id)
        return {"result": "session_selected_in_codex", "session_id": thread_id}

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            client = self._client
            listener = self._listener
            rollout_watcher = self._rollout_watcher
        if client is not None:
            with contextlib.suppress(CodexError, OSError):
                client.close()
        if listener is not None and listener is not threading.current_thread():
            listener.join(timeout=2)
        if rollout_watcher is not None and rollout_watcher is not threading.current_thread():
            rollout_watcher.join(timeout=2)
