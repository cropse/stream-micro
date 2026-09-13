"""Local control-plane service shared by the API and Stream Deck loop."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .control_config import (
    ButtonBinding,
    ControlConfig,
    control_config_data,
    load_control_config,
    parse_control_config,
    write_control_config,
)
from .openchamber import (
    OpenChamberDesktop,
    OpenChamberError,
    discover_openchamber_desktop,
    select_openchamber_session,
)
from .plugins.base import AgentSession, BridgePlugin, PluginSnapshot
from .plugins.antigravity import AntigravityPlugin
from .plugins.claude_code import ClaudeCodePlugin
from .plugins.codex import CodexPlugin
from .plugins.ollama_quota import OllamaQuotaPlugin
from .plugins.opencode import OpenCodePlugin
from .windows import (
    WindowCaptureError,
    focus_window_binding,
    foreground_window_binding,
)


class BridgeError(RuntimeError):
    pass


# Exponential backoff for re-discovering OpenChamber Desktop when it starts
# after the Bridge.  The first retry waits ``_OPENCHAMBER_INITIAL_BACKOFF``
# seconds; each subsequent attempt doubles the delay, up to
# ``_OPENCHAMBER_MAX_BACKOFF``.  Once discovered, the backoff resets so a
# future restart of OpenChamber triggers a fresh fast retry cycle.
_OPENCHAMBER_INITIAL_BACKOFF = 1.0
_OPENCHAMBER_MAX_BACKOFF = 60.0
_OPENCHAMBER_BACKOFF_FACTOR = 2.0


@dataclass(frozen=True)
class PinnedSession:
    plugin_id: str
    session: AgentSession


class BridgeService:
    """Owns loaded configuration, providers, and selected dashboard Session."""

    def __init__(
        self, config_path: Path | str, *, plugins: dict[str, BridgePlugin] | None = None
    ) -> None:
        self.config_path = Path(config_path)
        self.config = load_control_config(self.config_path)
        self.openchamber = None if plugins is not None else discover_openchamber_desktop()
        self.plugins = plugins or self._build_plugins(self.config, self.openchamber)
        self.selected_session_id: str | None = None
        self.selected_session_ids: dict[str, str] = {}
        self.pending_window_bindings: dict[str, dict[str, str]] = {}
        self.active_sessions_by_plugin: dict[str, tuple[AgentSession, ...]] = {}
        # Compatibility for callers that still populate the OpenCode list directly.
        self.active_sessions: tuple[AgentSession, ...] = ()
        self.pinned_sessions: dict[int, PinnedSession] = {}
        self.device_status: dict[str, Any] = {
            "connected": False,
            "detail": "Waiting for Stream Deck",
        }
        # Exponential backoff state for OpenChamber Desktop re-discovery.
        # When OpenChamber is already connected, ``_openchamber_backoff`` is 0
        # and no retry is attempted.  When it is missing, each failed attempt
        # doubles the delay (capped at ``_OPENCHAMBER_MAX_BACKOFF``), and a
        # successful discovery resets it to ``_OPENCHAMBER_INITIAL_BACKOFF``.
        self._openchamber_backoff = 0.0
        self._openchamber_last_attempt = 0.0
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _build_plugins(
        config: ControlConfig, openchamber: OpenChamberDesktop | None = None
    ) -> dict[str, BridgePlugin]:
        plugins: dict[str, BridgePlugin] = {}
        open_code = config.plugins.get("opencode")
        if open_code and open_code.get("enabled", True):
            open_code_config = dict(open_code)
            if openchamber is not None:
                configured = open_code_config.get("endpoints", [])
                open_code_config["endpoints"] = list(
                    dict.fromkeys([openchamber.opencode_proxy_url, *configured])
                )
            plugins["opencode"] = OpenCodePlugin(open_code_config)
        ollama = config.plugins.get("ollama_quota")
        if ollama is not None:
            plugins["ollama_quota"] = OllamaQuotaPlugin(ollama)
        codex = config.plugins.get("codex")
        if codex and codex.get("enabled", False):
            plugins["codex"] = CodexPlugin(codex)
        claude_code = config.plugins.get("claude_code")
        if claude_code and claude_code.get("enabled", False):
            plugins["claude_code"] = ClaudeCodePlugin(claude_code)
        antigravity = config.plugins.get("antigravity")
        if antigravity and antigravity.get("enabled", False):
            plugins["antigravity"] = AntigravityPlugin(antigravity)
        return plugins

    def config_data(self) -> dict[str, Any]:
        return control_config_data(self.config)

    def public_config_data(self) -> dict[str, Any]:
        """Return browser-safe configuration without persisted credentials."""

        data = self.config_data()
        ollama = data.get("plugins", {}).get("ollama_quota")
        if isinstance(ollama, dict):
            cookie = ollama.get("cookie_value")
            ollama["cookie_configured"] = isinstance(cookie, str) and bool(cookie)
            ollama["cookie_value"] = ""
        return data

    def replace_config(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(data)
        ollama = normalized.get("plugins", {}).get("ollama_quota")
        if isinstance(ollama, dict):
            configured = ollama.pop("cookie_configured", False)
            if ollama.get("cookie_value") == "" and configured is True:
                existing = self.config.plugins.get("ollama_quota", {}).get("cookie_value")
                if isinstance(existing, str) and existing:
                    ollama["cookie_value"] = existing
        config = parse_control_config(normalized)
        write_control_config(self.config_path, config)
        old_plugins = self.plugins
        self.config = config
        self.plugins = self._build_plugins(config, self.openchamber)
        self._close_plugins(old_plugins)
        enabled = set(self.plugins)
        self.selected_session_ids = {
            plugin_id: session_id
            for plugin_id, session_id in self.selected_session_ids.items()
            if plugin_id in enabled
        }
        if self.selected_session_id not in self.selected_session_ids.values():
            self.selected_session_id = None
        self.pinned_sessions = {
            key_index: pinned
            for key_index, pinned in self.pinned_sessions.items()
            if (parent := self._span_parent(key_index)) is not None
            and parent.plugin == pinned.plugin_id
        }
        return self.public_config_data()

    def manifests(self) -> list[dict[str, Any]]:
        return [asdict(plugin.manifest) for plugin in self.plugins.values()]

    def snapshots(self) -> dict[str, PluginSnapshot]:
        return {plugin_id: plugin.snapshot() for plugin_id, plugin in self.plugins.items()}

    def snapshots_data(self) -> dict[str, dict[str, Any]]:
        """Return every provider snapshot without hard-coding a session source."""

        return {plugin_id: asdict(snapshot) for plugin_id, snapshot in self.snapshots().items()}

    def set_active_snapshots(self, snapshots: dict[str, PluginSnapshot]) -> None:
        """Cache the exact per-provider session lists currently rendered on the Deck."""

        self.active_sessions_by_plugin = {
            plugin_id: snapshot.sessions for plugin_id, snapshot in snapshots.items()
        }
        self.active_sessions = self.active_sessions_by_plugin.get("opencode", ())
        for key_index, pinned in tuple(self.pinned_sessions.items()):
            fresh = next(
                (
                    session
                    for session in self.active_sessions_by_plugin.get(pinned.plugin_id, ())
                    if session.id == pinned.session.id
                ),
                None,
            )
            if fresh is not None:
                self.pinned_sessions[key_index] = PinnedSession(pinned.plugin_id, fresh)

    def status_data(self, *, include_plugins: bool = True) -> dict[str, Any]:
        """Return Bridge state, optionally without expensive Plugin snapshots."""

        status: dict[str, Any] = {
            "selected_session_id": self.selected_session_id,
            "selected_session_ids": dict(self.selected_session_ids),
            "pinned_sessions": {
                str(key_index): {
                    "plugin": pinned.plugin_id,
                    **asdict(pinned.session),
                }
                for key_index, pinned in self.pinned_sessions.items()
            },
            "pending_window_bindings": self.pending_window_bindings,
            "device": self.device_status,
            "openchamber": {
                "connected": self.openchamber is not None,
                "detail": (
                    f"OpenChamber Desktop {self.openchamber.version}"
                    if self.openchamber is not None
                    else "OpenChamber Desktop was not discovered on loopback"
                ),
                "url": self.openchamber.url if self.openchamber is not None else None,
            },
        }
        if include_plugins:
            status["plugins"] = {
                plugin_id: asdict(snapshot) for plugin_id, snapshot in self.snapshots().items()
            }
        return status

    def capture_session_window(self, session_id: str) -> dict[str, str]:
        if not isinstance(session_id, str) or not session_id:
            raise BridgeError("session_id is required")
        try:
            binding = foreground_window_binding()
        except WindowCaptureError as exc:
            raise BridgeError(str(exc)) from exc
        self.pending_window_bindings[session_id] = binding
        self.selected_session_id = session_id
        return binding

    def confirm_session_window(self, session_id: str) -> dict[str, Any]:
        try:
            binding = self.pending_window_bindings.pop(session_id)
        except KeyError as exc:
            raise BridgeError("No captured window binding is waiting for confirmation") from exc
        data = self.config_data()
        data["session_window_bindings"][session_id] = binding
        return self.replace_config(data)

    def focus_session_window(self, session_id: str) -> None:
        try:
            binding = self.config.session_window_bindings[session_id]
        except KeyError as exc:
            raise BridgeError("Session has no confirmed window binding") from exc
        try:
            focus_window_binding(binding)
        except WindowCaptureError as exc:
            raise BridgeError(str(exc)) from exc

    def select_openchamber_session(self, session: AgentSession) -> None:
        try:
            select_openchamber_session(
                session_id=session.id,
                directory=session.directory,
            )
        except OpenChamberError as exc:
            raise BridgeError(str(exc)) from exc

    def select_session_in_openchamber(self, session_id: str) -> dict[str, str]:
        """Focus one current OpenCode Session in OpenChamber Desktop by ID."""

        if not isinstance(session_id, str) or not session_id.strip():
            raise BridgeError("session_id is required")
        plugin = self._plugin("opencode")
        if not isinstance(plugin, OpenCodePlugin):
            raise BridgeError("OpenCode plugin is not enabled")
        session = next(
            (item for item in plugin.sessions() if item.id == session_id),
            None,
        )
        if session is None:
            raise BridgeError("Session no longer exists on the active OpenCode server")
        self._select_openchamber_session(session)
        return {"result": "session_selected_in_openchamber", "session_id": session.id}

    def _select_openchamber_session(self, session: AgentSession) -> None:
        self.select_openchamber_session(session)
        self.selected_session_id = session.id
        self.selected_session_ids["opencode"] = session.id
        plugin = self.plugins.get("opencode")
        if isinstance(plugin, OpenCodePlugin):
            plugin.mark_viewed(session.id)

    def press_key(self, key_index: int) -> dict[str, Any]:
        """Dispatch one MK.2 key press; shared by HID and local diagnostics."""

        if not isinstance(key_index, int) or not 0 <= key_index < len(self.config.buttons):
            raise BridgeError("key_index must be an MK.2 key from 0 through 14")
        binding = self.config.buttons[key_index]
        parent = self._span_parent(key_index)
        # If this key is inside a span parent, dispatch through the parent.
        if parent is not None and parent != binding:
            return self._press_span_child(parent, key_index)
        if parent is not None:
            return self._press_span_child(parent, key_index)
        if binding.plugin and binding.action:
            params = dict(binding.action_params)
            if binding.session_id:
                params.setdefault("session_id", binding.session_id)
            return self.invoke(binding.plugin, binding.action, params)
        return {"result": "no_action"}

    def toggle_session_pin(self, key_index: int) -> dict[str, Any]:
        """Pin or unpin the Session currently displayed in one physical slot."""

        if not isinstance(key_index, int) or not 0 <= key_index < len(self.config.buttons):
            raise BridgeError("key_index must be an MK.2 key from 0 through 14")
        pinned = self.pinned_sessions.pop(key_index, None)
        if pinned is not None:
            return {
                "result": "session_unpinned",
                "key_index": key_index,
                "session_id": pinned.session.id,
            }
        parent = self._span_parent(key_index)
        if parent is None or not parent.plugin:
            raise BridgeError("Only a key displaying a Session can be pinned")
        session = self.session_for_key(key_index)
        if session is None:
            raise BridgeError("This key has no Session to pin")
        self.pinned_sessions[key_index] = PinnedSession(parent.plugin, session)
        return {
            "result": "session_pinned",
            "key_index": key_index,
            "session_id": session.id,
        }

    def is_key_pinned(self, key_index: int) -> bool:
        return key_index in self.pinned_sessions

    def _span_parent(self, key_index: int) -> ButtonBinding | None:
        """Return the span zone root that owns ``key_index``, or None.

        A span-1 session key is its own zone root so it dispatches via the
        slot-based session selector, not the generic action dispatcher.
        """
        for b in self.config.buttons:
            if (
                b.plugin
                and b.component == "session"
                and b.key_index <= key_index < b.key_index + max(b.span, 1)
            ):
                return b
        return None

    def _press_span_child(
        self, parent: ButtonBinding, key_index: int
    ) -> dict[str, Any]:
        if not parent.plugin or not parent.action:
            return {"result": "no_action"}
        session = self.session_for_key(key_index)
        if session is not None:
            params = {**parent.action_params, "session_id": session.id}
            return self.invoke(parent.plugin, parent.action, params)
        return {"result": "no_action"}

    def session_for_key(self, key_index: int) -> AgentSession | None:
        """Resolve a Session slot while keeping pinned slots stable and unique."""

        parent = self._span_parent(key_index)
        if parent is None or not parent.plugin:
            return None
        if parent.span == 1 and parent.session_id and key_index not in self.pinned_sessions:
            return next(
                (
                    session
                    for session in self._sessions_for_plugin(parent.plugin)
                    if session.id == parent.session_id
                ),
                None,
            )

        zone_indices = range(parent.key_index, parent.key_index + max(parent.span, 1))
        pinned_by_key = {
            index: pinned.session
            for index in zone_indices
            if (pinned := self.pinned_sessions.get(index)) is not None
            and pinned.plugin_id == parent.plugin
        }
        pinned_ids = {session.id for session in pinned_by_key.values()}
        available = iter(
            session
            for session in self._sessions_for_plugin(parent.plugin)
            if session.id not in pinned_ids
        )
        for index in zone_indices:
            session = pinned_by_key.get(index)
            if session is None:
                session = next(available, None)
            if index == key_index:
                return session
        return None

    def _sessions_for_plugin(self, plugin_id: str) -> tuple[AgentSession, ...]:
        sessions = self.active_sessions_by_plugin.get(plugin_id)
        if sessions is not None:
            return sessions
        if plugin_id == "opencode":
            return self.active_sessions
        return ()

    def opencode_servers(self) -> list[dict[str, str]]:
        plugin = self._plugin("opencode")
        if not isinstance(plugin, OpenCodePlugin):
            raise BridgeError("OpenCode plugin is not enabled")
        return [asdict(endpoint) for endpoint in plugin.discover()]

    def opencode_sessions(self) -> list[dict[str, Any]]:
        plugin = self._plugin("opencode")
        if not isinstance(plugin, OpenCodePlugin):
            raise BridgeError("OpenCode plugin is not enabled")
        return [asdict(session) for session in plugin.sessions()]

    def handle_claude_code_hook(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        plugin = self._plugin("claude_code")
        if not isinstance(plugin, ClaudeCodePlugin):
            raise BridgeError("Claude Code plugin is not enabled")
        return plugin.handle_hook(payload)

    def handle_claude_code_quota(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        plugin = self._plugin("claude_code")
        if not isinstance(plugin, ClaudeCodePlugin):
            raise BridgeError("Claude Code plugin is not enabled")
        return plugin.handle_quota(payload)

    def handle_antigravity_status(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
        plugin = self._plugin("antigravity")
        if not isinstance(plugin, AntigravityPlugin):
            raise BridgeError("Antigravity plugin is not enabled")
        return plugin.handle_status(payload)

    def invoke(self, plugin_id: str, action: str, params: dict[str, Any]) -> Any:
        plugin = self._plugin(plugin_id)
        if action not in plugin.manifest.actions:
            raise BridgeError(f"Action {action} is not allowed for plugin {plugin_id}")
        # ``select_session`` is a Bridge action: OpenCode supplies the list,
        # while OpenChamber Desktop owns the visible Session selection.
        # Keeping this interception makes legacy API callers follow the same
        # path as a physical Session key and the Dashboard.
        if plugin_id == "opencode" and action == "select_session":
            session_id = params.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise BridgeError("select_session requires session_id")
            return self.select_session_in_openchamber(session_id)
        result = plugin.invoke(action, params)
        if action == "select_session":
            session_id = params.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.selected_session_id = session_id
                self.selected_session_ids[plugin_id] = session_id
        return result

    def _plugin(self, plugin_id: str) -> BridgePlugin:
        try:
            return self.plugins[plugin_id]
        except KeyError as exc:
            raise BridgeError(f"Plugin {plugin_id} is not enabled") from exc

    @staticmethod
    def _close_plugins(plugins: dict[str, BridgePlugin]) -> None:
        for plugin in plugins.values():
            close = getattr(plugin, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        self._close_plugins(self.plugins)

    def rediscover_openchamber(self, *, now: float = 0.0) -> bool:
        """Attempt to re-discover OpenChamber Desktop with exponential backoff.

        Called from the Deck refresh loop on every cycle.  When OpenChamber is
        already connected, this is a no-op.  When it is missing, the first call
        waits ``_OPENCHAMBER_INITIAL_BACKOFF`` seconds (measured against
        ``now``, which the caller supplies from ``time.monotonic()``); each
        subsequent failed call doubles the delay, capped at
        ``_OPENCHAMBER_MAX_BACKOFF``.  A successful discovery resets the backoff
        and live-updates the OpenCode plugin's endpoints so sessions return
        immediately without a Bridge restart.

        Returns ``True`` if OpenChamber is connected after this call (either
        already was, or just re-discovered), ``False`` if still not found.
        """

        if self.openchamber is not None:
            return True

        # In test contexts ``now`` may be 0; fall back to monotonic time.
        if now == 0.0:
            import time as _time

            now = _time.monotonic()

        elapsed = now - self._openchamber_last_attempt
        if elapsed < self._openchamber_backoff:
            return False

        self._openchamber_last_attempt = now
        desktop = discover_openchamber_desktop()
        if desktop is None:
            # Back off: first failure starts at INITIAL_BACKOFF, subsequent
            # failures double the delay up to MAX_BACKOFF.
            if self._openchamber_backoff == 0.0:
                self._openchamber_backoff = _OPENCHAMBER_INITIAL_BACKOFF
            else:
                self._openchamber_backoff = min(
                    self._openchamber_backoff * _OPENCHAMBER_BACKOFF_FACTOR,
                    _OPENCHAMBER_MAX_BACKOFF,
                )
            self._logger.info(
                "OpenChamber Desktop not found; retrying in %.1fs",
                self._openchamber_backoff,
            )
            return False

        # Success — update state and live-patch the OpenCode plugin endpoints.
        self._logger.info(
            "OpenChamber Desktop %s discovered at %s", desktop.version, desktop.url
        )
        self.openchamber = desktop
        self._openchamber_backoff = 0.0
        self._openchamber_last_attempt = 0.0
        plugin = self.plugins.get("opencode")
        if isinstance(plugin, OpenCodePlugin):
            configured = plugin.config.get("endpoints", [])
            plugin.config["endpoints"] = list(
                dict.fromkeys([desktop.opencode_proxy_url, *configured])
            )
            # Clear the cached endpoint so the next ``discover()`` / ``sessions()``
            # call re-probes with the updated endpoint list.
            plugin.endpoint = None
        return True
