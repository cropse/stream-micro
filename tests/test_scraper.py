from datetime import datetime, timezone

import httpx
import pytest

from stream_micro.config import OllamaConfig
from stream_micro.models import WindowKind
from stream_micro.scraper import (
    ExpiredCookieError,
    ScrapeError,
    fetch_quota,
    parse_quota_page,
)

QUOTA_HTML = """
<section><div aria-label="Session usage 10% used"></div></section>
<section><div aria-label="Weekly usage 20% used"></div></section>
"""


def test_parse_aria_quota_and_nearest_reset_timestamps() -> None:
    document = """
    <section><h2>Session</h2><div aria-label="Session usage 4.6% used"></div>
      <time data-time="2026-07-28T15:30:00Z"></time></section>
    <section><h2>Weekly</h2><div aria-label="Weekly usage 81% used"></div>
      <time data-time="2026-08-01T00:00:00+00:00"></time></section>
    """

    snapshot = parse_quota_page(document)

    assert snapshot.session.percent_used == 4.6
    assert snapshot.session.reset_at == datetime(2026, 7, 28, 15, 30, tzinfo=timezone.utc)
    assert snapshot.weekly.percent_used == 81
    assert snapshot.weekly.reset_at == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_parse_visible_text_fallback_without_css_widths() -> None:
    document = """
    <section><h2>Session</h2><p>Usage: 34% used</p><div style="width: 99%"></div>
      <span data-time="2026-07-29T10:00:00Z"></span></section>
    <section><h2>Weekly</h2><p>Used 67%</p><div style="width: 2%"></div>
      <span data-time="2026-08-03T10:00:00Z"></span></section>
    """

    snapshot = parse_quota_page(document)

    assert snapshot.session.percent_used == 34
    assert snapshot.weekly.percent_used == 67
    assert snapshot.session.reset_at == datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    assert snapshot.weekly.reset_at == datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_fetch_detects_signed_out_redirect_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<h1>Sign in to Ollama</h1>", request=request)

    with pytest.raises(ExpiredCookieError, match="expired or invalid"):
        fetch_quota(OllamaConfig(cookie_value="secret"), transport=httpx.MockTransport(handler))


def test_fetch_rejects_cross_origin_redirect_before_destination_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "ollama.com":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/steal"},
                request=request,
            )
        raise AssertionError("Cross-origin destination must never be requested")

    with pytest.raises(ScrapeError, match="cross-origin redirect"):
        fetch_quota(OllamaConfig(cookie_value="secret"), transport=httpx.MockTransport(handler))

    assert len(requests) == 1
    assert requests[0].headers["cookie"] == "__Secure-session=secret"


def test_fetch_follows_same_origin_redirect_and_keeps_cookie() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/settings":
            return httpx.Response(302, headers={"Location": "/account/usage"}, request=request)
        return httpx.Response(200, text=QUOTA_HTML, request=request)

    snapshot = fetch_quota(
        OllamaConfig(cookie_value="secret"),
        transport=httpx.MockTransport(handler),
    )

    assert snapshot.session.percent_used == 10
    assert [request.url.path for request in requests] == ["/settings", "/account/usage"]
    assert all(request.headers["cookie"] == "__Secure-session=secret" for request in requests)


def test_reset_before_aria_label_is_associated_with_its_section() -> None:
    document = """
    <section><time data-time="2026-07-28T09:00:00Z"></time>
      <div aria-label="Session usage 12% used"></div></section>
    <section><time data-time="2026-08-02T09:00:00Z"></time>
      <div aria-label="Weekly usage 34% used"></div></section>
    """

    snapshot = parse_quota_page(document)

    assert snapshot.session.reset_at == datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    assert snapshot.weekly.reset_at == datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)


def test_visible_fallback_ignores_unrelated_earlier_labels_and_times() -> None:
    document = """
    <nav>Session help <time data-time="2020-01-01T00:00:00Z"></time></nav>
    <section><h2>Session</h2><p>Usage 45% used</p>
      <time data-time="2026-07-30T10:00:00Z"></time></section>
    <aside>Weekly documentation <time data-time="2021-01-01T00:00:00Z"></time></aside>
    <section><h2>Weekly</h2><p>Used 65%</p>
      <time data-time="2026-08-04T10:00:00Z"></time></section>
    """

    snapshot = parse_quota_page(document)

    assert snapshot.session.reset_at == datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    assert snapshot.weekly.reset_at == datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


# Newer plans: monthly credits instead of 5-hour/weekly windows (made-up numbers).
MONTHLY_HTML = """
<div><span>Monthly credits</span><span>$53.41</span></div>
<p>Refills to $60 on November 14, 2026. Unused monthly credits do not carry over.</p>
<div role="progressbar" aria-label="Monthly credits used: $6.59 of $60"></div>
"""


def test_monthly_credit_plans_are_read_with_their_refill_date() -> None:
    snapshot = parse_quota_page(MONTHLY_HTML)

    assert snapshot.session is None and snapshot.weekly is None
    assert snapshot.monthly is not None
    assert snapshot.monthly.kind == WindowKind.MONTHLY
    assert round(snapshot.monthly.percent_used, 2) == 10.98
    assert snapshot.monthly.reset_at == datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_a_page_with_no_quota_at_all_still_fails() -> None:
    with pytest.raises(ScrapeError, match="session, weekly"):
        parse_quota_page("<p>Settings</p>")
