"""Tests for foreground forcing on app-switch deep links.

Windows denies ``SetForegroundWindow`` from background processes while the
foreground lock is engaged.  The Stream Deck press path must therefore force
the switched-to app window to the front after dispatching the deep link.
"""

from __future__ import annotations

import pytest

from stream_micro.windows_focus import WindowFocusError, focus_app_windows


def test_select_openchamber_session_forces_the_desktop_window_foreground(
    monkeypatch,
) -> None:
    """The deep link alone does not raise the window; focus must follow it."""

    opened: list[str] = []
    focused: list[tuple[str, ...]] = []
    monkeypatch.setattr("stream_micro.openchamber.os.startfile", opened.append)
    monkeypatch.setattr(
        "stream_micro.openchamber.focus_app_windows", lambda names: focused.append(names)
    )

    from stream_micro.openchamber import select_openchamber_session

    select_openchamber_session(session_id="ses_abc", directory=None)

    assert opened == ["openchamber://session/ses_abc"]
    assert focused == [("OpenChamber.exe",)]


def test_select_openchamber_session_swallows_focus_errors(monkeypatch) -> None:
    """A missing window must not fail the session switch deep link."""

    opened: list[str] = []
    monkeypatch.setattr("stream_micro.openchamber.os.startfile", opened.append)

    def failing_focus(names: tuple[str, ...]) -> None:
        raise WindowFocusError("no visible window")

    monkeypatch.setattr("stream_micro.openchamber.focus_app_windows", failing_focus)

    from stream_micro.openchamber import select_openchamber_session

    select_openchamber_session(session_id="ses_abc", directory=None)

    assert opened == ["openchamber://session/ses_abc"]


def test_select_codex_thread_forces_the_chatgpt_window_foreground(monkeypatch) -> None:
    opened: list[str] = []
    focused: list[tuple[str, ...]] = []
    monkeypatch.setattr("stream_micro.plugins.codex.os.startfile", opened.append)
    monkeypatch.setattr(
        "stream_micro.plugins.codex.focus_app_windows", lambda names: focused.append(names)
    )
    monkeypatch.setattr("stream_micro.plugins.codex.os.name", "nt")

    from stream_micro.plugins.codex import select_codex_thread

    select_codex_thread("019f/thread id")

    assert opened == ["codex://threads/019f%2Fthread%20id"]
    assert focused == [("ChatGPT.exe",)]


def test_focus_app_windows_raises_when_no_window_exists(monkeypatch) -> None:
    def fake_enum(user32, kernel32, names):
        return []

    monkeypatch.setattr("stream_micro.windows_focus._enum_windows_find", fake_enum)
    with pytest.raises(WindowFocusError):
        focus_app_windows(("GhostApp.exe",))


def test_focus_app_windows_skips_empty_targets() -> None:
    focus_app_windows(())
