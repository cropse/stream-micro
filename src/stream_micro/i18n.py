"""Backend string i18n for tray and CLI surfaces.

Mirrors the Dashboard's approach (frontend/src/i18n.ts): English source text
is the key, a Traditional Chinese dictionary translates it, and anything
without a translation simply stays English. Only English and Traditional
Chinese are supported in this version.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .control_config import load_control_config

# Windows LANGIDs treated as Traditional Chinese (zh-TW, zh-HK, zh-MO).
_ZH_HANT_LANGIDS = frozenset({0x0404, 0x0C04, 0x1404})

ZH_TW: dict[str, str] = {
    "Open Dashboard": "開啟 Dashboard",
    "Restart Bridge": "重新啟動 Bridge",
    "Stop Bridge": "停止 Bridge",
    "Run at log in": "登入自動啟動",
    "Exit": "結束",
    "Starting": "啟動中",
    "Stopping Bridge": "正在停止 Bridge",
    "Starting Bridge": "正在啟動 Bridge",
    "Bridge stopped": "Bridge 已停止",
    "Bridge running": "Bridge 執行中",
    "Bridge stopped or not responding": "Bridge 已停止或沒有回應",
    "Operation failed; see bridge.log": "操作失敗；請查看 bridge.log",
    "created Bridge configuration at {path}": "已建立 Bridge 設定檔：{path}",
    "stopped": "已停止",
    (
        "Stream Micro could not start. Check bridge.log and run uv sync --dev "
        "in the project folder, then try again."
    ): "Stream Micro 無法啟動。請查看 bridge.log，在專案資料夾執行 uv sync --dev 後再試一次。",
    (
        "Stream Micro could not start. Check bridge.log next to StreamMicro.exe, "
        "then try again."
    ): "Stream Micro 無法啟動。請查看 StreamMicro.exe 旁的 bridge.log 後再試一次。",
}


def t(text: str, language: str) -> str:
    """Return `text` in `language` ("en" or "zh-TW"); unknown keys stay English."""

    return ZH_TW.get(text, text) if language == "zh-TW" else text


def windows_ui_language() -> int | None:
    """Return the Windows default UI language LANGID, or None when unknown."""

    if sys.platform != "win32":
        return None
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return None
    return langid if langid > 0 else None


def resolve_language(language: str, *, ui_language: int | None = None) -> str:
    """Resolve a configured language to "en" or "zh-TW".

    Explicit values pass through; "auto" follows the Windows UI language when
    it is a Traditional Chinese one (an injected `ui_language` wins, for tests).
    """

    if language in ("en", "zh-TW"):
        return language
    langid = ui_language if ui_language is not None else windows_ui_language()
    return "zh-TW" if langid in _ZH_HANT_LANGIDS else "en"


def language_from_config(config_path: Path) -> str:
    """Read the configured language; any failure falls back to "auto".

    Best-effort by contract: the tray polls this every cycle, so a broken
    configuration must degrade to "auto", never take the worker down —
    including parser errors outside the ControlConfigError hierarchy.
    """

    try:
        return load_control_config(config_path).language
    except Exception:
        return "auto"