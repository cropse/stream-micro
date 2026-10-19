from __future__ import annotations

from datetime import datetime, timezone

from stream_micro.models import QuotaSnapshot, QuotaWindow, WindowKind
from stream_micro.plugins.ollama_quota import OllamaQuotaPlugin
from stream_micro.scraper import ScrapeError


def _snapshot(session_percent: float = 12.5) -> QuotaSnapshot:
    from datetime import datetime, timezone

    reset = datetime(2026, 9, 13, 4, 0, tzinfo=timezone.utc)
    return QuotaSnapshot(
        session=QuotaWindow(WindowKind.SESSION, session_percent, reset),
        weekly=QuotaWindow(WindowKind.WEEKLY, 42.0, reset),
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeQuotaSource:
    def __init__(self, results: list[QuotaSnapshot | Exception] | None = None) -> None:
        self.results = results or [_snapshot()]
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _plugin(source: FakeQuotaSource, clock: FakeClock) -> OllamaQuotaPlugin:
    plugin = OllamaQuotaPlugin(
        {"cookie_value": "secret", "refresh_interval_seconds": 10},
        clock=clock,
        fetch_quota=source,
    )
    return plugin


def test_no_cookie_shows_helpful_error() -> None:
    plugin = OllamaQuotaPlugin({"cookie_value": ""})

    snapshot = plugin.snapshot()

    assert snapshot.health.connected is False
    assert "cookie" in snapshot.health.detail.lower()
    assert snapshot.signals == {}
    assert plugin.manifest.actions == ()


def test_unknown_config_key_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown"):
        OllamaQuotaPlugin({"unexpected": "value"})


def test_legacy_keys_are_ignored() -> None:
    plugin = OllamaQuotaPlugin(
        {"enabled": True, "legacy_config_path": "config.yaml", "cookie_value": ""}
    )
    snapshot = plugin.snapshot()
    assert snapshot.health.connected is False
    assert "cookie" in snapshot.health.detail.lower()


def test_snapshot_is_cached_between_refreshes() -> None:
    source = FakeQuotaSource()
    clock = FakeClock()
    plugin = _plugin(source, clock)

    first = plugin.snapshot()
    second = plugin.snapshot()

    assert source.calls == 1
    assert first.signals is not None
    assert second.signals is not None
    assert first.signals["session_percent"] == 12.5
    assert second.signals["session_percent"] == 12.5


def test_cache_expires_after_refresh_interval() -> None:
    source = FakeQuotaSource(results=[_snapshot(10.0), _snapshot(20.0)])
    clock = FakeClock()
    plugin = _plugin(source, clock)

    first = plugin.snapshot()
    assert first.signals is not None
    assert first.signals["session_percent"] == 10.0
    clock.now += 10.0
    second = plugin.snapshot()
    assert second.signals is not None
    assert second.signals["session_percent"] == 20.0
    assert source.calls == 2


def test_default_refresh_interval_is_ten_seconds() -> None:
    source = FakeQuotaSource(results=[_snapshot(10.0), _snapshot(20.0)])
    clock = FakeClock()
    plugin = OllamaQuotaPlugin(
        {"cookie_value": "secret"}, clock=clock, fetch_quota=source
    )

    initial = plugin.snapshot()
    assert initial.signals is not None
    assert initial.signals["session_percent"] == 10.0
    clock.now += 9.0
    cached = plugin.snapshot()
    assert cached.signals is not None
    assert cached.signals["session_percent"] == 10.0
    clock.now += 1.0
    refreshed = plugin.snapshot()
    assert refreshed.signals is not None
    assert refreshed.signals["session_percent"] == 20.0


def test_stale_data_is_kept_when_refresh_fails() -> None:
    source = FakeQuotaSource(results=[_snapshot(10.0), ScrapeError("network down")])
    clock = FakeClock()
    plugin = _plugin(source, clock)

    initial = plugin.snapshot()
    assert initial.signals is not None
    assert initial.signals["session_percent"] == 10.0
    clock.now += 10.0
    snapshot = plugin.snapshot()

    assert snapshot.health.connected is True
    assert snapshot.health.detail == "Connected (stale)"
    assert snapshot.signals is not None
    assert snapshot.signals["session_percent"] == 10.0
    # Keys draw last-seen numbers grey.
    assert snapshot.signals["stale"] is True
    assert isinstance(snapshot.signals["seen_at"], str)


def test_error_snapshot_is_cached_until_next_refresh() -> None:
    source = FakeQuotaSource(results=[ScrapeError("network down"), _snapshot(20.0)])
    clock = FakeClock()
    plugin = _plugin(source, clock)

    first = plugin.snapshot()
    assert first.health.connected is False
    clock.now += 10.0
    second = plugin.snapshot()
    assert second.health.connected is True
    assert second.signals is not None
    assert second.signals["session_percent"] == 20.0


def test_concurrent_snapshots_trigger_one_fetch() -> None:
    import threading

    source = FakeQuotaSource()
    clock = FakeClock()
    plugin = _plugin(source, clock)
    results: list = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(plugin.snapshot())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert source.calls == 1
    assert all(
        snapshot.signals is not None and snapshot.signals["session_percent"] == 12.5
        for snapshot in results
    )


def test_monthly_credits_become_a_generic_quota_row() -> None:
    reset = datetime(2026, 11, 14, tzinfo=timezone.utc)
    monthly = QuotaSnapshot(monthly=QuotaWindow(WindowKind.MONTHLY, 11.0, reset))
    plugin = OllamaQuotaPlugin({"cookie_value": "secret"}, fetch_quota=lambda _config: monthly)

    assert plugin.snapshot().signals == {
        "primary_percent": 11.0,
        "primary_label": "MONTH",
        "primary_reset_at": "2026-11-14T00:00:00+00:00",
        "primary_reset_precision": "date",
    }


def test_last_seen_numbers_give_way_to_the_error_after_the_grace_period() -> None:
    failures = [ScrapeError("cookie expired") for _ in range(20)]
    source = FakeQuotaSource(results=[_snapshot(10.0), *failures])
    clock = FakeClock()
    plugin = _plugin(source, clock)
    assert plugin.snapshot().health.connected

    seen = []
    for _ in range(12):  # a failed read every interval, for two minutes past the grace
        clock.now += 35.0
        seen.append(plugin.snapshot().health.detail)
    assert seen[0] == "Connected (stale)"
    assert seen[-1] == "cookie expired"
