import json
from pathlib import Path

import pytest

from stream_micro.control_config import default_control_config
from stream_micro.plugins.antigravity import (
    AntigravityCacheSession,
    AntigravityError,
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
