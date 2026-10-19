from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stream_micro import claude_setup
from stream_micro.claude_setup import ClaudeSetupError, hook_command, inspect, statusline_command
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.server import create_app

PORT = 8766


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _hooks(command: str) -> dict:
    return {
        event: [{"hooks": [{"type": "command", "command": command, "timeout": 3}]}]
        for event in claude_setup.HOOK_EVENTS
    }


def test_the_check_reports_what_is_in_the_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert inspect(path, PORT)["hooks"]["state"] == "missing"
    assert inspect(path, PORT)["statusline"]["state"] == "missing"
    _write(
        path,
        {
            "hooks": _hooks(hook_command(PORT)),
            "statusLine": {"type": "command", "command": statusline_command(PORT)},
        },
    )
    status = inspect(path, PORT)
    assert (status["hooks"]["state"], status["statusline"]["state"]) == ("ours", "ours")
    assert inspect(path, 9000)["hooks"]["state"] == "wrong_port"
    hooks = _hooks(hook_command(PORT))
    del hooks["Stop"]
    _write(path, {"hooks": hooks, "statusLine": {"type": "command", "command": "ccstatusline"}})
    status = inspect(path, PORT)
    assert (status["hooks"]["state"], status["statusline"]["state"]) == ("partial", "other")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ClaudeSetupError):
        inspect(path, PORT)


def test_commands_need_no_shell_variables(tmp_path: Path) -> None:
    script = claude_setup._script(claude_setup.HOOK_SCRIPT)
    url = f"http://127.0.0.1:{PORT}/api/hooks/claude-code"
    assert hook_command(PORT) == f'node "{script}" --stream-micro-url={url}'
    # An earlier, environment-variable entry is reported as needing an update.
    path = tmp_path / "settings.json"
    _write(path, {"hooks": _hooks(f'STREAM_MICRO_CLAUDE_HOOK_URL={url} node "{script}"')})
    assert inspect(path, PORT)["hooks"]["state"] == "wrong_port"
    # A look-alike command of someone else's is not ours.
    _write(path, {"hooks": _hooks("echo report-status.mjs")})
    assert inspect(path, PORT)["hooks"]["state"] == "missing"


def test_unsafe_or_missing_script_paths_are_reported(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    unsafe = tmp_path / "a$(id)b"
    (unsafe / "scripts").mkdir(parents=True)
    for name in (claude_setup.HOOK_SCRIPT, claude_setup.QUOTA_SCRIPT):
        (unsafe / "scripts" / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", unsafe)
    assert "characters a shell would expand" in inspect(path, PORT)["error"]
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", tmp_path / "missing")
    assert "was not found" in inspect(path, PORT)["error"]
    assert not path.exists()


def test_the_setup_check_endpoint_only_reads(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["claude_code"] = {"enabled": False, "claude_home": str(tmp_path / ".claude")}
    write_control_config(config_path, parse_control_config(data))
    with TestClient(create_app(config_path)) as client:
        status = client.get("/api/claude-code/setup").json()
        assert status["settings_path"].endswith("settings.json")
        assert status["hooks"]["state"] == "missing"
        assert status["port"] == data["bridge"]["port"]
        assert client.post("/api/claude-code/setup", json={}).status_code == 405
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_a_script_with_a_similar_name_is_not_ours(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    url = f"http://127.0.0.1:{PORT}/api/hooks/claude-code"
    for other in (
        f'node "/x/custom-report-status.mjs" --stream-micro-url={url}',
        f'node "/x/report-status.mjs.bak" --stream-micro-url={url}',
    ):
        _write(path, {"hooks": _hooks(other)})
        assert inspect(path, PORT)["hooks"]["state"] == "missing"
    _write(path, {"hooks": _hooks(f"node '/elsewhere/report-status.mjs' --stream-micro-url={url}")})
    assert inspect(path, PORT)["hooks"]["state"] == "ours"  # our script, another checkout

