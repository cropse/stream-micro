from PIL import Image, ImageDraw

from stream_micro.control_config import ButtonBinding
from stream_micro.control_renderer import _font, _wrap_title, render_control_key, render_quota_key
from stream_micro.plugins.base import AgentSession, PluginHealth, PluginSnapshot, SessionState


def test_control_font_renders_cjk_glyphs_instead_of_missing_glyph_boxes() -> None:
    """A Chinese Session title must not collapse into the font's missing glyph."""

    font = _font(16)
    chinese = Image.new("RGB", (64, 32))
    missing = Image.new("RGB", (64, 32))
    ImageDraw.Draw(chinese).text((0, 0), "中文", fill="white", font=font)
    ImageDraw.Draw(missing).text((0, 0), "\ufffd\ufffd", fill="white", font=font)

    assert chinese.tobytes() != missing.tobytes()


def test_session_key_renders_name_project_folder_and_state() -> None:
    image = render_control_key(
        ButtonBinding(key_index=0, plugin="opencode", component="session", action="select_session"),
        AgentSession(
            "session-1",
            "A deliberately long Section title that must fit",
            SessionState.RUNNING,
            "stream-micro",
        ),
        selected=False,
        size=(96, 96),
    )

    # The three label bands must contain drawn pixels rather than one clipped
    # title line.  Exact glyph comparison would make this font-dependent.
    background = image.getpixel((48, 48))
    assert any(image.getpixel((x, y)) != background for y in range(18, 36) for x in range(8, 88))
    assert any(image.getpixel((x, y)) != background for y in range(40, 58) for x in range(8, 88))
    assert any(image.getpixel((x, y)) != background for y in range(62, 82) for x in range(8, 88))
    assert all(
        image.getpixel((x, y)) != (95, 231, 165)
        for x in range(image.width)
        for y in range(image.height)
    )  # Legacy unbound green frame.
    # State is a full-width, bright badge so it stays readable on the physical Deck.
    assert image.getpixel((8, 72)) == (74, 158, 255)


def test_session_title_wraps_to_two_lines_before_truncating() -> None:
    image = Image.new("RGB", (96, 96))
    title = _wrap_title(
        ImageDraw.Draw(image),
        "A deliberately long Section title that must fit",
        _font(11, bold=True),
        84,
    )

    assert title.count("\n") == 1
    assert "Section" in title


def test_claude_code_session_uses_its_provider_accent() -> None:
    image = render_control_key(
        ButtonBinding(
            key_index=0,
            plugin="claude_code",
            component="session",
            action="select_session",
        ),
        AgentSession("claude-1", "Claude task", SessionState.RUNNING, "stream-micro"),
        selected=False,
        size=(96, 96),
        provider_id="claude_code",
    )

    assert image.getpixel((10, 1)) == (226, 139, 103)


def test_antigravity_session_uses_its_provider_accent() -> None:
    image = render_control_key(
        ButtonBinding(
            key_index=0,
            plugin="antigravity",
            component="session",
            action="select_session",
        ),
        AgentSession("session-1", "Antigravity task", SessionState.RUNNING, "project"),
        selected=False,
        size=(96, 96),
        provider_id="antigravity",
    )

    assert image.getpixel((10, 1)) == (111, 92, 255)


def test_codex_and_opencode_session_keys_have_distinct_provider_identity() -> None:
    session = AgentSession("thread-1", "Provider-specific chat", SessionState.IDLE, "repo")
    codex = render_control_key(
        ButtonBinding(key_index=0, plugin="codex", component="session"),
        session,
        selected=False,
        size=(96, 96),
    )
    opencode = render_control_key(
        ButtonBinding(key_index=1, plugin="opencode", component="session"),
        session,
        selected=False,
        size=(96, 96),
    )

    assert codex.getpixel((10, 1)) == (72, 224, 180)
    assert opencode.getpixel((10, 1)) == (138, 167, 255)
    assert codex.tobytes() != opencode.tobytes()


def test_pinned_session_key_has_orange_frame() -> None:
    image = render_control_key(
        ButtonBinding(key_index=0, plugin="opencode", component="session"),
        AgentSession("session-1", "Pinned", SessionState.IDLE, "repo"),
        selected=False,
        pinned=True,
        size=(96, 96),
    )

    assert image.getpixel((1, 48)) == (245, 158, 11)


def test_quota_key_renders_generic_primary_and_secondary_windows() -> None:
    image = render_quota_key(
        ButtonBinding(key_index=5, plugin="codex", component="quota"),
        PluginSnapshot(
            PluginHealth(True, "Connected"),
            signals={
                "primary_percent": 25.0,
                "primary_label": "5H",
                "primary_reset_at": "2024-11-07T00:00:00+00:00",
                "secondary_percent": 80.0,
                "secondary_label": "WEEK",
                "secondary_reset_at": "2024-11-14T00:00:00+00:00",
            },
        ),
        size=(96, 96),
    )

    # Primary bar spans y≈44..49, secondary bar y≈73..78 on a 96px key.
    assert image.getpixel((6, 47)) == (34, 197, 94)
    assert image.getpixel((6, 76)) == (245, 158, 11)
