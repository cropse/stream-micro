from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from stream_micro.plugins.http_quota import (
    HttpQuotaError,
    HttpQuotaPlugin,
    read_path,
    usage_signals,
    validate_http_quota_config,
)

NOW = 1_800_000_000.0
BODY = {
    "data": {
        "limits": [
            {"type": "TIME_LIMIT", "percentage": 12, "nextResetTime": 1_800_003_600_000},
            {"type": "TOKENS_LIMIT", "percentage": 40, "nextResetTime": 1_800_018_000},
        ],
        "credits": {"used": 7.5, "limit": 10, "left": 2.5},
        "ratio": 0.25,
        "reset": "2027-01-15T10:00:00Z",
    }
}


def _source(**fields: Any) -> dict[str, Any]:
    return {
        "id": "demo",
        "url": "https://usage.example/api",
        "api_key": "secret-key",
        "windows": [{"label": "5h", "used_percent": "data.limits[type=TOKENS_LIMIT].percentage"}],
        **fields,
    }


def _plugin(handler, *sources: dict[str, Any], tmp_path: Path | None = None) -> HttpQuotaPlugin:
    config: dict[str, Any] = {"enabled": True, "sources": list(sources) or [_source()]}
    if tmp_path is not None:
        config["cache_path"] = str(tmp_path / "http_quota.json")
    return HttpQuotaPlugin(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
        start_thread=False,
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("data.limits[type=TOKENS_LIMIT].percentage", 40),
        ("data.limits.0.type", "TIME_LIMIT"),
        ("data.limits[type=NOPE].percentage", None),
        ("data.credits.used", 7.5),
        ("data.missing.deeper", None),
        ("data.limits.9", None),
    ],
)
def test_json_paths(path: str, expected: object) -> None:
    assert read_path(BODY, path) == expected


def test_windows_from_percent_fraction_used_and_remaining() -> None:
    signals = usage_signals(
        BODY,
        [
            {
                "label": "5h",
                "used_percent": "data.limits[type=TOKENS_LIMIT].percentage",
                "reset_at": "data.limits[type=TOKENS_LIMIT].nextResetTime",
            },
            {"label": "credit", "limit": "data.credits.limit", "remaining": "data.credits.left"},
        ],
    )
    assert signals["primary_percent"] == 40
    assert signals["primary_label"] == "5H"
    assert signals["primary_reset_at"] == "2027-01-15T13:00:00+00:00"  # epoch seconds
    assert signals["secondary_percent"] == 75
    assert signals["secondary_label"] == "CREDIT"
    assert signals["secondary_reset_at"] is None

    other = usage_signals(
        BODY,
        [
            {"remaining_percent": "data.ratio", "fraction": True, "reset_at": "data.reset"},
            {
                "used": "data.credits.used",
                "limit": "data.credits.limit",
                "reset_at": "data.limits[type=TIME_LIMIT].nextResetTime",
            },
        ],
    )
    assert other["primary_percent"] == 75
    assert other["primary_reset_at"] == "2027-01-15T10:00:00+00:00"
    assert other["secondary_percent"] == 75
    assert other["secondary_reset_at"] == "2027-01-15T09:00:00+00:00"  # epoch milliseconds


def test_missing_or_zero_limits_give_no_window() -> None:
    body = {"used": 3, "limit": 0}
    assert usage_signals(body, [{"used": "used", "limit": "limit"}]) == {}
    assert usage_signals({}, [{"used_percent": "a.b"}]) == {}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "http://usage.example/api"}, "https"),
        ({"url": "https://user:pw@usage.example/api"}, "credentials"),
        ({"id": "Bad Id"}, "id"),
        ({"preset": "nope"}, "preset"),
        ({"windows": []}, "one or two windows"),
        ({"windows": [{"label": "x"}]}, "needs used_percent"),
        ({"windows": [{"used": "a"}]}, "needs used_percent"),
        ({"windows": [{"used_percent": "a", "bogus": 1}]}, "unknown"),
        ({"auth_header": "Bad Header:"}, "header"),
        ({"extra": 1}, "unknown"),
    ],
)
def test_validation(change: dict[str, Any], message: str) -> None:
    with pytest.raises(HttpQuotaError, match=message):
        validate_http_quota_config({"sources": [_source(**change)]})


def test_loopback_may_use_plain_http_and_ids_are_unique() -> None:
    validate_http_quota_config({"sources": [_source(url="http://127.0.0.1:8080/usage")]})
    with pytest.raises(HttpQuotaError, match="twice"):
        validate_http_quota_config({"sources": [_source(), _source()]})


def test_reads_with_the_key_in_the_configured_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=BODY)

    plugin = _plugin(handler, _source(), _source(id="raw", auth_scheme="", auth_header="X-Api-Key"))
    assert plugin.refresh() is True

    assert seen[0].headers["Authorization"] == "Bearer secret-key"
    assert seen[1].headers["X-Api-Key"] == "secret-key"
    snapshot = plugin.snapshot()
    assert snapshot.health.connected
    assert [account.id for account in snapshot.accounts] == ["demo", "raw"]
    assert snapshot.accounts[0].title == "DEMO"
    assert snapshot.accounts[0].signals == {
        "primary_percent": 40.0,
        "primary_label": "5H",
        "primary_reset_at": None,
    }


def test_key_from_environment_wins(monkeypatch) -> None:
    monkeypatch.setenv("USAGE_KEY_FOR_TEST", "env-key")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json=BODY)

    _plugin(handler, _source(api_key_env="USAGE_KEY_FOR_TEST")).refresh()
    assert seen == ["Bearer env-key"]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), "key rejected"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json={"other": 1}), "no quota numbers"),
    ],
)
def test_failures_are_reported_per_source(response: httpx.Response, error: str) -> None:
    plugin = _plugin(lambda request: response)
    assert plugin.refresh() is False

    account = plugin.snapshot().accounts[0]
    assert error in str(account.error)
    assert account.signals is None
    assert "secret-key" not in str(account.error)


def test_last_numbers_turn_stale_when_a_source_fails() -> None:
    answers = [httpx.Response(200, json=BODY), httpx.Response(503)]
    plugin = _plugin(lambda request: answers.pop(0))
    plugin.refresh()
    plugin.refresh()

    signals = plugin.snapshot().accounts[0].signals
    assert signals is not None
    assert signals["primary_percent"] == 40.0
    assert signals["stale"] is True


def test_cache_survives_restart_but_not_a_changed_url(tmp_path: Path) -> None:
    _plugin(lambda request: httpx.Response(200, json=BODY), tmp_path=tmp_path).refresh()
    cache = json.loads((tmp_path / "http_quota.json").read_text(encoding="utf-8"))
    assert "secret-key" not in json.dumps(cache)

    restarted = _plugin(lambda request: httpx.Response(500), tmp_path=tmp_path)
    signals = restarted.snapshot().accounts[0].signals
    assert signals is not None and signals["stale"] is True

    moved = _plugin(
        lambda request: httpx.Response(500),
        _source(url="https://elsewhere.example/api"),
        tmp_path=tmp_path,
    )
    assert moved.snapshot().accounts == ()


def test_no_sources_is_a_quiet_empty_provider() -> None:
    plugin = HttpQuotaPlugin(
        {"enabled": True, "sources": []},
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        start_thread=False,
    )
    assert plugin.refresh() is True
    assert plugin.snapshot().health.detail == "No sources configured"


# Reply shapes as documented by OpenRouter; numbers are made up.
OPENROUTER_KEY_BODY = {
    "data": {
        "label": "sk-or-v1-x",
        "limit": 100,
        "limit_remaining": 74.5,
        "limit_reset": "monthly",
        "usage": 25.5,
    }
}
OPENROUTER_CREDITS_BODY = {"data": {"total_credits": 100.5, "total_usage": 25.75}}
# Ollama's documented BalanceResponse (docs.ollama.com/openapi.yaml).
OLLAMA_BALANCE_BODY = {
    "included": {
        "balance_usd": 18.0,
        "allowance_usd": 20.0,
        "period": {"from": "2027-01-01T00:00:00Z", "until": "2027-02-01T00:00:00Z"},
    },
    "purchased": {"balance_usd": 0},
}


@pytest.mark.parametrize(
    ("preset", "body", "header", "expected"),
    [
        (
            "openrouter",
            OPENROUTER_KEY_BODY,
            "Bearer secret-key",
            {
                "primary_percent": 25.5,
                "primary_label": "LIMIT",
                "primary_reset_at": None,
            },
        ),
        (
            "openrouter_credits",
            OPENROUTER_CREDITS_BODY,
            "Bearer secret-key",
            {
                "primary_percent": pytest.approx(25.62, abs=0.01),
                "primary_label": "CREDIT",
                "primary_reset_at": None,
            },
        ),
        (
            "ollama",
            OLLAMA_BALANCE_BODY,
            "Bearer secret-key",
            {
                "primary_percent": pytest.approx(10.0),
                "primary_label": "MONTH",
                "primary_reset_at": "2027-02-01T00:00:00+00:00",
            },
        ),
    ],
)
def test_presets_need_only_a_key(
    preset: str, body: dict, header: str, expected: dict[str, object]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    source = {"id": preset.replace("_", "-"), "preset": preset, "api_key": "secret-key"}
    validate_http_quota_config({"sources": [source]})
    plugin = _plugin(handler, source)
    plugin.refresh()

    assert seen[0].url.scheme == "https"
    assert seen[0].headers["Authorization"] == header
    account = plugin.snapshot().accounts[0]
    assert account.signals == expected
    assert account.provider == preset


def test_preset_fields_can_be_overridden() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=OPENROUTER_KEY_BODY)

    source = {
        "id": "or-eu",
        "preset": "openrouter",
        "label": "OR EU",
        "url": "https://eu.openrouter.example/api/v1/key",
        "api_key": "secret-key",
    }
    plugin = _plugin(handler, source)
    plugin.refresh()

    assert seen == ["https://eu.openrouter.example/api/v1/key"]
    assert plugin.snapshot().accounts[0].title == "OR EU"


def test_openrouter_key_without_a_limit_explains_itself() -> None:
    body = {"data": {"limit": None, "limit_remaining": None, "usage": 3.2}}
    plugin = _plugin(
        lambda request: httpx.Response(200, json=body),
        {"id": "or", "preset": "openrouter", "api_key": "k"},
    )
    plugin.refresh()
    assert "no quota numbers" in str(plugin.snapshot().accounts[0].error)


def test_current_readings_age_too() -> None:
    now = [NOW]
    body = {"used": 10, "limit": 100, "reset": "2027-01-15T09:00:00Z"}  # NOW + 1 hour
    plugin = HttpQuotaPlugin(
        {
            "enabled": True,
            "sources": [
                _source(
                    windows=[{"label": "5H", "used": "used", "limit": "limit", "reset_at": "reset"}]
                )
            ],
        },
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        ),
        clock=lambda: now[0],
        start_thread=False,
    )
    plugin.refresh()
    assert "stale" not in (plugin.snapshot().accounts[0].signals or {})

    now[0] = NOW + 20 * 60  # no refresh since: grey, still shown
    signals = plugin.snapshot().accounts[0].signals
    assert signals is not None and signals["stale"] is True and signals["primary_percent"] == 10.0

    now[0] = NOW + 2 * 3600  # past its reset: never shown as refilled
    assert "primary_percent" not in (plugin.snapshot().accounts[0].signals or {})


def test_cache_is_dropped_when_the_key_or_paths_change(tmp_path: Path) -> None:
    _plugin(lambda request: httpx.Response(200, json=BODY), tmp_path=tmp_path).refresh()
    cache = (tmp_path / "http_quota.json").read_text(encoding="utf-8")
    assert "secret-key" not in cache

    def restarted(**change: Any) -> HttpQuotaPlugin:
        return _plugin(lambda request: httpx.Response(500), _source(**change), tmp_path=tmp_path)

    assert restarted().snapshot().accounts  # same source and key: last seen numbers
    assert restarted(api_key="another-account").snapshot().accounts == ()
    assert (
        restarted(windows=[{"used_percent": "data.ratio", "fraction": True}]).snapshot().accounts
        == ()
    )


def test_a_closed_worker_publishes_and_saves_nothing(tmp_path: Path) -> None:
    plugin: HttpQuotaPlugin

    def handler(request: httpx.Request) -> httpx.Response:
        plugin.close()  # closed while this request is out
        return httpx.Response(200, json=BODY)

    plugin = _plugin(handler, tmp_path=tmp_path)
    assert plugin.refresh() is False
    assert plugin.snapshot().accounts == ()
    assert not (tmp_path / "http_quota.json").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://usage.example/api?api_key=SECRET",
        "https://usage.example/api?token=SECRET",
        "https://usage.example/api?X-Amz-Signature=abc",
        "https://usage.example/api?jwt=abc",
        "https://usage.example/api?pwd=abc",
        "https://usage.example/api?client_secret=abc",
        "https://usage.example/api?X-Amz-Security-Token=abc",
        "https://usage.example/api?apiKey=abc",
    ],
)
def test_keys_in_the_url_are_refused(url: str) -> None:
    with pytest.raises(HttpQuotaError, match="must not carry credentials"):
        validate_http_quota_config({"sources": [_source(url=url)]})


@pytest.mark.parametrize(
    "name", ["period", "pageToken", "sort_key", "monkey", "keyword", "author", "bypass_cache",
             "assignment"]
)  # fmt: skip
def test_ordinary_query_parameters_are_allowed(name: str) -> None:
    url = f"https://usage.example/api?{name}=1"
    validate_http_quota_config({"sources": [_source(url=url)]})


def test_a_bad_port_is_refused_when_saving() -> None:
    with pytest.raises(HttpQuotaError, match="invalid port"):
        validate_http_quota_config({"sources": [_source(url="https://usage.example:99999/api")]})


def test_the_default_port_is_the_same_host() -> None:
    from stream_micro.plugins.http_quota import source_origin

    plain = source_origin(_source(url="https://usage.example/api"))
    assert plain == source_origin(_source(url="https://usage.example:443/api"))
    assert plain != source_origin(_source(url="https://usage.example:8443/api"))


@pytest.mark.parametrize("seconds", [0.5, 121, float("inf")])
def test_request_timeout_has_bounds(seconds: float) -> None:
    with pytest.raises(HttpQuotaError, match="request_timeout_seconds"):
        validate_http_quota_config({"request_timeout_seconds": seconds})
    validate_http_quota_config({"request_timeout_seconds": 120})


def test_filters_compare_json_true_false_and_null() -> None:
    body = {"items": [{"on": False, "v": 1}, {"on": True, "v": 2}, {"on": None, "v": 3}]}
    assert read_path(body, "items[on=true].v") == 2
    assert read_path(body, "items[on=false].v") == 1
    assert read_path(body, "items[on=null].v") == 3


def test_numeric_object_keys_are_keys() -> None:
    body = {"windows": {"0": {"used": 3}, "1": {"used": 7}}, "list": [{"used": 9}]}
    assert read_path(body, "windows.1.used") == 7
    assert read_path(body, "list.0.used") == 9


def test_sources_are_read_side_by_side() -> None:
    import threading

    started = threading.Barrier(3, timeout=5)

    def handler(request: httpx.Request) -> httpx.Response:
        started.wait()  # all three requests are out at once
        return httpx.Response(200, json=BODY)

    sources = [_source(id=name) for name in ("one", "two", "three")]
    plugin = _plugin(handler, *sources)
    assert plugin.refresh() is True
    assert [account.id for account in plugin.snapshot().accounts] == ["one", "two", "three"]


def test_a_fast_source_shows_before_a_slow_one_answers() -> None:
    import threading

    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "slow.example":
            assert release.wait(5)
        return httpx.Response(200, json=BODY)

    plugin = _plugin(handler, _source(id="slow", url="https://slow.example/api"), _source())
    worker = threading.Thread(target=plugin.refresh)
    worker.start()
    try:
        for _ in range(200):
            if plugin.snapshot().accounts:
                break
            threading.Event().wait(0.01)
        shown = plugin.snapshot()
        assert [account.id for account in shown.accounts] == ["demo"]
        assert shown.accounts[0].signals is not None
    finally:
        release.set()
        worker.join(5)
    # Once both answered, the keys follow the configured order again.
    assert [account.id for account in plugin.snapshot().accounts] == ["slow", "demo"]


def test_reads_waiting_their_turn_are_not_sent_after_close(monkeypatch) -> None:
    import stream_micro.plugins.http_quota as http_quota

    monkeypatch.setattr(http_quota, "MAX_PARALLEL_READS", 1)
    plugin: HttpQuotaPlugin
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        plugin.close()
        return httpx.Response(200, json=BODY)

    sources = [_source(id=name, url=f"https://usage.example/{name}") for name in "abc"]
    plugin = _plugin(handler, *sources)
    assert plugin.refresh() is False
    assert sent == ["/a"]
