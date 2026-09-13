from pathlib import Path
from typing import Any, cast

import httpx
from fastapi.testclient import TestClient

from stream_micro.bridge import BridgeService
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.plugins.claude_code import ClaudeCodePlugin
from stream_micro.plugins.antigravity import AntigravityPlugin
from stream_micro.plugins.opencode import OpenCodePlugin
from stream_micro.server import create_app


def opencode_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/global/health":
        return httpx.Response(200, json={"healthy": True})
    if request.method == "GET" and request.url.path == "/session":
        return httpx.Response(200, json=[{"id": "session-1", "title": "Agent task"}])
    if request.url.path == "/session/status":
        return httpx.Response(200, json={"session-1": {"type": "idle"}})
    if request.method == "POST" and request.url.path == "/session":
        return httpx.Response(200, json={"id": "new-session"})
    return httpx.Response(404)


def app_with_plugin(tmp_path: Path) -> TestClient:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    app = create_app(config_path)
    app.state.bridge.plugins["opencode"] = OpenCodePlugin(
        {"enabled": True, "endpoints": ["http://127.0.0.1:4096"], "password_env": None},
        client=httpx.Client(transport=httpx.MockTransport(opencode_handler)),
        port_provider=lambda: {4096},
    )
    return TestClient(app)


def test_local_api_returns_config_plugin_status_and_sessions(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        config = client.get("/api/config")
        plugins = client.get("/api/plugins")
        snapshots = client.get("/api/plugins/snapshots")
        status = client.get("/api/status")
        sessions = client.get("/api/opencode/sessions")

    assert config.status_code == 200
    assert config.json()["stream_deck"]["model"] == "mk2"
    assert plugins.json()[0]["id"] == "opencode"
    assert snapshots.json()["opencode"]["sessions"][0]["id"] == "session-1"
    assert status.json()["plugins"]["opencode"]["health"]["connected"] is True
    assert sessions.json()[0]["id"] == "session-1"


def test_basic_status_skips_expensive_plugin_snapshots(tmp_path: Path, monkeypatch) -> None:
    """The dashboard heartbeat must not re-poll every OpenCode Session."""

    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        monkeypatch.setattr(
            service,
            "snapshots",
            lambda: (_ for _ in ()).throw(AssertionError("snapshots should not run")),
        )
        response = client.get("/api/status?include_plugins=false")

    assert response.status_code == 200
    assert "plugins" not in response.json()


def test_claude_code_hook_updates_the_provider_snapshot(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["claude_code"] = ClaudeCodePlugin(
            {"enabled": True, "claude_home": str(tmp_path)},
            launcher=lambda _session_id, _directory: None,
        )
        hook = client.post(
            "/api/hooks/claude-code",
            json={
                "session_id": "claude-1",
                "cwd": str(tmp_path),
                "hook_event_name": "PermissionRequest",
            },
        )
        snapshots = client.get("/api/plugins/snapshots")

    assert hook.status_code == 200
    assert hook.json()["state"] == "waiting"
    assert snapshots.json()["claude_code"]["sessions"][0]["state"] == "waiting"


def test_claude_code_quota_endpoint_updates_the_provider_snapshot(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["claude_code"] = ClaudeCodePlugin(
            {"enabled": True, "claude_home": str(tmp_path)},
            launcher=lambda _session_id, _directory: None,
        )
        quota = client.post(
            "/api/hooks/claude-code/quota",
            json={
                "session_id": "claude-1",
                "rate_limits": {
                    "five_hour": {"used_percentage": 18, "resets_at": 1_738_425_600},
                    "seven_day": {"used_percentage": 44, "resets_at": 1_738_857_600},
                },
            },
        )
        snapshots = client.get("/api/plugins/snapshots")

    assert quota.status_code == 200
    assert quota.json()["quota_updated"] is True
    assert snapshots.json()["claude_code"]["signals"]["primary_percent"] == 18.0
    assert snapshots.json()["claude_code"]["signals"]["secondary_label"] == "7D"


def test_antigravity_status_endpoint_updates_snapshot(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["antigravity"] = AntigravityPlugin(
            {"enabled": True}, launcher=lambda _id, _cwd: None
        )
        response = client.post(
            "/api/hooks/antigravity/status",
            json={
                "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                "cwd": str(tmp_path),
                "agent_state": "working",
                "tool_confirmation_pending": False,
                "task_count": 0,
                "quota": {},
            },
        )
        snapshots = client.get("/api/plugins/snapshots")

    assert response.status_code == 200
    assert snapshots.json()["antigravity"]["sessions"][0]["state"] == "running"


def test_antigravity_status_endpoint_rejects_sensitive_fields(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["antigravity"] = AntigravityPlugin(
            {"enabled": True}, launcher=lambda _id, _cwd: None
        )
        response = client.post(
            "/api/hooks/antigravity/status",
            json={
                "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                "cwd": str(tmp_path),
                "agent_state": "idle",
                "tool_confirmation_pending": False,
                "task_count": 0,
                "quota": {},
                "transcript_path": "secret",
            },
        )

    assert response.status_code == 422


def test_config_api_never_returns_persisted_cookie_and_blank_save_preserves_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["opencode"]["enabled"] = False
    data["plugins"]["ollama_quota"]["enabled"] = False
    data["plugins"]["ollama_quota"]["cookie_value"] = "server-only-secret"
    write_control_config(path, parse_control_config(data))
    service = BridgeService(path, plugins={})
    app = create_app(path, service=service)

    with TestClient(app) as client:
        response = client.get("/api/config")
        public = response.json()
        saved = client.put("/api/config", json=public)

    assert "server-only-secret" not in response.text
    assert "server-only-secret" not in saved.text
    assert public["plugins"]["ollama_quota"]["cookie_value"] == ""
    assert public["plugins"]["ollama_quota"]["cookie_configured"] is True
    assert service.config.plugins["ollama_quota"]["cookie_value"] == "server-only-secret"


def test_action_rejects_actions_not_declared_by_plugin(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        response = client.post(
            "/api/actions", json={"plugin": "opencode", "action": "shell", "params": {}}
        )

    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


def test_session_select_endpoint_uses_openchamber_navigation(
    tmp_path: Path, monkeypatch
) -> None:
    selected: list[str] = []
    monkeypatch.setattr(
        "stream_micro.bridge.select_openchamber_session",
        lambda *, session_id, directory: selected.append(session_id),
    )
    with app_with_plugin(tmp_path) as client:
        response = client.post("/api/opencode/sessions/session-1/select")

    assert response.status_code == 200
    assert response.json() == {
        "result": "session_selected_in_openchamber",
        "session_id": "session-1",
    }
    assert selected == ["session-1"]


def test_legacy_select_action_uses_the_same_openchamber_navigation(
    tmp_path: Path, monkeypatch
) -> None:
    selected: list[str] = []
    monkeypatch.setattr(
        "stream_micro.bridge.select_openchamber_session",
        lambda *, session_id, directory: selected.append(session_id),
    )
    with app_with_plugin(tmp_path) as client:
        response = client.post(
            "/api/actions",
            json={
                "plugin": "opencode",
                "action": "select_session",
                "params": {"session_id": "session-1"},
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == "session_selected_in_openchamber"
    assert selected == ["session-1"]


def test_local_server_serves_built_dashboard(tmp_path: Path) -> None:
    with app_with_plugin(tmp_path) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="root"' in response.text
