"""Quota from any service that reports usage as JSON over HTTPS.

Each configured source is one account: a URL, an optional key sent in a
header, and where the numbers sit in the JSON reply.  Presets fill those in
for known services, so most users only paste a key.  Keys are stored like the
Ollama cookie: never returned to the dashboard, never logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import parse_qsl, urlsplit

import httpx

from ..timeparse import epoch_to_iso, parse_timestamp
from .base import PluginHealth, PluginManifest, PluginSnapshot, QuotaAccount
from .quota_cache import QuotaSignals, finite_percent, load_cache, save_cache
from .quota_freshness import age_quota_signals

LOGGER = logging.getLogger(__name__)

RETRY_SECONDS = 60.0
# Sources are read side by side, so one slow service does not delay the rest.
MAX_PARALLEL_READS = 4
MAX_SOURCES = 15
_CACHE_SOURCE = "http_quota"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Query parameters that carry credentials: those belong in api_key (a header),
# where they are never shown or logged; a URL is both. Names are compared with
# case, "-", "_" and "." ignored, whole or by ending, so "X-Amz-Signature" and
# "client_secret" are refused while "pageToken" or "sort_key" are not.
_SECRET_NAMES = frozenset(
    {
        "key", "apikey", "token", "accesstoken", "authtoken", "idtoken", "apitoken",
        "refreshtoken", "secret", "clientsecret", "password", "passwd", "pwd", "pass",
        "auth", "authorization", "sig", "signature", "credential", "credentials", "jwt",
        "bearer", "session", "sessionid", "privatekey", "accesskey", "secretkey",
    }
)  # fmt: skip
_SECRET_ENDINGS = (
    "apikey", "accesskey", "secretkey", "privatekey", "secret", "password", "passwd",
    "accesstoken", "authtoken", "idtoken", "apitoken", "refreshtoken", "securitytoken",
    "sessiontoken", "signature", "credential", "credentials",
)  # fmt: skip
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_PLUGIN_KEYS = frozenset(
    {"enabled", "sources", "refresh_interval_seconds", "request_timeout_seconds", "cache_path"}
)
_SOURCE_KEYS = frozenset(
    {
        "id",
        "label",
        "preset",
        "url",
        "api_key",
        "api_key_env",
        "auth_header",
        "budget",
        "auth_scheme",
        "windows",
    }
)
_WINDOW_KEYS = frozenset(
    {
        "label",
        "used_percent",
        "remaining_percent",
        "used",
        "remaining",
        "limit",
        "fraction",
        "reset_at",
        "currency",
        "unit",
        "amount_scale",
        "auto_limit",
    }
)
_PATH_KEYS = (
    "used_percent",
    "remaining_percent",
    "used",
    "remaining",
    "limit",
    "reset_at",
    "currency",
)

# Known services: everything but the key.  A source may still override any field.
# Only documented endpoints belong here.
PRESETS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "title": "OPENROUTER",
        "url": "https://openrouter.ai/api/v1/key",
        # Spending limit of this API key; a key without a limit has no percentage.
        "windows": [{"label": "LIMIT", "limit": "data.limit", "remaining": "data.limit_remaining"}],
    },
    "openrouter_credits": {
        "title": "OPENROUTER",
        "url": "https://openrouter.ai/api/v1/credits",
        # Account credits; OpenRouter may require a management key for this.
        "windows": [{"label": "CREDIT", "limit": "data.total_credits", "used": "data.total_usage"}],
    },
    "deepseek": {
        "title": "DEEPSEEK",
        # Documented at api-docs.deepseek.com (GET /user/balance). It reports the
        # balance only: the balance after the latest top-up counts as full
        # (`auto_limit`), unless the source sets a `budget`.
        "url": "https://api.deepseek.com/user/balance",
        "windows": [
            {
                "label": "BAL",
                "remaining": "balance_infos.0.total_balance",
                "currency": "balance_infos.0.currency",
                "auto_limit": True,
            }
        ],
    },
    # Balance-only services: what is left is shown as money; the bar compares it
    # with the balance after the latest top-up. All documented, read with an
    # ordinary API key, and free to call.
    "moonshot": {
        "title": "MOONSHOT",
        # platform.moonshot.ai (USD): GET /v1/users/me/balance.
        "url": "https://api.moonshot.ai/v1/users/me/balance",
        "windows": [
            {
                "label": "BAL",
                "remaining": "data.available_balance",
                "unit": "USD",
                "auto_limit": True,
            }
        ],
    },
    "moonshot_cn": {
        "title": "MOONSHOT",
        # platform.moonshot.cn (CNY): the same call on the China site.
        "url": "https://api.moonshot.cn/v1/users/me/balance",
        "windows": [
            {
                "label": "BAL",
                "remaining": "data.available_balance",
                "unit": "CNY",
                "auto_limit": True,
            }
        ],
    },
    "siliconflow": {
        "title": "SILICONFLOW",
        # siliconflow.cn (CNY): GET /v1/user/info, data.totalBalance.
        "url": "https://api.siliconflow.cn/v1/user/info",
        "windows": [
            {"label": "BAL", "remaining": "data.totalBalance", "unit": "CNY", "auto_limit": True}
        ],
    },
    "siliconflow_intl": {
        "title": "SILICONFLOW",
        # siliconflow.com (USD): the same call on the international site.
        "url": "https://api.siliconflow.com/v1/user/info",
        "windows": [
            {"label": "BAL", "remaining": "data.totalBalance", "unit": "USD", "auto_limit": True}
        ],
    },
    "novita": {
        "title": "NOVITA",
        # GET /openapi/v1/billing/balance/detail, in 1/10000 USD.
        "url": "https://api.novita.ai/openapi/v1/billing/balance/detail",
        "windows": [
            {
                "label": "BAL",
                "remaining": "availableBalance",
                "unit": "USD",
                "amount_scale": 0.0001,
                "auto_limit": True,
            }
        ],
    },
    "ollama": {
        "title": "OLLAMA",
        # Documented in https://docs.ollama.com/openapi.yaml (GET /api/balance):
        # the plan's included credits for the current period. Reading it uses
        # no credits; it needs an ollama.com API key, not the session cookie.
        "url": "https://ollama.com/api/balance",
        "windows": [
            {
                "label": "MONTH",
                "limit": "included.allowance_usd",
                "remaining": "included.balance_usd",
                "reset_at": "included.period.until",
            }
        ],
    },
}


class HttpQuotaError(RuntimeError):
    """Raised when an HTTP quota source is misconfigured or answers oddly."""


# -- JSON paths ----------------------------------------------------------------

_SEGMENT = re.compile(r"([^.\[\]]+)|\[([^\]]+)\]")
_Step = str | int | tuple[tuple[str, str], ...]


def parse_path(path: str) -> list[_Step]:
    """Split ``data.limits[type=TOKENS_LIMIT,unit=3].percentage`` / ``items.0.used``.

    A plain number picks a list item; ``[field=value,...]`` picks the first
    list item whose fields all equal those values.
    """

    steps: list[_Step] = []
    position = 0
    text = path.strip()
    while position < len(text):
        if text[position] == ".":
            position += 1
            continue
        match = _SEGMENT.match(text, position)
        if match is None:
            raise HttpQuotaError(f"Cannot read JSON path {path!r}")
        if match.group(1) is not None:
            name = match.group(1)
            steps.append(int(name) if name.isdigit() else name)
        else:
            conditions = []
            for condition in match.group(2).split(","):
                field, equals, wanted = condition.partition("=")
                if not equals or not field.strip():
                    raise HttpQuotaError(f"Cannot read JSON path {path!r}")
                conditions.append((field.strip(), wanted.strip()))
            steps.append(tuple(conditions))
        position = match.end()
    if not steps:
        raise HttpQuotaError("JSON path must not be empty")
    return steps


def _same(value: object, wanted: str) -> bool:
    """Compare a JSON value with filter text written the JSON way (true, false, null)."""

    if isinstance(value, bool):
        return wanted.lower() == ("true" if value else "false")
    if value is None:
        return wanted.lower() == "null"
    return str(value) == wanted


def read_path(body: object, path: str) -> object:
    """The value at ``path`` in ``body``, or None when any step is missing."""

    current = body
    for step in parse_path(path):
        if isinstance(step, tuple):
            if not isinstance(current, list):
                return None
            current = next(
                (
                    item
                    for item in current
                    if isinstance(item, Mapping)
                    and all(_same(item.get(field), wanted) for field, wanted in step)
                ),
                None,
            )
        elif isinstance(step, int):
            if isinstance(current, Mapping):
                current = current.get(str(step))  # an object keyed "0", "1", ...
            elif isinstance(current, list) and -len(current) <= step < len(current):
                current = current[step]
            else:
                return None
        else:
            if not isinstance(current, Mapping):
                return None
            current = current.get(step)
        if current is None:
            return None
    return current


def _number(value: object) -> float | None:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


_EXTRA_DIGITS = re.compile(r"(\.\d{6})\d+")


def _reset_iso(value: object) -> str | None:
    """A reset time given as ISO text, epoch seconds, or epoch milliseconds."""

    if isinstance(value, str) and _number(value) is None:
        # Some services send nanoseconds; datetime keeps microseconds.
        parsed = parse_timestamp(_EXTRA_DIGITS.sub(lambda match: match.group(1), value.strip()))
        return parsed.isoformat() if parsed is not None else None
    number = _number(value)
    if number is None:
        return None
    return epoch_to_iso(number / 1000 if number > 1e11 else number)


def _is_amount(value: object) -> TypeGuard[float]:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _amount_scale(window: Mapping[str, Any]) -> float:
    scale = window.get("amount_scale")
    return float(scale) if _is_amount(scale) else 1.0


def window_used_percent(body: object, window: Mapping[str, Any]) -> float | None:
    """Used percentage of one window, from whichever fields the window names."""

    def value(name: str) -> float | None:
        path = window.get(name)
        if name == "limit" and _is_amount(path):
            return float(path)  # a fixed amount (a budget), not a path
        number = _number(read_path(body, path)) if isinstance(path, str) and path else None
        if number is not None and name in ("used", "remaining", "limit"):
            number *= _amount_scale(window)
        return number

    scale = 100.0 if window.get("fraction") is True else 1.0
    used = value("used_percent")
    if used is not None:
        return finite_percent(used * scale)
    remaining = value("remaining_percent")
    if remaining is not None:
        return finite_percent(100 - remaining * scale)
    limit = value("limit")
    if limit is None or limit <= 0:
        return None
    spent = value("used")
    if spent is not None:
        return finite_percent(spent / limit * 100)
    left = value("remaining")
    if left is not None:
        return finite_percent((limit - left) / limit * 100)
    return None


def usage_signals(body: object, windows: list[Mapping[str, Any]]) -> QuotaSignals:
    """Translate a usage reply into Deck quota signals (top row, bottom row)."""

    signals: QuotaSignals = {}
    for prefix, window in zip(("primary", "secondary"), windows[:2], strict=False):
        used = window_used_percent(body, window)
        if used is None:
            continue
        signals[f"{prefix}_percent"] = used
        signals[f"{prefix}_label"] = str(window.get("label") or "").strip().upper()[:8] or None
        currency = window.get("currency")
        fixed_unit = window.get("unit")
        remaining = window.get("remaining")
        is_money = (isinstance(currency, str) and currency) or isinstance(fixed_unit, str)
        if is_money and isinstance(remaining, str) and remaining:
            # A money balance: show what is left as money.
            left = _number(read_path(body, remaining))
            unit = (
                read_path(body, currency) if isinstance(currency, str) and currency else fixed_unit
            )
            if left is not None:
                signals[f"{prefix}_amount_left"] = round(left * _amount_scale(window), 6)
                signals[f"{prefix}_currency"] = unit if isinstance(unit, str) else None
        reset = window.get("reset_at")
        signals[f"{prefix}_reset_at"] = (
            _reset_iso(read_path(body, reset)) if isinstance(reset, str) and reset else None
        )
    return signals


# -- configuration -------------------------------------------------------------


def resolve_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """A source with its preset filled in; the source's own fields win."""

    preset = PRESETS.get(str(source.get("preset") or "custom"), {})
    resolved = {"auth_header": "Authorization", "auth_scheme": "Bearer", **preset}
    for key, value in source.items():
        # Blank means "use the preset", except auth_scheme: "" sends the bare key.
        if value is not None and (key == "auth_scheme" or value not in ("", [])):
            resolved[key] = value
    # A budget is the 100% of windows that report only what is left.
    budget = resolved.pop("budget", None)
    if _is_amount(budget) and isinstance(resolved.get("windows"), list):
        resolved["windows"] = [
            {**window, "limit": budget}
            if isinstance(window, Mapping) and not window.get("limit")
            else window
            for window in resolved["windows"]
        ]
    return resolved


def _validate_url(source_id: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise HttpQuotaError(f"http_quota source {source_id} needs a url")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HttpQuotaError(f"http_quota source {source_id} url must be an http(s) URL")
    if parts.username or parts.password or parts.fragment:
        raise HttpQuotaError(
            f"http_quota source {source_id} url must not contain credentials or a fragment"
        )
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise HttpQuotaError(f"http_quota source {source_id} url has an invalid port") from None
    # The key travels in a header: only loopback may use plain HTTP.
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise HttpQuotaError(f"http_quota source {source_id} url must use https://")
    secret = [name for name, _ in parse_qsl(parts.query) if _is_secret_param(name)]
    if secret:
        raise HttpQuotaError(
            f"http_quota source {source_id} url must not carry credentials "
            f"({', '.join(secret)}); put the key in api_key, which is sent as a header"
        )


def _is_secret_param(name: str) -> bool:
    plain = re.sub(r"[-_.]", "", name.lower())
    return plain in _SECRET_NAMES or plain.endswith(_SECRET_ENDINGS)


def source_origin(source: Mapping[str, Any]) -> tuple[str, str, int | None] | None:
    """Where a source sends its key: (scheme, host, port) of its resolved URL."""

    url = resolve_source(source).get("url")
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS.get(scheme)


def _validate_window(source_id: str, index: int, window: object) -> None:
    where = f"http_quota source {source_id} window {index + 1}"
    if not isinstance(window, Mapping):
        raise HttpQuotaError(f"{where} must be a mapping")
    unknown = sorted(set(window) - _WINDOW_KEYS)
    if unknown:
        raise HttpQuotaError(f"{where}: unknown setting(s): {', '.join(unknown)}")
    if "fraction" in window and not isinstance(window["fraction"], bool):
        raise HttpQuotaError(f"{where}: fraction must be a boolean")
    if "auto_limit" in window and not isinstance(window["auto_limit"], bool):
        raise HttpQuotaError(f"{where}: auto_limit must be a boolean")
    unit = window.get("unit")
    if unit is not None and not (isinstance(unit, str) and re.fullmatch(r"[A-Z]{3}", unit)):
        raise HttpQuotaError(f"{where}: unit must be a currency code such as USD")
    if "amount_scale" in window and not _is_amount(window["amount_scale"]):
        raise HttpQuotaError(f"{where}: amount_scale must be a positive number")
    label = window.get("label")
    if label is not None and not isinstance(label, str):
        raise HttpQuotaError(f"{where}: label must be text")
    for name in _PATH_KEYS:
        path = window.get(name)
        if path is None or path == "":
            continue
        if name == "limit" and _is_amount(path):
            continue
        if not isinstance(path, str):
            raise HttpQuotaError(f"{where}: {name} must be a JSON path")
        parse_path(path)
    has = {name for name in _PATH_KEYS if window.get(name)}
    if not (
        has & {"used_percent", "remaining_percent"}
        or ("limit" in has and has & {"used", "remaining"})
        or (window.get("auto_limit") is True and "remaining" in has)
    ):
        raise HttpQuotaError(
            f"{where} needs used_percent, remaining_percent, or limit with used or remaining"
        )


def validate_http_quota_config(config: Mapping[str, object]) -> None:
    unknown = sorted(set(config) - _PLUGIN_KEYS)
    if unknown:
        raise HttpQuotaError(f"Unknown http_quota setting(s): {', '.join(unknown)}")
    if "enabled" in config and not isinstance(config["enabled"], bool):
        raise HttpQuotaError("http_quota.enabled must be a boolean")
    if config.get("cache_path") is not None and not isinstance(config.get("cache_path"), str):
        raise HttpQuotaError("http_quota.cache_path must be a string or null")
    for name, minimum, maximum in (
        ("refresh_interval_seconds", 60.0, math.inf),
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
            raise HttpQuotaError(f"http_quota.{name} must be a number {limit}")
    sources = config.get("sources", [])
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise HttpQuotaError(f"http_quota.sources must be a list of at most {MAX_SOURCES}")
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise HttpQuotaError("http_quota.sources entries must be mappings")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
            raise HttpQuotaError(
                "http_quota source id must be 1-32 lowercase letters, digits, - or _"
            )
        if source_id in seen:
            raise HttpQuotaError(f"http_quota source id {source_id} is used twice")
        seen.add(source_id)
        unknown = sorted(set(source) - _SOURCE_KEYS)
        if unknown:
            raise HttpQuotaError(
                f"http_quota source {source_id}: unknown setting(s): {', '.join(unknown)}"
            )
        preset = source.get("preset") or "custom"
        if preset != "custom" and preset not in PRESETS:
            raise HttpQuotaError(
                f"http_quota source {source_id}: preset must be custom or one of "
                f"{', '.join(PRESETS)}"
            )
        for name in ("label", "api_key", "api_key_env", "auth_header", "auth_scheme"):
            value = source.get(name)
            if value is not None and not isinstance(value, str):
                raise HttpQuotaError(f"http_quota source {source_id}: {name} must be text")
        header = source.get("auth_header")
        if header and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(header)):
            raise HttpQuotaError(f"http_quota source {source_id}: auth_header is not a header name")
        budget = source.get("budget")
        if budget is not None and not _is_amount(budget):
            raise HttpQuotaError(f"http_quota source {source_id}: budget must be a positive number")
        resolved = resolve_source(source)
        _validate_url(source_id, resolved.get("url"))
        windows = resolved.get("windows")
        if not isinstance(windows, list) or not 1 <= len(windows) <= 2:
            raise HttpQuotaError(f"http_quota source {source_id} needs one or two windows")
        for index, window in enumerate(windows):
            _validate_window(source_id, index, window)


# -- provider ------------------------------------------------------------------


class HttpQuotaPlugin:
    """Expose one quota record per configured source.

    A quota key picks a source with ``action_params.account`` (the source id).
    """

    manifest = PluginManifest(
        id="http_quota", name="Usage API quota", components=("quota",), actions=()
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
        self._sources = [resolve_source(source) for source in config.get("sources", [])]
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
        self._accounts: dict[str, QuotaAccount] = {}
        self._seen_at: dict[str, float] = {}
        self._fresh: set[str] = set()
        # Balance-only windows: the balance after the latest top-up (the full
        # mark) and the last balance read, per "source id:window index".
        self._full: dict[str, float] = {}
        self._last_left: dict[str, float] = {}
        self._load_cache()
        self._detail = "Waiting for first quota refresh"
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._loop, name="http-quota-refresh", daemon=True
            )
            self._thread.start()

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise HttpQuotaError("http_quota config must be a mapping")
        validate_http_quota_config(config)

    def _title(self, source: Mapping[str, Any]) -> str:
        label = source.get("label")
        if isinstance(label, str) and label.strip():
            return label.strip().upper()[:16]
        preset = PRESETS.get(str(source.get("preset") or ""), {})
        return str(preset.get("title") or source["id"]).upper()[:16]

    def _load_cache(self) -> None:
        cached = load_cache(self.cache_path, _CACHE_SOURCE)
        if cached is None:
            return
        prints = {source["id"]: self._fingerprint(source) for source in self._sources}
        entries = cached[0].get("accounts")
        for entry in entries if isinstance(entries, list) else []:
            # The cache is a local file anyone could edit: accept only clean data,
            # and only for a source read the same way, with the same key.
            if not isinstance(entry, Mapping) or prints.get(entry.get("id")) != entry.get("source"):
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
            source = next(item for item in self._sources if item["id"] == entry["id"])
            marks = entry.get("full")
            raw_lefts = entry.get("left")
            lefts: Mapping[str, Any] = raw_lefts if isinstance(raw_lefts, Mapping) else {}
            for name, mark in marks.items() if isinstance(marks, dict) else ():
                if (
                    isinstance(name, str)
                    and name.startswith(f"{source['id']}:")
                    and _is_amount(mark)
                ):
                    self._full[name] = float(mark)
                    # The last balance read, so a top-up while stopped still counts.
                    left = lefts.get(name)
                    if isinstance(left, bool) or not isinstance(left, (int, float)):
                        left = mark
                    self._last_left[name] = float(left) if math.isfinite(left) else float(mark)
            self._accounts[source["id"]] = QuotaAccount(
                source["id"], self._title(source), str(source.get("preset") or "custom"), signals
            )
            self._seen_at[source["id"]] = float(seen)

    def _fingerprint(self, source: Mapping[str, Any]) -> str:
        """Identify how a source is read (and with which key) without storing the key."""

        fields = ("url", "auth_header", "auth_scheme", "windows")
        setup = {name: source.get(name) for name in fields}
        setup["key"] = hashlib.sha256((self._key(source) or "").encode()).hexdigest()
        return hashlib.sha256(json.dumps(setup, sort_keys=True).encode()).hexdigest()

    def _save_cache(self) -> None:
        prints = {source["id"]: self._fingerprint(source) for source in self._sources}
        # One writer at a time; a stopped worker no longer writes.
        with self._cache_lock:
            if self._stop.is_set():
                return
            with self._lock:
                entries = [
                    {
                        "id": account.id,
                        "source": prints[account.id],
                        "signals": account.signals,
                        "seen_at": self._seen_at[account.id],
                        "full": {
                            name: mark
                            for name, mark in self._full.items()
                            if name.startswith(f"{account.id}:")
                        },
                        "left": {
                            name: left
                            for name, left in self._last_left.items()
                            if name.startswith(f"{account.id}:")
                        },
                    }
                    for account in self._accounts.values()
                    if account.signals and account.id in self._seen_at and account.id in prints
                ]
            if entries:
                save_cache(self.cache_path, _CACHE_SOURCE, {"accounts": entries}, self._clock())

    def _key(self, source: Mapping[str, Any]) -> str | None:
        env_name = source.get("api_key_env")
        if isinstance(env_name, str) and env_name and os.environ.get(env_name):
            return os.environ[env_name]
        key = source.get("api_key")
        return key if isinstance(key, str) and key else None

    def _read(self, source: Mapping[str, Any]) -> tuple[QuotaSignals, str | None]:
        if self._stop.is_set():
            return {}, "stopped"  # closed while this read waited its turn: send nothing
        headers = {"Accept": "application/json"}
        key = self._key(source)
        if key is not None:
            scheme = str(source.get("auth_scheme") or "").strip()
            headers[str(source.get("auth_header") or "Authorization")] = (
                f"{scheme} {key}" if scheme else key
            )
        try:
            response = self._client.get(str(source["url"]), headers=headers)
        except httpx.HTTPError as exc:
            return {}, f"unreachable: {type(exc).__name__}"
        if response.status_code in (401, 403):
            return {}, "key rejected" if key else "needs a key"
        if response.status_code != 200:
            return {}, f"HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return {}, "reply is not JSON"
        windows = [
            self._with_full_mark(source["id"], index, body, window)
            for index, window in enumerate(source.get("windows") or [])
        ]
        signals = usage_signals(body, windows)
        return signals, None if signals else "no quota numbers at the configured paths"

    def _with_full_mark(
        self, source_id: str, index: int, body: object, window: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Give a balance-only window a limit: the balance after the latest top-up.

        A balance that went up was topped up, so what is there now is full;
        spending only lowers it. A set `budget` (a limit) wins.
        """

        remaining = window.get("remaining")
        if (
            window.get("auto_limit") is not True
            or window.get("limit")
            or not isinstance(remaining, str)
        ):
            return window
        left = _number(read_path(body, remaining))
        if left is None:
            return window
        left *= _amount_scale(window)
        name = f"{source_id}:{index}"
        with self._lock:
            full = self._full.get(name)
            last = self._last_left.get(name)
            if full is None or last is None or left > last:
                full = left
            self._full[name] = full
            self._last_left[name] = left
        # An empty account reads as fully used rather than as no number.
        return {**window, "limit": full if full > 0 else 1.0}

    def refresh(self) -> bool:
        """Read every source once; returns whether every source answered.

        Each answer is shown as soon as it arrives, so a slow service does not
        hold back the others.
        """

        if self._stop.is_set():
            return False
        if not self._sources:
            with self._lock:
                self._accounts, self._fresh = {}, set()
                self._connected, self._detail = True, "No sources configured"
            return True
        answered: set[str] = set()
        workers = min(MAX_PARALLEL_READS, len(self._sources))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="http-quota") as pool:
            reads = {pool.submit(self._read, source): source for source in self._sources}
            for done in as_completed(reads):
                if self._stop.is_set():  # closed while requests were out
                    return False
                signals, error = done.result()
                self._publish(reads[done], signals, error)
                if error is None:
                    answered.add(reads[done]["id"])
        self._save_cache()
        return len(answered) == len(self._sources)

    def _publish(self, source: Mapping[str, Any], signals: QuotaSignals, error: str | None) -> None:
        source_id = source["id"]
        with self._lock:
            previous = self._accounts.get(source_id)
            account = QuotaAccount(
                source_id,
                self._title(source),
                str(source.get("preset") or "custom"),
                signals if error is None else previous.signals if previous else None,
                error,
            )
            accounts = {**self._accounts, source_id: account}
            # Keys follow the configured order, whatever order the answers came in.
            self._accounts = {
                item["id"]: accounts[item["id"]] for item in self._sources if item["id"] in accounts
            }
            if error is None:
                self._seen_at[source_id] = self._clock()
                self._fresh.add(source_id)
            else:
                self._fresh.discard(source_id)
            current, total = len(self._fresh), len(self._sources)
            self._connected = current > 0
            if current == total:
                self._detail = f"{total} source(s) current"
            else:
                self._detail = f"{current} of {total} source(s) current"

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ok = self.refresh()
            except Exception:  # Keep polling; report instead of dying silently.
                LOGGER.exception("HTTP quota refresh failed")
                with self._lock:
                    self._connected = False
                    self._fresh = set()
                    self._detail = "Quota refresh failed; see bridge log"
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
                QuotaAccount(account.id, account.title, account.provider, signals, account.error)
            )
        if not shown and not self._sources:
            detail = "No sources configured"
        return PluginSnapshot(PluginHealth(connected, detail, None), accounts=tuple(shown))

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        raise HttpQuotaError(f"Unsupported http_quota action: {action}")
