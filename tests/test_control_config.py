from dataclasses import replace
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


def test_quota_display_defaults_to_remaining_and_rejects_unknown_modes() -> None:
    data = control_config_data(default_control_config())
    data["stream_deck"].pop("quota_display")
    assert parse_control_config(data).stream_deck.quota_display == "remaining"

    data["stream_deck"]["quota_display"] = "used"
    assert parse_control_config(data).stream_deck.quota_display == "used"

    data["stream_deck"]["quota_display"] = "percent"
    with pytest.raises(ControlConfigError, match="quota_display"):
        parse_control_config(data)


def test_quota_keys_cover_exactly_one_key() -> None:
    data = control_config_data(default_control_config())
    data["buttons"] = list(data["buttons"])
    data["buttons"][5] = {**data["buttons"][5], "plugin": "ollama_quota", "component": "quota",
                          "span": 2}
    with pytest.raises(ControlConfigError, match="one key"):
        parse_control_config(data)


def test_quota_keys_accept_only_text_account_and_label_options() -> None:
    data = control_config_data(default_control_config())
    data["buttons"] = list(data["buttons"])
    data["buttons"][5] = {
        **data["buttons"][5],
        "plugin": "ollama_quota",
        "component": "quota",
        "action": None,
        "action_params": {"account": "codex-a.json", "label": "Codex 1"},
    }
    parse_control_config(data)

    for bad in ({"account": ["a", "b"]}, {"label": {"text": "x"}}, {"command": "rm"}):
        data["buttons"][5]["action_params"] = bad
        with pytest.raises(ControlConfigError):
            parse_control_config(data)


def test_default_config_has_a_disabled_cliproxy_quota_service() -> None:
    section = default_control_config().plugins["cliproxy_quota"]

    assert section["enabled"] is False
    assert section["management_key"] is None
    assert section["allow_insecure_http"] is False
    assert section["providers"] == ["codex", "claude"]


def test_language_accepts_valid_values() -> None:
    base = control_config_data(default_control_config())
    for value in ("auto", "en", "zh-TW"):
        assert parse_control_config({**base, "language": value}).language == value


def test_language_defaults_to_auto_when_missing() -> None:
    base = control_config_data(default_control_config())
    base.pop("language")
    assert parse_control_config(base).language == "auto"


def test_language_rejects_invalid_value() -> None:
    base = control_config_data(default_control_config())
    with pytest.raises(ControlConfigError, match="language must be one of: auto, en, zh-TW"):
        parse_control_config({**base, "language": "fr"})


def test_language_round_trips_through_serialization() -> None:
    config = replace(default_control_config(), language="zh-TW")
    assert parse_control_config(control_config_data(config)).language == "zh-TW"
