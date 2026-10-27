from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stream_micro import claude_setup
from stream_micro.claude_setup import (
    ClaudeSetupError,
    hook_command,
    inspect,
    install,
    remove,
    statusline_command,
)
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.server import create_app

PORT = 8766


def _settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ours(data: dict, event: str) -> list[str]:
    return [
        hook["command"]
        for entry in data["hooks"].get(event, [])
        for hook in entry["hooks"]
        if claude_setup.HOOK_SCRIPT in hook["command"]
    ]


def test_install_adds_hooks_and_status_line_for_this_port(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    status = install(path, PORT, {"hooks", "statusline"})
    data = _settings(path)
    for event in claude_setup.HOOK_EVENTS:
        assert len(_ours(data, event)) == 1
        assert f"127.0.0.1:{PORT}/api/hooks/claude-code" in _ours(data, event)[0]
    assert f":{PORT}/api/hooks/claude-code/quota" in data["statusLine"]["command"]
    assert status["hooks"]["state"] == "ours"
    assert status["statusline"]["state"] == "ours"


def test_installing_again_or_on_a_new_port_never_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    install(path, 8765, {"hooks"})
    assert inspect(path, PORT)["hooks"]["state"] == "wrong_port"
    install(path, PORT, {"hooks"})
    install(path, PORT, {"hooks"})
    data = _settings(path)
    assert all(len(_ours(data, event)) == 1 for event in claude_setup.HOOK_EVENTS)
    assert inspect(path, PORT)["hooks"]["state"] == "ours"


def test_the_users_own_settings_and_hooks_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    mine = {"type": "command", "command": "echo mine"}
    path.write_text(
        json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [mine]}]}}), encoding="utf-8"
    )
    result = install(path, PORT, {"hooks"})
    data = _settings(path)
    assert data["model"] == "opus"
    assert {"hooks": [mine]} in data["hooks"]["Stop"]
    assert result["backup"] and Path(result["backup"]).exists()

    remove(path, PORT, {"hooks"})
    assert _settings(path)["hooks"] == {"Stop": [{"hooks": [mine]}]}


def test_another_status_line_is_chained_or_replaced_only_on_request(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    other = {"type": "command", "command": "npx ccstatusline"}
    path.write_text(json.dumps({"statusLine": other}), encoding="utf-8")
    assert inspect(path, PORT)["statusline"]["state"] == "other"
    with pytest.raises(ClaudeSetupError, match="another status line"):
        install(path, PORT, {"statusline"})
    assert _settings(path)["statusLine"] == other  # untouched

    status = install(path, PORT, {"statusline"}, chain_statusline=True)
    assert status["statusline"] == {"state": "ours", "chained": True}
    chain = json.loads((tmp_path / claude_setup.CHAIN_FILE).read_text(encoding="utf-8"))
    assert chain == {"statusLine": other}

    remove(path, PORT, {"statusline"})
    assert _settings(path)["statusLine"] == other  # given back
    assert not (tmp_path / claude_setup.CHAIN_FILE).exists()


def test_broken_settings_are_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ClaudeSetupError, match="JSON"):
        install(path, PORT, {"hooks"})
    assert path.read_text(encoding="utf-8") == "{ not json"


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    home = tmp_path / "claude"
    home.mkdir()
    data = control_config_data(default_control_config())
    data["plugins"]["claude_code"] = {"enabled": False, "claude_home": str(home)}
    config_path = tmp_path / "bridge.yaml"
    write_control_config(config_path, parse_control_config(data))
    return TestClient(create_app(config_path), base_url="http://127.0.0.1:8765"), home


def test_only_the_bridge_page_may_change_claude_settings(tmp_path: Path, monkeypatch) -> None:
    client, home = _client(tmp_path, monkeypatch)
    body = {"action": "install", "parts": ["hooks"]}
    with client:
        assert client.get("/api/claude-code/setup").json()["hooks"]["state"] == "missing"
        assert client.post("/api/claude-code/setup", json=body).status_code == 403
        evil = {"x-stream-micro": "1", "origin": "https://evil.example"}
        assert client.post("/api/claude-code/setup", json=body, headers=evil).status_code == 403
        rebound = {"x-stream-micro": "1", "host": "evil.example:8765"}
        assert client.post("/api/claude-code/setup", json=body, headers=rebound).status_code == 403
        assert not (home / "settings.json").exists()

        no_origin = {"x-stream-micro": "1"}
        assert (
            client.post("/api/claude-code/setup", json=body, headers=no_origin).status_code == 403
        )
        ok = {"x-stream-micro": "1", "origin": "http://127.0.0.1:8765"}
        response = client.post("/api/claude-code/setup", json=body, headers=ok)
        assert response.status_code == 200
        assert response.json()["hooks"]["state"] == "ours"


def test_only_commands_with_our_argument_are_ours(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    lookalike = {"type": "command", "command": "echo report-status.mjs"}
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [lookalike]}]}}), encoding="utf-8")
    assert inspect(path, PORT)["hooks"]["state"] == "missing"
    install(path, PORT, {"hooks"})
    remove(path, PORT, {"hooks"})
    assert _settings(path)["hooks"] == {"Stop": [{"hooks": [lookalike]}]}


def test_commands_need_no_shell_variables_and_earlier_installs_are_upgraded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    script = claude_setup._script(claude_setup.HOOK_SCRIPT)
    url = f"http://127.0.0.1:{PORT}/api/hooks/claude-code"
    old = f'STREAM_MICRO_CLAUDE_HOOK_URL={url} node "{script}"'
    path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old}]}]}}),
        encoding="utf-8",
    )
    assert inspect(path, PORT)["hooks"]["state"] == "wrong_port"  # offered as "Fix it"
    install(path, PORT, {"hooks"})
    commands = _ours(_settings(path), "Stop")
    assert commands == [
        f'node "{script}" --stream-micro-url=http://127.0.0.1:{PORT}/api/hooks/claude-code'
    ]


def test_a_chained_status_line_comes_back_whole(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    other = {"type": "command", "command": "npx ccstatusline", "padding": 2, "refreshInterval": 5}
    path.write_text(json.dumps({"statusLine": other}), encoding="utf-8")
    install(path, PORT, {"statusline"}, chain_statusline=True)
    chained = _settings(path)["statusLine"]
    assert (chained["padding"], chained["refreshInterval"]) == (2, 5)
    assert "--stream-micro-next=" in chained["command"]
    remove(path, PORT, {"statusline"})
    assert _settings(path)["statusLine"] == other


def test_unsafe_or_missing_script_paths_are_refused(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    unsafe = tmp_path / "a$(id)b"
    (unsafe / "scripts").mkdir(parents=True)
    for name in (claude_setup.HOOK_SCRIPT, claude_setup.QUOTA_SCRIPT):
        (unsafe / "scripts" / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", unsafe)
    with pytest.raises(ClaudeSetupError, match="characters a shell would expand"):
        install(path, PORT, {"hooks"})
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", tmp_path / "missing")
    with pytest.raises(ClaudeSetupError, match="was not found"):
        install(path, PORT, {"hooks"})
    assert "was not found" in inspect(path, PORT)["error"]
    assert not path.exists()


def test_backups_in_the_same_second_do_not_overwrite(tmp_path: Path) -> None:
    from datetime import datetime

    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    moment = datetime(2026, 10, 15, 12, 0, 0)
    first = install(path, PORT, {"hooks"}, clock=moment)["backup"]
    second = install(path, PORT, {"hooks"}, clock=moment)["backup"]
    assert first != second
    assert Path(first).read_text(encoding="utf-8") == "{}"


def test_a_failed_write_keeps_the_chained_status_line(tmp_path: Path, monkeypatch) -> None:
    settings = tmp_path / "settings.json"
    other = {"type": "command", "command": "ccstatusline", "padding": 1}
    settings.write_text(json.dumps({"statusLine": other}), encoding="utf-8")
    claude_setup.install(settings, 8766, {"statusline"}, chain_statusline=True)
    sidecar = tmp_path / claude_setup.CHAIN_FILE
    assert sidecar.exists()

    def broken(path, data, *, private=False):
        raise OSError("disk full")

    monkeypatch.setattr(claude_setup, "_write", broken)
    with pytest.raises(OSError):
        claude_setup.remove(settings, 8766, {"statusline"})
    assert sidecar.exists()  # the settings still point at it


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


def test_the_setup_check_endpoint_reads_without_writing(tmp_path: Path) -> None:
    config_path = tmp_path / "control.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["claude_code"] = {"enabled": False, "claude_home": str(tmp_path / ".claude")}
    write_control_config(config_path, parse_control_config(data))
    with TestClient(create_app(config_path)) as client:
        status = client.get("/api/claude-code/setup").json()
        assert status["settings_path"].endswith("settings.json")
        assert status["hooks"]["state"] == "missing"
        assert status["port"] == data["bridge"]["port"]
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
    assert (
        inspect(path, PORT)["hooks"]["state"] == "wrong_port"
    )  # another checkout -> stale, offered the fix flow


def test_moved_installation_is_reported_as_stale(tmp_path: Path, monkeypatch) -> None:
    integration = tmp_path / "integrations" / "claude-code-stream-micro"
    scripts = integration / "scripts"
    scripts.mkdir(parents=True)
    for name in ("report-status.mjs", "report-quota.mjs"):
        (scripts / name).write_text("export {}", encoding="utf-8")
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", integration)
    settings = tmp_path / "settings.json"
    claude_setup.install(settings, 8765, {"hooks", "statusline"})
    assert claude_setup.inspect(settings, 8765)["hooks"]["state"] == "ours"
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", tmp_path / "moved")
    result = claude_setup.inspect(settings, 8765)
    assert result["hooks"]["state"] == "wrong_port"
    assert result["statusline"]["state"] == "wrong_port"

