"""Physical MK.2 refresh and press-dispatch loop."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Event

from .bridge import BridgeError, BridgeService
from .control_config import ButtonBinding, reset_thresholds_for
from .control_renderer import render_control_key, render_quota_key
from .device import DeviceError, Mk2DeckOutput
from .plugins.base import AgentSession
from .plugins.opencode import OpenCodeError


class Mk2BridgeRunner:
    LONG_PRESS_SECONDS = 2.0

    def __init__(
        self, service: BridgeService, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.service = service
        self.sessions: tuple[AgentSession, ...] = ()
        self.sessions_by_plugin: dict[str, tuple[AgentSession, ...]] = {}
        self._refresh_requested = Event()
        self._stop_requested = Event()
        self._pressed_at: dict[int, float] = {}
        self._clock = clock
        self._logger = logging.getLogger(__name__)

    def refresh(self, output: Mk2DeckOutput) -> None:
        # Push the configured brightness on every refresh.  __enter__ only
        # sets it once at connect time, so without this the dashboard slider
        # has no effect until the Deck reconnects.
        configured = self.service.config.stream_deck.brightness
        self._logger.debug("refresh: pushing brightness=%d to device", configured)
        output.set_brightness(configured)
        # Re-discover OpenChamber Desktop if it was not running when the Bridge
        # started.  This uses exponential backoff internally so it is cheap
        # when OpenChamber is already connected (a no-op) and throttled when
        # it is still missing.
        self.service.rediscover_openchamber(now=self._clock())
        snapshots = self.service.snapshots()
        self.sessions_by_plugin = {
            plugin_id: snapshot.sessions for plugin_id, snapshot in snapshots.items()
        }
        self.sessions = self.sessions_by_plugin.get("opencode", ())
        self.service.set_active_snapshots(snapshots)
        # Build span zone lookup: key_index -> parent ButtonBinding
        span_parents: dict[int, ButtonBinding] = {}
        for b in self.service.config.buttons:
            if b.plugin and b.component == "session":
                for i in range(b.key_index, b.key_index + max(b.span, 1)):
                    span_parents[i] = b
        for binding in self.service.config.buttons:
            session = None
            parent = span_parents.get(binding.key_index)
            if parent is not None:
                session = self.service.session_for_key(binding.key_index)
            if binding.component == "quota":
                image = render_quota_key(
                    binding,
                    snapshots.get(binding.plugin or ""),
                    size=output.key_size,
                    display=self.service.config.stream_deck.quota_display,
                    thresholds=reset_thresholds_for(self.service.config.stream_deck, binding),
                )
            else:
                image = render_control_key(
                    binding,
                    session,
                    selected=(
                        session is not None
                        and parent is not None
                        and session.id == self.service.selected_session_ids.get(parent.plugin or "")
                    ),
                    pinned=self.service.is_key_pinned(binding.key_index),
                    size=output.key_size,
                    provider_id=parent.plugin if parent is not None else binding.plugin,
                    # A zone's keys share its accent.
                    accent_color=(parent or binding).accent,
                    state_colors=self.service.config.stream_deck.state_colors,
                    accent_line=self.service.config.stream_deck.session_accent_line,
                )
            output.set_key(binding.key_index, image)

    def press(self, key_index: int) -> None:
        self.service.press_key(key_index)

    def stop(self) -> None:
        self._stop_requested.set()
        self._refresh_requested.set()

    def run(self, *, once: bool = False, sleep: Callable[[float], None] = time.sleep) -> None:
        logger = logging.getLogger(__name__)
        while not self._stop_requested.is_set():
            try:
                with Mk2DeckOutput(self.service.config.stream_deck) as output:
                    self.service.device_status = {
                        "connected": True,
                        "detail": "MK.2 connected",
                        "key_count": 15,
                    }
                    output.set_key_callback(lambda _deck, key, pressed: self._on_key(key, pressed))
                    while not self._stop_requested.is_set():
                        self.refresh(output)
                        if once:
                            return
                        self._refresh_requested.wait(
                            self.service.config.bridge.refresh_interval_seconds
                        )
                        self._refresh_requested.clear()
            except DeviceError as exc:
                self.service.device_status = {"connected": False, "detail": str(exc)}
                if once:
                    raise
                logger.error("Stream Deck unavailable: %s", exc)
                interval = self.service.config.bridge.refresh_interval_seconds
                if sleep is time.sleep:
                    self._stop_requested.wait(interval)
                else:
                    sleep(interval)

    def _on_key(self, key_index: int, pressed: bool) -> None:
        if pressed:
            self._pressed_at.setdefault(key_index, self._clock())
            return
        started_at = self._pressed_at.pop(key_index, None)
        if started_at is None:
            return
        try:
            if self._clock() - started_at >= self.LONG_PRESS_SECONDS:
                self.service.toggle_session_pin(key_index)
            else:
                self.press(key_index)
        except (BridgeError, OpenCodeError) as exc:
            logging.getLogger(__name__).error("Key %s action failed: %s", key_index, exc)
        except Exception:
            logging.getLogger(__name__).exception("Key %s callback failed", key_index)
        finally:
            # A physical press changes the selected Session. Do not make the
            # corresponding Deck display wait for the polling interval.
            self._refresh_requested.set()
