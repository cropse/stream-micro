from __future__ import annotations

import contextlib
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont

from .config import DisplayConfig
from .models import QuotaWindow


def status_color(percent: float, config: DisplayConfig) -> str:
    if percent >= config.critical_threshold:
        return config.critical_color
    if percent >= config.warning_threshold:
        return config.warning_color
    return config.healthy_color


@lru_cache(maxsize=8)
def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Load a font that can render CJK key labels before generic fallbacks.

    Pillow does not use Windows' font fallback chain, so Arial renders CJK
    labels as missing-glyph boxes even when Windows can display them elsewhere.
    """

    cjk_names = (
        (
            "NotoSansSC-VF.ttf",
            "NotoSansTC-VF.ttf",
            "msyhbd.ttc",
            "msjhbd.ttc",
            "meiryob.ttc",
        )
        if bold
        else ("NotoSansSC-VF.ttf", "NotoSansTC-VF.ttf", "msyh.ttc", "msjh.ttc", "meiryo.ttc")
    )
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts"
    cjk_candidates = [
        *(windows_fonts / name for name in cjk_names),
        *(Path("/usr/share/fonts/opentype/noto") / name for name in cjk_names),
        *(Path("/usr/share/fonts/truetype/noto") / name for name in cjk_names),
        *(Path(name) for name in cjk_names),
    ]
    for candidate in cjk_candidates:
        try:
            font = ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
        if candidate.name.endswith("-VF.ttf"):
            # Variable Noto fonts default to their Thin (100) instance, which
            # made even "bold" labels hard to read on the Deck.
            with contextlib.suppress(OSError, ValueError):
                font.set_variation_by_axes([800 if bold else 500])
        return font

    fallback_names = (
        ("arialbd.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("arial.ttf", "DejaVuSans.ttf")
    )
    for name in fallback_names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _reset_text(reset_at: datetime | None, timezone_name: str) -> str:
    if reset_at is None:
        return "RESET --"
    if timezone_name.lower() == "local":
        shown = reset_at.astimezone()
    else:
        try:
            shown = reset_at.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            shown = reset_at.astimezone()
    return shown.strftime("RESET %a %H:%M").upper()


def render_quota_key(
    quota: QuotaWindow,
    label: str,
    config: DisplayConfig,
    size: tuple[int, int] = (96, 96),
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, config.background_color)
    draw = ImageDraw.Draw(image)
    accent = status_color(quota.percent_used, config)
    padding = max(5, width // 14)
    title_font = _font(max(10, height // 8), bold=True)
    percent_font = _font(max(18, height // 3), bold=True)
    reset_font = _font(max(8, height // 11))
    draw.text((padding, padding), label, font=title_font, fill=config.text_color)
    percent_text = f"{quota.percent_used:g}%"
    bbox = draw.textbbox((0, 0), percent_text, font=percent_font)
    text_width = bbox[2] - bbox[0]
    draw.text(
        ((width - text_width) // 2, height * 0.25),
        percent_text,
        font=percent_font,
        fill=accent,
    )
    bar_left, bar_right = padding, width - padding
    bar_top, bar_bottom = int(height * 0.68), int(height * 0.77)
    draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), radius=3, fill="#263044")
    fill_right = bar_left + round((bar_right - bar_left) * quota.percent_used / 100)
    if fill_right > bar_left:
        draw.rounded_rectangle((bar_left, bar_top, fill_right, bar_bottom), radius=3, fill=accent)
    reset = _reset_text(quota.reset_at, config.reset_timezone)
    reset_bbox = draw.textbbox((0, 0), reset, font=reset_font)
    reset_width = reset_bbox[2] - reset_bbox[0]
    reset_position = (
        (width - reset_width) // 2,
        height - padding - (reset_bbox[3] - reset_bbox[1]),
    )
    draw.text(reset_position, reset, font=reset_font, fill=config.text_color)
    return image


def render_blank_key(config: DisplayConfig, size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, config.background_color)


def render_error_key(message: str, config: DisplayConfig, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, config.background_color)
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.text(
        (width // 2, height * 0.22),
        "ERROR",
        anchor="mm",
        font=_font(max(11, height // 7), bold=True),
        fill=config.error_color,
    )
    compact = message[:18].upper()
    draw.multiline_text(
        (width // 2, height * 0.58),
        compact,
        anchor="mm",
        align="center",
        font=_font(max(8, height // 11)),
        fill=config.text_color,
    )
    return image
