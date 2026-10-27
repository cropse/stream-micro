from dataclasses import replace
from pathlib import Path

from stream_micro import i18n
from stream_micro.control_config import default_control_config, write_control_config
from stream_micro.i18n import language_from_config, resolve_language, t


def test_t_returns_translation_and_falls_back() -> None:
    assert t("Bridge running", "zh-TW") == "Bridge 執行中"
    assert t("Bridge running", "en") == "Bridge running"
    assert t("not in dictionary", "zh-TW") == "not in dictionary"


def test_resolve_language_explicit_passthrough() -> None:
    assert resolve_language("en") == "en"
    assert resolve_language("zh-TW") == "zh-TW"


def test_resolve_language_auto_uses_zh_hant_langids() -> None:
    for langid in (0x0404, 0x0C04, 0x1404):  # zh-TW, zh-HK, zh-MO
        assert resolve_language("auto", ui_language=langid) == "zh-TW"
    for langid in (0x0409, 0x0804):  # en-US, zh-CN
        assert resolve_language("auto", ui_language=langid) == "en"


def test_resolve_language_auto_without_detection_is_english(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "windows_ui_language", lambda: None)
    assert resolve_language("auto") == "en"


def test_language_from_config_reads_and_falls_back(tmp_path: Path) -> None:
    config = replace(default_control_config(), language="zh-TW")
    path = tmp_path / "bridge.yaml"
    write_control_config(path, config)
    assert language_from_config(path) == "zh-TW"
    assert language_from_config(tmp_path / "missing.yaml") == "auto"
    bad = tmp_path / "bad.yaml"
    bad.write_text("{not: valid: yaml", encoding="utf-8")
    assert language_from_config(bad) == "auto"


def test_language_from_config_tolerates_structurally_invalid_yaml(tmp_path: Path) -> None:
    """Best-effort reader: any failure must fall back to "auto", never raise.

    Mixed-type top-level keys make parse_control_config's sorted() raise
    TypeError; the tray's 2-second poll must survive that.
    """

    bad = tmp_path / "structurally-bad.yaml"
    bad.write_text("1: broken\nunexpected: true\n", encoding="utf-8")
    assert language_from_config(bad) == "auto"