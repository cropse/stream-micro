from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from stream_micro import app
from stream_micro.config import AppConfig, load_config
from stream_micro.device import DeviceError
from stream_micro.models import QuotaSnapshot, QuotaWindow, WindowKind
from stream_micro.scraper import ScrapeError


def config_for(tmp_path: Path, **deck_changes: object) -> AppConfig:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ollama:\n  cookie_value: secret\n", encoding="utf-8")
    config = load_config(config_path)
    return replace(config, stream_deck=replace(config.stream_deck, **deck_changes))


def snapshot() -> QuotaSnapshot:
    return QuotaSnapshot(
        QuotaWindow(WindowKind.SESSION, 10),
        QuotaWindow(WindowKind.WEEKLY, 20),
    )


class FakeOutput:
    def __init__(self, *, enter_error: DeviceError | None = None) -> None:
        self.key_size = (72, 72)
        self.enter_error = enter_error
        self.entered = 0
        self.exited = 0
        self.writes: list[tuple[int, object]] = []
        self.exit_exception: type[BaseException] | None = None

    def __enter__(self) -> FakeOutput:
        self.entered += 1
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        self.exited += 1
        self.exit_exception = exc_type

    def set_key(self, index: int, image: object) -> None:
        self.writes.append((index, image))


def output_sequence(monkeypatch, outputs: list[FakeOutput]) -> None:
    remaining = iter(outputs)
    monkeypatch.setattr(app, "StreamDeckOutput", lambda _config: next(remaining))


def use_named_images(monkeypatch) -> None:
    monkeypatch.setattr(
        app,
        "render_quota_key",
        lambda quota, label, _config, _size: f"{label}:{quota.percent_used:g}",
    )
    monkeypatch.setattr(app, "render_error_key", lambda *_args: "ERROR")
    monkeypatch.setattr(app, "render_blank_key", lambda *_args: "BLANK")


def test_run_once_success_updates_configured_keys_without_sleep(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path, session_key_index=3, weekly_key_index=5)
    output = FakeOutput()
    output_sequence(monkeypatch, [output])
    use_named_images(monkeypatch)
    monkeypatch.setattr(app, "fetch_quota", lambda _config: snapshot())
    sleeps: list[float] = []

    app.run(config, once=True, sleep=sleeps.append)

    assert output.writes == [(3, "5H:10"), (5, "WEEK:20")]
    assert output.exited == 1
    assert sleeps == []


def test_run_retries_after_fetch_failure_and_sleeps_between_polls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    output = FakeOutput()
    output_sequence(monkeypatch, [output])
    use_named_images(monkeypatch)
    fetches = 0

    def fetch(_config) -> QuotaSnapshot:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            raise ScrapeError("temporary network failure")
        return snapshot()

    monkeypatch.setattr(app, "fetch_quota", fetch)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        app.run(config, sleep=sleep)

    assert output.writes == [(0, "ERROR"), (1, "ERROR"), (0, "5H:10"), (1, "WEEK:20")]
    assert sleeps == [60.0, 60.0]
    assert output.exit_exception is KeyboardInterrupt


def test_run_once_fetch_failure_writes_error_to_both_configured_keys(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path, session_key_index=4, weekly_key_index=7)
    output = FakeOutput()
    output_sequence(monkeypatch, [output])
    use_named_images(monkeypatch)
    monkeypatch.setattr(
        app,
        "fetch_quota",
        lambda _config: (_ for _ in ()).throw(ScrapeError("expired")),
    )

    app.run(config, once=True, sleep=lambda _seconds: pytest.fail("once must not sleep"))

    assert output.writes == [(4, "ERROR"), (7, "ERROR")]
    assert output.exited == 1


def test_run_reconnects_after_device_failure(monkeypatch, tmp_path: Path) -> None:
    config = config_for(tmp_path)
    unavailable = FakeOutput(enter_error=DeviceError("disconnected"))
    connected = FakeOutput()
    output_sequence(monkeypatch, [unavailable, connected])
    monkeypatch.setattr(
        app,
        "update_once",
        lambda _config, _output: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    sleeps: list[float] = []

    with pytest.raises(KeyboardInterrupt):
        app.run(config, sleep=sleeps.append)

    assert unavailable.entered == 1
    assert connected.entered == 1
    assert connected.exit_exception is KeyboardInterrupt
    assert sleeps == [60.0]


def test_keyboard_interrupt_during_fetch_closes_output(monkeypatch, tmp_path: Path) -> None:
    config = config_for(tmp_path)
    output = FakeOutput()
    output_sequence(monkeypatch, [output])
    monkeypatch.setattr(
        app,
        "fetch_quota",
        lambda _config: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        app.run(config, once=True)

    assert output.exited == 1
    assert output.exit_exception is KeyboardInterrupt


def test_monthly_credit_plan_shows_on_the_first_key(monkeypatch, tmp_path: Path) -> None:
    config = config_for(tmp_path, session_key_index=3, weekly_key_index=5)
    output = FakeOutput()
    output_sequence(monkeypatch, [output])
    use_named_images(monkeypatch)
    monthly = QuotaSnapshot(monthly=QuotaWindow(WindowKind.MONTHLY, 11))
    monkeypatch.setattr(app, "fetch_quota", lambda _config: monthly)

    app.run(config, once=True, sleep=lambda _seconds: None)

    assert output.writes == [(3, "MONTH:11"), (5, "BLANK")]
