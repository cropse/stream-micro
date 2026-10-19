from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from .config import OllamaConfig
from .models import QuotaSnapshot, QuotaWindow, WindowKind


class ScrapeError(RuntimeError):
    """Raised when quota data cannot be fetched or parsed."""


class ExpiredCookieError(ScrapeError):
    """Raised when Ollama returns a signed-out page."""


ARIA_PATTERN = re.compile(
    r'aria-label=["\'](?P<label>Session|Weekly)\s+usage\s+'
    r'(?P<percent>\d{1,3}(?:\.\d+)?)%\s+used["\']',
    re.IGNORECASE,
)
# Newer Ollama plans meter monthly credits instead of 5-hour/weekly windows.
MONTHLY_PATTERN = re.compile(
    r'aria-label=["\']Monthly credits used:\s*\$(?P<used>[\d,]+(?:\.\d+)?)\s+of\s+'
    r'\$(?P<limit>[\d,]+(?:\.\d+)?)["\']',
    re.IGNORECASE,
)
REFILL_PATTERN = re.compile(
    r"Refills\s+to\s+\$[\d,.]+\s+on\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
DATA_TIME_PATTERN = re.compile(r'data-time=["\'](?P<value>[^"\']+)["\']', re.IGNORECASE)
MAX_REDIRECTS = 5


def _parse_datetime(value: str) -> datetime | None:
    normalized = html.unescape(value.strip())
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _nearest_reset(document: str, marker_position: int, start: int, end: int) -> datetime | None:
    candidates: list[tuple[int, datetime]] = []
    for match in DATA_TIME_PATTERN.finditer(document, start, end):
        parsed = _parse_datetime(match.group("value"))
        if parsed is not None:
            candidates.append((abs(match.start() - marker_position), parsed))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _section_bounds(
    document: str,
    marker_position: int,
    fallback_start: int,
    fallback_end: int,
) -> tuple[int, int]:
    opening_matches = list(re.finditer(r"<section\b", document[:marker_position], re.IGNORECASE))
    closing_match = re.search(r"</section\s*>", document[marker_position:], re.IGNORECASE)
    if opening_matches and closing_match:
        start = opening_matches[-1].start()
        end = marker_position + closing_match.end()
        return start, end
    return fallback_start, fallback_end


def _find_matches(document: str) -> dict[WindowKind, tuple[float, int]]:
    found: dict[WindowKind, tuple[float, int]] = {}
    for match in ARIA_PATTERN.finditer(document):
        kind = (
            WindowKind.SESSION if match.group("label").lower() == "session" else WindowKind.WEEKLY
        )
        found.setdefault(kind, (float(match.group("percent")), match.end()))
    if len(found) == 2:
        return found

    for kind, label in ((WindowKind.SESSION, "Session"), (WindowKind.WEEKLY, "Weekly")):
        if kind in found:
            continue
        patterns = (
            re.compile(
                rf"{label}(?:\s+usage)?.{{0,180}}?"
                rf"(?P<percent>\d{{1,3}}(?:\.\d+)?)\s*%\s*used",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                rf"{label}(?:\s+usage)?.{{0,180}}?used\s*"
                rf"(?P<percent>\d{{1,3}}(?:\.\d+)?)\s*%",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        label_matches = list(re.finditer(label, document, re.IGNORECASE))
        for label_index, label_match in enumerate(label_matches):
            segment_end = (
                label_matches[label_index + 1].start()
                if label_index + 1 < len(label_matches)
                else min(len(document), label_match.start() + 4000)
            )
            segment = document[label_match.start() : segment_end]
            visible = re.sub(
                r"<script\b.*?</script>|<style\b.*?</style>",
                " ",
                segment,
                flags=re.IGNORECASE | re.DOTALL,
            )
            visible = html.unescape(re.sub(r"<[^>]+>", " ", visible))
            visible = re.sub(r"\s+", " ", visible)
            match = None
            for candidate in patterns:
                match = candidate.search(visible)
                if match:
                    break
            if match:
                found[kind] = (float(match.group("percent")), label_match.start())
                break
    return found


def _monthly_window(document: str) -> QuotaWindow | None:
    """Monthly credits used of the plan's allowance, refilling on the stated date."""

    match = MONTHLY_PATTERN.search(document)
    if match is None:
        return None
    used = float(match.group("used").replace(",", ""))
    limit = float(match.group("limit").replace(",", ""))
    if limit <= 0:
        return None
    visible = html.unescape(re.sub(r"<[^>]+>", " ", document))
    refill = REFILL_PATTERN.search(re.sub(r"\s+", " ", visible))
    reset = None
    if refill is not None:
        text = " ".join(refill.group("date").replace(",", "").split())
        for pattern in ("%B %d %Y", "%b %d %Y"):  # "November 14 2026" or "Nov 14 2026"
            try:
                day = datetime.strptime(text, pattern)
            except ValueError:
                continue
            # The page gives a date only: noon UTC keeps that calendar date in
            # every time zone from UTC-11 to UTC+11.
            reset = day.replace(hour=12, tzinfo=timezone.utc)
            break
    return QuotaWindow(WindowKind.MONTHLY, min(max(used / limit * 100, 0.0), 100.0), reset)


def parse_quota_page(document: str) -> QuotaSnapshot:
    matches = _find_matches(document)
    windows = (WindowKind.SESSION, WindowKind.WEEKLY)
    missing = [kind.value for kind in windows if kind not in matches]
    if missing:
        monthly = _monthly_window(document)
        if monthly is not None:
            return QuotaSnapshot(monthly=monthly)
        raise ScrapeError(f"Could not find Ollama quota data for: {', '.join(missing)}")

    positions = sorted((position, kind) for kind, (_, position) in matches.items())
    resets: dict[WindowKind, datetime | None] = {}
    for index, (position, kind) in enumerate(positions):
        previous_position = positions[index - 1][0] if index > 0 else 0
        next_position = positions[index + 1][0] if index + 1 < len(positions) else len(document)
        fallback_start = (previous_position + position) // 2 if index > 0 else 0
        fallback_end = (
            (position + next_position) // 2 if index + 1 < len(positions) else len(document)
        )
        section_start, section_end = _section_bounds(
            document,
            position,
            fallback_start,
            fallback_end,
        )
        resets[kind] = _nearest_reset(document, position, section_start, section_end)

    try:
        return QuotaSnapshot(
            session=QuotaWindow(
                WindowKind.SESSION,
                matches[WindowKind.SESSION][0],
                resets[WindowKind.SESSION],
            ),
            weekly=QuotaWindow(
                WindowKind.WEEKLY,
                matches[WindowKind.WEEKLY][0],
                resets[WindowKind.WEEKLY],
            ),
        )
    except ValueError as exc:
        raise ScrapeError(f"Invalid percentage in Ollama quota page: {exc}") from exc


def is_signed_out(response: httpx.Response) -> bool:
    url = str(response.url).lower()
    body = response.text.lower()
    return (
        any(part in url for part in ("/login", "/signin", "/sign-in"))
        or 'name="password"' in body
        or "sign in to ollama" in body
        or "log in to ollama" in body
    )


def _effective_port(url: httpx.URL) -> int | None:
    if url.port is not None:
        return url.port
    if url.scheme == "https":
        return 443
    if url.scheme == "http":
        return 80
    return None


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, _effective_port(url)


def _request_with_safe_redirects(
    client: httpx.Client,
    config: OllamaConfig,
    headers: dict[str, str],
) -> httpx.Response:
    original_url = httpx.URL(config.settings_url)
    original_origin = _origin(original_url)
    current_url = original_url
    for redirect_count in range(MAX_REDIRECTS + 1):
        if _origin(current_url) != original_origin:
            raise ScrapeError(
                "Ollama request attempted a cross-origin redirect; refusing to continue"
            )
        request_headers = {
            **headers,
            "Cookie": f"{config.cookie_name}={config.cookie_value}",
        }
        response = client.get(current_url, headers=request_headers, follow_redirects=False)
        if not response.is_redirect:
            return response
        if redirect_count == MAX_REDIRECTS:
            raise ScrapeError(f"Ollama settings exceeded {MAX_REDIRECTS} redirects")
        location = response.headers.get("location")
        if not location:
            raise ScrapeError("Ollama returned a redirect without a Location header")
        destination = httpx.URL(urljoin(str(current_url), location))
        if _origin(destination) != original_origin:
            raise ScrapeError(
                "Ollama request attempted a cross-origin redirect; refusing to continue"
            )
        current_url = destination
    raise ScrapeError(f"Ollama settings exceeded {MAX_REDIRECTS} redirects")


def fetch_quota(
    config: OllamaConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> QuotaSnapshot:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=config.request_timeout_seconds,
            transport=transport,
        ) as client:
            response = _request_with_safe_redirects(client, config, headers)
            if is_signed_out(response):
                raise ExpiredCookieError(
                    "Ollama session cookie is expired or invalid; sign in again "
                    "and replace only the cookie value"
                )
            response.raise_for_status()
    except (ExpiredCookieError, ScrapeError):
        raise
    except httpx.HTTPError as exc:
        raise ScrapeError(f"Failed to fetch Ollama settings: {exc}") from exc
    return parse_quota_page(response.text)
