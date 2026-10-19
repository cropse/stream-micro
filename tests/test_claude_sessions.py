from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stream_micro.plugins.base import SessionState
from stream_micro.plugins.claude_sessions import (
    BackgroundTaskTracker,
    read_live_processes,
    scan_background_tasks,
    transcript_entrypoint,
    transcript_state,
)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    ("records", "age", "expected"),
    [
        ([{"type": "user", "message": {"content": "do it"}}], 5, SessionState.RUNNING),
        (
            [{"type": "assistant", "message": {"stop_reason": "tool_use", "content": []}}],
            5,
            SessionState.RUNNING,
        ),
        (
            [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}],
            5,
            SessionState.COMPLETED,
        ),
        (
            [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}],
            7200,
            SessionState.IDLE,
        ),
        ([{"type": "user", "message": {"content": "go"}}], 7200, SessionState.IDLE),
        (
            [{"type": "user", "message": {"content": "[Request interrupted by user]"}}],
            5,
            SessionState.IDLE,
        ),
        ([{"type": "assistant", "isApiErrorMessage": True, "message": {}}], 5, SessionState.ERROR),
    ],
)
def test_transcript_state_reads_newest_turn_record(
    tmp_path: Path, records: list, age: float, expected: SessionState
) -> None:
    path = tmp_path / "s.jsonl"
    lines = [*records, {"type": "last-prompt"}, {"type": "custom-title"}]
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
    os.utime(path, (1_000_000, 1_000_000))

    assert transcript_state(path, 1_000_000 + age) == (expected, 1_000_000)


def test_transcript_age_uses_record_timestamp_not_file_mtime(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    record = {"type": "user", "timestamp": iso(1_000_000), "message": {"content": "go"}}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")  # mtime is "now"

    assert transcript_state(path, 1_000_000 + 7200) == (SessionState.IDLE, 1_000_000)


def _subagent(folder: Path, agent_id: str, shape: str, modified: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    transcript = folder / f"agent-{agent_id}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    (folder / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"requestShape": shape}), encoding="utf-8"
    )
    os.utime(transcript, (modified, modified))


def _notification(task_id: str, when: str) -> dict:
    return {
        "type": "queue-operation",
        "timestamp": when,
        "content": f"<task-notification>\n<task-id>{task_id}</task-id>\n</task-notification>",
    }


NOW = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc).timestamp()


def test_background_subagent_without_completion_notice_is_active(tmp_path: Path) -> None:
    transcript = tmp_path / "s1.jsonl"
    folder = tmp_path / "s1" / "subagents"
    _subagent(folder, "done1", "background", NOW - 900)
    _subagent(folder, "fg1", "foreground", NOW - 30)
    transcript.write_text(
        json.dumps(_notification("done1", "2026-10-06T05:46:00Z")) + "\n", encoding="utf-8"
    )
    tracker = BackgroundTaskTracker()
    # A finished background agent (notice after its last write) and a
    # foreground agent (it keeps the main turn running) do not count.
    assert not tracker.subagents_active(transcript, NOW)

    _subagent(folder, "live1", "background", NOW - 3000)
    assert tracker.subagents_active(transcript, NOW)
    # Work last written before the current Claude process started died with it.
    assert not tracker.subagents_active(transcript, NOW, since=NOW - 60)


def test_resumed_subagent_counts_again_after_older_notice(tmp_path: Path) -> None:
    transcript = tmp_path / "s2.jsonl"
    _subagent(tmp_path / "s2" / "subagents", "again", "background", NOW - 60)
    transcript.write_text(
        json.dumps(_notification("again", "2026-10-06T05:30:00Z")) + "\n", encoding="utf-8"
    )
    assert BackgroundTaskTracker().subagents_active(transcript, NOW)


def test_external_agent_background_command_counts_until_notified(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    launch = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "input": {
                        "command": "codeagent-wrapper --backend codex task",
                        "run_in_background": True,
                    },
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "input": {"command": "node server.mjs", "run_in_background": True},
                },
            ]
        },
    }
    results = {
        "type": "user",
        "timestamp": "2026-10-06T05:00:00Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "Command running in background with ID: bext1. Output ...",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "Command running in background with ID: bsrv1. Output ...",
                },
            ]
        },
    }
    transcript.write_text(json.dumps(launch) + "\n" + json.dumps(results) + "\n", "utf-8")
    launched = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc).timestamp()

    tasks = scan_background_tasks(transcript)
    assert tasks.external_launches == {"bext1": launched}  # the dev server is ignored
    assert tasks.external_pending(since=launched - 60)
    assert not tasks.external_pending(since=launched + 60)  # launched by a dead process

    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_notification("bext1", "2026-10-06T05:10:00Z")) + "\n")
    tracker = BackgroundTaskTracker()
    assert not tracker.tasks(transcript).external_pending(since=launched - 60)


def test_tracker_reads_only_appended_lines_and_rereads_a_shrunk_file(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    launch = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "input": {"command": "codex exec task", "run_in_background": True},
                }
            ]
        },
    }
    result = {
        "type": "user",
        "timestamp": "2026-10-06T05:00:00Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "Command running in background with ID: bx. Output ...",
                }
            ]
        },
    }
    launched = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc).timestamp()
    transcript.write_text(json.dumps(launch) + "\n", "utf-8")
    tracker = BackgroundTaskTracker()
    assert tracker.tasks(transcript).external_launches == {}

    # Appended lines are picked up; a half-written last line waits for its end.
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(result) + "\n" + json.dumps(_notification("bx", "2026-10-06T05:10:00Z"))[:20]
        )
    tasks = tracker.tasks(transcript)
    assert tasks.external_launches == {"bx": launched}
    assert tasks.external_pending(since=launched - 60)
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_notification("bx", "2026-10-06T05:10:00Z"))[20:] + "\n")
    assert not tracker.tasks(transcript).external_pending(since=launched - 60)

    # A rewritten, shorter file is read again from the start.
    transcript.write_text(json.dumps(launch) + "\n", "utf-8")
    assert tracker.tasks(transcript).external_launches == {}


def test_transcript_entrypoint_names_the_front_end(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(
        json.dumps({"type": "summary"})
        + "\n"
        + json.dumps({"type": "user", "entrypoint": "claude-desktop"})
        + "\n",
        "utf-8",
    )
    assert transcript_entrypoint(transcript) == "claude-desktop"
    assert transcript_entrypoint(tmp_path / "missing.jsonl") is None


def test_live_processes_skip_dead_pids_and_unknown_process_tables(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for pid, session_id, extra in (
        (11, "cli-1", {"entrypoint": "cli"}),
        (12, "desk-1", {"entrypoint": "claude-desktop", "hostSessionId": "local_ab"}),
        (13, "gone-1", {}),
    ):
        record = {
            "pid": pid,
            "sessionId": session_id,
            "startedAt": 5000,
            "procStart": str(pid - 4),
            "status": "idle",
            **extra,
        }
        (sessions / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")

    live = read_live_processes(sessions, {11: 7, 12: 8}.get)
    assert live is not None
    assert set(live) == {"cli-1", "desk-1"}
    assert live["desk-1"].desktop and not live["cli-1"].desktop
    assert live["cli-1"].started_at == 5.0

    # No way to check processes, or no registry: fall back to other evidence.
    assert read_live_processes(sessions, None) is None
    assert read_live_processes(tmp_path / "missing", {}.get) is None


def test_live_process_identity_uses_the_recorded_start_time(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for pid, session_id, started in ((21, "node-cli", "777"), (22, "reused", "888")):
        record = {"pid": pid, "sessionId": session_id, "procStart": started, "status": "busy"}
        (sessions / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")

    # Any program name is fine (an npm install runs as node.exe) as long as the
    # start time matches; pid 22 now belongs to a different process.
    live = read_live_processes(sessions, {21: 777, 22: 999}.get)
    assert live is not None and set(live) == {"node-cli"}


def test_a_registry_in_another_time_unit_is_not_trusted(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    # Claude Code once wrote Windows FILETIME ticks; milliseconds would mean the
    # format changed, and every Session would wrongly look closed.
    record = {"pid": 31, "sessionId": "a1", "procStart": "1791987832345", "status": "busy"}
    (sessions / "31.json").write_text(json.dumps(record), encoding="utf-8")

    assert read_live_processes(sessions, {31: 134_000_000_000_000_000}.get) is None


def test_a_registration_without_procstart_must_date_from_the_process(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    filetime_epoch = 116_444_736_000_000_000
    started_at = 1_800_000_000  # seconds
    ticks = filetime_epoch + started_at * 10_000_000
    for pid, session_id, registered in ((41, "same", started_at + 3), (42, "reused", 1_000)):
        record = {"pid": pid, "sessionId": session_id, "startedAt": registered * 1000}
        (sessions / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")

    live = read_live_processes(sessions, {41: ticks, 42: ticks}.get)
    assert live is not None and set(live) == {"same"}


def test_subagent_records_in_the_main_transcript_are_not_the_turn(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    now = 1_800_000_000.0
    records = [
        {
            "type": "assistant",
            "timestamp": iso(now - 60),
            "message": {"stop_reason": "end_turn", "content": []},
        },
        {
            "type": "user",
            "isSidechain": True,
            "timestamp": iso(now - 5),
            "message": {"content": "subagent work"},
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")

    state = transcript_state(path, now)
    assert state is not None and state[0] == SessionState.COMPLETED


def test_an_api_error_fades_like_done(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    now = 1_800_000_000.0
    record = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "timestamp": iso(now - 7200),
        "message": {},
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    state = transcript_state(path, now)
    assert state is not None and state[0] == SessionState.IDLE


def test_the_front_end_is_found_after_a_huge_first_prompt(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    huge = {"type": "user", "message": {"content": "x" * 200_000}}
    marked = {"type": "assistant", "entrypoint": "claude-desktop"}
    path.write_text(json.dumps(huge) + "\n" + json.dumps(marked) + "\n", encoding="utf-8")

    assert transcript_entrypoint(path) == "claude-desktop"


def test_a_cached_transcript_still_ages_and_follows_changes(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    now = 1_800_000_000.0
    turn = {"type": "user", "timestamp": iso(now - 5), "message": {"content": "go"}}
    path.write_text(json.dumps(turn) + "\n", encoding="utf-8")

    assert transcript_state(path, now) == (SessionState.RUNNING, now - 5)
    # Same file, later: the cached record is judged against the new time.
    assert transcript_state(path, now + 3600) == (SessionState.IDLE, now - 5)
    done = {
        "type": "assistant",
        "timestamp": iso(now + 10),
        "message": {"stop_reason": "end_turn", "content": []},
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(done) + "\n")
    assert transcript_state(path, now + 20) == (SessionState.COMPLETED, now + 10)


def test_one_odd_registration_is_skipped_not_the_whole_registry(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    good = {"pid": 51, "sessionId": "good", "procStart": "134000000000000001", "status": "busy"}
    odd = {"pid": 52, "sessionId": "odd", "procStart": "1791987832345", "status": "busy"}
    for record in (good, odd):
        (sessions / f"{record['pid']}.json").write_text(json.dumps(record), encoding="utf-8")

    probe = {51: 134_000_000_000_000_001, 52: 134_000_000_000_000_002}.get
    live = read_live_processes(sessions, probe)
    assert live is not None and set(live) == {"good"}


def test_a_pid_reused_soon_after_a_registration_without_procstart_is_rejected(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    filetime_epoch = 116_444_736_000_000_000
    registered = 1_800_000_000  # seconds
    record = {"pid": 61, "sessionId": "gone", "startedAt": registered * 1000}
    (sessions / "61.json").write_text(json.dumps(record), encoding="utf-8")

    # Another program got PID 61 thirty seconds after Claude registered it.
    reused = filetime_epoch + (registered + 30) * 10_000_000
    assert read_live_processes(sessions, {61: reused}.get) == {}
    # The registering process itself started a few seconds before registering.
    own = filetime_epoch + (registered - 3) * 10_000_000
    live = read_live_processes(sessions, {61: own}.get)
    assert live is not None and set(live) == {"gone"}


def test_a_failed_transcript_read_is_retried_not_cached(tmp_path: Path, monkeypatch) -> None:
    import stream_micro.plugins.claude_sessions as sessions_module

    path = tmp_path / "s.jsonl"
    now = 1_800_000_000.0
    turn = {"type": "user", "timestamp": iso(now - 5), "message": {"content": "go"}}
    path.write_text(json.dumps(turn) + "\n", encoding="utf-8")
    real = sessions_module.read_tail
    calls = []

    def flaky(target):
        calls.append(target)
        return None if len(calls) == 1 else real(target)

    monkeypatch.setattr(sessions_module, "read_tail", flaky)
    assert transcript_state(path, now) is None  # the file could not be read just now
    assert transcript_state(path, now) == (SessionState.RUNNING, now - 5)  # same file version
