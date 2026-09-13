"""Small, self-contained visual language for control-surface keys."""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from .control_config import ButtonBinding
from .plugins.base import AgentSession, PluginSnapshot, SessionState
from .renderer import _font

_STATUS_COLORS = {
    SessionState.IDLE: "#ffffff",
    SessionState.RUNNING: "#4a9eff",
    SessionState.WAITING: "#ffc15a",
    SessionState.COMPLETED: "#5af08c",
    SessionState.ERROR: "#ff7983",
    SessionState.DISCONNECTED: "#6f8099",
}

_PROVIDER_STYLES = {
    "antigravity": ("ANTIGRAVITY", "#17142c", "#6f5cff"),
    "claude_code": ("CLAUDE", "#251811", "#e28b67"),
    "codex": ("CODEX", "#0b1f1c", "#48e0b4"),
    "opencode": ("OPENCODE", "#142038", "#8aa7ff"),
    "ollama_quota": ("OLLAMA", "#181a2b", "#b69cff"),
}


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


def _wrap_title(draw: ImageDraw.ImageDraw, title: str, font, max_width: int) -> str:
    """Fit a Session title on two centred key-display lines.

    Session titles may use words or CJK text, so wrapping by individual
    characters gives both forms a predictable result on the small MK.2 key.
    """

    source = title.strip() or "Untitled session"
    lines: list[str] = []
    line = ""
    for character in source:
        candidate = f"{line}{character}"
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(line.rstrip())
            line = character.lstrip()
            if len(lines) == 2:
                break
        else:
            line = candidate
    if len(lines) < 2 and line:
        lines.append(line.rstrip())
    if not lines:
        return "Untitled"
    if len(lines) == 2 and len("".join(lines)) < len(source):
        lines[-1] = _fit_text(draw, f"{lines[-1]}...", font, max_width)
    return "\n".join(lines[:2])


def render_control_key(
    binding: ButtonBinding,
    session: AgentSession | None,
    *,
    selected: bool,
    pinned: bool = False,
    size: tuple[int, int],
    provider_id: str | None = None,
) -> Image.Image:
    provider_label, provider_background, provider_accent = _provider_style(
        provider_id or binding.plugin
    )
    background = provider_background if session else "#101827"
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((0, 0, width - 1, max(2, height // 24)), fill=provider_accent)
    draw.text(
        (width // 2, height * 0.10),
        provider_label,
        anchor="mm",
        fill=provider_accent,
        font=_font(max(6, height // 12), bold=True),
    )
    if session:
        status_color = _STATUS_COLORS.get(session.state, "#ffffff")
        title_font = _font(11, bold=True)
        project_font = _font(9)
        status_font = _font(10, bold=True)
        draw.multiline_text(
            (width // 2, height * 0.33),
            _wrap_title(draw, session.title, title_font, width - 12),
            anchor="mm",
            align="center",
            fill="#ffffff",
            font=title_font,
            spacing=0,
        )
        draw.text(
            (width // 2, height * 0.57),
            _fit_text(draw, session.project or "NO PROJECT", project_font, width - 12),
            anchor="mm",
            fill="#b9c7da",
            font=project_font,
        )
        status_top = int(height * 0.70)
        status_bottom = int(height * 0.90)
        draw.rounded_rectangle(
            (6, status_top, width - 6, status_bottom), radius=4, fill=status_color
        )
        draw.text(
            (width // 2, (status_top + status_bottom) // 2),
            session.state.value.upper(),
            anchor="mm",
            fill="#08131f",
            font=status_font,
        )
    else:
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


def _quota_color(pct: float) -> str:
    if pct >= 90:
        return "#ef4444"
    if pct >= 70:
        return "#f59e0b"
    return "#22c55e"


def _format_reset(value: str | None) -> str:
    if not value:
        return "RESET --"
    try:
        shown = datetime.fromisoformat(value).astimezone()
        return shown.strftime("RESET %a %H:%M").upper()
    except (ValueError, TypeError):
        return "RESET --"


def render_quota_key(
    binding: ButtonBinding,
    snapshot: PluginSnapshot | None,
    *,
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    provider_label, background, provider_accent = _provider_style(binding.plugin)
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)

    label = f"{provider_label} QUOTA"
    title_font = _font(max(9, height // 10), bold=True)
    draw.rectangle((0, 0, width - 1, max(2, height // 24)), fill=provider_accent)

    if snapshot is None or not snapshot.health.connected or not snapshot.signals:
        draw.text(
            (width // 2, height * 0.3),
            label,
            anchor="mm",
            align="center",
            fill=provider_accent,
            font=title_font,
        )
        detail = (snapshot.health.detail if snapshot else "No data")[:18].upper()
        draw.text(
            (width // 2, height * 0.55),
            detail,
            anchor="mm",
            align="center",
            fill="#8292aa",
            font=_font(max(7, height // 12)),
        )
        draw.text(
            (width // 2, height * 0.78),
            f"KEY {binding.key_index + 1}",
            anchor="mm",
            fill="#5a6a7a",
            font=_font(max(7, height // 13)),
        )
        return image

    signals = snapshot.signals
    primary_pct = signals.get("primary_percent", signals.get("session_percent"))
    secondary_pct = signals.get("secondary_percent", signals.get("weekly_percent"))
    primary_label = signals.get("primary_label", "SESSION")
    secondary_label = signals.get("secondary_label", "WEEK")
    primary_reset = signals.get("primary_reset_at", signals.get("session_reset_at"))

    draw.text(
        (width // 2, height * 0.12),
        label,
        anchor="mm",
        align="center",
        fill=provider_accent,
        font=title_font,
    )

    reset_font = _font(max(7, height // 12))
    reset_color = "#b9c7da"

    if primary_pct is not None:
        pct = float(primary_pct)
        color = _quota_color(pct)
        draw.text(
            (width // 2, height * 0.28),
            f"{pct:g}%",
            anchor="mm",
            align="center",
            fill=color,
            font=_font(max(16, height // 4), bold=True),
        )
        draw.text(
            (width // 2, height * 0.42),
            str(primary_label).upper(),
            anchor="mm",
            align="center",
            fill="#b9c7da",
            font=_font(max(7, height // 13), bold=True),
        )
        bar_left, bar_right = 5, width - 5
        bar_top, bar_bottom = int(height * 0.46), int(height * 0.52)
        draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), radius=2, fill="#263044")
        fill_right = bar_left + round((bar_right - bar_left) * pct / 100)
        if fill_right > bar_left:
            draw.rounded_rectangle(
                (bar_left, bar_top, fill_right, bar_bottom), radius=2, fill=color
            )
        # Primary reset time right below the bar.
        draw.text(
            (width // 2, height * 0.57),
            _format_reset(primary_reset if isinstance(primary_reset, str) else None),
            anchor="mm",
            align="center",
            fill=reset_color,
            font=reset_font,
        )

    secondary_reset = signals.get("secondary_reset_at", signals.get("weekly_reset_at"))

    if secondary_pct is not None:
        pct = float(secondary_pct)
        color = _quota_color(pct)
        draw.text(
            (width // 2, height * 0.68),
            f"{str(secondary_label).upper()} {pct:g}%",
            anchor="mm",
            align="center",
            fill=color,
            font=_font(max(9, height // 9), bold=True),
        )
        bar_left, bar_right = 5, width - 5
        bar_top, bar_bottom = int(height * 0.76), int(height * 0.82)
        draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), radius=2, fill="#263044")
        fill_right = bar_left + round((bar_right - bar_left) * pct / 100)
        if fill_right > bar_left:
            draw.rounded_rectangle(
                (bar_left, bar_top, fill_right, bar_bottom), radius=2, fill=color
            )
        # Secondary reset time right below the bar.
        draw.text(
            (width // 2, height * 0.89),
            _format_reset(secondary_reset if isinstance(secondary_reset, str) else None),
            anchor="mm",
            align="center",
            fill=reset_color,
            font=reset_font,
        )
    elif primary_pct is not None:
        # No secondary window — keep a single reset at the bottom.
        draw.text(
            (width // 2, height * 0.85),
            _format_reset(primary_reset if isinstance(primary_reset, str) else None),
            anchor="mm",
            align="center",
            fill=reset_color,
            font=reset_font,
        )
    return image


def render_ollama_key(
    binding: ButtonBinding,
    snapshot: PluginSnapshot | None,
    *,
    size: tuple[int, int],
) -> Image.Image:
    """Backward-compatible alias for the generic quota renderer."""

    return render_quota_key(binding, snapshot, size=size)
