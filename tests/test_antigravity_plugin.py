import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from stream_micro.bridge import BridgeService
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.plugins.antigravity import (
    MAX_CACHE_BYTES,
    MAX_VISIBLE_SESSIONS,
    AntigravityCacheSession,
    AntigravityError,
    AntigravityPlugin,
    launch_antigravity_session,
    map_status_payload,
    read_last_conversations,
    resolve_agy_command,
    validate_antigravity_config,
)
from stream_micro.plugins.base import SessionState


def test_companion_fixture_matches_plugin_contract():
    """The bundled status-line example must remain accepted by the plugin."""
    payload = json.loads(
        Path("integrations/antigravity-stream-micro/fixtures/status.json").read_text(
            encoding="utf-8"
        )
    )
    plugin = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)

    assert plugin.handle_status(payload)["result"] == "status_updated"


def test_companion_forwards_only_allowlisted_status_fields(monkeypatch: pytest.MonkeyPatch):
    """The reporter must not send sensitive status-line fields to the Bridge."""
    received: list[dict[str, object]] = []

    class StatusHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers["content-length"])
            received.append(json.loads(self.rfile.read(content_length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "STREAM_MICRO_ANTIGRAVITY_STATUS_URL",
        f"http://127.0.0.1:{server.server_port}/api/hooks/antigravity/status",
    )
    try:
        result = subprocess.run(
            ["node", "integrations/antigravity-stream-micro/scripts/report-status.mjs"],
            input=json.dumps(
                {
                    "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                    "cwd": "C:/work/project",
                    "agent_state": "working",
                    "tool_confirmation_pending": False,
                    "task_count": 0,
                    "quota": {},
                    "transcript_path": "secret",
                    "model": "sensitive-model",
                }
            ),
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=None,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode == 0
    assert result.stdout == "stream-micro: reported\n"
    assert received == [
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": "C:/work/project",
            "agent_state": "working",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {},
        }
    ]


def test_companion_rejects_remote_status_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "STREAM_MICRO_ANTIGRAVITY_STATUS_URL",
        "http://example.com/api/hooks/antigravity/status",
    )

    result = subprocess.run(
        ["node", "integrations/antigravity-stream-micro/scripts/report-status.mjs"],
        input=json.dumps(
            {
                "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                "cwd": "C:/work/project",
                "agent_state": "working",
                "tool_confirmation_pending": False,
                "task_count": 0,
                "quota": {},
            }
        ),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert result.stdout == "stream-micro: unavailable\n"


def test_companion_reconstructs_quota_without_nested_fields(monkeypatch: pytest.MonkeyPatch):
    received: list[dict[str, object]] = []

    class StatusHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers["content-length"])
            received.append(json.loads(self.rfile.read(content_length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "STREAM_MICRO_ANTIGRAVITY_STATUS_URL",
        f"http://localhost:{server.server_port}/api/hooks/antigravity/status",
    )
    try:
        result = subprocess.run(
            ["node", "integrations/antigravity-stream-micro/scripts/report-status.mjs"],
            input=json.dumps(
                {
                    "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                    "cwd": "C:/work/project",
                    "agent_state": "working",
                    "tool_confirmation_pending": False,
                    "task_count": 0,
                    "quota": {
                        "gemini-weekly": {
                            "remaining_fraction": 0.75,
                            "reset_time": "2026-09-14T00:00:00Z",
                            "raw_response": {"token": "secret"},
                        },
                        "invalid bucket": {
                            "remaining_fraction": 0.5,
                            "reset_time": "2026-09-14T00:00:00Z",
                        },
                    },
                }
            ),
            capture_output=True,
            check=False,
            encoding="utf-8",
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode == 0
    assert received[0]["quota"] == {
        "gemini-weekly": {
            "remaining_fraction": 0.75,
            "reset_time": "2026-09-14T00:00:00Z",
        }
    }


@pytest.mark.parametrize("input_data", ["{}", "not json"])
def test_companion_never_blocks_the_cli_on_reporting_failures(
    monkeypatch: pytest.MonkeyPatch, input_data: str
):
    """Malformed input and unreachable Bridge reporting must exit successfully."""
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        port = reserved_port.getsockname()[1]
        monkeypatch.setenv(
            "STREAM_MICRO_ANTIGRAVITY_STATUS_URL",
            f"http://127.0.0.1:{port}/api/hooks/antigravity/status",
        )

        result = subprocess.run(
            ["node", "integrations/antigravity-stream-micro/scripts/report-status.mjs"],
            input=input_data,
            capture_output=True,
            check=False,
            encoding="utf-8",
        )

    assert result.returncode == 0
    assert result.stdout == "stream-micro: unavailable\n"


def test_bridge_registers_antigravity_only_when_enabled(tmp_path: Path):
    """Enabled Antigravity config creates the provider for status reporting."""
    data = control_config_data(default_control_config())
    data["plugins"]["opencode"]["enabled"] = False
    data["plugins"]["ollama_quota"]["enabled"] = False
    data["plugins"]["antigravity"]["enabled"] = True
    path = tmp_path / "bridge.yaml"
    write_control_config(path, parse_control_config(data))
    service = BridgeService(path)
    try:
        assert isinstance(service.plugins["antigravity"], AntigravityPlugin)
    finally:
        service.close()


def test_status_payload_prioritizes_confirmation_and_normalizes_quota():
    """A pending confirmation must override activity and quota is display-safe."""
    state, signals = map_status_payload(
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": "C:/work/project",
            "agent_state": "working",
            "tool_confirmation_pending": True,
            "task_count": 1,
            "quota": {
                "gemini-weekly": {
                    "remaining_fraction": 0.75,
                    "reset_time": "2026-09-14T00:00:00Z",
                }
            },
        }
    )

    assert state is SessionState.WAITING
    assert signals == {
        "primary_percent": 25.0,
        "primary_label": "GEMINI-WEEKL",
        "primary_reset_at": "2026-09-14T00:00:00+00:00",
    }


def test_plugin_uses_workspace_name_without_reading_prompt(tmp_path: Path):
    """Cache-only snapshots use the workspace folder rather than prompt content."""
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}), encoding="utf-8"
    )
    plugin = AntigravityPlugin({"enabled": True, "agy_home": str(tmp_path)})

    snapshot = plugin.snapshot()

    assert snapshot.sessions[0].title == "project"
    assert snapshot.sessions[0].directory == str(project)
    assert snapshot.sessions[0].state is SessionState.IDLE


def test_snapshot_is_unavailable_without_agy_or_recognized_data(tmp_path: Path):
    """An enabled provider without its CLI, cache, or status input is unavailable."""
    plugin = AntigravityPlugin({"enabled": True, "agy_home": str(tmp_path)})

    snapshot = plugin.snapshot()

    assert snapshot.health.connected is False


def test_snapshot_requires_agy_even_when_cache_has_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cached sessions cannot claim availability when the CLI cannot restore them."""
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}), encoding="utf-8"
    )
    plugin = AntigravityPlugin({"enabled": True, "agy_home": str(tmp_path)})
    monkeypatch.setattr("stream_micro.plugins.antigravity.shutil.which", lambda _command: None)

    snapshot = plugin.snapshot()

    assert snapshot.health.connected is False


def test_snapshot_connects_when_agy_and_live_status_are_available(tmp_path: Path):
    """An accepted live update and resolvable CLI make the provider available."""
    executable = tmp_path / "agy.exe"
    executable.touch()
    plugin = AntigravityPlugin({"enabled": True, "agy_path": str(executable)})
    plugin.handle_status(
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": str(tmp_path),
            "agent_state": "working",
            "tool_confirmation_pending": False,
            "task_count": 1,
            "quota": {},
        }
    )

    assert plugin.snapshot().health.connected is True


def test_snapshot_connects_from_live_status_without_agy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    plugin = AntigravityPlugin({"enabled": True})
    monkeypatch.setattr("stream_micro.plugins.antigravity.shutil.which", lambda _command: None)
    plugin.handle_status(
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": str(tmp_path),
            "agent_state": "working",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {},
        }
    )

    health = plugin.snapshot().health

    assert health.connected is True
    assert "live companion: available" in health.detail
    assert "quota: unavailable" in health.detail


def test_snapshot_health_detail_reports_cache_live_and_quota(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}),
        encoding="utf-8",
    )
    executable = tmp_path / "agy.exe"
    executable.touch()
    plugin = AntigravityPlugin(
        {"enabled": True, "agy_home": str(tmp_path), "agy_path": str(executable)}
    )

    detail = plugin.snapshot().health.detail

    assert detail == "Cache: available; live companion: unavailable; quota: unavailable"


def test_disabled_plugin_snapshot_reports_disabled():
    plugin = AntigravityPlugin({"enabled": False})

    assert plugin.snapshot().health.detail == "Disabled"


def test_snapshot_rejects_directory_as_agy_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A directory configured as agy_path must not establish provider health."""
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}), encoding="utf-8"
    )
    monkeypatch.setattr("stream_micro.plugins.antigravity.shutil.which", lambda _command: None)
    plugin = AntigravityPlugin(
        {"enabled": True, "agy_home": str(tmp_path), "agy_path": str(tmp_path)}
    )

    assert plugin.snapshot().health.connected is False


def test_selection_resumes_only_a_current_session(tmp_path: Path):
    """Selection must reject an ID missing from the current snapshot."""
    plugin = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)

    with pytest.raises(AntigravityError, match="no longer exists"):
        plugin.invoke("select_session", {"session_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460"})


def test_windows_launch_uses_official_specific_conversation_flag(tmp_path: Path):
    """The launcher opens a new console for precisely the selected conversation."""
    with (
        patch("stream_micro.plugins.antigravity.os.name", "nt"),
        patch("stream_micro.plugins.antigravity.resolve_agy_command", return_value=["agy.exe"]),
        patch("stream_micro.plugins.antigravity.subprocess.Popen") as popen,
    ):
        launch_antigravity_session(
            "de5c0341-9e8a-40d3-b93f-e87bc874a460", str(tmp_path), agy_path="agy"
        )

    assert popen.call_args.args[0] == [
        "agy.exe",
        "--conversation",
        "de5c0341-9e8a-40d3-b93f-e87bc874a460",
    ]
    assert popen.call_args.kwargs["cwd"] == str(tmp_path)
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["creationflags"] != 0


def test_windows_launch_omits_missing_stored_workspace(tmp_path: Path):
    missing = tmp_path / "removed"
    with (
        patch("stream_micro.plugins.antigravity.os.name", "nt"),
        patch("stream_micro.plugins.antigravity.resolve_agy_command", return_value=["agy.exe"]),
        patch("stream_micro.plugins.antigravity.subprocess.Popen") as popen,
    ):
        launch_antigravity_session(
            "de5c0341-9e8a-40d3-b93f-e87bc874a460", str(missing), agy_path="agy"
        )

    assert popen.call_args.args[0] == [
        "agy.exe",
        "--conversation",
        "de5c0341-9e8a-40d3-b93f-e87bc874a460",
    ]
    assert popen.call_args.kwargs["cwd"] is None


def test_selection_ignores_caller_provided_cwd(tmp_path: Path):
    launched: list[tuple[str, str | None]] = []
    plugin = AntigravityPlugin(
        {"enabled": True}, launcher=lambda session_id, cwd: launched.append((session_id, cwd))
    )
    conversation_id = "de5c0341-9e8a-40d3-b93f-e87bc874a460"
    plugin.handle_status(
        {
            "conversation_id": conversation_id,
            "cwd": str(tmp_path),
            "agent_state": "idle",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {},
        }
    )

    plugin.invoke("select_session", {"session_id": conversation_id, "cwd": "C:/unsafe"})

    assert launched == [(conversation_id, str(tmp_path))]


def test_selection_resumes_live_conversation_after_stored_workspace_is_removed(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    launched: list[tuple[str, str | None]] = []
    plugin = AntigravityPlugin(
        {"enabled": True}, launcher=lambda session_id, cwd: launched.append((session_id, cwd))
    )
    conversation_id = "de5c0341-9e8a-40d3-b93f-e87bc874a460"
    plugin.handle_status(
        {
            "conversation_id": conversation_id,
            "cwd": str(workspace),
            "agent_state": "idle",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {},
        }
    )
    workspace.rmdir()

    plugin.invoke("select_session", {"session_id": conversation_id})

    assert launched == [(conversation_id, None)]


def test_idle_status_after_activity_stays_completed_until_selected(tmp_path: Path):
    """An acknowledged completion returns to idle without retaining status input."""
    directory = str(tmp_path)
    conversation_id = "de5c0341-9e8a-40d3-b93f-e87bc874a460"
    plugin = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)
    busy = {
        "conversation_id": conversation_id,
        "cwd": directory,
        "agent_state": "working",
        "tool_confirmation_pending": False,
        "task_count": 1,
        "quota": {},
    }

    plugin.handle_status(busy)
    plugin.handle_status({**busy, "agent_state": "idle", "task_count": 0})
    plugin.handle_status({**busy, "agent_state": "idle", "task_count": 0})

    assert plugin.snapshot().sessions[0].state is SessionState.COMPLETED
    plugin.invoke("select_session", {"session_id": conversation_id})
    assert plugin.snapshot().sessions[0].state is SessionState.IDLE


def test_live_state_is_bounded_and_evicts_oldest_deterministically(tmp_path: Path):
    times = iter((1.0, 2.0, 3.0))
    plugin = AntigravityPlugin({"enabled": True, "max_sessions": 2}, clock=lambda: next(times))
    ids = [f"de5c0341-9e8a-40d3-b93f-{index:012x}" for index in range(3)]
    for conversation_id in ids:
        plugin.handle_status(
            {
                "conversation_id": conversation_id,
                "cwd": str(tmp_path),
                "agent_state": "working",
                "tool_confirmation_pending": False,
                "task_count": 1,
                "quota": {},
            }
        )

    assert set(plugin._live_sessions) == set(ids[1:])
    assert set(plugin._live_states) == set(ids[1:])
    assert len(plugin._completed) <= 2


def test_completed_state_is_evicted_with_its_live_session(tmp_path: Path):
    times = iter((1.0, 2.0, 3.0, 4.0))
    plugin = AntigravityPlugin({"enabled": True, "max_sessions": 1}, clock=lambda: next(times))
    first = "de5c0341-9e8a-40d3-b93f-000000000001"
    second = "de5c0341-9e8a-40d3-b93f-000000000002"
    base = {
        "cwd": str(tmp_path),
        "tool_confirmation_pending": False,
        "quota": {},
    }
    plugin.handle_status(
        {**base, "conversation_id": first, "agent_state": "working", "task_count": 1}
    )
    plugin.handle_status(
        {**base, "conversation_id": first, "agent_state": "idle", "task_count": 0}
    )
    plugin.handle_status(
        {**base, "conversation_id": second, "agent_state": "working", "task_count": 1}
    )

    assert first not in plugin._completed
    assert set(plugin._live_sessions) == {second}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": "C:/work/project",
            "agent_state": "idle",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {},
            "prompt": "sensitive input",
        },
    ],
)
def test_status_payload_rejects_unknown_fields(payload):
    """Unexpected status fields, including sensitive content, are rejected."""
    with pytest.raises(AntigravityError, match="fields"):
        map_status_payload(payload)


def test_status_payload_rejects_missing_required_field():
    """A partial status cannot be mapped to a display-safe session."""
    payload = {
        "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
        "cwd": "C:/work/project",
        "agent_state": "idle",
        "tool_confirmation_pending": False,
        "task_count": 0,
    }

    with pytest.raises(AntigravityError, match="fields"):
        map_status_payload(payload)


@pytest.mark.parametrize(
    "quota",
    [
        {"weekly": {"remaining_fraction": 0.5}},
        {"weekly": {"remaining_fraction": 1.5, "reset_time": "2026-09-14T00:00:00Z"}},
    ],
)
def test_status_payload_rejects_malformed_quota(quota):
    """Quota windows must have complete bounded documented values."""
    payload = {
        "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
        "cwd": "C:/work/project",
        "agent_state": "idle",
        "tool_confirmation_pending": False,
        "task_count": 0,
        "quota": quota,
    }

    with pytest.raises(AntigravityError):
        map_status_payload(payload)


def test_status_payload_rejects_invalid_quota_timestamp():
    """Quota reset timestamps must be timezone-aware ISO dates."""
    payload = {
        "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
        "cwd": "C:/work/project",
        "agent_state": "idle",
        "tool_confirmation_pending": False,
        "task_count": 0,
        "quota": {"weekly": {"remaining_fraction": 0.5, "reset_time": "tomorrow"}},
    }

    with pytest.raises(AntigravityError, match="ISO date"):
        map_status_payload(payload)


def test_status_payload_sorts_two_quota_buckets_by_id():
    """The first two display slots follow deterministic bucket-ID ordering."""
    _state, signals = map_status_payload(
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": "C:/work/project",
            "agent_state": "idle",
            "tool_confirmation_pending": False,
            "task_count": 0,
            "quota": {
                "zeta": {"remaining_fraction": 0.2, "reset_time": "2026-09-15T00:00:00Z"},
                "alpha": {"remaining_fraction": 0.75, "reset_time": "2026-09-14T00:00:00Z"},
            },
        }
    )

    assert signals == {
        "primary_percent": 25.0,
        "primary_label": "ALPHA",
        "primary_reset_at": "2026-09-14T00:00:00+00:00",
        "secondary_percent": 80.0,
        "secondary_label": "ZETA",
        "secondary_reset_at": "2026-09-15T00:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("agy_path", "expected"),
    [
        ("C:/tools/agy.cmd", ["cmd.exe", "/d", "/s", "/c", "C:/tools/agy.cmd"]),
        (
            "C:/tools/agy.ps1",
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                "C:/tools/agy.ps1",
            ],
        ),
    ],
)
def test_windows_command_resolution_uses_constrained_wrappers(agy_path, expected):
    """Windows script wrappers are invoked without a general-purpose shell."""
    with (
        patch("stream_micro.plugins.antigravity.os.name", "nt"),
        patch("stream_micro.plugins.antigravity.shutil.which", return_value=None),
        patch.dict("stream_micro.plugins.antigravity.os.environ", {"COMSPEC": "cmd.exe"}),
    ):
        assert resolve_agy_command(agy_path) == expected


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"enabled": "yes"}, "boolean"),
        ({"agy_path": ""}, "agy_path"),
        ({"agy_home": []}, "agy_home"),
        ({"max_sessions": 16}, "max_sessions"),
        ({"command": "cmd.exe"}, "Unknown"),
    ],
)
def test_antigravity_config_rejects_invalid_values(config, message):
    with pytest.raises(AntigravityError, match=message):
        validate_antigravity_config(config)


def test_last_conversations_maps_workspaces_to_sessions(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "last_conversations.json"
    cache.write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}),
        encoding="utf-8",
    )

    assert read_last_conversations(cache) == (
        AntigravityCacheSession(
            "de5c0341-9e8a-40d3-b93f-e87bc874a460", str(project), 0.0
        ),
    )


def test_last_conversations_ignores_non_absolute_paths_and_invalid_ids(tmp_path: Path):
    cache = tmp_path / "last_conversations.json"
    cache.write_text(
        json.dumps({"relative": "good-id", str(tmp_path): "not valid id!"}),
        encoding="utf-8",
    )

    assert read_last_conversations(cache) == ()


def test_default_control_config_disables_antigravity_without_paths():
    assert default_control_config().plugins["antigravity"] == {
        "enabled": False,
        "agy_path": None,
        "agy_home": None,
        "max_sessions": 15,
    }


def test_plugin_validates_and_stores_config_and_injected_dependencies(tmp_path: Path):
    def launcher(_session_id, _directory):
        return None

    def clock():
        return 123.0

    config = {"enabled": True, "agy_home": str(tmp_path), "max_sessions": 2}

    plugin = AntigravityPlugin(config, launcher=launcher, clock=clock)

    assert plugin.config == config
    assert plugin._launcher is launcher
    assert plugin._clock is clock
    assert plugin.home == tmp_path
    assert plugin.cache_path == tmp_path / "cache" / "last_conversations.json"
    assert plugin.max_sessions == 2


def test_last_conversations_ignores_oversized_cache_before_reading(tmp_path: Path):
    cache = tmp_path / "last_conversations.json"
    cache.write_bytes(b"{" + b" " * MAX_CACHE_BYTES + b"}")

    assert read_last_conversations(cache) == ()


def test_last_conversations_caps_entries_before_returning(tmp_path: Path):
    entries = {}
    for index in range(MAX_VISIBLE_SESSIONS + 5):
        directory = tmp_path / f"project-{index:02d}"
        directory.mkdir()
        entries[str(directory)] = f"de5c0341-9e8a-40d3-b93f-{index:012x}"
    cache = tmp_path / "last_conversations.json"
    cache.write_text(json.dumps(entries), encoding="utf-8")

    sessions = read_last_conversations(cache)

    assert len(sessions) == MAX_VISIBLE_SESSIONS


def test_last_conversations_sorts_by_normalized_directory(tmp_path: Path):
    first = tmp_path / "z" / ".." / "project-a"
    second = tmp_path / "project-b"
    (tmp_path / "z").mkdir()
    (tmp_path / "project-a").mkdir()
    second.mkdir()
    cache = tmp_path / "last_conversations.json"
    cache.write_text(
        json.dumps(
            {
                str(first): "de5c0341-9e8a-40d3-b93f-e87bc874a460",
                str(second): "de5c0341-9e8a-40d3-b93f-e87bc874a461",
            }
        ),
        encoding="utf-8",
    )

    sessions = read_last_conversations(cache)

    assert [Path(session.directory).resolve() for session in sessions] == [
        first.resolve(),
        second.resolve(),
    ]


def test_last_conversations_applies_requested_max_sessions_cap(tmp_path: Path):
    entries = {}
    for index in range(3):
        directory = tmp_path / f"project-{index}"
        directory.mkdir()
        entries[str(directory)] = f"de5c0341-9e8a-40d3-b93f-{index:012x}"
    cache = tmp_path / "last_conversations.json"
    cache.write_text(json.dumps(entries), encoding="utf-8")

    assert len(read_last_conversations(cache, max_sessions=2)) == 2


@pytest.mark.parametrize("max_sessions", [0, -1])
def test_last_conversations_never_exceeds_nonpositive_cap(tmp_path: Path, max_sessions: int):
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "last_conversations.json"
    cache.write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}),
        encoding="utf-8",
    )

    assert read_last_conversations(cache, max_sessions=max_sessions) == ()
