from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from queue import Queue
from tempfile import NamedTemporaryFile
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
import yaml

from stream_micro.bridge import BridgeService
from stream_micro.control_config import control_config_data, default_control_config
from stream_micro.plugins.base import SessionState
from stream_micro.plugins.codex import (
    CodexAppServerClient,
    CodexError,
    CodexPlugin,
    CodexRolloutMonitor,
    ThreadNotification,
    map_thread_status,
    parse_notification,
    parse_rate_limit_notification,
    rate_limit_signals,
    resolve_codex_command,
    thread_deep_link,
    validate_codex_config,
)


class FakeAppServerClient:
    """Scripted in-memory app-server client for listener tests."""

    def __init__(
        self,
        threads: list[dict[str, object]],
        *,
        rate_limits: dict[str, object] | None = None,
    ) -> None:
        self.threads = threads
        self.rate_limits = rate_limits
        self.calls: list[str] = []
        self.messages: Queue[dict[str, object] | None] = Queue()
        self.started = Event()
        self.alive = True
        self.read_count = 0
        self.list_count = 0
        self.rate_limit_count = 0
        self.alive_count = 0

    def start(self) -> None:
        self.calls.append("start")

    def initialize(self) -> dict[str, object]:
        self.calls.append("initialize")
        return {}

    def send_initialized(self) -> None:
        self.calls.append("initialized")

    def list_threads(self) -> list[dict[str, object]]:
        self.calls.append("list_threads")
        self.list_count += 1
        self.started.set()
        return self.threads

    def read_rate_limits(self) -> dict[str, object]:
        self.calls.append("rate_limits")
        self.rate_limit_count += 1
        return {"rateLimits": self.rate_limits}

    def read_line(self) -> dict[str, object] | None:
        self.read_count += 1
        return self.messages.get()

    def is_alive(self) -> bool:
        self.alive_count += 1
        return self.alive

    def close(self) -> None:
        self.calls.append("close")
        self.alive = False
        self.messages.put(None)

    def notify(self, message: dict[str, object]) -> None:
        self.messages.put(message)


class FakeTextInput:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, line: str) -> int:
        self.lines.append(line)
        return len(line)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeTextOutput:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def readline(self) -> str:
        return self.lines.pop(0) if self.lines else ""


class FakePopen:
    def __init__(self, lines: list[str], *, timeout_on_terminate: bool = False) -> None:
        self.stdin = FakeTextInput()
        self.stdout = FakeTextOutput(lines)
        self.returncode: int | None = None
        self.events: list[str] = []
        self.timeout_on_terminate = timeout_on_terminate

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(f"wait:{timeout}")
        if self.timeout_on_terminate and "kill" not in self.events:
            raise subprocess.TimeoutExpired("codex", timeout or 0)
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def _factory(client: FakeAppServerClient) -> Callable[[str, str | None], CodexAppServerClient]:
    return cast(Callable[[str, str | None], CodexAppServerClient], lambda _path, _home: client)


def _wait_for(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 1
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    pytest.fail("timed out waiting for fake app-server listener")


def _enabled_plugin(client: FakeAppServerClient) -> CodexPlugin:
    plugin = CodexPlugin(
        {"enabled": True, "codex_path": "not-a-real-codex", "codex_home": None},
        client_factory=_factory(client),
        sleep=lambda _delay: None,
    )
    assert client.started.wait(1)
    return plugin


def _json_lines(*messages: object) -> list[str]:
    return [json.dumps(message) + "\n" for message in messages]


def test_app_server_client_sends_handshake_and_parses_thread_list() -> None:
    process = FakePopen(
        _json_lines(
            {"jsonrpc": "2.0", "id": 1, "result": {"server": "ready"}},
            {"method": "warning", "params": {"message": "queued while listing"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"data": [{"id": "one"}, "bad"]}},
        )
    )
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process) as popen:
        client = CodexAppServerClient("fake-codex", "C:/codex-home")
        client.start()
        assert client.initialize() == {"server": "ready"}
        client.send_initialized()
        assert client.list_threads() == [{"id": "one"}]
        assert client.read_line() == {
            "method": "warning",
            "params": {"message": "queued while listing"},
        }

    popen.assert_called_once()
    command = popen.call_args.args[0]
    options = popen.call_args.kwargs
    assert command == ["fake-codex", "app-server", "--listen", "stdio://"]
    assert options["stdin"] is subprocess.PIPE
    assert options["stdout"] is subprocess.PIPE
    assert options["stderr"] is subprocess.DEVNULL
    assert options["shell"] is False
    assert options["text"] is True
    assert options["encoding"] == "utf-8"
    assert options["env"]["CODEX_HOME"] == "C:/codex-home"

    written = [json.loads(line) for line in process.stdin.lines]
    assert written[0]["jsonrpc"] == "2.0"
    assert written[0]["id"] == 1
    assert written[0]["method"] == "initialize"
    assert written[0]["params"]["clientInfo"]["name"] == "stream-micro"
    assert written[0]["params"]["clientInfo"]["title"] == "Stream Micro Bridge"
    from stream_micro import __version__

    assert written[0]["params"]["clientInfo"]["version"] == __version__
    assert written[1] == {"jsonrpc": "2.0", "method": "initialized", "params": {}}
    assert written[2] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "thread/list",
        "params": {
            "limit": 15,
            "sortKey": "recency_at",
            "sortDirection": "desc",
            "sourceKinds": ["cli", "vscode", "appServer"],
        },
    }


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_app_server_client_uses_no_console_flag_only_on_windows(monkeypatch, platform) -> None:
    monkeypatch.setattr("stream_micro.process_launch.sys.platform", platform)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(
        subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0, wShowWindow=1), raising=False
    )
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    process = FakePopen([])
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process) as popen:
        client = CodexAppServerClient("fake-codex")
        try:
            client.start()
            options = popen.call_args.kwargs
            assert options["creationflags"] == (0x08000000 if platform == "win32" else 0)
            if platform == "win32":
                assert options["startupinfo"].dwFlags & 1
                assert options["startupinfo"].wShowWindow == 0
            else:
                assert options["startupinfo"] is None
            assert options["shell"] is False
            assert options["stdin"] is subprocess.PIPE
            assert options["stdout"] is subprocess.PIPE
        finally:
            client.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console regression")
def test_app_server_client_real_child_has_no_console(monkeypatch) -> None:
    probe = (
        "import ctypes,json; "
        "print(json.dumps({'method':'console_probe','params':"
        "{'handle':ctypes.windll.kernel32.GetConsoleWindow()}}),flush=True)"
    )
    monkeypatch.setattr(
        "stream_micro.plugins.codex.resolve_codex_command",
        lambda _path: [sys.executable, "-c", probe],
    )
    client = CodexAppServerClient("test-owned-console-probe")
    try:
        client.start()
        assert client.read_line() == {"method": "console_probe", "params": {"handle": 0}}
    finally:
        client.close()


def test_app_server_client_reads_chatgpt_rate_limits() -> None:
    process = FakePopen(
        _json_lines(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"rateLimits": {"primary": {"usedPercent": 25}}},
            }
        )
    )
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process):
        client = CodexAppServerClient("fake-codex")
        client.start()
        assert client.read_rate_limits() == {"rateLimits": {"primary": {"usedPercent": 25}}}

    assert [json.loads(line) for line in process.stdin.lines] == [
        {"jsonrpc": "2.0", "id": 1, "method": "account/rateLimits/read", "params": {}}
    ]


def test_app_server_client_skips_malformed_stdout_and_raises_request_errors() -> None:
    process = FakePopen(
        [
            "not json\n",
            "[]\n",
            json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "no"}}) + "\n",
        ]
    )
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process):
        client = CodexAppServerClient("fake-codex")
        client.start()

        with pytest.raises(CodexError, match="initialize failed"):
            client.initialize()


def test_app_server_client_raises_on_response_eof() -> None:
    process = FakePopen([])
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process):
        client = CodexAppServerClient("fake-codex")
        client.start()

        with pytest.raises(CodexError, match="closed while waiting for thread/list"):
            client.list_threads()


def test_app_server_client_close_kills_after_terminate_timeout() -> None:
    process = FakePopen([], timeout_on_terminate=True)
    with patch("stream_micro.plugins.codex.subprocess.Popen", return_value=process):
        client = CodexAppServerClient("fake-codex")
        client.start()
        client.close()

    assert process.stdin.closed is True
    assert process.events == ["terminate", "wait:2", "kill", "wait:2"]
    assert client.is_alive() is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"type": "idle"}, SessionState.IDLE),
        ({"type": "notLoaded"}, SessionState.IDLE),
        ({"type": "active"}, SessionState.RUNNING),
        ({"type": "active", "activeFlags": ["waitingOnApproval"]}, SessionState.WAITING),
        (
            {"type": "active", "active_flags": [{"type": "waitingOnUserInput"}]},
            SessionState.WAITING,
        ),
        ({"type": "systemError"}, SessionState.ERROR),
        ({"type": "unrecognized"}, SessionState.IDLE),
        (None, SessionState.IDLE),
    ],
)
def test_map_thread_status_variants(status: object, expected: SessionState) -> None:
    assert map_thread_status(status) is expected


def test_rollout_monitor_detects_cross_process_turn_lifecycle(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
        + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
        + "\n",
        encoding="utf-8",
    )
    monitor = CodexRolloutMonitor()

    assert monitor.poll("thread-one", str(rollout)) == ThreadNotification(
        "status", "thread-one", SessionState.RUNNING
    )
    assert monitor.poll("thread-one", str(rollout)) is None

    with rollout.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
            + "\n"
        )
    assert monitor.poll("thread-one", str(rollout)) == ThreadNotification(
        "status", "thread-one", SessionState.COMPLETED
    )

    with rollout.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
            + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}})
            + "\n"
        )
    assert monitor.poll("thread-one", str(rollout)) == ThreadNotification(
        "aborted", "thread-one", SessionState.IDLE
    )


def test_rollout_monitor_does_not_mark_old_completed_turn_unread(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
        + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
        + "\n",
        encoding="utf-8",
    )

    assert CodexRolloutMonitor().poll("thread-one", str(rollout)) is None


def test_rollout_monitor_detects_user_input_waiting_and_resume(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "call-one",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monitor = CodexRolloutMonitor()

    assert monitor.poll("thread-one", str(rollout)) == ThreadNotification(
        "status", "thread-one", SessionState.WAITING
    )

    with rollout.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "function_call_output", "call_id": "call-one"},
                }
            )
            + "\n"
        )
    assert monitor.poll("thread-one", str(rollout)) == ThreadNotification(
        "status", "thread-one", SessionState.RUNNING
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {
                "method": "thread/status/changed",
                "params": {"threadId": "one", "status": {"type": "active"}},
            },
            ThreadNotification("status", "one", SessionState.RUNNING),
        ),
        (
            {
                "method": "thread/started",
                "params": {"thread": {"id": "two", "status": {"type": "idle"}}},
            },
            ThreadNotification(
                "upsert",
                "two",
                SessionState.IDLE,
                thread={"id": "two", "status": {"type": "idle"}},
            ),
        ),
        (
            {
                "method": "turn/completed",
                "params": {"threadId": "three", "turn": {"status": "completed"}},
            },
            ThreadNotification("status", "three", SessionState.COMPLETED),
        ),
        (
            {
                "method": "turn/completed",
                "params": {"threadId": "four", "turn": {"status": "failed"}},
            },
            ThreadNotification("status", "four", SessionState.ERROR),
        ),
        (
            {"method": "thread/archived", "params": {"threadId": "five"}},
            ThreadNotification("remove", "five"),
        ),
        ({"method": "unknown", "params": {"threadId": "one"}}, None),
        ({"method": "thread/started", "params": {}}, None),
        ({"id": 1, "method": "turn/completed", "params": {"threadId": "one"}}, None),
        ([], None),
    ],
)
def test_parse_notification_recognizes_only_safe_thread_events(
    message: object, expected: ThreadNotification | None
) -> None:
    assert parse_notification(message) == expected


def test_rate_limit_signals_support_primary_and_secondary_windows() -> None:
    signals = rate_limit_signals(
        {
            "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1730947200},
            "secondary": {
                "usedPercent": 42.5,
                "windowDurationMins": 10080,
                "resetsAt": 1731552000,
            },
        }
    )

    assert signals == {
        "primary_percent": 25.0,
        "primary_label": "5H",
        "primary_reset_at": "2024-11-07T02:40:00+00:00",
        "secondary_percent": 42.5,
        "secondary_label": "WEEK",
        "secondary_reset_at": "2024-11-14T02:40:00+00:00",
    }


def test_rate_limit_signals_ignore_malformed_values_and_notifications() -> None:
    assert rate_limit_signals(
        {
            "primary": {"usedPercent": True, "windowDurationMins": 0, "resetsAt": "never"},
            "secondary": {"usedPercent": 101, "resetsAt": float("inf")},
        }
    ) == {}
    assert parse_rate_limit_notification(
        {
            "method": "account/rateLimits/updated",
            "params": {"rateLimits": {"primary": {"usedPercent": 10}}},
        }
    ) == {"primary_percent": 10.0}
    assert parse_rate_limit_notification({"method": "thread/started", "params": {}}) is None


def test_validate_codex_config_accepts_omitted_and_valid_values() -> None:
    validate_codex_config({})
    validate_codex_config({"enabled": False, "codex_path": "codex", "codex_home": "C:/codex"})


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"enabled": "yes"}, "boolean"),
        ({"codex_path": 4}, "codex_path"),
        ({"codex_path": "  "}, "codex_path"),
        ({"codex_home": []}, "codex_home"),
        ({"codex_home": "\t"}, "codex_home"),
    ],
)
def test_validate_codex_config_rejects_invalid_values(
    config: dict[str, object], message: str
) -> None:
    with pytest.raises(CodexError, match=message):
        validate_codex_config(config)


def test_disabled_plugin_declares_only_the_documented_thread_selection_action() -> None:
    plugin = CodexPlugin({"enabled": False})

    assert plugin.manifest.id == "codex"
    assert plugin.manifest.name == "Codex"
    assert plugin.manifest.components == ("session", "quota")
    assert plugin.manifest.actions == ("select_session",)
    assert plugin.snapshot().health.detail == "Disabled"
    with pytest.raises(CodexError, match="Unsupported"):
        plugin.invoke("anything", {})

    plugin.close()
    plugin.close()


def test_codex_thread_deep_link_encodes_the_exact_local_thread_id() -> None:
    assert thread_deep_link("019f/thread id") == "codex://threads/019f%2Fthread%20id"
    with pytest.raises(CodexError, match="required"):
        thread_deep_link("  ")


def test_windows_command_resolution_prefers_the_npm_native_executable(tmp_path: Path) -> None:
    cmd = tmp_path / "codex.cmd"
    cmd.write_text("@echo off", encoding="utf-8")
    native = (
        tmp_path
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")

    with (
        patch("stream_micro.plugins.codex.os.name", "nt"),
        patch(
            "stream_micro.plugins.codex.shutil.which",
            side_effect=lambda name: str(cmd) if str(name).endswith(".cmd") else None,
        ),
    ):
        assert resolve_codex_command("codex") == [str(native)]


def test_select_session_opens_the_chat_and_marks_completed_as_viewed() -> None:
    client = FakeAppServerClient(
        [{"id": "one", "name": "Thread", "status": {"type": "idle"}}]
    )
    selected: list[str] = []
    plugin = CodexPlugin(
        {"enabled": True, "codex_path": "not-a-real-codex"},
        client_factory=_factory(client),
        thread_selector=selected.append,
        sleep=lambda _delay: None,
    )
    assert client.started.wait(1)
    try:
        client.notify(
            {
                "method": "turn/completed",
                "params": {"threadId": "one", "turn": {"status": "completed"}},
            }
        )
        _wait_for(lambda: plugin.snapshot().sessions[0].state is SessionState.COMPLETED)

        result = plugin.invoke("select_session", {"session_id": "one"})

        assert result == {"result": "session_selected_in_codex", "session_id": "one"}
        assert selected == ["one"]
        assert plugin.snapshot().sessions[0].state is SessionState.IDLE
    finally:
        plugin.close()


def test_fake_client_handshake_populates_the_initial_cached_sessions() -> None:
    client = FakeAppServerClient(
        [
            {
                "id": "one",
                "name": "First",
                "status": {"type": "active"},
                "cwd": "C:/work/p",
            },
            {"id": "two", "status": {"type": "idle"}},
            {
                "id": "three",
                "preview": "Third",
                "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
            },
            {"id": "four", "status": {"type": "systemError"}},
            {"id": "five", "status": {"type": "notLoaded"}},
            {"id": "six", "status": {"type": "idle"}},
        ]
    )
    plugin = _enabled_plugin(client)
    try:
        snapshot = plugin.snapshot()

        _wait_for(lambda: client.rate_limit_count == 1)
        assert client.calls[:5] == [
            "start",
            "initialize",
            "initialized",
            "list_threads",
            "rate_limits",
        ]
        assert client.list_count == 1
        assert snapshot.health.connected is True
        assert [session.id for session in snapshot.sessions] == [
            "one", "two", "three", "four", "five", "six"
        ]
        assert snapshot.sessions[0].state is SessionState.RUNNING
        assert snapshot.sessions[0].project == "p"
        assert snapshot.sessions[0].directory == "C:/work/p"
        assert snapshot.sessions[1].title == "Untitled thread"
        assert snapshot.sessions[2].state is SessionState.WAITING
        assert snapshot.sessions[3].state is SessionState.ERROR
    finally:
        plugin.close()


def test_listener_caches_initial_and_updated_rate_limits() -> None:
    client = FakeAppServerClient(
        [{"id": "one", "status": {"type": "idle"}}],
        rate_limits={
            "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1730947200},
            "secondary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 1731552000},
        },
    )
    plugin = _enabled_plugin(client)
    try:
        _wait_for(lambda: plugin.snapshot().signals == {
            "primary_percent": 25.0,
            "primary_label": "5H",
            "primary_reset_at": "2024-11-07T02:40:00+00:00",
            "secondary_percent": 42.0,
            "secondary_label": "WEEK",
            "secondary_reset_at": "2024-11-14T02:40:00+00:00",
        })

        client.notify(
            {
                "method": "account/rateLimits/updated",
                "params": {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 31,
                            "windowDurationMins": 300,
                            "resetsAt": 1730948100,
                        }
                    }
                },
            }
        )
        _wait_for(lambda: plugin.snapshot().signals == {
            "primary_percent": 31.0,
            "primary_label": "5H",
            "primary_reset_at": "2024-11-07T02:55:00+00:00",
            "secondary_percent": 42.0,
            "secondary_label": "WEEK",
            "secondary_reset_at": "2024-11-14T02:40:00+00:00",
        })
    finally:
        plugin.close()


def test_snapshot_uses_cache_without_listener_client_io_and_detects_disconnect() -> None:
    client = FakeAppServerClient([{"id": "one", "status": {"type": "idle"}}])
    plugin = _enabled_plugin(client)
    try:
        reads_before = client.read_count
        lists_before = client.list_count
        snapshot = plugin.snapshot()

        assert snapshot.sessions[0].id == "one"
        assert client.read_count == reads_before
        assert client.list_count == lists_before

        client.alive = False
        disconnected = plugin.snapshot()
        assert disconnected.health.connected is False
        assert disconnected.health.detail == "Codex app-server not running"
        assert disconnected.sessions == snapshot.sessions
    finally:
        plugin.close()


def test_listener_updates_sessions_and_marks_active_to_idle_as_completed() -> None:
    client = FakeAppServerClient([{"id": "one", "title": "Thread", "status": {"type": "idle"}}])
    plugin = _enabled_plugin(client)
    try:
        client.notify(
            {
                "method": "thread/status/changed",
                "params": {"threadId": "one", "status": {"type": "active"}},
            }
        )
        _wait_for(lambda: plugin.snapshot().sessions[0].state is SessionState.RUNNING)

        client.notify(
            {
                "method": "turn/completed",
                "params": {"threadId": "one", "turn": {"status": "completed"}},
            }
        )
        _wait_for(lambda: plugin.snapshot().sessions[0].state is SessionState.COMPLETED)

        client.notify(
            {
                "method": "thread/started",
                "params": {
                    "thread": {
                        "id": "new",
                        "status": {
                            "type": "active",
                            "activeFlags": ["waitingOnUserInput"],
                        },
                    }
                },
            }
        )
        _wait_for(
            lambda: any(
                session.id == "new" and session.state is SessionState.WAITING
                for session in plugin.snapshot().sessions
            )
        )
    finally:
        plugin.close()


def test_malformed_or_unrecognized_listener_events_do_not_crash_the_listener() -> None:
    client = FakeAppServerClient([{"id": "one", "status": {"type": "idle"}}])
    plugin = _enabled_plugin(client)
    try:
        client.notify({"method": "thread/status/changed", "params": {"threadId": 4}})
        client.notify({"method": "unrecognized", "params": {"threadId": "one"}})
        client.notify(
            {
                "method": "thread/status/changed",
                "params": {"threadId": "one", "status": {"type": "active"}},
            }
        )

        _wait_for(lambda: plugin.snapshot().sessions[0].state is SessionState.RUNNING)
        assert plugin.snapshot().health.connected is True
    finally:
        plugin.close()


def test_crashed_fake_client_reports_disconnected_health_without_losing_cache() -> None:
    client = FakeAppServerClient([{"id": "one", "status": {"type": "active"}}])
    plugin = _enabled_plugin(client)
    try:
        client.alive = False

        snapshot = plugin.snapshot()

        assert snapshot.health.connected is False
        assert snapshot.sessions[0].state is SessionState.RUNNING
    finally:
        plugin.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_bridge_service_registers_codex_only_when_enabled(tmp_path: Path, enabled: bool) -> None:
    data = control_config_data(default_control_config())
    data["plugins"]["codex"] = {
        "enabled": enabled,
        "codex_path": str(tmp_path / "definitely-not-codex"),
        "codex_home": None,
    }
    with NamedTemporaryFile(
        mode="w", suffix=".yaml", dir=tmp_path, delete=False, encoding="utf-8"
    ) as config_file:
        yaml.safe_dump(data, config_file)
        config_path = Path(config_file.name)

    service = BridgeService(config_path)
    try:
        assert ("codex" in service.plugins) is enabled
    finally:
        plugin = service.plugins.get("codex")
        if isinstance(plugin, CodexPlugin):
            plugin.close()
        config_path.unlink()
