from __future__ import annotations

import json
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
from stream_micro.plugins.base import SessionState
from stream_micro.plugins.claude_code import (
    ClaudeCodeError,
    ClaudeCodePlugin,
    launch_claude_session,
    map_claude_hook_state,
    parse_claude_rate_limits,
    read_claude_history,
    validate_claude_code_config,
)


def _write_history(path: Path, *records: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")


def test_history_returns_recent_unique_sessions_with_display_titles(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    _write_history(
        history,
        {
            "sessionId": "older",
            "display": "First task",
            "project": str(tmp_path / "one"),
            "timestamp": 100,
        },
        {"not": "a session"},
        {
            "sessionId": "newer",
            "display": "  Add   the Claude provider  ",
            "project": str(tmp_path / "two"),
            "timestamp": 300,
        },
        {
            "sessionId": "older",
            "display": "Updated task",
            "project": str(tmp_path / "one"),
            "timestamp": 200,
        },
    )

    sessions = read_claude_history(history)

    assert [session.id for session in sessions] == ["newer", "older"]
    assert sessions[0].title == "Add the Claude provider"
    assert sessions[1].title == "Updated task"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"hook_event_name": "SessionStart", "source": "startup"}, SessionState.IDLE),
        ({"hook_event_name": "SessionStart", "source": "compact"}, SessionState.RUNNING),
        ({"hook_event_name": "UserPromptSubmit"}, SessionState.RUNNING),
        ({"hook_event_name": "PermissionRequest"}, SessionState.WAITING),
        (
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"},
            SessionState.WAITING,
        ),
        (
            {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
            SessionState.COMPLETED,
        ),
        ({"hook_event_name": "Stop"}, SessionState.COMPLETED),
        ({"hook_event_name": "StopFailure"}, SessionState.ERROR),
        ({"hook_event_name": "SessionEnd"}, SessionState.IDLE),
        ({"hook_event_name": "Unknown"}, None),
    ],
)
def test_hook_events_map_to_deck_states(
    payload: dict[str, object], expected: SessionState | None
) -> None:
    assert map_claude_hook_state(payload) is expected


def test_plugin_merges_history_with_live_hooks_and_resumes_exact_session(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_history(
        tmp_path / "history.jsonl",
        {
            "sessionId": "session-1",
            "display": "Initial task",
            "project": str(project),
            "timestamp": 100,
        },
    )
    launched: list[tuple[str, str | None]] = []
    plugin = ClaudeCodePlugin(
        {
            "enabled": True,
            "claude_path": None,
            "claude_home": str(tmp_path),
            "max_sessions": 15,
        },
        launcher=lambda session_id, directory: launched.append((session_id, directory)),
        clock=lambda: 1.0,
    )

    accepted = plugin.handle_hook(
        {
            "session_id": "session-1",
            "cwd": str(project),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Implement the Stream Deck integration",
        }
    )
    snapshot = plugin.snapshot()
    result = plugin.invoke("select_session", {"session_id": "session-1"})

    assert accepted == {"accepted": True, "session_id": "session-1", "state": "running"}
    assert snapshot.health.connected is True
    assert snapshot.sessions[0].title == "Implement the Stream Deck integration"
    assert snapshot.sessions[0].state is SessionState.RUNNING
    assert snapshot.sessions[0].project == "project"
    assert result == {
        "result": "session_selected_in_claude_code",
        "session_id": "session-1",
    }
    assert launched == [("session-1", str(project))]


def test_plugin_rejects_malformed_hooks_and_unknown_sessions(tmp_path: Path) -> None:
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(tmp_path)}, launcher=lambda _id, _cwd: None
    )

    with pytest.raises(ClaudeCodeError, match="requires session_id"):
        plugin.handle_hook({"hook_event_name": "Stop"})
    with pytest.raises(ClaudeCodeError, match="no longer exists"):
        plugin.invoke("select_session", {"session_id": "missing"})


def test_status_line_rate_limits_become_generic_quota_signals(tmp_path: Path) -> None:
    payload = {
        "session_id": "session-1",
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": 1_738_425_600},
            "seven_day": {"used_percentage": 41.2, "resets_at": 1_738_857_600},
        },
    }
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(tmp_path)}, launcher=lambda _id, _cwd: None
    )

    result = plugin.handle_quota(payload)
    snapshot = plugin.snapshot()

    assert parse_claude_rate_limits(payload) == {
        "primary_percent": 23.5,
        "primary_label": "5H",
        "primary_reset_at": "2025-02-01T16:00:00+00:00",
        "secondary_percent": 41.2,
        "secondary_label": "7D",
        "secondary_reset_at": "2025-02-06T16:00:00+00:00",
    }
    assert result["quota_updated"] is True
    assert snapshot.signals == parse_claude_rate_limits(payload)
    assert plugin.manifest.components == ("session", "quota")


def test_missing_status_line_limits_keep_the_last_known_quota(tmp_path: Path) -> None:
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(tmp_path)}, launcher=lambda _id, _cwd: None
    )
    plugin.handle_quota(
        {"rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": 1000}}}
    )

    result = plugin.handle_quota({"session_id": "new-session"})
    signals = plugin.snapshot().signals

    assert result["quota_updated"] is False
    assert signals is not None
    assert signals["primary_percent"] == 10.0


def test_windows_resume_launches_the_exact_session_with_the_companion_plugin(
    tmp_path: Path,
) -> None:
    companion = tmp_path / "companion"
    companion.mkdir()
    with (
        patch("stream_micro.plugins.claude_code.os.name", "nt"),
        patch(
            "stream_micro.plugins.claude_code.resolve_claude_command",
            return_value=["claude.exe"],
        ),
        patch("stream_micro.plugins.claude_code.subprocess.Popen") as popen,
    ):
        launch_claude_session(
            "session-1",
            str(tmp_path),
            claude_path="claude",
            plugin_dir=str(companion),
        )

    assert popen.call_args.args[0] == [
        "claude.exe",
        "--plugin-dir",
        str(companion),
        "--resume",
        "session-1",
    ]
    assert popen.call_args.kwargs["cwd"] == str(tmp_path)
    assert popen.call_args.kwargs["shell"] is False


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"enabled": "yes"}, "boolean"),
        ({"claude_path": ""}, "claude_path"),
        ({"plugin_dir": []}, "plugin_dir"),
        ({"max_sessions": 0}, "max_sessions"),
        ({"unknown": True}, "Unknown"),
    ],
)
def test_config_validation_rejects_invalid_values(
    config: dict[str, object], message: str
) -> None:
    with pytest.raises(ClaudeCodeError, match=message):
        validate_claude_code_config(config)


def test_bridge_registers_claude_code_only_when_enabled(tmp_path: Path) -> None:
    data = control_config_data(default_control_config())
    data["plugins"]["opencode"]["enabled"] = False
    data["plugins"]["ollama_quota"]["enabled"] = False
    data["plugins"]["claude_code"]["enabled"] = True
    data["plugins"]["claude_code"]["claude_home"] = str(tmp_path)
    path = tmp_path / "bridge.yaml"
    write_control_config(path, parse_control_config(data))

    service = BridgeService(path)
    try:
        assert isinstance(service.plugins["claude_code"], ClaudeCodePlugin)
    finally:
        service.close()


def _register(home: Path, pid: int, session_id: str, **fields) -> None:
    folder = home / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": pid,
        "sessionId": session_id,
        "startedAt": 1_000_000,
        "procStart": "1",
        **fields,
    }
    (folder / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")


def _history_ids(home: Path, *session_ids: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "history.jsonl").write_text(
        "".join(
            json.dumps({"sessionId": sid, "display": sid, "timestamp": 1000 + index}) + "\n"
            for index, sid in enumerate(session_ids)
        ),
        encoding="utf-8",
    )


def test_live_process_status_decides_state_and_dead_sessions_show_off(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "busy1", "idle1", "dead1", "stale1")
    _register(home, 11, "busy1", status="busy")
    _register(home, 12, "idle1", status="idle")
    # pid 13 was reused by another program: its start time no longer matches.
    _register(home, 13, "stale1", status="busy", procStart="9")
    _register(home, 14, "extra1", status="idle", name="Only in registry", cwd="F:/x")
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)},
        process_probe={11: 1, 12: 1, 13: 5, 14: 1}.get,
    )

    snapshot = plugin.snapshot()
    states = {item.id: item.state for item in snapshot.sessions}
    assert states == {
        "busy1": SessionState.RUNNING,
        "idle1": SessionState.IDLE,
        "extra1": SessionState.IDLE,
        "dead1": SessionState.DISCONNECTED,
        "stale1": SessionState.DISCONNECTED,
    }
    order = [item.id for item in snapshot.sessions]
    assert order.index("dead1") > order.index("extra1")  # open Sessions come first
    assert "3 open" in snapshot.health.detail


def test_unknown_process_table_does_not_turn_every_session_off(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1")
    _register(home, 11, "a1", status="busy")
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)}, process_probe=None
    )

    assert plugin.snapshot().sessions[0].state == SessionState.IDLE


def test_fresh_wait_hook_beats_a_coarse_busy_status(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1")
    _register(home, 11, "a1", status="busy", statusUpdatedAt=1_000_000)
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)},
        process_probe={11: 1}.get,
        clock=lambda: 2_000.0,  # hook arrives after the registry status update
    )
    plugin.handle_hook({"session_id": "a1", "hook_event_name": "PermissionRequest"})

    assert plugin.snapshot().sessions[0].state == SessionState.WAITING


def test_an_older_registry_status_never_hides_a_wait_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1")
    _register(home, 11, "a1", status="busy", statusUpdatedAt=999_000)
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)},
        process_probe={11: 1}.get,
        clock=lambda: 1_000.0,  # the hook arrives one second after "busy"
    )
    plugin.handle_hook({"session_id": "a1", "hook_event_name": "PermissionRequest"})
    assert plugin.snapshot().sessions[0].state == SessionState.WAITING

    # A newer busy report means the approval was given.
    _register(home, 11, "a1", status="busy", statusUpdatedAt=1_001_000)
    assert plugin.snapshot().sessions[0].state == SessionState.RUNNING


def test_finished_turn_with_working_subagent_shows_background(tmp_path: Path) -> None:
    import os
    import time as time_module
    from datetime import datetime, timezone

    home = tmp_path / "home"
    _history_ids(home, "abc")
    project = home / "projects" / "F--work"
    project.mkdir(parents=True)
    now = time_module.time()
    stamp = datetime.fromtimestamp(now - 5, tz=timezone.utc).isoformat()
    (project / "abc.jsonl").write_text(
        json.dumps(
            {"type": "assistant", "timestamp": stamp,
             "message": {"stop_reason": "end_turn", "content": []}}
        )
        + "\n",
        encoding="utf-8",
    )
    agents = project / "abc" / "subagents"
    agents.mkdir(parents=True)
    (agents / "agent-worker.jsonl").write_text("{}\n", encoding="utf-8")
    (agents / "agent-worker.meta.json").write_text(
        json.dumps({"requestShape": "background"}), encoding="utf-8"
    )
    os.utime(agents / "agent-worker.jsonl", (now - 10, now - 10))
    plugin = ClaudeCodePlugin({"enabled": True, "claude_home": str(home)}, clock=lambda: now)

    assert plugin.snapshot().sessions[0].state == SessionState.BACKGROUND


def test_sessions_hosted_by_the_claude_desktop_app_are_left_out(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "cli-a", "desk-a")
    _register(home, 11, "cli-a", status="idle", entrypoint="cli")
    _register(home, 12, "desk-a", status="busy", entrypoint="claude-desktop")
    _register(home, 13, "desk-b", status="idle", hostSessionId="local_b", name="Desktop tab")
    project = home / "projects" / "app"
    project.mkdir(parents=True)
    (project / "desk-a.jsonl").write_text(
        json.dumps({"type": "user", "entrypoint": "claude-desktop"}) + "\n", encoding="utf-8"
    )
    alive = {11: 1, 12: 1, 13: 1}
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)}, process_probe=lambda pid: alive.get(pid)
    )

    assert [item.id for item in plugin.snapshot().sessions] == ["cli-a"]
    # Still left out once the desktop app has closed it, also after a restart.
    alive.pop(12)
    assert [item.id for item in plugin.snapshot().sessions] == ["cli-a"]
    restarted = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)}, process_probe=lambda pid: alive.get(pid)
    )
    assert [item.id for item in restarted.snapshot().sessions] == ["cli-a"]


def test_old_done_hook_expires_and_newer_idle_ends_an_old_wait(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1", "b1")
    _register(home, 11, "a1", status="idle", statusUpdatedAt=1_000_000)
    _register(home, 12, "b1", status="idle", statusUpdatedAt=2_000_000)
    now = [1_100.0]
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)},
        process_probe={11: 1, 12: 1}.get,
        clock=lambda: now[0],
    )
    plugin.handle_hook({"session_id": "a1", "hook_event_name": "Stop"})
    plugin.handle_hook({"session_id": "b1", "hook_event_name": "PermissionRequest"})
    states = {item.id: item.state for item in plugin.snapshot().sessions}
    assert states["a1"] == SessionState.COMPLETED

    now[0] = 1_100.0 + 3600  # an hour later; b1's registry went idle after the prompt
    states = {item.id: item.state for item in plugin.snapshot().sessions}
    assert states["a1"] == SessionState.IDLE  # DONE is shown for a limited time
    assert states["b1"] == SessionState.IDLE


def test_an_old_stop_never_ends_a_newer_turn(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1")
    project = home / "projects" / "app"
    project.mkdir(parents=True)
    prompt = {"type": "user", "timestamp": "2026-10-14T00:00:01Z", "message": {"content": "next"}}
    (project / "a1.jsonl").write_text(json.dumps(prompt) + "\n", encoding="utf-8")
    from datetime import datetime, timezone

    stop_at = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    now = [stop_at]
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)}, process_probe=None, clock=lambda: now[0]
    )
    plugin.handle_hook({"session_id": "a1", "hook_event_name": "Stop"})  # one second earlier
    now[0] = stop_at + 5
    assert plugin.snapshot().sessions[0].state == SessionState.RUNNING


def test_a_session_alive_moments_ago_is_not_off_after_one_missed_read(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _history_ids(home, "a1")
    _register(home, 11, "a1", status="busy", statusUpdatedAt=1_000_000)
    alive = {11: 1}
    now = [1_000.0]
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)},
        process_probe=lambda pid: alive.get(pid),
        clock=lambda: now[0],
    )
    assert plugin.snapshot().sessions[0].state == SessionState.RUNNING

    (home / "sessions" / "11.json").write_text("{", encoding="utf-8")  # being rewritten
    now[0] += 5
    assert plugin.snapshot().sessions[0].state != SessionState.DISCONNECTED

    now[0] += 60  # still missing: the process is gone
    assert plugin.snapshot().sessions[0].state == SessionState.DISCONNECTED


def test_snapshots_run_one_at_a_time(tmp_path: Path) -> None:
    import threading
    import time

    home = tmp_path / "home"
    _history_ids(home, "a1")
    plugin = ClaudeCodePlugin({"enabled": True, "claude_home": str(home)}, process_probe=None)
    original, active, peak = plugin._snapshot, [0], [0]

    def slow():
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        try:
            return original()
        finally:
            active[0] -= 1

    plugin._snapshot = slow  # type: ignore[method-assign]
    threads = [threading.Thread(target=plugin.snapshot) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 1


def test_a_wait_hook_a_moment_before_its_record_still_shows_wait(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    home = tmp_path / "home"
    _history_ids(home, "a1")
    project = home / "projects" / "app"
    project.mkdir(parents=True)
    asked = datetime(2026, 10, 14, 0, 0, 0, 500000, tzinfo=timezone.utc)
    record = {
        "type": "assistant",
        "timestamp": asked.isoformat().replace("+00:00", "Z"),
        "message": {"stop_reason": "tool_use", "content": []},
    }
    (project / "a1.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    now = [asked.timestamp() - 0.3]  # the hook lands 0.3 s before the record's time
    plugin = ClaudeCodePlugin(
        {"enabled": True, "claude_home": str(home)}, process_probe=None, clock=lambda: now[0]
    )
    plugin.handle_hook({"session_id": "a1", "hook_event_name": "PermissionRequest"})
    now[0] = asked.timestamp() + 2
    assert plugin.snapshot().sessions[0].state == SessionState.WAITING


def test_a_transcript_without_a_front_end_is_scanned_once_per_version(
    tmp_path: Path, monkeypatch
) -> None:
    import stream_micro.plugins.claude_code as code_module

    home = tmp_path / "home"
    _history_ids(home, "a1")
    project = home / "projects" / "app"
    project.mkdir(parents=True)
    transcript = project / "a1.jsonl"
    transcript.write_text(json.dumps({"type": "summary"}) + "\n", encoding="utf-8")
    scans = []
    real = code_module.transcript_entrypoint

    def counting(path):
        scans.append(path)
        return real(path)

    monkeypatch.setattr(code_module, "transcript_entrypoint", counting)
    plugin = ClaudeCodePlugin({"enabled": True, "claude_home": str(home)}, process_probe=None)
    plugin.snapshot()
    plugin.snapshot()
    assert len(scans) == 1
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "user", "entrypoint": "cli"}) + "\n")
    plugin.snapshot()
    assert len(scans) == 2
