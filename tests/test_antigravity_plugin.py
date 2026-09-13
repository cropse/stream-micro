import json
from pathlib import Path

import pytest

from stream_micro.control_config import default_control_config
from stream_micro.plugins.antigravity import (
    MAX_CACHE_BYTES,
    MAX_VISIBLE_SESSIONS,
    AntigravityCacheSession,
    AntigravityError,
    AntigravityPlugin,
    read_last_conversations,
    validate_antigravity_config,
)


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
