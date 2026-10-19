"""Display-only adapter for the existing Ollama quota scraper."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ..config import OllamaConfig
from ..models import QuotaSnapshot
from ..scraper import ScrapeError, fetch_quota
from .base import PluginHealth, PluginManifest, PluginSnapshot

DEFAULT_SETTINGS_URL = "https://ollama.com/settings"
DEFAULT_COOKIE_NAME = "__Secure-session"
DEFAULT_REQUEST_TIMEOUT = 15.0
DEFAULT_REFRESH_INTERVAL = 10.0

_ALLOWED_KEYS = frozenset(
    {
        "cookie_value",
        "settings_url",
        "cookie_name",
        "request_timeout_seconds",
        "refresh_interval_seconds",
    }
)
_IGNORED_LEGACY_KEYS = frozenset({"enabled", "legacy_config_path"})

# How long a previously successful snapshot survives failed refreshes
# before the plugin reports the scrape error instead of stale data.
_STALE_GRACE_SECONDS = 300.0

_Fetcher = Callable[[OllamaConfig], QuotaSnapshot]


class OllamaQuotaPlugin:
    manifest = PluginManifest(
        id="ollama_quota",
        name="Ollama quota",
        components=("quota",),
        actions=(),
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        fetch_quota: _Fetcher = fetch_quota,
    ) -> None:
        self.validate_config(config)
        self.config = config
        self._clock = clock
        self._fetch = fetch_quota
        self._lock = threading.Lock()
        self._cached: PluginSnapshot | None = None
        self._cached_at: float | None = None

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("ollama_quota config must be a mapping")
        unknown = sorted(set(config) - _ALLOWED_KEYS - _IGNORED_LEGACY_KEYS)
        if unknown:
            raise ValueError(f"Unknown ollama_quota setting(s): {', '.join(unknown)}")
        if not isinstance(config.get("cookie_value", ""), str):
            raise ValueError("ollama_quota.cookie_value must be a string")
        url = config.get("settings_url", DEFAULT_SETTINGS_URL)
        if not isinstance(url, str) or not url:
            raise ValueError("ollama_quota.settings_url must be a non-empty string")
        name = config.get("cookie_name", DEFAULT_COOKIE_NAME)
        if not isinstance(name, str) or not name:
            raise ValueError("ollama_quota.cookie_name must be a non-empty string")
        timeout = config.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("ollama_quota.request_timeout_seconds must be a positive number")
        interval = config.get("refresh_interval_seconds", DEFAULT_REFRESH_INTERVAL)
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            raise ValueError("ollama_quota.refresh_interval_seconds must be a positive number")

    def _build_ollama_config(self) -> OllamaConfig:
        return OllamaConfig(
            settings_url=self.config.get("settings_url", DEFAULT_SETTINGS_URL),
            cookie_name=self.config.get("cookie_name", DEFAULT_COOKIE_NAME),
            cookie_value=self.config.get("cookie_value", ""),
            request_timeout_seconds=self.config.get(
                "request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT
            ),
            refresh_interval_seconds=self.config.get(
                "refresh_interval_seconds", DEFAULT_REFRESH_INTERVAL
            ),
        )

    def _refresh_interval(self) -> float:
        interval = self.config.get("refresh_interval_seconds", DEFAULT_REFRESH_INTERVAL)
        return float(interval) if isinstance(interval, (int, float)) else DEFAULT_REFRESH_INTERVAL

    def _no_cookie_snapshot(self) -> PluginSnapshot:
        return PluginSnapshot(PluginHealth(False, "No cookie value configured"), signals={})

    def _snapshot_from_quota(self, quota: QuotaSnapshot) -> PluginSnapshot:
        if quota.session is None or quota.weekly is None:
            monthly = quota.monthly
            assert monthly is not None
            return PluginSnapshot(
                PluginHealth(True, "Connected"),
                signals={
                    "primary_percent": monthly.percent_used,
                    "primary_label": "MONTH",
                    "primary_reset_at": monthly.reset_at.isoformat() if monthly.reset_at else None,
                },
            )
        return PluginSnapshot(
            PluginHealth(True, "Connected"),
            signals={
                "session_percent": quota.session.percent_used,
                "weekly_percent": quota.weekly.percent_used,
                "session_reset_at": quota.session.reset_at.isoformat()
                if quota.session.reset_at
                else None,
                "weekly_reset_at": quota.weekly.reset_at.isoformat()
                if quota.weekly.reset_at
                else None,
            },
        )

    def _error_snapshot(self, detail: str) -> PluginSnapshot:
        return PluginSnapshot(PluginHealth(False, detail), signals={})

    def snapshot(self) -> PluginSnapshot:
        """Return the cached quota snapshot, refreshing at most once per interval.

        Callers poll this from the Deck loop, the Dashboard, and /api/status;
        each call returns instantly from cache instead of scraping ollama.com
        synchronously.  A failed refresh keeps the last known data marked
        ``Connected (stale)`` so a transient network hiccup never blanks the
        Deck key.
        """

        if not self.config.get("cookie_value", "").strip():
            return self._no_cookie_snapshot()
        interval = self._refresh_interval()
        now = self._clock()
        with self._lock:
            if (
                self._cached is not None
                and self._cached_at is not None
                and now - self._cached_at < interval
            ):
                return self._cached
        # Fetch outside the lock so a slow ollama.com response never blocks
        # concurrent snapshot() callers; they may duplicate one in-flight
        # fetch, which is harmless.  The fetch takes at most
        # request_timeout_seconds (default 15 s), shorter than typical
        # FastAPI request timeouts, so a hung upstream surfaces as a health
        # error rather than a dead Bridge.
        try:
            quota = self._fetch(self._build_ollama_config())
            snapshot = self._snapshot_from_quota(quota)
        except ScrapeError as exc:
            with self._lock:
                if (
                    self._cached is not None
                    and self._cached.health.connected
                    and self._cached_at is not None
                    and now - self._cached_at < _STALE_GRACE_SECONDS + interval
                ):
                    stale = PluginSnapshot(
                        PluginHealth(True, "Connected (stale)"),
                        signals=dict(self._cached.signals or {}),
                    )
                    self._cached = stale
                    self._cached_at = now
                    return stale
                snapshot = self._error_snapshot(str(exc))
        with self._lock:
            self._cached = snapshot
            self._cached_at = now
        return snapshot

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        raise ValueError("Ollama quota is display-only")
