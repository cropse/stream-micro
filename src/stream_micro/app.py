from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .config import AppConfig
from .device import DeviceError, StreamDeckOutput
from .renderer import render_blank_key, render_error_key, render_quota_key
from .scraper import ScrapeError, fetch_quota


def update_once(config: AppConfig, output: StreamDeckOutput) -> None:
    snapshot = fetch_quota(config.ollama)
    size = output.key_size
    if snapshot.session is None or snapshot.weekly is None:
        # Monthly-credit plans have a single window: show it on the first key.
        monthly = snapshot.monthly
        assert monthly is not None
        output.set_key(
            config.stream_deck.session_key_index,
            render_quota_key(monthly, "MONTH", config.display, size),
        )
        output.set_key(config.stream_deck.weekly_key_index, render_blank_key(config.display, size))
        return
    output.set_key(
        config.stream_deck.session_key_index,
        render_quota_key(snapshot.session, "5H", config.display, size),
    )
    output.set_key(
        config.stream_deck.weekly_key_index,
        render_quota_key(snapshot.weekly, "WEEK", config.display, size),
    )


def run(
    config: AppConfig,
    *,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    logger = logging.getLogger(__name__)
    while True:
        try:
            with StreamDeckOutput(config.stream_deck) as output:
                while True:
                    try:
                        update_once(config, output)
                    except ScrapeError as exc:
                        logger.error("Quota refresh failed: %s", exc)
                        error_image = render_error_key(str(exc), config.display, output.key_size)
                        output.set_key(config.stream_deck.session_key_index, error_image)
                        output.set_key(config.stream_deck.weekly_key_index, error_image)
                    if once:
                        return
                    sleep(config.ollama.refresh_interval_seconds)
        except DeviceError as exc:
            if once:
                raise
            logger.error("Stream Deck unavailable: %s; reconnecting", exc)
            sleep(config.ollama.refresh_interval_seconds)
