import pytest
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
    # State is a full-width top strip so it is the first thing seen on the Deck.
    assert image.getpixel((8, 10)) == (74, 158, 255)  # the state strip, under the line


def test_session_title_wraps_to_two_lines_before_truncating() -> None:
    image = Image.new("RGB", (96, 96))
    title = _wrap_title(
        ImageDraw.Draw(image),
        "A deliberately long Section title that must fit",
        _font(11, bold=True),
        84,
    )

    first, second = title.split("\n")
    assert first and second
    # Whatever the installed font, the two lines are the title's opening words
    # in order, the second carrying on from the first before it is truncated.
    shown = " ".join(title.replace("…", " ").split())
    assert "A deliberately long Section title that must fit".startswith(shown)


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

    assert image.getpixel((10, 0)) == (226, 139, 103)  # the provider line, on top
    assert image.getpixel((10, 8)) != (226, 139, 103)  # the state strip right under it


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

    assert codex.getpixel((10, 0)) == (72, 224, 180)
    assert opencode.getpixel((10, 0)) == (138, 167, 255)
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


def _ink_columns(
    image: Image.Image, top: int, bottom: int, background: object | None = None
) -> set[int]:
    if background is None:
        background = image.getpixel((image.width // 2, image.height - 2))
    return {
        x
        for x in range(image.width)
        for y in range(top, bottom)
        if image.getpixel((x, y)) not in (background,)
    }


def test_quota_windows_keep_usage_canonical_and_show_either_mode() -> None:
    from stream_micro.control_renderer import quota_windows

    windows = quota_windows(
        {"primary_percent": 30, "primary_label": "5h", "secondary_percent": float("nan")}
    )
    assert len(windows) == 1
    window = windows[0]
    assert (window.label, window.used, window.remaining) == ("5H", 30.0, 70.0)
    assert window.shown("remaining") == 70.0
    assert window.shown("used") == 30.0


def test_quota_colour_follows_what_is_left_in_both_display_modes() -> None:
    def colours(display: str) -> set:
        image = render_quota_key(
            ButtonBinding(key_index=5, plugin="codex", component="quota"),
            PluginSnapshot(
                PluginHealth(True, "ok"), (), {"primary_percent": 95, "primary_label": "5H"}
            ),
            size=(72, 72),
            display=display,
        )
        return {color for _count, color in image.getcolors(maxcolors=72 * 72) or []}

    for display in ("remaining", "used"):
        assert (239, 68, 68) in colours(display)  # 5% left is red either way
        assert (34, 197, 94) not in colours(display)


def test_quota_rows_stay_inside_a_72px_key() -> None:
    from stream_micro.control_renderer import _quota_row

    draw = ImageDraw.Draw(Image.new("RGB", (72, 72)))
    for label in ("SESSION", "WEEK", "5H", "7D", "CLA", "GEM"):
        for value in ("0%", "9%", "99%", "100%"):
            for size in (22, 19, 16):
                fitted = _quota_row(draw, label, value, size, 1.0, 66)
                _label, _lf, label_width, _nf, number_width, gap, _size, _ls = fitted
                assert label_width + gap + number_width <= 66, (label, value, size)

    image = render_quota_key(
        ButtonBinding(key_index=5, plugin="ollama_quota", component="quota"),
        PluginSnapshot(
            PluginHealth(True, "ok"),
            (),
            {"session_percent": 0, "weekly_percent": 0},  # legacy SESSION / WEEK labels
        ),
        size=(72, 72),
    )
    edges = _ink_columns(image, 14, 60) & {0, 1, 70, 71}
    assert not edges


def test_long_session_titles_stay_inside_a_72px_key() -> None:
    for title in (
        "A deliberately long Section title that must fit somewhere",
        "非常長的中文工作階段標題必須在按鍵範圍內換行顯示",
    ):
        image = render_control_key(
            ButtonBinding(key_index=0, plugin="claude_code", component="session"),
            AgentSession("s", title, SessionState.RUNNING, "stream-micro"),
            selected=False,
            size=(72, 72),
            provider_id="claude_code",
        )
        claude_background = (37, 24, 17)
        assert not _ink_columns(image, 19, 66, claude_background) & {0, 71}


def test_footer_shows_the_reset_of_the_most_depleted_window() -> None:
    from datetime import datetime, timezone

    from stream_micro.control_renderer import _format_reset, quota_windows

    windows = quota_windows(
        {
            "primary_percent": 20,
            "primary_reset_at": "2026-10-08T05:00:00Z",
            "secondary_percent": 90,
            "secondary_reset_at": "2026-10-11T12:00:00Z",
        }
    )
    binding = min(windows, key=lambda window: window.remaining)
    assert binding.reset_at == "2026-10-11T12:00:00Z"

    now = datetime(2026, 10, 8, 9, 0).astimezone()
    today = now.replace(hour=15, minute=11).astimezone(timezone.utc).isoformat()
    friday = datetime(2026, 10, 9, 20, 25).astimezone().astimezone(timezone.utc).isoformat()
    assert _format_reset(today, now=now) == "TODAY 15:11"
    assert _format_reset(friday, now=now) == "FRI 20:25"
    assert _format_reset(None, now=now) == "RESET --"
    assert _format_reset("not a time", now=now) == "RESET --"


def test_titles_wrap_between_words_and_at_natural_breaks() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (72, 72)))
    font = _font(14, bold=True)
    from stream_micro.control_renderer import _wrap_lines

    assert _wrap_lines(draw, "小說skill", _font(17, bold=True), 66, 3)[0] == "小說\nskill"
    assert _wrap_lines(draw, "ClaudeProject 資料分析", font, 66, 3)[0].startswith(
        "Claude\nProject"
    )
    long_title = "A deliberately long Section title that must fit"
    text, truncated = _wrap_lines(draw, long_title, font, 66, 2)
    assert truncated and text.endswith("…")
    assert all(not line.startswith(" ") for line in text.split("\n"))


def test_stale_quota_is_drawn_grey_even_when_the_source_is_down() -> None:
    stale = PluginSnapshot(
        PluginHealth(False, "not reachable; showing last seen quota"),
        (),
        {"primary_percent": 0, "primary_label": "5H", "stale": True},
    )
    image = render_quota_key(
        ButtonBinding(key_index=7, plugin="codex", component="quota"), stale, size=(72, 72)
    )
    colours = {color for _count, color in image.getcolors(maxcolors=72 * 72) or []}
    assert (111, 128, 153) in colours  # grey numbers and bar
    assert (34, 197, 94) not in colours


def test_long_titles_on_error_keys_are_cut_not_clipped() -> None:
    image = render_quota_key(
        ButtonBinding(key_index=7, plugin="a_very_long_provider_name_for_quota", component="quota"),
        PluginSnapshot(PluginHealth(False, "Not reachable")),
        size=(72, 72),
    )
    assert not _ink_columns(image, 10, 24, (16, 24, 39)) & {0, 1, 70, 71}


def test_title_size_prefers_whole_words_over_a_bigger_split_word() -> None:
    from stream_micro.control_renderer import _wrap_lines

    draw = ImageDraw.Draw(Image.new("RGB", (72, 72)))
    # At 17 px "Release" alone is wider than the key; 14 px keeps it whole.
    assert _wrap_lines(draw, "Release notes", _font(17, bold=True), 66, 2, split_words=False)[1]
    image = render_control_key(
        ButtonBinding(key_index=0, plugin="claude_code", component="session"),
        AgentSession("s", "Release notes", SessionState.COMPLETED, "docs"),
        selected=False,
        size=(72, 72),
        provider_id="claude_code",
    )
    assert image.size == (72, 72)
    text, truncated = _wrap_lines(draw, "Release notes", _font(14, bold=True), 66, 3,
                                  split_words=False)
    assert not truncated and "Releas\n" not in text


def test_three_line_titles_never_run_into_the_project_line() -> None:
    background = (37, 24, 17)
    image = render_control_key(
        ButtonBinding(key_index=0, plugin="claude_code", component="session"),
        AgentSession("s", "Refactor settings page", SessionState.IDLE, "web-app"),
        selected=False,
        size=(72, 72),
        provider_id="claude_code",
    )
    # A blank row must separate the title from the project name.
    rows = [
        y for y in range(52, 60)
        if all(image.getpixel((x, y)) == background for x in range(2, 70))
    ]
    assert rows


def test_resets_a_week_or_more_away_show_their_date_and_time() -> None:
    from datetime import datetime, timezone

    from stream_micro.control_renderer import _format_reset

    now = datetime(2026, 10, 14, 9, 0).astimezone()
    next_month = datetime(2026, 11, 14, 20, 25).astimezone().astimezone(timezone.utc).isoformat()
    assert _format_reset(next_month, now=now) == "NOV 14 20:25"
    refill = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).isoformat()  # a date only
    assert _format_reset(refill, now=now, date_only=True) == "NOV 14"


def test_footer_shows_the_reset_of_a_window_that_refills() -> None:
    from stream_micro.control_renderer import QuotaWindow, _binding_window

    credit = QuotaWindow("CREDIT", 90.0, None)  # most depleted, never refills
    five_hours = QuotaWindow("5H", 60.0, "2026-10-14T12:00:00Z")
    assert _binding_window([credit, five_hours]) is five_hours
    week = QuotaWindow("WEEK", 70.0, "2026-10-18T12:00:00Z")
    assert _binding_window([five_hours, week]) is five_hours  # the first, until one is used up
    assert _binding_window([credit]) is credit


def test_the_footer_shows_5h_until_a_window_is_used_up() -> None:
    from stream_micro.control_renderer import QuotaWindow, _binding_window

    five_hours = QuotaWindow("5H", 20, "2026-10-16T08:00:00Z")  # 80% left
    week = QuotaWindow("7D", 50, "2026-10-19T00:00:00Z")  # 50% left
    month = QuotaWindow("MO", 10, "2026-11-16T00:00:00Z")
    assert _binding_window([five_hours, week, month]) is five_hours
    used_week = QuotaWindow("7D", 100, week.reset_at)
    assert _binding_window([five_hours, used_week, month]) is used_week
    used_five = QuotaWindow("5H", 100, five_hours.reset_at)
    assert _binding_window([used_five, used_week, month]) is used_week
    used_month = QuotaWindow("MO", 100, month.reset_at)
    assert _binding_window([used_five, used_week, used_month]) is used_month


def test_a_reset_in_the_past_shows_its_date_not_a_weekday() -> None:
    from datetime import datetime, timezone

    from stream_micro.control_renderer import _format_reset

    now = datetime(2026, 10, 14, 9, 0).astimezone()
    yesterday = datetime(2026, 10, 13, 15, 0).astimezone().astimezone(timezone.utc).isoformat()
    assert _format_reset(yesterday, now=now) == "OCT 13 15:00"


def test_the_legacy_ollama_alias_follows_the_display_setting() -> None:
    from stream_micro.control_renderer import render_ollama_key

    binding = ButtonBinding(key_index=4, plugin="ollama_quota", component="quota")
    snapshot = PluginSnapshot(PluginHealth(True, "Connected"), signals={"session_percent": 20.0})
    remaining = render_ollama_key(binding, snapshot, size=(72, 72))
    used = render_ollama_key(binding, snapshot, size=(72, 72), display="used")
    assert remaining.tobytes() == render_quota_key(binding, snapshot, size=(72, 72)).tobytes()
    assert used.tobytes() != remaining.tobytes()


def test_a_reset_known_only_as_a_date_shows_no_invented_time() -> None:
    from datetime import datetime, timezone

    from stream_micro.control_renderer import _format_reset, quota_windows

    now = datetime(2026, 10, 14, 9, 0).astimezone()
    thursday = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc).isoformat()  # noon UTC
    assert _format_reset(thursday, now=now, date_only=True) == "THU"
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).isoformat()
    assert _format_reset(today, now=now, date_only=True) == "TODAY"
    windows = quota_windows(
        {"primary_percent": 40, "primary_reset_at": thursday, "primary_reset_precision": "date"}
    )
    assert windows[0].date_only


def _accounts_snapshot(connected: bool = True) -> PluginSnapshot:
    from stream_micro.plugins.base import QuotaAccount

    return PluginSnapshot(
        PluginHealth(connected, "1 of 2 source(s) current"),
        accounts=(
            QuotaAccount(
                "openrouter", "OPENROUTER", "openrouter",
                {"primary_percent": 60.0, "primary_label": "LIMIT"},
            ),
            QuotaAccount("team-api", "TEAM API", "custom", None, "key rejected"),
        ),
    )


def test_quota_source_picks_the_stored_account_by_id() -> None:
    from stream_micro.control_renderer import quota_source

    def key(**params) -> ButtonBinding:
        return ButtonBinding(
            key_index=10, plugin="http_quota", component="quota", action_params=params
        )

    found = quota_source(key(account="OpenRouter"), _accounts_snapshot())
    assert found.signals is not None and found.signals["primary_percent"] == 60.0
    assert (found.title, found.style) == ("OPENROUTER", "http_quota")
    assert quota_source(key(account="team-api"), _accounts_snapshot()).problem == "key rejected"
    assert quota_source(key(account="other"), _accounts_snapshot()).problem == "Account not found"
    assert quota_source(key(), _accounts_snapshot()).problem == "Pick an account"


def test_custom_label_on_an_account_quota_key() -> None:
    binding = ButtonBinding(
        key_index=10, plugin="http_quota", component="quota",
        action_params={"account": "openrouter", "label": "My key"},
    )
    image = render_quota_key(binding, _accounts_snapshot(), size=(72, 72))
    assert image.getpixel((36, 1)) == (242, 184, 128)  # the provider's accent


def test_a_chosen_label_is_fitted_not_replaced() -> None:
    def key(label: str) -> Image.Image:
        binding = ButtonBinding(
            key_index=10, plugin="http_quota", component="quota",
            action_params={"account": "openrouter", "label": label},
        )
        return render_quota_key(binding, _accounts_snapshot(), size=(72, 72))

    # Too wide for the title row at its normal size, it is shrunk or cut,
    # never swapped for the provider's name ("USAGE").
    assert key("MY PERSONAL KEY").tobytes() != key("USAGE").tobytes()


def test_a_balance_shows_money_and_a_balance_footer() -> None:
    from stream_micro.control_renderer import quota_windows

    def money(**signals: object) -> str | None:
        return quota_windows({"primary_percent": 1, **signals})[0].amount_text

    assert money(primary_amount_left=64.0, primary_currency="CNY") == "¥64.0"
    assert money(primary_amount_left=0.4234, primary_currency="USD") == "$0.42"
    assert money(primary_amount_left=512.4) == "512"
    assert money() is None


def test_an_empty_balance_has_no_minus_sign_and_a_debt_puts_it_first() -> None:
    from stream_micro.control_renderer import _money_text

    assert _money_text(-0.0, "USD") == "$0.00"
    assert _money_text(-0.001, "CNY") == "¥0.00"
    assert _money_text(-1.234, "USD") == "-$1.23"
    assert _money_text(64.0, "CNY") == "¥64.0"


def test_custom_state_and_accent_colours_are_drawn() -> None:
    from stream_micro.control_config import ButtonBinding
    from stream_micro.control_renderer import render_control_key
    from stream_micro.plugins.base import AgentSession, SessionState

    session = AgentSession(id="s1", title="Demo", state=SessionState.COMPLETED, project="app")
    binding = ButtonBinding(
        key_index=0, plugin="claude_code", component="session", accent="#ff00ff"
    )
    image = render_control_key(
        binding,
        session,
        selected=False,
        size=(72, 72),
        state_colors={"completed": "#123456"},
    )
    assert image.getpixel((36, 0)) == (0xFF, 0x00, 0xFF)  # the accent, on top
    assert image.getpixel((2, 6)) == (0x12, 0x34, 0x56)  # the state strip under it


def test_colours_must_be_hex() -> None:
    import pytest

    from stream_micro.control_config import (
        ControlConfigError,
        control_config_data,
        default_control_config,
        parse_control_config,
    )

    data = control_config_data(default_control_config())
    data["stream_deck"]["state_colors"] = {"completed": "#00ff00"}
    data["buttons"][5]["accent"] = "#AbCdEf"
    parse_control_config(data)
    for change in (
        lambda d: d["stream_deck"].__setitem__("state_colors", {"done": "#00ff00"}),
        lambda d: d["stream_deck"].__setitem__("state_colors", {"idle": "green"}),
        lambda d: d["buttons"][5].__setitem__("accent", "#12345"),
    ):
        bad = control_config_data(default_control_config())
        change(bad)
        with pytest.raises(ControlConfigError):
            parse_control_config(bad)


def test_window_labels_are_short_and_the_same_everywhere() -> None:
    from stream_micro.control_renderer import quota_windows

    labels = [
        quota_windows({"primary_percent": 1, "primary_label": label})[0].label
        for label in ("WEEK", "WK", "MONTH", "5H", "7D")
    ]
    assert labels == ["7D", "7D", "MO", "5H", "7D"]


def test_label_and_number_sizes_do_not_depend_on_the_digits() -> None:
    from PIL import Image, ImageDraw

    from stream_micro.control_renderer import _quota_row

    draw = ImageDraw.Draw(Image.new("RGB", (72, 72)))
    short = _quota_row(draw, "5H", "80%", 19, 1.0, 66)
    full = _quota_row(draw, "5H", "100%", 19, 1.0, 66)
    assert short[7] == full[7]  # label size
    assert short[6] == full[6]  # number size


@pytest.mark.parametrize("line", [True, False])
def test_session_keys_may_drop_the_top_line_but_quota_keys_keep_it(line: bool) -> None:
    from stream_micro.control_config import ButtonBinding
    from stream_micro.control_renderer import render_control_key, render_quota_key
    from stream_micro.plugins.base import (
        AgentSession,
        PluginHealth,
        PluginSnapshot,
        SessionState,
    )

    accent = (226, 139, 103)
    session = render_control_key(
        ButtonBinding(key_index=0, plugin="claude_code", component="session"),
        AgentSession("s", "t", SessionState.RUNNING, "p"),
        selected=False,
        size=(72, 72),
        provider_id="claude_code",
        accent_line=line,
    )
    quota = render_quota_key(
        ButtonBinding(key_index=0, plugin="claude_code", component="quota"),
        PluginSnapshot(PluginHealth(True, "ok"), signals={"primary_percent": 10}),
        size=(72, 72),
    )
    assert (session.getpixel((36, 0)) == accent) is line
    assert quota.getpixel((36, 0)) == accent


def test_the_footer_colour_says_which_limit_is_used_up() -> None:
    from stream_micro.control_renderer import (
        FOOTER_COLOR,
        FOOTER_MONTH_COLOR,
        FOOTER_WEEK_COLOR,
        QuotaWindow,
        _footer_color,
    )

    reset = "2026-10-19T00:00:00Z"
    assert _footer_color(QuotaWindow("5H", 40, reset)) == FOOTER_COLOR
    assert _footer_color(QuotaWindow("5H", 100, reset)) == FOOTER_COLOR  # the usual reset
    assert _footer_color(QuotaWindow("7D", 60, reset)) == FOOTER_COLOR  # not used up
    assert _footer_color(QuotaWindow("7D", 100, reset)) == FOOTER_WEEK_COLOR
    assert _footer_color(QuotaWindow("MO", 100, reset)) == FOOTER_MONTH_COLOR
    assert _footer_color(QuotaWindow("30D", 100, reset)) == FOOTER_MONTH_COLOR
    assert _footer_color(QuotaWindow("MO", 100, None)) == FOOTER_COLOR  # no reset to show


def test_a_threshold_shows_a_window_reset_early() -> None:
    from stream_micro.control_renderer import (
        FOOTER_COLOR,
        FOOTER_WEEK_COLOR,
        QuotaWindow,
        _binding_window,
        _footer_color,
    )

    five_hours = QuotaWindow("5H", 40, "2026-10-16T08:00:00Z")  # 60% left
    week = QuotaWindow("7D", 85, "2026-10-19T00:00:00Z")  # 15% left
    month = QuotaWindow("MO", 30, "2026-11-16T00:00:00Z")  # 70% left
    assert _binding_window([five_hours, week, month]) is five_hours
    assert _binding_window([five_hours, week, month], {"7D": 20}) is week
    assert _binding_window([five_hours, week, month], {"7D": 10}) is five_hours
    low_month = QuotaWindow("MO", 90, month.reset_at)  # 10% left
    assert _binding_window([five_hours, week, low_month], {"7D": 20, "MO": 20}) is low_month
    assert _footer_color(week) == FOOTER_COLOR
    assert _footer_color(week, {"7D": 20}) == FOOTER_WEEK_COLOR


def test_thresholds_come_from_the_shared_default_then_the_service() -> None:
    from stream_micro.control_config import (
        ButtonBinding,
        ControlConfigError,
        control_config_data,
        default_control_config,
        parse_control_config,
        reset_thresholds_for,
    )

    data = control_config_data(default_control_config())
    data["stream_deck"]["reset_thresholds"] = {
        "default": {"7D": 20},
        "http_quota:openrouter": {"MO": 30, "7D": 0},
    }
    settings = parse_control_config(data).stream_deck
    claude = ButtonBinding(key_index=0, plugin="claude_code", component="quota")
    source = ButtonBinding(
        key_index=1,
        plugin="http_quota",
        component="quota",
        action_params={"account": "openrouter"},
    )
    assert reset_thresholds_for(settings, claude) == {"7D": 20}
    assert reset_thresholds_for(settings, source) == {"7D": 0, "MO": 30}
    for bad in ({"default": {"5H": 10}}, {"default": {"7D": 95}}, {"default": {"7D": "20"}}):
        broken = control_config_data(default_control_config())
        broken["stream_deck"]["reset_thresholds"] = bad
        with pytest.raises(ControlConfigError):
            parse_control_config(broken)


def test_a_long_footer_still_fits_the_key() -> None:
    from datetime import datetime, timedelta, timezone

    signals = {
        "primary_percent": 100,
        "primary_label": "MO",
        "primary_reset_at": (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
    }
    binding = ButtonBinding(key_index=0, plugin="ollama_quota", component="quota")
    image = render_quota_key(
        binding, PluginSnapshot(PluginHealth(True, "ok"), signals=signals), size=(72, 72)
    )
    footer = image.crop((0, 62, 72, 72)).convert("L")
    # Nothing drawn in the outermost pixel columns: the text was shrunk to fit.
    assert all(footer.getpixel((x, y)) < 120 for x in (0, 71) for y in range(10))


def _pool_snapshot() -> PluginSnapshot:
    from stream_micro.plugins.base import QuotaAccount

    return PluginSnapshot(
        PluginHealth(True, "3 pooled account(s)"),
        accounts=(
            QuotaAccount(
                "codex-a.json",
                "CODEX ALICE",
                "codex",
                {"primary_percent": 60.0, "primary_label": "5H"},
            ),
            QuotaAccount("claude-b.json", "CLAUDE BOB", "claude", None, "HTTP 401"),
            QuotaAccount("other.json", "OTHER", "gemini", {"primary_percent": 5.0}),
        ),
    )


def test_a_pooled_account_takes_the_look_of_its_provider() -> None:
    from stream_micro.control_renderer import quota_source

    def key(**params) -> ButtonBinding:
        return ButtonBinding(
            key_index=10, plugin="cliproxy_quota", component="quota", action_params=params
        )

    codex = quota_source(key(account="codex-a.json"), _pool_snapshot())
    assert codex.signals is not None and codex.signals["primary_percent"] == 60.0
    assert (codex.title, codex.style) == ("CODEX ALICE", "codex")
    claude = quota_source(key(account="claude-b.json"), _pool_snapshot())
    assert (claude.problem, claude.style) == ("HTTP 401", "claude_code")
    assert quota_source(key(account="other.json"), _pool_snapshot()).style == "cliproxy_quota"
    assert quota_source(key(account="nobody"), _pool_snapshot()).problem == "Account not found"
    assert quota_source(key(), _pool_snapshot()).problem == "Pick an account"


def test_pooled_quota_key_draws_the_provider_accent_not_the_proxy_grey() -> None:
    binding = ButtonBinding(
        key_index=10,
        plugin="cliproxy_quota",
        component="quota",
        action_params={"account": "codex-a.json"},
    )
    image = render_quota_key(binding, _pool_snapshot(), size=(72, 72))
    assert image.getpixel((36, 1)) == (72, 224, 180)  # the Codex accent
