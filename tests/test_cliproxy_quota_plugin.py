import json
from pathlib import Path

import httpx
import pytest

from stream_micro.plugins.cliproxy_quota import (
    CliProxyQuotaError,
    CliProxyQuotaPlugin,
    claude_usage_signals,
    codex_account_id,
    codex_usage_signals,
    validate_cliproxy_quota_config,
)

BASE = {"enabled": True, "base_url": "https://proxy.test:8317", "management_key": "mgmt"}


def _proxy(accounts: list[dict], upstream, key: str = "mgmt") -> httpx.Client:
    """A fake CLIProxyAPI: auth-files lists ``accounts``; api-call asks ``upstream``."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {key}"
        if request.url.path == "/v0/management/auth-files":
            return httpx.Response(200, json={"files": accounts})
        body = json.loads(request.content)
        assert "$TOKEN$" in body["header"]["Authorization"]
        status, payload = upstream(body)
        return httpx.Response(200, json={"status_code": status, "body": json.dumps(payload)})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_codex_usage_signals_map_windows_and_labels():
    signals = codex_usage_signals(
        {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 42,
                    "limit_window_seconds": 18000,
                    "reset_at": 1_900_000_000,
                },
                "secondary_window": {"used_percent": 7, "limit_window_seconds": 604800},
                "bogus_window": {"used_percent": float("nan")},
            },
        }
    )
    assert signals["primary_percent"] == 42.0
    assert signals["primary_label"] == "5H"
    assert str(signals["primary_reset_at"]).startswith("2030-")
    assert signals["secondary_label"] == "7D"
    assert signals["plan"] == "plus"


def test_claude_usage_signals_clamp_and_skip_non_numbers():
    signals = claude_usage_signals(
        {
            "five_hour": {"utilization": 12.5, "resets_at": "2026-10-06T05:00:00Z"},
            "seven_day": {"utilization": 130},
            "seven_day_opus": {"utilization": "n/a"},
        }
    )
    assert signals["primary_percent"] == 12.5
    assert signals["primary_reset_at"] == "2026-10-06T05:00:00Z"
    assert signals["secondary_percent"] == 100.0


def test_codex_account_id_reads_nested_jwt_claim():
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}}
    assert codex_account_id({"id_token": claims}) == "acct-1"
    assert codex_account_id({"id_token": {"chatgpt_account_id": "acct-3"}}) == "acct-3"
    assert codex_account_id({"metadata": {"chatgpt_account_id": "acct-2"}}) == "acct-2"
    # A raw token is never decoded by the Bridge.
    assert codex_account_id({"id_token": "header.eyJjaGF0Z3B0X2FjY291bnRfaWQiOiJ4In0.sig"}) is None


@pytest.mark.parametrize(
    "config",
    [
        {**BASE, "providers": ["gemini"]},
        {**BASE, "base_url": "https://user:secret@proxy.test:8317"},
        {**BASE, "base_url": "https://proxy.test:8317/?key=secret"},
        {**BASE, "refresh_interval_seconds": float("inf")},
        {**BASE, "surprise": True},
        {**BASE, "label": "Home"},
        {**BASE, "base_url": "https://proxy.test:99999"},
        {**BASE, "request_timeout_seconds": 121},
        {**BASE, "management_key": "demo-secret\n"},
        {**BASE, "management_key": " demo-secret"},
        {**BASE, "management_key": "demo\x00secret"},
    ],
)
def test_config_validation_rejects_bad_settings(config):
    with pytest.raises(CliProxyQuotaError):
        CliProxyQuotaPlugin(config, start_thread=False)


def test_refresh_lists_each_pooled_account_with_its_quota(tmp_path: Path):
    calls = []

    def upstream(body):
        calls.append(body)
        if "chatgpt.com" in body["url"]:
            assert body["header"]["Chatgpt-Account-Id"] == "acct-a"
            return 200, {"rate_limit": {"primary_window": {"used_percent": 80}}}
        return 200, {"five_hour": {"utilization": 5}}

    accounts = [
        {
            "name": "codex-a.json",
            "provider": "codex",
            "email": "alice@example.com",
            "auth_index": "1",
            "id_token": {"chatgpt_account_id": "acct-a"},
        },
        {"name": "claude.json", "provider": "claude", "auth_index": "2"},
        {"name": "off.json", "provider": "codex", "auth_index": "3", "disabled": True},
        {"name": "gem.json", "provider": "gemini-cli", "auth_index": "4"},
    ]
    plugin = CliProxyQuotaPlugin(
        {**BASE, "cache_path": str(tmp_path / "c.json")},
        client=_proxy(accounts, upstream),
        clock=lambda: 0,
        start_thread=False,
    )
    assert plugin.refresh()
    snapshot = plugin.snapshot()

    assert snapshot.health.connected
    assert len(calls) == 2
    by_id = {account.id: account for account in snapshot.accounts}
    assert set(by_id) == {"codex-a.json", "claude.json"}
    alice = by_id["codex-a.json"]
    assert (alice.title, alice.provider) == ("CODEX ALICE", "codex")
    assert alice.signals is not None and alice.signals["primary_percent"] == 80.0
    assert by_id["claude.json"].provider == "claude"


def test_failed_account_read_keeps_its_last_numbers_marked_stale(tmp_path: Path):
    fail = [False]

    def upstream(_body):
        if fail[0]:
            return 502, "bad gateway"
        return 200, {"rate_limit": {"primary_window": {"used_percent": 40}}}

    accounts = [{"name": "c.json", "provider": "codex", "auth_index": "1"}]
    plugin = CliProxyQuotaPlugin(
        {**BASE, "cache_path": str(tmp_path / "c.json")},
        client=_proxy(accounts, upstream),
        start_thread=False,
    )
    plugin.refresh()
    fail[0] = True
    plugin.refresh()

    account = plugin.snapshot().accounts[0]
    assert account.error == "HTTP 502"
    assert account.signals is not None
    assert account.signals["primary_percent"] == 40.0
    assert account.signals["stale"] is True


def test_last_seen_quota_survives_a_restart_while_the_proxy_is_down(tmp_path: Path):
    cache = tmp_path / "cliproxy.json"
    up = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        if not up[0]:
            raise httpx.ConnectTimeout("timed out")
        if request.url.path.endswith("auth-files"):
            return httpx.Response(
                200, json={"files": [{"name": "c.json", "provider": "codex", "auth_index": "1"}]}
            )
        upstream = {
            "rate_limit": {
                "primary_window": {"used_percent": 30, "reset_at": 3_000},
                "secondary_window": {"used_percent": 60, "reset_at": 9_000},
            }
        }
        return httpx.Response(200, json={"status_code": 200, "body": json.dumps(upstream)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = {**BASE, "cache_path": str(cache)}
    CliProxyQuotaPlugin(config, client=client, clock=lambda: 1_000, start_thread=False).refresh()
    assert cache.exists()

    up[0] = False  # Reboot: the proxy is unreachable and the Bridge starts fresh.
    restarted = CliProxyQuotaPlugin(config, client=client, clock=lambda: 5_000, start_thread=False)
    assert not restarted.refresh()
    snapshot = restarted.snapshot()

    assert not snapshot.health.connected  # cached data never pretends to be live
    assert "last seen" in snapshot.health.detail
    signals = snapshot.accounts[0].signals
    assert signals is not None and signals["stale"] is True
    # The 5H window reset at 3000 (passed): its current value is unknown, so it
    # is dropped instead of being shown as refilled.
    assert "primary_percent" not in signals
    assert signals["secondary_percent"] == 60.0

    other_proxy = {**config, "base_url": "https://other.test:8317"}
    elsewhere = CliProxyQuotaPlugin(other_proxy, client=client, start_thread=False)
    assert elsewhere.snapshot().accounts == ()


def test_malformed_cache_is_ignored(tmp_path: Path):
    cache = tmp_path / "c.json"
    cache.write_text("[]", encoding="utf-8")
    plugin = CliProxyQuotaPlugin(
        {**BASE, "cache_path": str(cache)},
        client=_proxy([], lambda _b: (200, {})),
        start_thread=False,
    )
    assert plugin.snapshot().accounts == ()


def test_missing_management_key_reports_detail(monkeypatch):
    monkeypatch.delenv("CLIPROXY_KEY", raising=False)
    plugin = CliProxyQuotaPlugin(
        {"enabled": True, "base_url": "https://p:1", "management_key_env": "CLIPROXY_KEY"},
        start_thread=False,
    )
    assert not plugin.refresh()
    snapshot = plugin.snapshot()
    assert not snapshot.health.connected
    assert "Management key" in snapshot.health.detail


def test_close_stops_the_worker_and_closes_its_own_client():
    plugin = CliProxyQuotaPlugin({**BASE, "base_url": "http://127.0.0.1:9"})
    plugin.close()
    assert plugin._thread is not None and not plugin._thread.is_alive()
    assert plugin._client.is_closed


def test_plain_http_to_another_machine_needs_an_explicit_opt_in():
    lan = {**BASE, "base_url": "http://192.168.1.10:8317"}
    with pytest.raises(CliProxyQuotaError, match="unencrypted"):
        CliProxyQuotaPlugin(lan, start_thread=False)
    CliProxyQuotaPlugin({**lan, "allow_insecure_http": True}, start_thread=False)
    CliProxyQuotaPlugin({**BASE, "base_url": "http://127.0.0.1:8317"}, start_thread=False)


def test_valid_json_with_the_wrong_shape_is_ignored(tmp_path: Path):
    cache = tmp_path / "c.json"
    for payload in (
        {"accounts": None},
        {"accounts": [{"id": "a", "signals": {"primary_percent": [1]}, "seen_at": 1}]},
        {"accounts": [{"id": "a", "signals": {"primary_percent": 5}, "seen_at": "x"}]},
    ):
        cache.write_text(
            json.dumps({"source": BASE["base_url"], "saved_at": 1, "payload": payload}), "utf-8"
        )
        plugin = CliProxyQuotaPlugin(
            {**BASE, "cache_path": str(cache)},
            client=_proxy([], lambda _b: (200, {})),
            start_thread=False,
        )
        assert plugin.snapshot().accounts == ()


def test_every_failure_marks_the_last_numbers_stale(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLIPROXY_KEY", "mgmt")
    accounts = [{"name": "c.json", "provider": "codex", "auth_index": "1"}]
    config = {**BASE, "management_key": None, "management_key_env": "CLIPROXY_KEY"}
    plugin = CliProxyQuotaPlugin(
        config,
        client=_proxy(
            accounts, lambda _b: (200, {"rate_limit": {"primary_window": {"used_percent": 10}}})
        ),
        start_thread=False,
    )
    assert plugin.refresh()
    assert not plugin.snapshot().accounts[0].signals.get("stale")  # type: ignore[union-attr]

    monkeypatch.delenv("CLIPROXY_KEY")  # the key disappears
    assert not plugin.refresh()
    snapshot = plugin.snapshot()
    assert not snapshot.health.connected
    assert snapshot.accounts[0].signals["stale"] is True  # type: ignore[index]


def test_rejected_management_key_is_reported_plainly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid management key"})

    plugin = CliProxyQuotaPlugin(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    assert not plugin.refresh()
    assert plugin.snapshot().health.detail == "Proxy answered: management key rejected"


def test_the_service_has_one_fixed_identity():
    plugin = CliProxyQuotaPlugin(BASE, start_thread=False)
    assert (plugin.manifest.id, plugin.manifest.name) == ("cliproxy_quota", "CLIProxyAPI quota")


def test_codex_reset_given_as_seconds_from_now():
    body = {"rate_limit": {"primary_window": {"used_percent": 5, "reset_after_seconds": 3600}}}
    signals = codex_usage_signals(body, now=1_900_000_000)
    assert (
        signals["primary_reset_at"]
        == codex_usage_signals(
            {"rate_limit": {"primary_window": {"used_percent": 5, "reset_at": 1_900_003_600}}}
        )["primary_reset_at"]
    )


def test_an_account_without_auth_index_is_listed_with_its_problem():
    accounts = [{"name": "noindex.json", "provider": "codex"}]
    plugin = CliProxyQuotaPlugin(
        BASE, client=_proxy(accounts, lambda _b: pytest.fail("no api-call")), start_thread=False
    )
    assert plugin.refresh()
    (account,) = plugin.snapshot().accounts
    assert (account.id, account.error) == ("noindex.json", "missing auth_index")


def test_an_unreachable_proxy_is_reported_plainly():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    plugin = CliProxyQuotaPlugin(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    assert not plugin.refresh()
    health = plugin.snapshot().health
    assert not health.connected
    assert health.detail.startswith("Proxy unreachable")


def test_bad_port_is_rejected():
    with pytest.raises(CliProxyQuotaError, match="invalid port"):
        CliProxyQuotaPlugin({**BASE, "base_url": "https://proxy.test:abc"}, start_thread=False)


def test_validation_does_not_echo_a_rejected_management_key():
    with pytest.raises(CliProxyQuotaError) as caught:
        validate_cliproxy_quota_config({**BASE, "management_key": "demo-secret\n"})
    assert "demo-secret" not in str(caught.value)


def test_a_key_that_slipped_past_validation_never_reaches_the_dashboard():
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("a malformed key must not be sent")

    config = {"enabled": True, "base_url": "http://127.0.0.1:8798", "management_key": "demo-secret"}
    plugin = CliProxyQuotaPlugin(
        config, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    plugin.config["management_key"] = "demo-secret\n"  # a hand-edited config file
    assert not plugin.refresh()
    snapshot = plugin.snapshot()
    assert snapshot.health.detail == "Management key contains invalid characters"
    assert "demo-secret" not in repr(snapshot)


def test_an_environment_key_with_a_trailing_newline_is_refused(monkeypatch):
    monkeypatch.setenv("CLIPROXY_KEY", "demo-secret\n")
    plugin = CliProxyQuotaPlugin(
        {"enabled": True, "base_url": "https://p:1", "management_key_env": "CLIPROXY_KEY"},
        client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("sent"))),
        start_thread=False,
    )
    assert not plugin.refresh()
    detail = plugin.snapshot().health.detail
    assert detail == "Management key contains invalid characters"
    assert "demo" not in detail


def test_transport_errors_never_carry_their_message_into_the_snapshot():
    secret = "demo-secret-key"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth-files") and not up[0]:
            raise httpx.ConnectError(f"Illegal header value {secret}", request=request)
        if request.url.path.endswith("auth-files"):
            return httpx.Response(200, json={"files": accounts})
        raise httpx.ReadError(f"Bearer {secret}", request=request)

    up = [False]
    accounts = [{"name": "c.json", "provider": "codex", "auth_index": "1"}]
    plugin = CliProxyQuotaPlugin(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    assert not plugin.refresh()
    assert plugin.snapshot().health.detail == "Proxy unreachable: ConnectError"

    up[0] = True
    assert plugin.refresh()
    snapshot = plugin.snapshot()
    assert snapshot.accounts[0].error == "ReadError"
    assert secret not in repr(snapshot)


def test_a_failing_api_call_reports_only_the_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth-files"):
            return httpx.Response(
                200, json={"files": [{"name": "c.json", "provider": "codex", "auth_index": "1"}]}
            )
        return httpx.Response(500, text="Bearer mgmt leaked")

    plugin = CliProxyQuotaPlugin(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    assert plugin.refresh()
    assert plugin.snapshot().accounts[0].error == "HTTP 500"


def test_the_cache_file_never_holds_the_management_key(tmp_path: Path):
    cache = tmp_path / "c.json"
    accounts = [{"name": "c.json", "provider": "codex", "auth_index": "1"}]
    key = "demo-secret-key"
    plugin = CliProxyQuotaPlugin(
        {**BASE, "management_key": key, "cache_path": str(cache)},
        client=_proxy(
            accounts,
            lambda _b: (200, {"rate_limit": {"primary_window": {"used_percent": 10}}}),
            key,
        ),
        start_thread=False,
    )
    assert plugin.refresh()
    assert cache.exists() and key not in cache.read_text(encoding="utf-8")


def test_a_fresh_account_loses_a_window_once_its_reset_has_passed():
    now = [1_000.0]
    body = {
        "rate_limit": {
            "primary_window": {"used_percent": 30, "reset_at": 3_000},
            "secondary_window": {"used_percent": 60, "reset_at": 90_000},
        }
    }
    accounts = [{"name": "c.json", "provider": "codex", "auth_index": "1"}]
    plugin = CliProxyQuotaPlugin(
        BASE,
        client=_proxy(accounts, lambda _b: (200, body)),
        clock=lambda: now[0],
        start_thread=False,
    )
    assert plugin.refresh()
    signals = plugin.snapshot().accounts[0].signals
    assert signals is not None and signals["primary_percent"] == 30.0

    now[0] = 4_000.0  # still the current read, but the 5H window has reset since
    signals = plugin.snapshot().accounts[0].signals
    assert signals is not None
    assert "primary_percent" not in signals
    assert signals["secondary_percent"] == 60.0


def test_a_stopped_plugin_neither_publishes_nor_writes_the_cache(tmp_path: Path):
    cache = tmp_path / "c.json"
    stopping = [False]
    plugin: CliProxyQuotaPlugin

    def upstream(body):
        if stopping[0]:
            plugin._stop.set()  # closed while the last account was being read
        return 200, {"rate_limit": {"primary_window": {"used_percent": 10}}}

    accounts = [{"name": "a.json", "provider": "codex", "auth_index": "1"}]
    plugin = CliProxyQuotaPlugin(
        {**BASE, "cache_path": str(cache)},
        client=_proxy(accounts, upstream),
        clock=lambda: 7.0,
        start_thread=False,
    )
    assert plugin.refresh()
    before_cache = cache.read_text(encoding="utf-8")
    before = plugin.snapshot()

    plugin._stop.clear()
    stopping[0] = True
    accounts.append({"name": "b.json", "provider": "claude", "auth_index": "2"})
    assert not plugin.refresh()
    assert cache.read_text(encoding="utf-8") == before_cache
    assert plugin.snapshot() == before
    plugin._save_cache()  # a stopped worker no longer writes
    assert cache.read_text(encoding="utf-8") == before_cache


def test_accounts_removed_from_the_proxy_do_not_come_back_after_a_restart(tmp_path: Path):
    accounts = [{"name": "a.json", "provider": "codex", "auth_index": "1"}]
    up = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        if not up[0]:
            raise httpx.ConnectError("down", request=request)
        if request.url.path.endswith("auth-files"):
            return httpx.Response(200, json={"files": accounts})
        body = {"rate_limit": {"primary_window": {"used_percent": 10}}}
        return httpx.Response(200, json={"status_code": 200, "body": json.dumps(body)})

    config = {**BASE, "cache_path": str(tmp_path / "c.json")}
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = CliProxyQuotaPlugin(config, client=client, start_thread=False)
    assert first.refresh() and first.snapshot().accounts

    accounts.clear()  # the account was removed from the proxy
    assert first.refresh()
    assert first.snapshot().accounts == ()

    up[0] = False
    restarted = CliProxyQuotaPlugin(config, client=client, start_thread=False)
    assert not restarted.refresh()
    assert restarted.snapshot().accounts == ()


@pytest.mark.parametrize("reply", [[], "files", {"files": "nope"}, {"accounts": []}, 5])
def test_a_reply_of_the_wrong_shape_is_not_called_unreachable(reply):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reply)

    plugin = CliProxyQuotaPlugin(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler)), start_thread=False
    )
    assert not plugin.refresh()
    assert plugin.snapshot().health.detail == "Proxy answered: unexpected reply"


def test_close_keeps_the_client_open_while_the_worker_is_still_in_a_request(monkeypatch):
    plugin = CliProxyQuotaPlugin(BASE, start_thread=False)

    class Busy:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(plugin, "_thread", Busy())
    plugin.close()
    assert not plugin._client.is_closed
