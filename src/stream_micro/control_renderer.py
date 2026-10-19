"""Small, self-contained visual language for control-surface keys."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from .control_config import ButtonBinding
from .plugins.base import AgentSession, PluginSnapshot, QuotaAccount, SessionState
from .renderer import _font
from .timeparse import parse_timestamp

_STATUS_COLORS = {
    SessionState.IDLE: "#166534",
    SessionState.RUNNING: "#4a9eff",
    SessionState.BACKGROUND: "#b794ff",
    SessionState.WAITING: "#ffc15a",
    SessionState.COMPLETED: "#ffffff",
    SessionState.ERROR: "#ff7983",
    SessionState.DISCONNECTED: "#166534",
}
# Bright strips mean "look at me"; the quiet states share a dark green strip
# (told apart by their label), so their label needs light text.
_DARK_STATUS = frozenset({SessionState.IDLE, SessionState.DISCONNECTED})
_SHORT_STATE = {
    SessionState.IDLE: "IDLE",
    SessionState.RUNNING: "RUN",
    SessionState.BACKGROUND: "SUB",
    SessionState.WAITING: "WAIT",
    SessionState.COMPLETED: "DONE",
    SessionState.ERROR: "ERR",
    SessionState.DISCONNECTED: "OFF",
}

_PROVIDER_STYLES = {
    "claude_code": ("CLAUDE", "#251811", "#e28b67"),
    "codex": ("CODEX", "#0b1f1c", "#48e0b4"),
    "opencode": ("OPENCODE", "#142038", "#8aa7ff"),
    "ollama_quota": ("OLLAMA", "#181a2b", "#b69cff"),
    "http_quota": ("USAGE", "#14181f", "#f2b880"),
}

QUOTA_DISPLAY_MODES = ("remaining", "used")
_STALE_COLOR = "#6f8099"


def _provider_style(plugin_id: str | None) -> tuple[str, str, str]:
    if plugin_id in _PROVIDER_STYLES:
        return _PROVIDER_STYLES[plugin_id]
    label = (plugin_id or "DECK").replace("_", " ").upper()
    return label, "#101827", "#8292aa"


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Fit a single line without letting Stream Deck labels run off the key."""

    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    ellipsis = "…"
    clipped = text
    while clipped and draw.textbbox((0, 0), f"{clipped}{ellipsis}", font=font)[2] > max_width:
        clipped = clipped[:-1]
    return f"{clipped}{ellipsis}" if clipped else ellipsis


_CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")


def _tokens(text: str) -> list[str]:
    """Split into wrap units: Latin words stay whole, CJK characters stand alone."""

    tokens: list[str] = []
    word = ""
    for character in text:
        if character.isspace() or _CJK.match(character):
            if word:
                tokens.append(word)
                word = ""
            tokens.append(" " if character.isspace() else character)
        else:
            word += character
    if word:
        tokens.append(word)
    return tokens


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    title: str,
    font,
    max_width: int,
    max_lines: int,
    *,
    split_words: bool = True,
) -> tuple[str, bool]:
    """Wrap a title onto at most ``max_lines`` lines; report whether text was cut.

    Latin words move to the next line whole and CJK text breaks between any two
    characters; only a single word wider than the key is split, and with
    ``split_words=False`` such a word counts as not fitting instead.
    """

    def fits(text: str) -> bool:
        return draw.textbbox((0, 0), text, font=font)[2] <= max_width

    source = " ".join(title.split()) or "Untitled session"
    lines: list[str] = []
    line = ""
    for token in _tokens(source):
        if fits(line + token):
            line += token
            continue
        if line.strip():
            lines.append(line.rstrip())
        line = token.lstrip()
        if line and not fits(line) and not split_words:
            return "", True
        while line and not fits(line):
            cut = len(line) - 1
            while cut > 1 and not fits(line[:cut]):
                cut -= 1
            # Prefer a natural break inside the word: "Claude|Project", "stream-|micro".
            natural = [
                index
                for index in range(3, cut + 1)
                if (line[index].isupper() and line[index - 1].islower())
                or line[index - 1] in "-_./"
            ]
            cut = natural[-1] if natural else cut
            lines.append(line[:cut])
            line = line[cut:]
    if line.strip():
        lines.append(line.rstrip())
    lines = lines or ["Untitled"]
    truncated = len(lines) > max_lines
    lines = lines[:max_lines]
    if truncated:
        lines[-1] = _fit_text(draw, f"{lines[-1]}…", font, max_width)
    return "\n".join(lines), truncated


def _wrap_title(
    draw: ImageDraw.ImageDraw, title: str, font, max_width: int, max_lines: int = 2
) -> str:
    """Fit a Session title on centred key-display lines."""

    text, _truncated = _wrap_lines(draw, title, font, max_width, max_lines)
    return text


# Largest title that fits wins; the last option truncates with an ellipsis.
_TITLE_SIZES = ((17, 2), (14, 3), (12, 3))


def render_control_key(
    binding: ButtonBinding,
    session: AgentSession | None,
    *,
    selected: bool,
    pinned: bool = False,
    size: tuple[int, int],
    provider_id: str | None = None,
) -> Image.Image:
    _provider_label, provider_background, provider_accent = _provider_style(
        provider_id or binding.plugin
    )
    background = provider_background if session else "#101827"
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    width, height = size
    scale = height / 72
    accent = max(2, height // 24)
    if session:
        # The provider's line stays along the top edge as on every other key;
        # the state strip sits right under it, where it is seen first.
        draw.rectangle((0, 0, width - 1, accent), fill=provider_accent)
        status_top = accent + 1
        status_bottom = status_top + round(height * 0.19) - 1
        draw.rectangle(
            (0, status_top, width - 1, status_bottom),
            fill=_STATUS_COLORS.get(session.state, "#ffffff"),
        )
        draw.text(
            (width // 2, (status_top + status_bottom) // 2 + 1),
            _SHORT_STATE.get(session.state, session.state.value.upper()),
            anchor="mm",
            fill="#dcfce7" if session.state in _DARK_STATUS else "#08131f",
            font=_font(round(11 * scale), bold=True),
        )

        title_font, title_text = None, ""
        project_y = round(height * 0.87)
        # The title fills the space between the state strip and the project
        # line, leaving a blank row above the project name.
        title_top, title_bottom = status_bottom + 1, project_y - round(7 * scale)
        title_height = title_bottom - title_top

        def fits_height(text: str, font) -> bool:
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=0)
            return box[3] - box[1] <= title_height

        # The largest size that fits (width and height) without splitting a
        # word wins; if none does, the smallest size splits long words.
        for size_px, lines in _TITLE_SIZES:
            title_font = _font(round(size_px * scale), bold=True)
            title_text, truncated = _wrap_lines(
                draw, session.title, title_font, width - 6, lines, split_words=False
            )
            if not truncated and fits_height(title_text, title_font):
                break
        else:
            title_text, _truncated = _wrap_lines(
                draw, session.title, title_font, width - 6, _TITLE_SIZES[-1][1]
            )
        draw.multiline_text(
            (width // 2, (title_top + title_bottom) // 2),
            title_text,
            anchor="mm",
            align="center",
            fill="#ffffff",
            font=title_font,
            spacing=0,
        )
        project_font = _font(round(9 * scale))
        draw.text(
            (width // 2, project_y),
            _fit_text(draw, session.project or "NO PROJECT", project_font, width - 6),
            anchor="mm",
            fill="#b9c7da",
            font=project_font,
        )
    else:
        draw.rectangle((0, 0, width - 1, accent), fill=provider_accent)
        label = (binding.component or "empty").replace("_", " ").upper()
        draw.text(
            (width // 2, height * 0.46),
            label,
            anchor="mm",
            align="center",
            fill="#dce6f6",
            font=_font(10, bold=True),
        )
        draw.text(
            (width // 2, height * 0.68),
            f"KEY {binding.key_index + 1}",
            anchor="mm",
            fill="#8292aa",
            font=_font(8),
        )
    if pinned:
        draw.rectangle((0, 0, width - 1, height - 1), outline="#f59e0b", width=3)
    if selected:
        inset = 3 if pinned else 0
        draw.rectangle(
            (inset, inset, width - 1 - inset, height - 1 - inset),
            outline="#ffffff",
            width=2,
        )
    return image


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window ready for display."""

    label: str
    used: float
    reset_at: str | None
    # Money left (for balances), shown instead of the percentage.
    amount_text: str | None = None
    # The source knows only the day it resets (e.g. Ollama's refill date).
    date_only: bool = False

    @property
    def remaining(self) -> float:
        return 100 - self.used

    def shown(self, display: str) -> float:
        return self.used if display == "used" else self.remaining


def quota_windows(signals: Mapping[str, object]) -> list[QuotaWindow]:
    """Normalise provider quota signals; percentages stay canonical as *used*."""

    windows: list[QuotaWindow] = []
    for prefix, fallback, legacy in (
        ("primary", "SESSION", "session"),
        ("secondary", "WEEK", "weekly"),
    ):
        value = signals.get(f"{prefix}_percent", signals.get(f"{legacy}_percent"))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        reset = signals.get(f"{prefix}_reset_at", signals.get(f"{legacy}_reset_at"))
        windows.append(
            QuotaWindow(
                str(signals.get(f"{prefix}_label") or fallback).upper(),
                float(min(max(value, 0), 100)),
                reset if isinstance(reset, str) and reset else None,
                _money_text(
                    signals.get(f"{prefix}_amount_left"), signals.get(f"{prefix}_currency")
                ),
                date_only=signals.get(f"{prefix}_reset_precision") == "date",
            )
        )
    return windows


_CURRENCY_SIGNS = {"CNY": "¥", "USD": "$", "EUR": "€", "JPY": "¥"}


def _money_text(amount: object, currency: object) -> str | None:
    """A balance short enough for a key: ``¥64``, ``$9.5``, ``$0.42``."""

    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if amount != amount or amount in (float("inf"), float("-inf")):
        return None
    sign = _CURRENCY_SIGNS.get(currency, "") if isinstance(currency, str) else ""
    size = abs(amount)
    digits = 0 if size >= 100 else 1 if size >= 10 else 2
    text = f"{size:.{digits}f}"
    # A debt reads -$1.23; anything that rounds to zero is just $0.00.
    minus = "-" if amount < 0 and float(text) != 0 else ""
    return f"{minus}{sign}{text}"


def select_account(snapshot: PluginSnapshot, key: str) -> QuotaAccount | None:
    """The account a quota key stores, by id."""

    wanted = key.strip().lower()
    return next((account for account in snapshot.accounts if account.id.lower() == wanted), None)


@dataclass(frozen=True)
class QuotaSource:
    """What one quota key draws: signals, or why there are none."""

    signals: Mapping[str, object] | None
    problem: str | None
    title: str | None
    style: str | None


def quota_source(binding: ButtonBinding, snapshot: PluginSnapshot | None) -> QuotaSource:
    """Pick the signals for a quota key, choosing an account when the source has several."""

    if snapshot is None:
        return QuotaSource(None, "No data", None, binding.plugin)
    account_key = binding.action_params.get("account") if binding.action_params else None
    if snapshot.accounts or account_key:
        if not isinstance(account_key, str) or not account_key:
            return QuotaSource(None, "Pick an account", None, binding.plugin)
        account = select_account(snapshot, account_key)
        if account is None:
            connected = snapshot.health.connected
            detail = "Account not found" if connected else snapshot.health.detail
            return QuotaSource(None, detail, None, binding.plugin)
        if not account.signals:
            problem = account.error or "No quota data"
            return QuotaSource(None, problem, account.title, binding.plugin)
        return QuotaSource(account.signals, None, account.title, binding.plugin)
    signals = snapshot.signals
    if not snapshot.health.connected and not (signals and signals.get("stale")):
        return QuotaSource(None, snapshot.health.detail, None, binding.plugin)
    return QuotaSource(signals, None, None, binding.plugin)


def _quota_color(remaining: float) -> str:
    """Colour by how much is left, whichever number the key shows."""

    if remaining <= 10:
        return "#ef4444"
    if remaining <= 30:
        return "#f59e0b"
    return "#22c55e"


def _binding_window(windows: list[QuotaWindow]) -> QuotaWindow:
    """The window whose reset the footer shows.

    Normally the first window (the shortest: 5H before 7D before MO). Once a
    window is used up, that one, since nothing works until it refills; of
    several used up, the one that refills last (MO, then 7D, then 5H). A
    window without a reset time (a credit balance) would only show ``RESET --``.
    """

    timed = [window for window in windows if window.reset_at]
    if not timed:
        return windows[0]
    spent = [window for window in timed if round(window.remaining) <= 0]
    if spent:
        return max(spent, key=lambda window: _reset_order(window.reset_at))
    return timed[0]


def _reset_order(value: str | None) -> float:
    parsed = parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else 0.0


def _format_reset(
    value: str | None, *, now: datetime | None = None, date_only: bool = False
) -> str:
    """When the window refills: ``TODAY 15:11``, ``THU 20:25`` this week, ``NOV 14`` later.

    A reset known only as a date shows no time (``TODAY``, ``THU``): the
    stored time of day is a placeholder, not when it happens.
    """

    parsed = parse_timestamp(value)
    if parsed is None:
        return "RESET --"
    # A date-only reset is a calendar day, kept at noon UTC: read it in UTC.
    shown = parsed.astimezone(timezone.utc) if date_only else parsed.astimezone()
    today = (now or datetime.now().astimezone()).date()
    if shown.date() == today:
        return "TODAY" if date_only else shown.strftime("TODAY %H:%M")
    days = (shown.date() - today).days
    if days < 0 or days >= 6:  # a weekday name would be ambiguous (or look ahead)
        return shown.strftime("%b %d").upper()
    return shown.strftime("%a" if date_only else "%a %H:%M").upper()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, size: int, minimum: int, max_width: int):
    """Shrink a bold label until it fits, so short key titles are not truncated."""

    font = _font(size, bold=True)
    while size > minimum and draw.textbbox((0, 0), text, font=font)[2] > max_width:
        size -= 1
        font = _font(size, bold=True)
    return font


def _quota_row(
    draw: ImageDraw.ImageDraw,
    window_label: str,
    number: str,
    number_size: int,
    scale: float,
    max_width: int,
):
    """Fit ``<label> <number>`` on one row: shrink the label, then the number.

    The big number matters most, so the window label gives way first; a label
    that still does not fit is truncated rather than drawn off the key.
    """

    gap = max(2, round(3 * scale))
    label_size = round(10 * scale)
    minimum_label = max(6, round(7 * scale))
    minimum_number = max(9, round(12 * scale))

    def widths(label_font, number_font) -> tuple[int, int]:
        return (
            round(draw.textlength(window_label, font=label_font)),
            round(draw.textlength(number, font=number_font)),
        )

    label_font = _font(label_size, bold=True)
    number_font = _font(number_size, bold=True)
    label_width, number_width = widths(label_font, number_font)
    while label_width + gap + number_width > max_width and label_size > minimum_label:
        label_size -= 1
        label_font = _font(label_size, bold=True)
        label_width, number_width = widths(label_font, number_font)
    while label_width + gap + number_width > max_width and number_size > minimum_number:
        number_size -= 1
        number_font = _font(number_size, bold=True)
        label_width, number_width = widths(label_font, number_font)
    if label_width + gap + number_width > max_width:
        window_label = _fit_text(draw, window_label, label_font, max_width - gap - number_width)
        label_width = round(draw.textlength(window_label, font=label_font))
    return window_label, label_font, label_width, number_font, number_width, gap, number_size


def render_quota_key(
    binding: ButtonBinding,
    snapshot: PluginSnapshot | None,
    *,
    size: tuple[int, int],
    display: str = "remaining",
) -> Image.Image:
    """Draw up to two quota windows: big number, bar, and when it resets.

    ``display`` chooses whether the number and bar show what is ``remaining``
    (default; a full green bar means plenty left) or what is ``used``.
    """

    width, height = size
    scale = height / 72
    source = quota_source(binding, snapshot)
    provider_label, background, provider_accent = _provider_style(source.style)
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)

    custom_label = binding.action_params.get("label") if binding.action_params else None
    if isinstance(custom_label, str) and custom_label.strip():
        label = custom_label.strip().upper()
    else:
        label = source.title or f"{provider_label} QUOTA"
    title_size = max(9, round(10 * scale))
    default_label = label == f"{provider_label} QUOTA"
    if default_label and draw.textlength(label, font=_font(max(6, round(7 * scale)), bold=True)) > (
        width - 4
    ):
        # e.g. "OPENCODE" rather than "OPENCODE Q…"; a chosen label is kept and fitted.
        label = provider_label
    draw.rectangle((0, 0, width - 1, max(2, height // 24)), fill=provider_accent)
    windows = quota_windows(source.signals or {})
    # Last-seen numbers whose source is unreachable are drawn grey, so they are
    # never mistaken for current quota.
    stale = bool(source.signals and source.signals.get("stale"))

    if source.problem is not None or not windows:
        error_title_font = _fit_font(draw, label, title_size, 7, width - 4)
        draw.text(
            (width // 2, height * 0.22),
            _fit_text(draw, label, error_title_font, width - 4),
            anchor="mm",
            align="center",
            fill=provider_accent,
            font=error_title_font,
        )
        detail_font = _font(max(8, round(9 * scale)), bold=True)
        message = source.problem or "No quota data"
        detail, _cut = _wrap_lines(draw, message.upper(), detail_font, width - 6, 3)
        draw.multiline_text(
            (width // 2, height * 0.57),
            detail,
            anchor="mm",
            align="center",
            fill="#dce6f6",
            font=detail_font,
            spacing=1,
        )
        return image

    title_font = _fit_font(draw, label, title_size, round(7 * scale), width - 4)
    draw.text(
        (width // 2, round(9 * scale)),
        _fit_text(draw, label, title_font, width - 4),
        anchor="mm",
        fill=_STALE_COLOR if stale else provider_accent,
        font=title_font,
    )

    # One window gets the whole key; two windows split it with the first larger.
    rows = (
        [(round(22 * scale), round(31 * scale))]
        if len(windows) == 1
        else [(round(19 * scale), round(24 * scale)), (round(16 * scale), round(46 * scale))]
    )
    for window, (size_px, center_y) in zip(windows, rows, strict=False):
        shown = window.shown(display)
        color = _STALE_COLOR if stale else _quota_color(window.remaining)
        number = window.amount_text or f"{shown:.0f}%"
        (
            window_label,
            label_font,
            label_width,
            number_font,
            number_width,
            gap,
            size_px,
        ) = _quota_row(draw, window.label, number, size_px, scale, width - 6)
        left = (width - label_width - gap - number_width) // 2
        draw.text((left, center_y), window_label, anchor="lm", fill="#dce6f6", font=label_font)
        draw.text(
            (left + label_width + gap, center_y),
            number,
            anchor="lm",
            fill=color,
            font=number_font,
        )
        bar_top = center_y + size_px // 2 + round(2 * scale)
        bar_bottom = bar_top + max(2, round(3 * scale))
        draw.rectangle((4, bar_top, width - 5, bar_bottom), fill="#263044")
        fill_right = 4 + round((width - 9) * shown / 100)
        if fill_right > 4:
            draw.rectangle((4, bar_top, fill_right, bar_bottom), fill=color)

    binding_window = _binding_window(windows)
    footer = (
        "BALANCE"
        if binding_window.amount_text and not binding_window.reset_at
        else _format_reset(binding_window.reset_at, date_only=binding_window.date_only)
    )
    draw.text(
        (width // 2, height - round(6 * scale)),
        footer,
        anchor="mm",
        fill="#dce6f6",
        font=_font(round(10 * scale), bold=True),
    )
    return image


def render_ollama_key(
    binding: ButtonBinding,
    snapshot: PluginSnapshot | None,
    *,
    size: tuple[int, int],
    display: str = "remaining",
) -> Image.Image:
    """Backward-compatible alias for the generic quota renderer."""

    return render_quota_key(binding, snapshot, size=size, display=display)
