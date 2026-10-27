from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from stream_micro.bridge import BridgeService
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.plugins.antigravity import AntigravityPlugin
from stream_micro.plugins.claude_code import ClaudeCodePlugin
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


def test_config_api_rejects_invalid_disabled_antigravity_config_without_persisting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "control.yaml"
    write_control_config(path, default_control_config())
    original = path.read_text(encoding="utf-8")
    service = BridgeService(path, plugins={})
    app = create_app(path, service=service)
    invalid = service.public_config_data()
    invalid["plugins"]["antigravity"] = {
        "enabled": False,
        "agy_path": None,
        "agy_home": None,
        "max_sessions": 16,
    }

    with TestClient(app) as client:
        response = client.put("/api/config", json=invalid)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "plugins.antigravity.max_sessions must be an integer from 1 to 15"
    )
    assert path.read_text(encoding="utf-8") == original


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


def test_dashboard_scripts_are_javascript_even_if_the_os_maps_js_to_text(
    tmp_path: Path, monkeypatch
) -> None:
    import mimetypes
    import re

    # Windows registry entries can map .js to text/plain; browsers then refuse
    # to run the dashboard's module script and the page stays blank.
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    with app_with_plugin(tmp_path) as client:
        index = client.get("/").text
        script = re.search(r'src="(/assets/[^"]+\.js)"', index)
        assert script is not None
        response = client.get(script.group(1))

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_invalid_provider_setting_is_rejected_without_saving(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    before = config_path.read_text(encoding="utf-8")
    app = create_app(config_path)
    service = cast(BridgeService, app.state.bridge)
    running_config = service.config
    data = control_config_data(service.config)
    data["plugins"]["claude_code"] = {"enabled": True, "not_a_setting": 1}

    with TestClient(app) as client:
        response = client.put("/api/config", json=data)

    assert response.status_code == 422
    assert "not_a_setting" in response.json()["detail"]
    assert config_path.read_text(encoding="utf-8") == before
    assert service.config is running_config


class _FakeProvider:
    instances: list["_FakeProvider"] = []

    def __init__(self, config) -> None:
        self.config = config
        self.closed = False
        _FakeProvider.instances.append(self)

    def close(self) -> None:
        self.closed = True


def _app_with_codex_enabled(tmp_path: Path, monkeypatch) -> tuple[TestClient, BridgeService, Path]:
    import stream_micro.bridge as bridge_module

    _FakeProvider.instances = []
    monkeypatch.setattr(bridge_module, "CodexPlugin", _FakeProvider)
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["codex"] = {"enabled": True, "codex_path": None, "codex_home": None}
    write_control_config(config_path, parse_control_config(data))
    app = create_app(config_path)
    return TestClient(app), cast(BridgeService, app.state.bridge), config_path


def test_layout_only_save_keeps_running_providers(tmp_path: Path, monkeypatch) -> None:
    client, service, _path = _app_with_codex_enabled(tmp_path, monkeypatch)
    with client:
        codex = service.plugins["codex"]
        data = client.get("/api/config").json()
        data["stream_deck"]["brightness"] = 55
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["codex"] is codex
        assert not cast(_FakeProvider, codex).closed

        data["plugins"]["codex"]["codex_home"] = str(tmp_path)
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["codex"] is not codex
        assert cast(_FakeProvider, codex).closed


def test_providers_built_before_a_failing_one_are_closed(tmp_path: Path, monkeypatch) -> None:
    client, service, path = _app_with_codex_enabled(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    with client:
        data = client.get("/api/config").json()
        data["plugins"]["codex"]["codex_home"] = str(tmp_path)  # forces a new Codex provider
        data["plugins"]["claude_code"] = {"enabled": True, "bogus": 1}  # then Claude fails
        assert client.put("/api/config", json=data).status_code == 422

        new_codex = _FakeProvider.instances[-1]
        assert new_codex is not service.plugins["codex"]
        assert new_codex.closed
        assert not cast(_FakeProvider, service.plugins["codex"]).closed
    assert path.read_text(encoding="utf-8") == before


def test_failed_write_keeps_the_running_config_and_providers(tmp_path: Path, monkeypatch) -> None:
    import stream_micro.bridge as bridge_module

    client, service, _path = _app_with_codex_enabled(tmp_path, monkeypatch)
    running_config, running_codex = service.config, service.plugins["codex"]

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bridge_module, "write_control_config", fail_write)
    data = control_config_data(service.config)
    data["plugins"]["codex"]["codex_home"] = str(tmp_path)
    with pytest.raises(OSError):
        service.replace_config(data)

    assert service.config is running_config
    assert service.plugins["codex"] is running_codex
    assert not cast(_FakeProvider, running_codex).closed
    assert _FakeProvider.instances[-1].closed  # the replacement that never went live


def test_concurrent_saves_run_one_at_a_time(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    from stream_micro.bridge import BridgeService

    client, service, _path = _app_with_codex_enabled(tmp_path, monkeypatch)
    original = BridgeService._build_plugins
    active, peak = [0], [0]
    lock = threading.Lock()

    def slow_build(*args, **kwargs):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)  # long enough that unserialized saves would overlap
        try:
            return original(*args, **kwargs)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(BridgeService, "_build_plugins", staticmethod(slow_build))
    first = control_config_data(service.config)
    second = control_config_data(service.config)
    second["plugins"]["codex"]["codex_home"] = str(tmp_path)
    threads = [
        threading.Thread(target=service.replace_config, args=(data,)) for data in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak[0] == 1
    assert not cast(_FakeProvider, service.plugins["codex"]).closed


def test_only_the_bridge_page_may_save_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    app = create_app(config_path)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        data = client.get("/api/config").json()
        own = {"Origin": "http://127.0.0.1:8766"}
        assert client.put("/api/config", json=data, headers=own).status_code == 200
        assert client.put("/api/config", json=data).status_code == 200  # a script
        other = {"Origin": "http://evil.example"}
        assert client.put("/api/config", json=data, headers=other).status_code == 403
    rebound = TestClient(create_app(config_path), base_url="http://evil.example:8766")
    with rebound as client:
        headers = {"Origin": "http://evil.example:8766"}  # its own Host, but not loopback
        assert client.put("/api/config", json=data, headers=headers).status_code == 403


def test_layout_only_save_keeps_quota_workers(tmp_path: Path, monkeypatch) -> None:
    import stream_micro.bridge as bridge_module
    from stream_micro.plugins.http_quota import HttpQuotaPlugin

    requests: list[httpx.Request] = []

    def offline(config):  # no worker thread, no real network
        return HttpQuotaPlugin(
            config,
            client=httpx.Client(transport=httpx.MockTransport(requests.append)),
            start_thread=False,
        )

    monkeypatch.setattr(bridge_module, "HttpQuotaPlugin", offline)
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    app = create_app(config_path)
    service = cast(BridgeService, app.state.bridge)
    with TestClient(app) as client:
        data = client.get("/api/config").json()
        data["plugins"]["http_quota"] = {
            "enabled": True,
            "sources": [{"id": "or", "preset": "openrouter", "api_key": "k"}],
        }
        assert client.put("/api/config", json=data).status_code == 200
        provider = service.plugins["http_quota"]
        assert str(getattr(provider, "cache_path", "")).endswith(
            str(Path(".state") / "http_quota.json")
        )

        data = client.get("/api/config").json()
        data["stream_deck"]["brightness"] = 55
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["http_quota"] is provider

        data = client.get("/api/config").json()
        data["plugins"]["http_quota"]["refresh_interval_seconds"] = 120
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["http_quota"] is not provider
    service.close()
    assert requests == []


def test_usage_api_keys_never_reach_the_browser_and_blank_save_keeps_them(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["http_quota"] = {
        "enabled": False,
        "sources": [
            {"id": "limit", "preset": "openrouter", "api_key": "first-secret"},
            {"id": "credits", "preset": "openrouter_credits", "api_key": "second-secret"},
        ],
    }
    write_control_config(config_path, parse_control_config(data))
    app = create_app(config_path)
    service = cast(BridgeService, app.state.bridge)
    with TestClient(app) as client:
        response = client.get("/api/config")
        assert "first-secret" not in response.text and "second-secret" not in response.text
        public = response.json()
        sources = public["plugins"]["http_quota"]["sources"]
        assert [source["api_key_configured"] for source in sources] == [True, True]

        # Reordered, one forgotten, one new: keys follow the source id.
        sources.reverse()
        sources[1]["api_key_configured"] = False
        sources.append({"id": "or", "preset": "openrouter", "api_key": "third-secret"})
        assert client.put("/api/config", json=public).status_code == 200

    saved = {
        source["id"]: source["api_key"]
        for source in service.config.plugins["http_quota"]["sources"]
    }
    assert saved == {"credits": "second-secret", "limit": None, "or": "third-secret"}
    assert "api_key_configured" not in config_path.read_text(encoding="utf-8")


def test_invalid_usage_source_is_rejected_before_saving(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    app = create_app(config_path)
    before = config_path.read_text(encoding="utf-8")
    with TestClient(app) as client:
        public = client.get("/api/config").json()
        public["plugins"]["http_quota"]["sources"] = [
            {"id": "x", "url": "http://plain.example/usage", "windows": [{"used_percent": "a"}]}
        ]
        response = client.put("/api/config", json=public)
    assert response.status_code == 422
    assert "https" in response.text
    assert config_path.read_text(encoding="utf-8") == before


def test_a_saved_usage_key_never_follows_a_source_to_another_host(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["http_quota"] = {
        "enabled": False,
        "sources": [
            {"id": "same", "preset": "openrouter", "api_key": "kept-secret"},
            {"id": "moved", "preset": "openrouter", "api_key": "moved-secret"},
        ],
    }
    write_control_config(config_path, parse_control_config(data))
    app = create_app(config_path)
    service = cast(BridgeService, app.state.bridge)
    with TestClient(app) as client:
        public = client.get("/api/config").json()
        sources = public["plugins"]["http_quota"]["sources"]
        sources[0]["url"] = "https://openrouter.ai/api/v1/key?period=month"  # same host
        sources[1]["url"] = "https://other.example/usage"  # another host
        assert client.put("/api/config", json=public).status_code == 200

    saved = {
        source["id"]: source["api_key"]
        for source in service.config.plugins["http_quota"]["sources"]
    }
    assert saved == {"same": "kept-secret", "moved": None}


def test_a_key_variable_never_follows_a_source_to_another_host(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["http_quota"] = {
        "enabled": False,
        "sources": [
            {"id": "same", "preset": "openrouter", "api_key_env": "SAME_KEY"},
            {"id": "moved", "preset": "openrouter", "api_key_env": "MOVED_KEY"},
            {"id": "renamed", "preset": "openrouter", "api_key_env": "OLD_KEY"},
        ],
    }
    write_control_config(config_path, parse_control_config(data))
    app = create_app(config_path)
    service = cast(BridgeService, app.state.bridge)
    with TestClient(app) as client:
        public = client.get("/api/config").json()
        sources = public["plugins"]["http_quota"]["sources"]
        sources[0]["url"] = "https://openrouter.ai:443/api/v1/key"  # same host, default port
        sources[1]["url"] = "https://other.example/usage"  # another host, same variable
        sources[2]["url"] = "https://other.example/usage"  # another host, named again
        sources[2]["api_key_env"] = "NEW_KEY"
        assert client.put("/api/config", json=public).status_code == 200

    saved = {
        source["id"]: source.get("api_key_env")
        for source in service.config.plugins["http_quota"]["sources"]
    }
    assert saved == {"same": "SAME_KEY", "moved": None, "renamed": "NEW_KEY"}


def test_a_bad_disabled_usage_source_says_why_it_blocks_the_save(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    app = create_app(config_path)
    with TestClient(app) as client:
        public = client.get("/api/config").json()
        public["plugins"]["http_quota"] = {
            "enabled": False,
            "sources": [{"id": "x", "preset": "nope"}],
        }
        response = client.put("/api/config", json=public)
    assert response.status_code == 422
    assert "checked even while disabled" in response.text


def _proxy_app(tmp_path: Path, **cliproxy) -> tuple[TestClient, BridgeService, Path]:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["cliproxy_quota"] = {
        "enabled": False,
        "base_url": "http://127.0.0.1:9",
        "management_key": "real-secret",
        **cliproxy,
    }
    write_control_config(config_path, parse_control_config(data))
    app = create_app(config_path)
    return TestClient(app), cast(BridgeService, app.state.bridge), config_path


def test_management_key_never_reaches_the_browser_and_blank_save_keeps_it(
    tmp_path: Path,
) -> None:
    client, service, config_path = _proxy_app(tmp_path)
    with client:
        public = client.get("/api/config").json()
        section = public["plugins"]["cliproxy_quota"]
        assert section["management_key"] == ""
        assert section["management_key_configured"] is True
        assert "real-secret" not in client.get("/api/config").text

        assert client.put("/api/config", json=public).status_code == 200
        assert service.config.plugins["cliproxy_quota"]["management_key"] == "real-secret"

        section["management_key_configured"] = False
        assert client.put("/api/config", json=public).status_code == 200
        assert service.config.plugins["cliproxy_quota"]["management_key"] is None
    config_text = config_path.read_text(encoding="utf-8")
    assert "real-secret" not in config_text
    assert "management_key_configured" not in config_text
    assert "management_key_source" not in config_text


def test_a_saved_management_key_never_follows_the_proxy_to_another_url(tmp_path: Path) -> None:
    client, service, _config_path = _proxy_app(tmp_path)
    with client:
        public = client.get("/api/config").json()
        public["plugins"]["cliproxy_quota"]["base_url"] = "http://127.0.0.1:9/"
        assert client.put("/api/config", json=public).status_code == 200
        # Only a trailing slash changed: still the same proxy, key kept.
        assert service.config.plugins["cliproxy_quota"]["management_key"] == "real-secret"

        public = client.get("/api/config").json()
        public["plugins"]["cliproxy_quota"]["management_key_env"] = "PROXY_KEY_FOR_TEST"
        assert client.put("/api/config", json=public).status_code == 200

        public = client.get("/api/config").json()
        public["plugins"]["cliproxy_quota"]["base_url"] = "http://127.0.0.1:10"
        assert client.put("/api/config", json=public).status_code == 200
        assert service.config.plugins["cliproxy_quota"]["management_key"] is None
        # The variable named for the old proxy does not follow it either.
        assert service.config.plugins["cliproxy_quota"]["management_key_env"] is None


def test_a_typed_management_key_replaces_the_saved_one(tmp_path: Path) -> None:
    client, service, _config_path = _proxy_app(tmp_path)
    with client:
        public = client.get("/api/config").json()
        public["plugins"]["cliproxy_quota"]["management_key"] = "new-secret"
        assert client.put("/api/config", json=public).status_code == 200
        assert service.config.plugins["cliproxy_quota"]["management_key"] == "new-secret"
        assert "new-secret" not in client.get("/api/config").text


def test_invalid_disabled_proxy_is_rejected_before_saving(tmp_path: Path) -> None:
    client, _service, config_path = _proxy_app(tmp_path)
    before = config_path.read_text(encoding="utf-8")
    with client:
        data = client.get("/api/config").json()
        data["plugins"]["cliproxy_quota"]["base_url"] = "http://user:pw@proxy.test"
        response = client.put("/api/config", json=data)
    assert response.status_code == 422
    assert config_path.read_text(encoding="utf-8") == before


def test_layout_only_save_keeps_the_proxy_worker(tmp_path: Path, monkeypatch) -> None:
    import stream_micro.bridge as bridge_module
    from stream_micro.plugins.cliproxy_quota import CliProxyQuotaPlugin

    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    def offline(config):  # no worker thread, no real network
        return CliProxyQuotaPlugin(
            config,
            client=httpx.Client(transport=httpx.MockTransport(record)),
            start_thread=False,
        )

    monkeypatch.setattr(bridge_module, "CliProxyQuotaPlugin", offline)
    client, service, _config_path = _proxy_app(tmp_path)
    with client:
        data = client.get("/api/config").json()
        data["plugins"]["cliproxy_quota"]["enabled"] = True
        assert client.put("/api/config", json=data).status_code == 200
        provider = service.plugins["cliproxy_quota"]
        assert str(getattr(provider, "cache_path", "")).endswith(
            str(Path(".state") / "cliproxy_quota.json")
        )

        data = client.get("/api/config").json()
        data["stream_deck"]["brightness"] = 55
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["cliproxy_quota"] is provider

        data = client.get("/api/config").json()
        data["plugins"]["cliproxy_quota"]["refresh_interval_seconds"] = 120
        assert client.put("/api/config", json=data).status_code == 200
        assert service.plugins["cliproxy_quota"] is not provider
    service.close()
    assert requests == []


def test_a_disabled_proxy_is_not_started(tmp_path: Path) -> None:
    client, service, _config_path = _proxy_app(tmp_path)
    with client:
        assert "cliproxy_quota" not in service.plugins


def test_dashboard_learns_which_key_is_used_but_never_the_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PROXY_KEY_FOR_TEST", "env-secret")
    monkeypatch.delenv("PROXY_KEY_MISSING_FOR_TEST", raising=False)
    client, _service, _path = _proxy_app(tmp_path)
    with client:
        section = client.get("/api/config").json()["plugins"]["cliproxy_quota"]
        assert section["management_key_source"] == "saved"

        data = client.get("/api/config").json()
        data["plugins"]["cliproxy_quota"]["management_key_env"] = "PROXY_KEY_FOR_TEST"
        assert client.put("/api/config", json=data).status_code == 200
        response = client.get("/api/config")
        assert response.json()["plugins"]["cliproxy_quota"]["management_key_source"] == (
            "environment"
        )
        assert "env-secret" not in response.text and "real-secret" not in response.text

        data = client.get("/api/config").json()
        section = data["plugins"]["cliproxy_quota"]
        section["management_key_env"] = "PROXY_KEY_MISSING_FOR_TEST"
        section["management_key_configured"] = False
        assert client.put("/api/config", json=data).status_code == 200
        section = client.get("/api/config").json()["plugins"]["cliproxy_quota"]
        assert section["management_key_source"] == "missing"


def test_config_round_trip_preserves_language(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    with TestClient(create_app(config_path)) as client:
        data = client.get("/api/config").json()
        data["language"] = "zh-TW"
        assert client.put("/api/config", json=data).status_code == 200
        assert client.get("/api/config").json()["language"] == "zh-TW"
