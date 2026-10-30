import json
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from stream_micro.plugins.base import SessionState
from stream_micro.plugins.opencode import (
    STALE_THRESHOLD_SECONDS,
    OpenCodeError,
    OpenCodePlugin,
    default_loopback_ports,
    listening_loopback_ports,
)


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/global/health":
        return httpx.Response(200, json={"healthy": True, "version": "1.0"})
    if request.url.path == "/experimental/session":
        return httpx.Response(
            200,
            json=[
                {
                    "id": "old",
                    "title": "Created later but used earlier",
                    "projectID": "project-a",
                    "directory": "C:\\work\\project-a",
                    "time": {"created": 20, "updated": 10},
                },
                {
                    "id": "one",
                    "title": "Refactor API",
                    "projectID": "project-a",
                    "directory": "C:\\work\\project-a",
                    "time": {"created": 1, "updated": 30},
                },
                {
                    "id": "two",
                    "title": "Bug fix",
                    "projectID": "project-b",
                    "directory": "C:\\work\\project-b",
                    "time": {"created": 5, "updated": 40},
                },
                {
                    "id": "three",
                    "title": "Docs",
                    "projectID": "project-c",
                    "directory": "C:\\work\\project-c",
                    "time": {"created": 6, "updated": 50},
                },
                {
                    "id": "child",
                    "title": "Delegated review",
                    "parentID": "one",
                    "projectID": "project-a",
                    "directory": "C:\\work\\project-a",
                    "time": {"created": 7, "updated": 60},
                },
            ],
        )
    if request.url.path == "/session":
        if request.method == "POST":
            return httpx.Response(200, json={"id": "new", "title": "New session"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": "one",
                    "title": "Refactor API",
                    "projectID": "project-a",
                    "directory": "C:\\work\\project-a",
                }
            ],
        )
    if request.url.path == "/session/status":
        return httpx.Response(
            200,
            json={
                "one": {"type": "busy"},
                "two": {"type": "retry", "attempt": 1, "message": "rate limit", "next": 30},
            },
        )
    if request.url.path == "/permission":
        return httpx.Response(
            200,
            json=[
                {"id": "perm-1", "sessionID": "two", "permission": "bash", "patterns": ["*"]},
            ],
        )
    if request.url.path == "/question":
        return httpx.Response(200, json=[])
    if request.url.path == "/tui/select-session":
        assert json.loads(request.content) == {"sessionID": "one"}
        return httpx.Response(200, json=True)
    if request.url.path == "/session/one/prompt_async":
        return httpx.Response(204)
    if request.url.path == "/session/one/abort":
        return httpx.Response(200, json=True)
    if request.url.path.startswith("/session/") and request.url.path.endswith("/message"):
        sid = request.url.path.split("/")[2]
        if sid == "one":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 1, "completed": 2},
                            "finish": "tool-calls",
                        },
                        "parts": [],
                    }
                ],
            )
        if sid == "two":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_y",
                            "role": "assistant",
                            "time": {"created": 3, "completed": 4},
                            "finish": "tool-calls",
                        },
                        "parts": [],
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "info": {
                        "id": "msg_z",
                        "role": "assistant",
                        "time": {"created": 5, "completed": 6},
                        "finish": "stop",
                    },
                    "parts": [],
                }
            ],
        )
    return httpx.Response(404)


def plugin() -> OpenCodePlugin:
    return OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        port_provider=lambda: {4096, 9000},
    )


def test_discovery_uses_configured_and_listening_loopback_endpoints() -> None:
    found = plugin().discover()

    assert found[0].url == "http://127.0.0.1:4096"


def test_snapshot_recovers_when_server_restarts_on_a_different_port() -> None:
    active_port = 4096

    def restarting_handler(request: httpx.Request) -> httpx.Response:
        if request.url.port != active_port:
            raise httpx.ConnectError("Connection refused", request=request)
        return handler(request)

    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"]},
        client=httpx.Client(transport=httpx.MockTransport(restarting_handler)),
        port_provider=lambda: {active_port},
    )
    assert client.snapshot().health.connected
    active_port = 9000
    disconnected = client.snapshot()
    assert not disconnected.health.connected
    assert disconnected.health.detail == "OpenCode server disconnected; waiting to reconnect"
    assert client.endpoint is None
    reconnected = client.snapshot()
    assert reconnected.health.connected
    assert reconnected.health.endpoint == "http://127.0.0.1:9000"
    assert reconnected.sessions


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_listener_discovery_runs_without_a_windows_console(monkeypatch, platform: str) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, "  TCP  127.0.0.1:4096  0.0.0.0:0  LISTENING  100\n"
        )

    monkeypatch.setattr("stream_micro.process_launch.sys.platform", platform)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(
        subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0, wShowWindow=1), raising=False
    )
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    assert default_loopback_ports() == {4096}
    assert calls[0][1]["creationflags"] == (0x08000000 if platform == "win32" else 0)
    assert calls[0][1]["capture_output"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console regression")
def test_listener_discovery_child_has_no_console_on_windows(monkeypatch) -> None:
    real_run = subprocess.run
    console_handles = []

    def probe_console(command, **kwargs):
        result = real_run(
            [
                sys.executable,
                "-c",
                "import ctypes, sys; "
                "sys.stderr.write(str(ctypes.windll.kernel32.GetConsoleWindow())); "
                "print(' TCP 127.0.0.1:4096 0.0.0.0:0 LISTENING 100')",
            ],
            **kwargs,
        )
        console_handles.append(result.stderr)
        return result

    monkeypatch.setattr(subprocess, "run", probe_console)
    assert default_loopback_ports() == {4096}
    assert console_handles == ["0"]


def test_snapshot_reads_open_code_session_status() -> None:
    snapshot = plugin().snapshot()

    assert snapshot.health.connected is True
    assert snapshot.sessions[0].title == "Docs"
    assert snapshot.sessions[0].project == "project-c"
    assert snapshot.sessions[0].state is SessionState.IDLE
    assert snapshot.sessions[1].title == "Bug fix"
    assert snapshot.sessions[1].state is SessionState.WAITING
    assert snapshot.sessions[2].title == "Refactor API"
    assert snapshot.sessions[2].state is SessionState.RUNNING
    assert snapshot.sessions[3].id == "old"
    assert all(session.id != "child" for session in snapshot.sessions)


def test_busy_to_idle_transition_shows_completed() -> None:
    client = plugin()
    snapshot = client.snapshot()
    refactor = next(s for s in snapshot.sessions if s.id == "one")
    assert refactor.state is SessionState.RUNNING

    def idle_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session/status":
            return httpx.Response(200, json={"one": {"type": "idle"}})
        return handler(request)

    client._client = httpx.Client(transport=httpx.MockTransport(idle_handler))
    snapshot2 = client.snapshot()
    refactor2 = next(s for s in snapshot2.sessions if s.id == "one")
    assert refactor2.state is SessionState.COMPLETED


def test_empty_status_falls_back_to_message_polling() -> None:
    """When /session/status returns {} (OpenCode v1.18.7+), derive RUNNING
    from the last assistant message lacking time.completed, and COMPLETED
    once it appears."""

    def empty_status_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "one",
                        "title": "Refactor API",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 30},
                    },
                ],
            )
        if request.url.path == "/session/one/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 1},
                            "finish": "",
                        },
                        "parts": [
                            {"type": "tool", "state": {"status": "running"}},
                        ],
                    }
                ],
            )
        if request.url.path == "/permission":
            return httpx.Response(200, json=[])
        if request.url.path == "/question":
            return httpx.Response(200, json=[])
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        return httpx.Response(404)

    client = plugin()
    client._client = httpx.Client(transport=httpx.MockTransport(empty_status_handler))

    snapshot1 = client.snapshot()
    session1 = next(s for s in snapshot1.sessions if s.id == "one")
    assert session1.state is SessionState.RUNNING

    def completed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session/one/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 1, "completed": 2},
                            "finish": "stop",
                        }
                    }
                ],
            )
        return empty_status_handler(request)

    client._client = httpx.Client(transport=httpx.MockTransport(completed_handler))
    snapshot2 = client.snapshot()
    session2 = next(s for s in snapshot2.sessions if s.id == "one")
    assert session2.state is SessionState.COMPLETED


def test_running_tool_part_detected_as_running() -> None:
    """When /session/status is empty but the last message has a tool part
    with state.status='running', the session should show RUNNING."""

    def tool_running_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "one",
                        "title": "Active tool",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 30},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/one/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 10, "completed": 20},
                            "finish": "tool-calls",
                        },
                        "parts": [
                            {"type": "step-start"},
                            {"type": "tool", "tool": "bash", "state": {"status": "running"}},
                        ],
                    }
                ],
            )
        if request.url.path == "/permission":
            return httpx.Response(200, json=[])
        if request.url.path == "/question":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = plugin()
    client._client = httpx.Client(transport=httpx.MockTransport(tool_running_handler))
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is SessionState.RUNNING


def test_question_tool_part_detected_as_waiting() -> None:
    """When the last message has a tool part with tool='question' and
    state.status='running', the session should show WAITING, not RUNNING.

    Real OpenCode API: the assistant message has ``time.completed`` absent
    (the turn is still open while the user hasn't answered), and the
    question tool part has ``state.status='running'``.
    """

    def question_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "one",
                        "title": "Asking question",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 30},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={"one": {"type": "busy"}})
        if request.url.path == "/session/one/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 10},
                            "finish": "",
                        },
                        "parts": [
                            {"type": "step-start"},
                            {"type": "reasoning", "text": "thinking..."},
                            {"type": "text", "text": "I have a question..."},
                            {
                                "type": "tool",
                                "tool": "question",
                                "state": {
                                    "status": "running",
                                    "input": {
                                        "questions": [
                                            {
                                                "header": "Test",
                                                "question": "Pick one",
                                                "options": [
                                                    {"label": "A", "description": "opt A"},
                                                    {"label": "B", "description": "opt B"},
                                                ],
                                            }
                                        ]
                                    },
                                },
                            },
                        ],
                    }
                ],
            )
        if request.url.path == "/permission":
            return httpx.Response(200, json=[])
        if request.url.path == "/question":
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/api/session/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = plugin()
    client._client = httpx.Client(transport=httpx.MockTransport(question_handler))
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is SessionState.WAITING


def test_permission_tool_part_detected_as_waiting() -> None:
    """When the last message has a tool part with tool='permission' and
    state.status='pending', the session should show WAITING, not RUNNING."""

    def permission_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "one",
                        "title": "Needs approval",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 30},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={"one": {"type": "busy"}})
        if request.url.path == "/session/one/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_x",
                            "role": "assistant",
                            "time": {"created": 10, "completed": 20},
                            "finish": "tool-calls",
                        },
                        "parts": [
                            {"type": "step-start"},
                            {"type": "tool", "tool": "permission", "state": {"status": "pending"}},
                        ],
                    }
                ],
            )
        if request.url.path == "/permission":
            return httpx.Response(200, json=[])
        if request.url.path == "/question":
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/api/session/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = plugin()
    client._client = httpx.Client(transport=httpx.MockTransport(permission_handler))
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is SessionState.WAITING


def test_v2_question_endpoint_checked_even_when_v1_permission_exists() -> None:
    """v2 per-session question endpoints must be checked even when v1
    /permission already returned an (unrelated) pending item."""

    def mixed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "perm-sess",
                        "title": "Has permission",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 10},
                    },
                    {
                        "id": "quest-sess",
                        "title": "Has question",
                        "projectID": "project-b",
                        "directory": "C:\\work\\project-b",
                        "time": {"created": 2, "updated": 20},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/permission":
            # v1 /permission returns a pending item for a DIFFERENT session
            return httpx.Response(
                200,
                json=[{"id": "perm-1", "sessionID": "perm-sess", "permission": "bash"}],
            )
        if request.url.path == "/question":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/session/quest-sess/question":
            # v2 per-session question endpoint finds the question
            return httpx.Response(200, json={"data": [{"id": "q-1"}]})
        if request.url.path == "/api/session/perm-sess/permission":
            return httpx.Response(200, json={"data": [{"id": "perm-1"}]})
        if request.url.path.startswith("/api/session/"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/message"):
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_z",
                            "role": "assistant",
                            "time": {"created": 5, "completed": 6},
                            "finish": "stop",
                        },
                        "parts": [],
                    }
                ],
            )
        return httpx.Response(404)

    client = plugin()
    client._client = httpx.Client(transport=httpx.MockTransport(mixed_handler))
    snapshot = client.snapshot()
    quest = next(s for s in snapshot.sessions if s.id == "quest-sess")
    assert quest.state is SessionState.WAITING


def test_deep_activity_checks_are_limited_to_visible_sessions() -> None:
    """A long history must not turn one dashboard refresh into hundreds of requests."""

    calls: list[str] = []

    def many_sessions_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": f"session-{index}",
                        "title": f"Session {index}",
                        "time": {"updated": 10 - index},
                    }
                    for index in range(1, 8)
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path in ("/permission", "/question"):
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/api/session/"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(many_sessions_handler)),
        port_provider=lambda: {4096},
    )

    snapshot = client.snapshot()

    assert len(snapshot.sessions) == 7
    assert "/session/session-5/message" in calls
    assert "/session/session-6/message" not in calls
    assert "/api/session/session-6/question" not in calls


def test_select_session_clears_completed() -> None:
    client = plugin()
    snapshot = client.snapshot()
    assert next(s for s in snapshot.sessions if s.id == "one").state is SessionState.RUNNING

    def idle_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session/status":
            return httpx.Response(200, json={"one": {"type": "idle"}})
        return handler(request)

    client._client = httpx.Client(transport=httpx.MockTransport(idle_handler))
    client.snapshot()
    assert "one" in client._completed

    client.mark_viewed("one")
    assert "one" not in client._completed


def test_select_session_is_the_only_supported_action() -> None:
    client = plugin()

    assert client.invoke("select_session", {"session_id": "one"}) is True
    with pytest.raises(OpenCodeError, match="Unsupported"):
        client.invoke("shell", {})


def test_netstat_parser_only_returns_loopback_listening_ports() -> None:
    output = "\n".join(
        (
            "  TCP    127.0.0.1:4096       0.0.0.0:0      LISTENING       100",
            "  TCP    0.0.0.0:22             0.0.0.0:0      LISTENING       101",
            "  TCP    [::1]:9000             [::]:0         LISTENING       102",
        )
    )

    assert listening_loopback_ports(output) == {4096, 9000}


def test_stale_message_without_completed_shows_idle() -> None:
    """When a session crashed or was killed mid-turn, the last assistant
    message has no ``time.completed`` but is old.  The session should NOT
    be shown as RUNNING — it should be IDLE (or COMPLETED via the
    ``_recently_busy`` transition)."""

    def stale_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "crashed",
                        "title": "Crashed session",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 1},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/crashed/message":
            # created is 5 minutes ago (300_000 ms), no completed
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_old",
                            "role": "assistant",
                            "time": {"created": 300_000},
                            "finish": "",
                        },
                        "parts": [],
                    }
                ],
            )
        if request.url.path in ("/permission", "/question"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(stale_handler)),
        port_provider=lambda: {4096},
        time_provider=lambda: 300_000 + 10,  # now is 10 seconds after created
    )
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is not SessionState.RUNNING


def test_stale_tool_part_shows_idle() -> None:
    """When a tool part is stuck at state.status='running' but the tool
    started long ago, the session was killed mid-tool and should NOT be
    shown as RUNNING."""

    def stale_tool_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "stuck",
                        "title": "Stuck tool",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 1},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/stuck/message":
            # Tool started 5 minutes ago, still "running"
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_old",
                            "role": "assistant",
                            "time": {"created": 300_000},
                            "finish": "",
                        },
                        "parts": [
                            {
                                "type": "tool",
                                "tool": "bash",
                                "state": {
                                    "status": "running",
                                    "time": {"start": 300_000},
                                },
                            },
                        ],
                    }
                ],
            )
        if request.url.path in ("/permission", "/question"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(stale_tool_handler)),
        port_provider=lambda: {4096},
        time_provider=lambda: 300_000 + 10 + STALE_THRESHOLD_SECONDS * 1000,
    )
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is not SessionState.RUNNING


def test_finish_empty_string_with_completed_is_not_running() -> None:
    """When a message has finish='' (empty string) AND time.completed set,
    the turn ended normally without tool-calls.  The session should NOT
    be shown as RUNNING."""

    def finish_empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "done",
                        "title": "Finished normally",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 1},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/done/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_done",
                            "role": "assistant",
                            "time": {"created": 1000, "completed": 2000},
                            "finish": "",
                        },
                        "parts": [],
                    }
                ],
            )
        if request.url.path in ("/permission", "/question"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(finish_empty_handler)),
        port_provider=lambda: {4096},
    )
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is not SessionState.RUNNING


def test_recent_message_without_completed_is_running() -> None:
    """A message created very recently with no completed timestamp should
    still be detected as RUNNING — the staleness guard must not break the
    positive case."""

    def recent_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.0"})
        if request.url.path == "/experimental/session":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "live",
                        "title": "Actively generating",
                        "projectID": "project-a",
                        "directory": "C:\\work\\project-a",
                        "time": {"created": 1, "updated": 1},
                    },
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/live/message":
            # created 2 seconds ago — well within the staleness threshold
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "id": "msg_live",
                            "role": "assistant",
                            "time": {"created": 1700000000000},
                            "finish": None,
                        },
                        "parts": [],
                    }
                ],
            )
        if request.url.path in ("/permission", "/question"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    # now is 2 seconds after created (in seconds; created is in ms)
    now = 1700000000000 / 1000 + 2
    client = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(recent_handler)),
        port_provider=lambda: {4096},
        time_provider=lambda: now,
    )
    snapshot = client.snapshot()
    assert snapshot.sessions[0].state is SessionState.RUNNING
