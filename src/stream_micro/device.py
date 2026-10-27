from __future__ import annotations

import logging
import os
from collections.abc import Callable
from types import TracebackType
from typing import Any

from PIL import Image

from . import paths
from .config import StreamDeckConfig
from .control_config import MK2_KEY_COUNT, Mk2Settings

_HIDAPI_DIRECTORY: Any | None = None


class DeviceError(RuntimeError):
    """Raised when no suitable Stream Deck can be used."""


def _prepare_hidapi_search_path() -> None:
    global _HIDAPI_DIRECTORY
    bundled_hidapi = paths.app_root() / "hidapi.dll"
    if bundled_hidapi.is_file() and _HIDAPI_DIRECTORY is None and hasattr(os, "add_dll_directory"):
        _HIDAPI_DIRECTORY = os.add_dll_directory(str(bundled_hidapi.parent))


def discover_mk2_devices() -> list[dict[str, str | int | None]]:
    """List usable 15-key devices without selecting or changing one."""

    _prepare_hidapi_search_path()
    try:
        from StreamDeck.DeviceManager import DeviceManager
    except (ImportError, OSError) as exc:
        raise DeviceError(f"Could not load Stream Deck HID support: {exc}") from exc
    try:
        decks = DeviceManager().enumerate()
    except Exception as exc:
        raise DeviceError(f"Could not enumerate Stream Deck devices: {exc}") from exc
    devices: list[dict[str, str | int | None]] = []
    for deck in decks:
        try:
            if not deck.is_visual() or deck.key_count() != MK2_KEY_COUNT:
                continue
            devices.append(
                {
                    "model": "mk2",
                    "key_count": deck.key_count(),
                    "serial": deck.get_serial_number(),
                }
            )
        except Exception:
            continue
    return devices


class StreamDeckOutput:
    def __init__(
        self,
        config: StreamDeckConfig,
        *,
        manager_factory: Callable[[], Any] | None = None,
        pil_helper: Any | None = None,
    ) -> None:
        self.config = config
        self.deck: Any | None = None
        self._manager_factory = manager_factory
        self._pil_helper = pil_helper

    def _load_dependencies(self) -> tuple[Callable[[], Any], Any]:
        if self._manager_factory is not None and self._pil_helper is not None:
            return self._manager_factory, self._pil_helper
        _prepare_hidapi_search_path()
        try:
            from StreamDeck.DeviceManager import DeviceManager
            from StreamDeck.ImageHelpers import PILHelper
        except (ImportError, OSError) as exc:
            raise DeviceError(
                "Could not load Stream Deck HID support; install the matching hidapi.dll "
                f"and StreamDeck package: {exc}"
            ) from exc
        return self._manager_factory or DeviceManager, self._pil_helper or PILHelper

    @staticmethod
    def _close_after_failure(deck: Any, reason: BaseException) -> None:
        try:
            deck.close()
        except Exception as close_error:
            raise DeviceError(
                f"Stream Deck setup failed ({reason}) and the device could not be closed: "
                f"{close_error}"
            ) from close_error

    def __enter__(self) -> StreamDeckOutput:
        logger = logging.getLogger(__name__)
        manager_factory, pil_helper = self._load_dependencies()
        try:
            decks = [deck for deck in manager_factory().enumerate() if deck.is_visual()]
        except Exception as exc:
            raise DeviceError(
                "Could not enumerate Stream Deck devices; quit the official app and "
                f"check HID access: {exc}"
            ) from exc

        selected: Any | None = None
        try:
            deck: Any = self._select_deck(decks)
            selected = deck
            required_keys = (
                max(
                    self.config.session_key_index,
                    self.config.weekly_key_index,
                )
                + 1
            )
            key_count = deck.key_count()
            if key_count < required_keys:
                raise DeviceError(
                    f"Selected Stream Deck has {key_count} keys; at least {required_keys} required"
                )
            if self.config.reset_on_start:
                deck.reset()
            logger.debug("__enter__: setting initial brightness=%d", self.config.brightness)
            deck.set_brightness(self.config.brightness)
        except BaseException as exc:
            if selected is not None:
                self._close_after_failure(selected, exc)
            if isinstance(exc, DeviceError):
                raise
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise DeviceError(f"Failed to initialize Stream Deck: {exc}") from exc

        self.deck = selected
        self._pil_helper = pil_helper
        return self

    def _open(self, deck: Any) -> None:
        try:
            deck.open()
        except Exception as exc:
            raise DeviceError(
                f"Could not open Stream Deck; quit the official app and check HID access: {exc}"
            ) from exc

    def _select_deck(self, decks: list[Any]) -> Any:
        if not decks:
            raise DeviceError(
                "No visual Stream Deck found; quit the official app and check HID access"
            )
        if not self.config.device_serial:
            selected = decks[0]
            self._open(selected)
            return selected

        open_errors: list[str] = []
        for candidate in decks:
            try:
                self._open(candidate)
            except DeviceError as exc:
                open_errors.append(str(exc))
                continue
            try:
                if candidate.get_serial_number() == self.config.device_serial:
                    return candidate
            except Exception as exc:
                self._close_after_failure(candidate, exc)
                raise DeviceError(f"Could not read Stream Deck serial number: {exc}") from exc
            candidate.close()

        details = f" ({'; '.join(open_errors)})" if open_errors else ""
        raise DeviceError(
            f"No visual Stream Deck found with serial {self.config.device_serial}{details}"
        )

    @property
    def key_size(self) -> tuple[int, int]:
        if self.deck is None:
            raise DeviceError("Stream Deck is not open")
        try:
            return tuple(self.deck.key_image_format()["size"])
        except Exception as exc:
            raise DeviceError(f"Could not read Stream Deck key size: {exc}") from exc

    def set_key(self, index: int, image: Image.Image) -> None:
        if self.deck is None or self._pil_helper is None:
            raise DeviceError("Stream Deck is not open")
        try:
            scaled = self._pil_helper.create_scaled_key_image(self.deck, image)
            native = self._pil_helper.to_native_key_format(self.deck, scaled)
            with self.deck:
                self.deck.set_key_image(index, native)
        except Exception as exc:
            raise DeviceError(f"Failed to update Stream Deck key {index}: {exc}") from exc

    def set_brightness(self, brightness: int) -> None:
        """Update the physical Deck brightness without reopening the device."""
        logger = logging.getLogger(__name__)
        if self.deck is None:
            raise DeviceError("Stream Deck is not open")
        try:
            logger.debug("set_brightness: calling deck.set_brightness(%d)", brightness)
            self.deck.set_brightness(brightness)
            logger.debug("set_brightness: deck.set_brightness(%d) returned OK", brightness)
        except Exception as exc:
            logger.error("set_brightness: deck.set_brightness(%d) failed: %s", brightness, exc)
            raise DeviceError(f"Failed to set Stream Deck brightness: {exc}") from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.deck is None:
            return
        deck = self.deck
        self.deck = None
        reset_error: Exception | None = None
        try:
            if self.config.reset_on_exit:
                deck.reset()
        except Exception as error:
            reset_error = error
        try:
            deck.close()
        except Exception as close_error:
            raise DeviceError(f"Failed to close Stream Deck: {close_error}") from close_error
        if reset_error is not None:
            raise DeviceError(
                f"Failed to reset Stream Deck on exit: {reset_error}"
            ) from reset_error


class Mk2DeckOutput(StreamDeckOutput):
    """Stream Deck output restricted to the supported 15-key MK.2."""

    def __init__(
        self,
        settings: Mk2Settings,
        *,
        manager_factory: Callable[[], Any] | None = None,
        pil_helper: Any | None = None,
    ) -> None:
        super().__init__(
            StreamDeckConfig(
                device_serial=settings.device_serial,
                brightness=settings.brightness,
                reset_on_start=False,
                reset_on_exit=False,
            ),
            manager_factory=manager_factory,
            pil_helper=pil_helper,
        )

    def __enter__(self) -> Mk2DeckOutput:
        super().__enter__()
        if self.deck is not None and self.deck.key_count() != MK2_KEY_COUNT:
            key_count = self.deck.key_count()
            self.__exit__(None, None, None)
            raise DeviceError(
                f"Selected Stream Deck has {key_count} keys; only the 15-key MK.2 is supported"
            )
        return self

    def set_key_callback(self, callback: Callable[[Any, int, bool], None]) -> None:
        if self.deck is None:
            raise DeviceError("Stream Deck is not open")
        try:
            self.deck.set_key_callback(callback)
        except Exception as exc:
            raise DeviceError(f"Failed to register Stream Deck key callback: {exc}") from exc
