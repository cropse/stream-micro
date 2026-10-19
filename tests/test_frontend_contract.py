from pathlib import Path

FRONTEND = Path(__file__).parents[1] / "frontend" / "src" / "main.tsx"


def test_deck_preview_only_selects_a_key_for_editing() -> None:
    """The browser dashboard configures keys; it must not invoke a Deck action."""
    source = FRONTEND.read_text(encoding="utf-8")

    assert "opencode/sessions/${encodeURIComponent(session.id)}/select" not in source
    assert "setEditingKey(index)" in source
    assert "KEY CONFIGURATION" in source


def test_dashboard_refresh_is_lightweight_and_never_dispatches_actions() -> None:
    """Configuration UI must not saturate the Bridge or execute a key action."""

    source = FRONTEND.read_text(encoding="utf-8")

    assert "refreshInFlightRef" in source
    assert 'status?include_plugins=false' in source
    assert 'plugins/snapshots' in source
    assert "sessionsForPlugin(parent.plugin)" in source
    assert 'api<Session[]>("opencode/sessions")' not in source
    assert "Test action" not in source
    assert 'api("actions"' not in source


def test_provider_and_quota_settings_have_a_dedicated_configuration_tab() -> None:
    source = FRONTEND.read_text(encoding="utf-8")

    assert 'useState<"buttons" | "configuration">("buttons")' in source
    assert 'role="tablist"' in source
    assert "Hardware, providers and quota" in source
    assert 'activeTab === "buttons" ?' in source
    assert '<OpenCodeCard config={config}' in source
    assert '<CodexCard config={config}' in source
    assert '<ClaudeCodeCard config={config}' in source
    assert '<OllamaQuotaCard config={config}' in source
    # Services in use are listed; the rest are offered by "+ Add service".
    assert "Connected services" in source
    assert "<AddServiceDialog" in source
    assert 'aria-label="Bridge configuration"' in source
