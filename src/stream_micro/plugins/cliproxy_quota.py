"""Per-account quota read through a CLIProxyAPI management endpoint.

CLIProxyAPI keeps the OAuth credentials for every pooled account.  Its
management ``/api-call`` endpoint substitutes ``$TOKEN$`` server-side, so the
Bridge reads each account's official usage endpoint without ever holding the
provider tokens itself.  Only the management key is stored locally, and it is
never returned to the dashboard.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..timeparse import epoch_to_iso
from .base import PluginHealth, PluginManifest, PluginSnapshot, QuotaAccount
from .quota_cache import QuotaSignals, finite_percent, load_cache, save_cache
from .quota_freshness import age_quota_signals

LOGGER = logging.getLogger(__name__)

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
SUPPORTED_PROVIDERS = ("codex", "claude")
# After a failed refresh (e.g. the network is not up yet at boot) retry sooner.
RETRY_SECONDS = 60.0
_ALLOWED_KEYS = frozenset(
    {
        "enabled",
        "base_url",
        "management_key",
        "management_key_env",
        "providers",
        "refresh_interval_seconds",
        "request_timeout_seconds",
        "cache_path",
        "allow_insecure_http",
    }
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class CliProxyQuotaError(RuntimeError):
    """Raised when the CLIProxyAPI quota integration is misconfigured."""


def _validate_base_url(value: object, allow_insecure_http: object = False) -> None:
    if not isinstance(value, str):
        raise CliProxyQuotaError("cliproxy_quota.base_url must be an http(s) URL")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CliProxyQuotaError("cliproxy_quota.base_url must be an http(s) URL")
    # Credentials in the URL would bypass the management-key masking.
    if parts.username or parts.password or parts.query or parts.fragment:
        raise CliProxyQuotaError(
            "cliproxy_quota.base_url must not contain credentials, a query, or a fragment"
        )
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise CliProxyQuotaError("cliproxy_quota.base_url has an invalid port") from None
    # The management key travels in a header: over plain HTTP anyone on the
    # network path can read it, so remote HTTP needs an explicit opt-in.
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS and not allow_insecure_http:
        raise CliProxyQuotaError(
            "cliproxy_quota.base_url sends the management key unencrypted over plain HTTP; "
            "use https:// or set allow_insecure_http: true"
        )


def _has_unsafe_characters(key: str) -> bool:
    """A key unusable as a header value: padded, or holding control characters."""

    return key != key.strip() or any(ord(char) < 32 or ord(char) == 127 for char in key)


def validate_cliproxy_quota_config(config: Mapping[str, object]) -> None:
    unknown = sorted(set(config) - _ALLOWED_KEYS)
    if unknown:
        raise CliProxyQuotaError(f"Unknown cliproxy_quota setting(s): {', '.join(unknown)}")
    for name in ("enabled", "allow_insecure_http"):
        if name in config and not isinstance(config[name], bool):
            raise CliProxyQuotaError(f"cliproxy_quota.{name} must be a boolean")
    if config.get("enabled") or config.get("base_url"):
        _validate_base_url(config.get("base_url"), config.get("allow_insecure_http", False))
    for name in ("management_key", "management_key_env", "cache_path"):
        value = config.get(name)
        if value is not None and not isinstance(value, str):
            raise CliProxyQuotaError(f"cliproxy_quota.{name} must be a string or null")
    key = config.get("management_key")
    if isinstance(key, str) and _has_unsafe_characters(key):
        raise CliProxyQuotaError(
            "cliproxy_quota.management_key must not have spaces around it or control characters"
        )
    providers = config.get("providers", list(SUPPORTED_PROVIDERS))
    if (
        not isinstance(providers, list)
        or not providers
        or not all(isinstance(item, str) and item in SUPPORTED_PROVIDERS for item in providers)
    ):
        raise CliProxyQuotaError(
            f"cliproxy_quota.providers must list some of: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    for name, minimum, maximum in (
        ("refresh_interval_seconds", 30.0, math.inf),
        ("request_timeout_seconds", 1.0, 120.0),
    ):
        value = config.get(name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            limit = f"at least {minimum:g}"
            if math.isfinite(maximum):
                limit = f"between {minimum:g} and {maximum:g}"
            raise CliProxyQuotaError(f"cliproxy_quota.{name} must be a number {limit}")


def _window_label(seconds: object) -> str | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    hours = seconds / 3600
    return f"{hours:g}H" if hours < 24 else f"{hours / 24:g}D"


def codex_usage_signals(body: object, *, now: float | None = None) -> QuotaSignals:
    """Translate a ChatGPT ``wham/usage`` body into Deck quota signals."""

    if not isinstance(body, Mapping):
        return {}
    rate_limit = body.get("rate_limit")
    if not isinstance(rate_limit, Mapping):
        return {}
    signals: QuotaSignals = {}
    for prefix, key in (("primary", "primary_window"), ("secondary", "secondary_window")):
        window = rate_limit.get(key)
        used = finite_percent(window.get("used_percent")) if isinstance(window, Mapping) else None
        if not isinstance(window, Mapping) or used is None:
            continue
        signals[f"{prefix}_percent"] = used
        signals[f"{prefix}_label"] = _window_label(window.get("limit_window_seconds")) or (
            "5H" if prefix == "primary" else "WEEK"
        )
        reset = epoch_to_iso(window.get("reset_at"))
        after = window.get("reset_after_seconds")
        if reset is None and isinstance(after, (int, float)) and not isinstance(after, bool):
            reset = epoch_to_iso((now if now is not None else time.time()) + after)
        signals[f"{prefix}_reset_at"] = reset
    plan = body.get("plan_type")
    if signals and isinstance(plan, str) and plan:
        signals["plan"] = plan
    return signals


def claude_usage_signals(body: object) -> QuotaSignals:
    """Translate an Anthropic ``oauth/usage`` body into Deck quota signals."""

    if not isinstance(body, Mapping):
        return {}
    signals: QuotaSignals = {}
    for prefix, key, label in (("primary", "five_hour", "5H"), ("secondary", "seven_day", "7D")):
        window = body.get(key)
        used = finite_percent(window.get("utilization")) if isinstance(window, Mapping) else None
        if not isinstance(window, Mapping) or used is None:
            continue
        signals[f"{prefix}_percent"] = used
        signals[f"{prefix}_label"] = label
        reset = window.get("resets_at")
        signals[f"{prefix}_reset_at"] = reset if isinstance(reset, str) and reset else None
    return signals


def codex_account_id(entry: Mapping[str, object]) -> str | None:
    """Find the ChatGPT account ID the management UI sends with usage reads."""

    for container in (entry, entry.get("metadata"), entry.get("attributes")):
        if isinstance(container, Mapping):
            for key in ("chatgpt_account_id", "chatgptAccountId"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    # CLIProxyAPI lists id_token as its decoded claims; a raw token is never decoded here.
    claims = entry.get("id_token")
    if isinstance(claims, Mapping):
        for container in (claims, claims.get("https://api.openai.com/auth")):
            if isinstance(container, Mapping):
                value = container.get("chatgpt_account_id")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _decode_body(response: Mapping[str, object]) -> object:
    body = response.get("body")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return body


def _account_title(provider: str, entry: Mapping[str, object]) -> str:
    email = entry.get("email")
    who = email.split("@", 1)[0] if isinstance(email, str) and email else entry.get("name")
    prefix = "CODEX" if provider == "codex" else "CLAUDE"
    return f"{prefix} {str(who or '')[:10]}".strip().upper()


class CliProxyQuotaPlugin:
    """Expose one quota record per pooled CLIProxyAPI account.

    Accounts are identified by their CLIProxyAPI auth file name; a quota key
    picks one with ``action_params.account`` (that file name).
    """

    manifest = PluginManifest(
        id="cliproxy_quota", name="CLIProxyAPI quota", components=("quota",), actions=()
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        start_thread: bool = True,
    ) -> None:
        self.validate_config(config)
        self.config = dict(config)
        self._base_url = str(config["base_url"]).rstrip("/")
        self._providers = tuple(config.get("providers", SUPPORTED_PROVIDERS))
        self._interval = float(config.get("refresh_interval_seconds", 300))
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=float(config.get("request_timeout_seconds", 20)), follow_redirects=False
        )
        self._clock = clock
        cache_path = config.get("cache_path")
        self.cache_path = Path(cache_path).expanduser() if isinstance(cache_path, str) else None
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # Last good numbers per account and when they were read; shown as stale
        # while the proxy or that account cannot be read.
        self._accounts: dict[str, QuotaAccount] = {}
        self._seen_at: dict[str, float] = {}
        self._fresh: set[str] = set()
        self._load_cache()
        self._detail = "Waiting for first quota refresh"
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._loop, name="cliproxy-quota-refresh", daemon=True
            )
            self._thread.start()

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise CliProxyQuotaError("cliproxy_quota config must be a mapping")
        validate_cliproxy_quota_config(config)
        _validate_base_url(config.get("base_url"), config.get("allow_insecure_http", False))

    def _load_cache(self) -> None:
        cached = load_cache(self.cache_path, self._base_url)
        if cached is None:
            return
        payload, _saved_at = cached
        entries = payload.get("accounts")
        for entry in entries if isinstance(entries, list) else []:
            # The cache is a local file anyone could edit: accept only clean data.
            if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
                continue
            signals, seen = entry.get("signals"), entry.get("seen_at")
            if not isinstance(signals, dict) or isinstance(seen, bool):
                continue
            if not isinstance(seen, (int, float)) or not math.isfinite(seen):
                continue
            if not all(
                isinstance(key, str)
                and (
                    value is None
                    or isinstance(value, (str, bool))
                    or (isinstance(value, (int, float)) and math.isfinite(value))
                )
                for key, value in signals.items()
            ):
                continue
            account = QuotaAccount(
                entry["id"],
                str(entry.get("title") or entry["id"]),
                str(entry.get("provider") or ""),
                signals,
            )
            self._accounts[account.id] = account
            self._seen_at[account.id] = float(seen)

    def _save_cache(self) -> None:
        # One writer at a time; a stopped worker no longer writes.
        with self._cache_lock:
            if self._stop.is_set():
                return
            with self._lock:
                entries = [
                    {
                        "id": account.id,
                        "title": account.title,
                        "provider": account.provider,
                        "signals": account.signals,
                        "seen_at": self._seen_at[account.id],
                    }
                    for account in self._accounts.values()
                    if account.signals and account.id in self._seen_at
                ]
            # Also an empty pool: accounts removed from the proxy must not come back.
            save_cache(self.cache_path, self._base_url, {"accounts": entries}, self._clock())

    def _fail(self, detail: str) -> None:
        """Any failed refresh: numbers on hand stay, but no longer count as current."""

        with self._lock:
            self._connected = False
            self._fresh = set()
            self._detail = detail

    def _management_key(self) -> str | None:
        env_name = self.config.get("management_key_env")
        key = self.config.get("management_key")
        if isinstance(env_name, str) and env_name and os.environ.get(env_name):
            key = os.environ[env_name]
        if not isinstance(key, str) or not key:
            return None
        if _has_unsafe_characters(key):
            raise CliProxyQuotaError("Management key contains invalid characters")
        return key

    def _api_call(self, key: str, auth_index: str, url: str, header: dict[str, str]) -> object:
        response = self._client.post(
            f"{self._base_url}/v0/management/api-call",
            headers={"Authorization": f"Bearer {key}"},
            json={"auth_index": auth_index, "method": "GET", "url": url, "header": header},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise CliProxyQuotaError("api-call returned an unexpected payload")
        status = data.get("status_code")
        if isinstance(status, bool) or not isinstance(status, int):
            raise CliProxyQuotaError("api-call returned an unexpected payload")
        if not 200 <= status < 300:
            raise CliProxyQuotaError(f"HTTP {status}")
        return _decode_body(data)

    def _read_account(
        self, key: str, provider: str, entry: Mapping[str, object]
    ) -> tuple[QuotaSignals, str | None]:
        auth_index = entry.get("auth_index")
        if not isinstance(auth_index, str) or not auth_index:
            return {}, "missing auth_index"
        try:
            if provider == "codex":
                header = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json"}
                account_id = codex_account_id(entry)
                if account_id:
                    header["Chatgpt-Account-Id"] = account_id
                body = self._api_call(key, auth_index, CODEX_USAGE_URL, header)
                signals = codex_usage_signals(body, now=self._clock())
            else:
                header = {
                    "Authorization": "Bearer $TOKEN$",
                    "Content-Type": "application/json",
                    "anthropic-beta": "oauth-2025-04-20",
                }
                signals = claude_usage_signals(
                    self._api_call(key, auth_index, CLAUDE_USAGE_URL, header)
                )
        except httpx.HTTPStatusError as exc:
            return {}, f"HTTP {exc.response.status_code}"
        except CliProxyQuotaError as exc:
            return {}, str(exc)  # fixed text from this module
        except (httpx.HTTPError, ValueError) as exc:
            # Never the message: transport errors can quote the request headers.
            return {}, type(exc).__name__
        return signals, None if signals else "no quota windows"

    def refresh(self) -> bool:
        """Read every pooled account once; returns whether the proxy answered."""

        if self._stop.is_set():
            return False
        try:
            key = self._management_key()
        except CliProxyQuotaError as exc:
            self._fail(str(exc))
            return False
        if key is None:
            self._fail("Management key is not configured")
            return False
        try:
            response = self._client.get(
                f"{self._base_url}/v0/management/auth-files",
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = "management key rejected" if status in (401, 403) else f"HTTP {status}"
            self._fail(f"Proxy answered: {reason}")
            return False
        except (httpx.HTTPError, ValueError) as exc:
            # Never the message: transport errors can quote the request headers.
            self._fail(f"Proxy unreachable: {type(exc).__name__}")
            return False
        files = body.get("files") if isinstance(body, Mapping) else None
        if not isinstance(files, list):
            self._fail("Proxy answered: unexpected reply")
            return False

        accounts: dict[str, QuotaAccount] = {}
        seen: dict[str, float] = {}
        for entry in files:
            if not isinstance(entry, Mapping) or entry.get("disabled") is True:
                continue
            provider = str(entry.get("provider") or entry.get("type") or "").lower()
            name = entry.get("name")
            if provider not in self._providers or not isinstance(name, str) or not name:
                continue
            signals, error = self._read_account(key, provider, entry)
            if self._stop.is_set():  # closed while the request was out: publish nothing
                return False
            previous = self._accounts.get(name)
            if error is None:
                seen[name] = self._clock()
            accounts[name] = QuotaAccount(
                name,
                _account_title(provider, entry),
                provider,
                signals if error is None else previous.signals if previous else None,
                error,
            )
        failed = len(accounts) - len(seen)
        with self._lock:
            self._seen_at.update(seen)
            self._accounts = accounts
            self._fresh = set(seen)
            self._connected = True
            self._detail = f"{len(accounts)} pooled account(s)" + (
                f": {len(seen)} current, {failed} failed" if failed else ""
            )
        self._save_cache()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ok = self.refresh()
            except Exception:  # Keep polling; report instead of dying silently.
                LOGGER.exception("CLIProxyAPI quota refresh failed")
                self._fail("Quota refresh failed; see bridge log")
                ok = False
            self._stop.wait(self._interval if ok else min(self._interval, RETRY_SECONDS))
        if self._owns_client:
            self._client.close()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                return  # still in a request; the worker closes its client when done
        if self._owns_client:
            self._client.close()

    def snapshot(self) -> PluginSnapshot:
        now = self._clock()
        with self._lock:
            accounts = list(self._accounts.values())
            seen_at = dict(self._seen_at)
            fresh = set(self._fresh)
            connected, detail = self._connected, self._detail
        shown = []
        for account in accounts:
            signals = account.signals
            if signals:
                signals = age_quota_signals(
                    signals, seen_at.get(account.id, now), now, stale=account.id not in fresh
                )
            shown.append(
                QuotaAccount(
                    account.id,
                    account.title,
                    account.provider,
                    signals,
                    account.error,
                )
            )
        if not connected and shown:
            detail = f"{detail}; showing last seen quota"
        return PluginSnapshot(
            PluginHealth(connected, detail, self._base_url), accounts=tuple(shown)
        )

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        raise CliProxyQuotaError(f"Unsupported cliproxy_quota action: {action}")
