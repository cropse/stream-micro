from pathlib import Path
from typing import cast

from stream_micro.bridge import BridgeService
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)
from stream_micro.deck_bridge import Mk2BridgeRunner
from stream_micro.device import Mk2DeckOutput
from stream_micro.plugins.base import (
    AgentSession,
    BridgePlugin,
    PluginHealth,
    PluginSnapshot,
    SessionState,
)


class SnapshotPlugin:
    def __init__(self, snapshot: PluginSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> PluginSnapshot:
        return self._snapshot


class RecordingOutput:
    key_size = (96, 96)

    def __init__(self) -> None:
        self.brightness: list[int] = []
        self.images = {}

    def set_brightness(self, brightness: int) -> None:
        self.brightness.append(brightness)

    def set_key(self, key_index: int, image) -> None:
        self.images[key_index] = image


class RecordingService(BridgeService):
    def __init__(self, path: Path) -> None:
        super().__init__(path, plugins={})
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.captured: list[str] = []
        self.focused: list[str] = []
        self.gui_selected: list[AgentSession] = []
        self.codex_selected: list[str] = []

    def invoke(self, plugin_id: str, action: str, params: dict[str, str]):
        self.calls.append((plugin_id, action, params))
        if action == "select_session":
            session_id = params["session_id"]
            if plugin_id == "opencode":
                session = next(
                    item for item in self._sessions_for_plugin(plugin_id) if item.id == session_id
                )
                self._select_openchamber_session(session)
                return {
                    "result": "session_selected_in_openchamber",
                    "session_id": session_id,
                }
            if plugin_id == "codex":
                self.codex_selected.append(session_id)
                return {"result": "session_selected_in_codex", "session_id": session_id}
        return {"result": "invoked"}

    def capture_session_window(self, session_id: str):
        self.captured.append(session_id)
        return {"executable": "x", "window_class": "y", "title_pattern": "z"}

    def focus_session_window(self, session_id: str) -> None:
        self.focused.append(session_id)

    def select_openchamber_session(self, session: AgentSession) -> None:
        self.gui_selected.append(session)


def test_session_key_ignores_legacy_window_binding(tmp_path: Path) -> None:
    path = tmp_path / "bridge.yaml"
    config = default_control_config()
    data = {
        **control_config_data(config),
        "session_window_bindings": {
            "session-1": {"executable": "x", "window_class": "y", "title_pattern": "z"}
        },
    }
    write_control_config(path, parse_control_config(data))
    service = RecordingService(path)
    runner = Mk2BridgeRunner(service)
    runner.sessions = (AgentSession("session-1", "Test", SessionState.IDLE),)
    service.active_sessions = runner.sessions

    runner.press(0)

    assert [session.id for session in service.gui_selected] == ["session-1"]
    assert service.focused == []


def test_session_key_selects_the_matching_openchamber_session(tmp_path: Path) -> None:
    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    service = RecordingService(path)
    runner = Mk2BridgeRunner(service)
    runner.sessions = (AgentSession("session-1", "Test", SessionState.IDLE),)
    service.active_sessions = runner.sessions

    result = service.press_key(0)

    assert result == {"result": "session_selected_in_openchamber", "session_id": "session-1"}
    assert [session.id for session in service.gui_selected] == ["session-1"]
    assert service.calls == [("opencode", "select_session", {"session_id": "session-1"})]
    assert service.captured == []


def test_span_child_key_selects_its_session_slot(tmp_path: Path) -> None:
    """A child key inside a span zone selects the Session at its slot offset."""

    path = tmp_path / "bridge.yaml"
    data = control_config_data(default_control_config())
    # Key 0 is span=5 session zone; press key 3 → slot 3 → session-4
    write_control_config(path, parse_control_config(data))
    service = RecordingService(path)
    service.active_sessions = tuple(
        AgentSession(f"session-{index}", f"Test {index}", SessionState.IDLE)
        for index in range(1, 6)
    )

    result = service.press_key(3)

    assert result == {"result": "session_selected_in_openchamber", "session_id": "session-4"}
    assert [session.id for session in service.gui_selected] == ["session-4"]
    assert service.calls == [("opencode", "select_session", {"session_id": "session-4"})]


def test_long_press_pins_session_to_slot_while_other_slots_reorder(tmp_path: Path) -> None:
    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    service = RecordingService(path)
    first = tuple(
        AgentSession(f"session-{index}", f"Test {index}", SessionState.IDLE)
        for index in range(1, 5)
    )
    service.active_sessions = first

    assert service.toggle_session_pin(1) == {
        "result": "session_pinned",
        "key_index": 1,
        "session_id": "session-2",
    }

    service.active_sessions = (
        AgentSession("session-4", "Test 4", SessionState.RUNNING),
        AgentSession("session-3", "Test 3", SessionState.IDLE),
        AgentSession("session-2", "Test 2 updated", SessionState.WAITING),
        AgentSession("session-1", "Test 1", SessionState.IDLE),
    )
    service.set_active_snapshots(
        {
            "opencode": PluginSnapshot(
                PluginHealth(True, "Connected"), service.active_sessions
            )
        }
    )

    resolved = [service.session_for_key(index) for index in range(4)]
    assert all(session is not None for session in resolved)
    assert [session.id for session in resolved if session is not None] == [
        "session-4",
        "session-2",
        "session-3",
        "session-1",
    ]
    pinned_session = service.session_for_key(1)
    assert pinned_session is not None
    assert pinned_session.title == "Test 2 updated"
    assert service.status_data(include_plugins=False)["pinned_sessions"]["1"]["id"] == (
        "session-2"
    )

    assert service.toggle_session_pin(1)["result"] == "session_unpinned"
    unpinned_session = service.session_for_key(1)
    assert unpinned_session is not None
    assert unpinned_session.id == "session-3"


def test_physical_release_dispatches_short_press_or_long_press_pin(tmp_path: Path) -> None:
    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    service = RecordingService(path)
    service.active_sessions = (
        AgentSession("session-1", "First", SessionState.IDLE),
        AgentSession("session-2", "Second", SessionState.IDLE),
    )
    now = [10.0]
    runner = Mk2BridgeRunner(service, clock=lambda: now[0])

    runner._on_key(0, True)
    now[0] = 10.2
    runner._on_key(0, False)

    assert service.calls == [("opencode", "select_session", {"session_id": "session-1"})]
    assert not service.is_key_pinned(0)

    runner._on_key(1, True)
    now[0] = 12.3
    runner._on_key(1, False)

    assert len(service.calls) == 1
    assert service.is_key_pinned(1)

    runner._on_key(1, True)
    now[0] = 14.4
    runner._on_key(1, False)

    assert not service.is_key_pinned(1)


def test_standalone_session_key_on_key_6(tmp_path: Path) -> None:
    """A span=1 session key placed on key 6 selects the first available Session."""

    path = tmp_path / "bridge.yaml"
    data = control_config_data(default_control_config())
    data["buttons"][6].update(
        {"plugin": "opencode", "component": "session", "span": 1, "action": "select_session"}
    )
    write_control_config(path, parse_control_config(data))
    service = RecordingService(path)
    service.active_sessions = tuple(
        AgentSession(f"session-{index}", f"Test {index}", SessionState.IDLE)
        for index in range(1, 8)
    )

    result = service.press_key(6)

    assert result == {"result": "session_selected_in_openchamber", "session_id": "session-1"}
    assert [session.id for session in service.gui_selected] == ["session-1"]


def test_codex_quota_component_renders_from_the_codex_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "bridge.yaml"
    data = control_config_data(default_control_config())
    data["buttons"][5].update({"plugin": "codex", "component": "quota"})
    write_control_config(path, parse_control_config(data))
    service = RecordingService(path)
    service.plugins["codex"] = cast(
        BridgePlugin,
        SnapshotPlugin(
            PluginSnapshot(
                PluginHealth(True, "Connected"),
                signals={"primary_percent": 25.0, "primary_label": "5H"},
            )
        ),
    )
    output = RecordingOutput()

    Mk2BridgeRunner(service).refresh(cast(Mk2DeckOutput, output))

    assert output.brightness == [40]
    quota_image = output.images[5]
    assert any(
        quota_image.getpixel((x, y)) == (34, 197, 94)
        for x in range(quota_image.width)
        for y in range(quota_image.height)
    )  # Plenty remaining renders in green.


def test_codex_session_zone_uses_codex_sessions_and_codex_deep_link_action(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridge.yaml"
    data = control_config_data(default_control_config())
    data["plugins"]["codex"]["enabled"] = True
    data["buttons"][5].update(
        {
            "plugin": "codex",
            "component": "session",
            "span": 2,
            "action": "select_session",
        }
    )
    write_control_config(path, parse_control_config(data))
    service = RecordingService(path)
    service.plugins = {
        "opencode": cast(
            BridgePlugin,
            SnapshotPlugin(
                PluginSnapshot(
                    PluginHealth(True, "Connected"),
                    sessions=(AgentSession("open-1", "OpenCode chat", SessionState.IDLE),),
                )
            ),
        ),
        "codex": cast(
            BridgePlugin,
            SnapshotPlugin(
                PluginSnapshot(
                    PluginHealth(True, "Connected"),
                    sessions=(
                        AgentSession("codex-1", "Codex chat", SessionState.RUNNING),
                        AgentSession("codex-2", "Second Codex chat", SessionState.WAITING),
                    ),
                )
            ),
        ),
    }
    output = RecordingOutput()
    runner = Mk2BridgeRunner(service)

    runner.refresh(cast(Mk2DeckOutput, output))
    result = service.press_key(6)

    assert service.active_sessions_by_plugin["opencode"][0].id == "open-1"
    assert [session.id for session in service.active_sessions_by_plugin["codex"]] == [
        "codex-1",
        "codex-2",
    ]
    assert output.images[0].getpixel((10, 0)) == (138, 167, 255)
    assert output.images[5].getpixel((10, 0)) == (72, 224, 180)
    assert output.images[6].getpixel((10, 0)) == (72, 224, 180)
    assert result == {"result": "session_selected_in_codex", "session_id": "codex-2"}
    assert service.codex_selected == ["codex-2"]
    assert service.gui_selected == []
