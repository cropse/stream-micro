from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image

from stream_micro.config import StreamDeckConfig
from stream_micro.device import DeviceError, StreamDeckOutput


class FakeDeck:
    def __init__(
        self,
        serial: str,
        *,
        keys: int = 15,
        visual: bool = True,
        brightness_error: Exception | None = None,
    ) -> None:
        self.serial = serial
        self.keys = keys
        self.visual = visual
        self.brightness_error = brightness_error
        self.opened = 0
        self.closed = 0
        self.resets = 0
        self.brightness: list[int] = []
        self.writes: list[tuple[int, object]] = []
        self.context_entries = 0

    def is_visual(self) -> bool:
        return self.visual

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def get_serial_number(self) -> str:
        return self.serial

    def key_count(self) -> int:
        return self.keys

    def reset(self) -> None:
        self.resets += 1

    def set_brightness(self, brightness: int) -> None:
        if self.brightness_error is not None:
            raise self.brightness_error
        self.brightness.append(brightness)

    def key_image_format(self) -> dict[str, tuple[int, int]]:
        return {"size": (72, 72)}

    def set_key_image(self, index: int, native: object) -> None:
        self.writes.append((index, native))

    def __enter__(self) -> FakeDeck:
        self.context_entries += 1
        return self

    def __exit__(self, *_args) -> None:
        return None


class FakeManager:
    def __init__(self, decks: list[FakeDeck]) -> None:
        self.decks = decks

    def enumerate(self) -> list[FakeDeck]:
        return self.decks


class FakePILHelper:
    @staticmethod
    def create_scaled_key_image(_deck: FakeDeck, image: Image.Image) -> tuple[str, Image.Image]:
        return "scaled", image

    @staticmethod
    def to_native_key_format(
        _deck: FakeDeck,
        scaled: tuple[str, Image.Image],
    ) -> tuple[str, tuple[str, Image.Image]]:
        return "native", scaled


def output_for(config: StreamDeckConfig, decks: list[FakeDeck]) -> StreamDeckOutput:
    return StreamDeckOutput(
        config,
        manager_factory=lambda: FakeManager(decks),
        pil_helper=FakePILHelper,
    )


def test_default_setup_updates_only_requested_keys_without_resets() -> None:
    deck = FakeDeck("first")
    config = StreamDeckConfig(session_key_index=2, weekly_key_index=4, brightness=55)

    with output_for(config, [deck]) as output:
        output.set_key(2, Image.new("RGB", (72, 72)))
        output.set_key(4, Image.new("RGB", (72, 72)))

    assert deck.opened == 1
    assert deck.closed == 1
    assert deck.resets == 0
    assert deck.brightness == [55]
    assert [index for index, _image in deck.writes] == [2, 4]
    assert deck.context_entries == 2


def test_serial_selection_closes_non_matching_deck() -> None:
    first = FakeDeck("wrong")
    selected = FakeDeck("target")
    config = StreamDeckConfig(device_serial="target")

    with output_for(config, [first, selected]):
        pass

    assert first.opened == 1
    assert first.closed == 1
    assert selected.opened == 1
    assert selected.closed == 1


def test_undersized_deck_is_closed_and_rejected() -> None:
    deck = FakeDeck("small", keys=1)

    with (
        pytest.raises(DeviceError, match="at least 2 required"),
        output_for(StreamDeckConfig(), [deck]),
    ):
        pass

    assert deck.closed == 1


def test_setup_failure_closes_open_deck_with_friendly_error() -> None:
    deck = FakeDeck("broken", brightness_error=OSError("device removed"))

    with (
        pytest.raises(DeviceError, match="Failed to initialize Stream Deck"),
        output_for(StreamDeckConfig(), [deck]),
    ):
        pass

    assert deck.closed == 1


def test_reset_on_exit_is_opt_in_and_still_closes() -> None:
    deck = FakeDeck("reset")
    config = replace(StreamDeckConfig(), reset_on_exit=True)

    with output_for(config, [deck]):
        pass

    assert deck.resets == 1
    assert deck.closed == 1


def test_hidapi_search_path_uses_app_root(monkeypatch, tmp_path) -> None:
    import os

    from stream_micro import device

    added: list[str] = []
    monkeypatch.setattr(device.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda value: added.append(value) or object())
    (tmp_path / "hidapi.dll").touch()
    device._HIDAPI_DIRECTORY = None
    try:
        device._prepare_hidapi_search_path()
    finally:
        device._HIDAPI_DIRECTORY = None
    assert added == [str(tmp_path)]


def test_hidapi_search_path_skipped_without_dll(monkeypatch, tmp_path) -> None:
    import os

    from stream_micro import device

    added: list[str] = []
    monkeypatch.setattr(device.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda value: added.append(value) or object())
    device._HIDAPI_DIRECTORY = None
    try:
        device._prepare_hidapi_search_path()
    finally:
        device._HIDAPI_DIRECTORY = None
    assert added == []
