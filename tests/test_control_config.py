from pathlib import Path

import pytest

from stream_micro.control_config import (
    MK2_KEY_COUNT,
    ControlConfigError,
    control_config_data,
    default_control_config,
    load_control_config,
    parse_control_config,
    write_control_config,
)


def test_default_control_config_has_mk2_layout_and_span_session_zone() -> None:
    config = default_control_config()

    assert config.stream_deck.model == "mk2"
    assert len(config.buttons) == MK2_KEY_COUNT
    assert config.buttons[0].component == "session"
    assert config.buttons[0].span == 5
    assert config.buttons[1].component is None
    assert config.buttons[5].component is None
    assert config.plugins["claude_code"]["enabled"] is False


def test_legacy_five_session_keys_migrate_to_one_span_zone() -> None:
    """Pre-span bridge.yaml files must still show five distinct Sessions."""

    data = control_config_data(default_control_config())
    for index in range(5):
        data["buttons"][index].update(
            {
                "plugin": "opencode",
                "component": "session",
                "span": 1,
                "session_id": None,
                "action": "select_session",
            }
        )

    config = parse_control_config(data)

    assert config.buttons[0].span == 5
    assert all(config.buttons[index].component is None for index in range(1, 5))


def test_control_config_round_trip_is_atomic_and_loadable(tmp_path: Path) -> None:
    path = tmp_path / "control.yaml"
    write_control_config(path, default_control_config())

    loaded = load_control_config(path)

    assert control_config_data(loaded) == control_config_data(default_control_config())
    assert not path.with_suffix(".yaml.tmp").exists()


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data["stream_deck"].update({"model": "xl"}), "model"),
        (lambda data: data.update({"buttons": data["buttons"][:-1]}), "exactly 15"),
        (lambda data: data["buttons"][0].update({"plugin": "missing"}), "unknown plugin"),
        (
            lambda data: data["buttons"][0].update(
                {"window_binding": {"executable": "x", "window_class": "y"}}
            ),
            "window_binding",
        ),
    ],
)
def test_control_config_rejects_invalid_bindings(tmp_path: Path, mutator, message: str) -> None:
    import yaml

    path = tmp_path / "invalid.yaml"
    data = control_config_data(default_control_config())
    mutator(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ControlConfigError, match=message):
        load_control_config(path)


def test_control_config_validates_antigravity_even_when_disabled() -> None:
    data = control_config_data(default_control_config())
    data["plugins"]["antigravity"]["enabled"] = False
    data["plugins"]["antigravity"]["agy_path"] = ""

    with pytest.raises(ControlConfigError, match=r"plugins\.antigravity\.agy_path"):
        parse_control_config(data)
