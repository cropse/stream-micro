"""Local control-plane service shared by the API and Stream Deck loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import claude_setup
from .control_config import (
    ButtonBinding,
    ControlConfig,
    ControlConfigError,
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
from .plugins.claude_code import ClaudeCodePlugin
from .plugins.codex import CodexPlugin
from .plugins.http_quota import (
    HttpQuotaError,
    HttpQuotaPlugin,
    source_origin,
    validate_http_quota_config,
)
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
        # One save at a time: each save builds, writes, publishes, then closes
        # the providers it replaced; interleaved saves could close a live one.
        self._save_lock = threading.Lock()
        self.openchamber = None if plugins is not None else discover_openchamber_desktop()
        self.plugins = plugins or self._build_plugins(
            self.config, self.openchamber, state_dir=self.state_dir
        )
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

    @property
    def state_dir(self) -> Path:
        """Where providers keep last-seen data, next to this Bridge's config."""

        return self.config_path.parent / ".state"

    @staticmethod
    def _build_plugins(
        config: ControlConfig,
        openchamber: OpenChamberDesktop | None = None,
        *,
        state_dir: Path | None = None,
        previous: tuple[ControlConfig, dict[str, BridgePlugin]] | None = None,
    ) -> dict[str, BridgePlugin]:
        """Build enabled providers, reusing ones whose settings did not change.

        ``previous`` is the running config and providers: reusing them keeps
        app-server processes, hook state, and quota caches alive across
        layout-only saves.
        """

        plugins: dict[str, BridgePlugin] = {}
        created: dict[str, BridgePlugin] = {}

        def add(plugin_id: str, build: Callable[[], BridgePlugin]) -> None:
            if previous is not None:
                old_config, old_plugins = previous
                if (
                    plugin_id in old_plugins
                    and old_config.plugins.get(plugin_id) == config.plugins.get(plugin_id)
                ):
                    plugins[plugin_id] = old_plugins[plugin_id]
                    return
            plugins[plugin_id] = created[plugin_id] = build()

        def with_cache(settings: dict[str, Any], name: str) -> dict[str, Any]:
            if settings.get("cache_path") or state_dir is None:
                return dict(settings)
            return {**settings, "cache_path": str(state_dir / name)}

        try:
            open_code = config.plugins.get("opencode")
            if open_code and open_code.get("enabled", True):
                open_code_config = dict(open_code)
                if openchamber is not None:
                    configured = open_code_config.get("endpoints", [])
                    open_code_config["endpoints"] = list(
                        dict.fromkeys([openchamber.opencode_proxy_url, *configured])
                    )
                add("opencode", lambda: OpenCodePlugin(open_code_config))
            ollama = config.plugins.get("ollama_quota")
            if ollama is not None:
                add("ollama_quota", lambda: OllamaQuotaPlugin(ollama))
            codex = config.plugins.get("codex")
            if codex and codex.get("enabled", False):
                add("codex", lambda: CodexPlugin(codex))
            claude_code = config.plugins.get("claude_code")
            if claude_code and claude_code.get("enabled", False):
                add("claude_code", lambda: ClaudeCodePlugin(claude_code))
            # Optional quota sources: validated even while disabled, started
            # only when enabled.
            http_quota = config.plugins.get("http_quota")
            if http_quota is not None:
                try:
                    validate_http_quota_config(http_quota)
                except HttpQuotaError as exc:
                    if http_quota.get("enabled", False):
                        raise
                    raise HttpQuotaError(
                        f"{exc} (usage API sources are checked even while disabled; "
                        "fix or remove that source)"
                    ) from exc
                if http_quota.get("enabled", False):
                    add(
                        "http_quota",
                        lambda: HttpQuotaPlugin(with_cache(http_quota, "http_quota.json")),
                    )
        except Exception:
            # Do not leak providers (e.g. a started Codex app-server) built before
            # a later provider rejected its settings; reused ones keep running.
            BridgeService._close_plugins(created)
            raise
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
        http_quota = data.get("plugins", {}).get("http_quota")
        sources = http_quota.get("sources") if isinstance(http_quota, dict) else None
        for source in sources if isinstance(sources, list) else []:
            if isinstance(source, dict):
                key = source.get("api_key")
                source["api_key_configured"] = isinstance(key, str) and bool(key)
                source["api_key"] = ""
        return data

    def replace_config(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._save_lock:
            return self._replace_config(data)

    def _replace_config(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(data)
        ollama = normalized.get("plugins", {}).get("ollama_quota")
        if isinstance(ollama, dict):
            configured = ollama.pop("cookie_configured", False)
            if ollama.get("cookie_value") == "" and configured is True:
                existing = self.config.plugins.get("ollama_quota", {}).get("cookie_value")
                if isinstance(existing, str) and existing:
                    ollama["cookie_value"] = existing
        http_quota = normalized.get("plugins", {}).get("http_quota")
        sources = http_quota.get("sources") if isinstance(http_quota, dict) else None
        if isinstance(sources, list):
            # Like the Ollama cookie, per source: a blank key keeps the saved key
            # of the source with that id; api_key_configured: false clears it.
            # A key is never carried over to another host: a changed URL or
            # service must be given its key again.
            saved_sources = self.config.plugins.get("http_quota", {}).get("sources")
            saved = {
                item.get("id"): item
                for item in (saved_sources if isinstance(saved_sources, list) else [])
                if isinstance(item, dict)
            }
            for source in sources:
                if not isinstance(source, dict):
                    continue
                configured = source.pop("api_key_configured", False)
                before = saved.get(source.get("id"))
                origin = source_origin(before) if before is not None else None
                same_host = origin is not None and origin == source_origin(source)
                if (
                    before is not None
                    and not same_host
                    and source.get("api_key_env")
                    and source.get("api_key_env") == before.get("api_key_env")
                ):
                    # The variable named for the old host is not sent to a new one
                    # unless it is named again for it.
                    source["api_key_env"] = None
                if source.get("api_key"):
                    continue
                existing = before.get("api_key") if before is not None else None
                keep = configured is True and same_host and isinstance(existing, str)
                source["api_key"] = existing if keep and existing else None
        config = parse_control_config(normalized)
        # Build (and so validate) the new providers before touching the saved
        # file, so an invalid provider setting leaves the running Bridge as-is.
        old_plugins = self.plugins
        try:
            plugins = self._build_plugins(
                config,
                self.openchamber,
                state_dir=self.state_dir,
                previous=(self.config, old_plugins),
            )
        except (ValueError, RuntimeError) as exc:
            raise ControlConfigError(str(exc)) from exc
        reused = {id(plugin) for plugin in plugins.values()} & {
            id(plugin) for plugin in old_plugins.values()
        }
        try:
            write_control_config(self.config_path, config)
        except Exception:
            self._close_plugins(
                {key: plugin for key, plugin in plugins.items() if id(plugin) not in reused}
            )
            raise
        self.config = config
        self.plugins = plugins
        self._close_plugins(
            {key: plugin for key, plugin in old_plugins.items() if id(plugin) not in reused}
        )
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

    def _claude_settings_path(self) -> Path:
        home = self.config.plugins.get("claude_code", {}).get("claude_home")
        base = (
            Path(home).expanduser() if isinstance(home, str) and home else Path.home() / ".claude"
        )
        return base / "settings.json"

    def claude_setup_status(self) -> dict[str, Any]:
        """What of the Claude Code companion is installed, and what has reached us."""

        port = self.config.bridge.port
        try:
            status: dict[str, Any] = claude_setup.inspect(self._claude_settings_path(), port)
        except claude_setup.ClaudeSetupError as exc:
            status = {"settings_path": str(self._claude_settings_path()), "error": str(exc)}
        plugin = self.plugins.get("claude_code")
        activity = plugin.setup_activity() if isinstance(plugin, ClaudeCodePlugin) else {}
        return {**status, **activity, "port": port}

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
