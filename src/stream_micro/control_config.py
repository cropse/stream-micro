"""Configuration contract for the local Agent Control Surface.

This is intentionally separate from the legacy Ollama-only configuration while
the Bridge is being introduced.  The `serve` command will use this contract.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .plugins.antigravity import AntigravityError, validate_antigravity_config

MK2_KEY_COUNT = 15
# Display options a display-only quota key may keep in action_params:
# "account" picks one account of a multi-account source, "label" renames the key.
QUOTA_DISPLAY_PARAMS = frozenset({"account", "label"})
SCHEMA_VERSION = 1
# Colours the dashboard may set: a key's accent, and each Session state's colour.
SESSION_STATE_NAMES = frozenset(
    {"idle", "running", "background", "waiting", "completed", "error", "disconnected"}
)
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ControlConfigError(ValueError):
    """Raised when the Agent Control Surface configuration is invalid."""


@dataclass(frozen=True)
class BridgeSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    refresh_interval_seconds: float = 5.0


RESET_THRESHOLD_WINDOWS = ("7D", "MO")


def reset_thresholds_for(settings: Mk2Settings, binding: ButtonBinding) -> dict[str, int]:
    """The thresholds a quota key uses: the shared default, then its service's own."""

    service = binding.plugin or ""
    account = binding.action_params.get("account") if binding.action_params else None
    if service == "http_quota" and isinstance(account, str) and account:
        service = f"http_quota:{account}"
    return {
        **settings.reset_thresholds.get("default", {}),
        **settings.reset_thresholds.get(service, {}),
    }


@dataclass(frozen=True)
class Mk2Settings:
    model: str = "mk2"
    device_serial: str | None = None
    brightness: int = 40
    # Quota keys show what is "remaining" (a full green bar = plenty left) or "used".
    quota_display: str = "remaining"
    # Session state -> "#rrggbb", replacing the built-in strip colours.
    state_colors: dict[str, str] = field(default_factory=dict)
    # Session keys draw the provider colour as a top line (quota keys always do).
    session_accent_line: bool = True
    # When a 7D or MO window has at most this % left, the footer shows its reset
    # as if it were used up. "default" applies to every key; a service key
    # ("claude_code", "http_quota:<source id>", ...) overrides it. 5H stays at 0.
    reset_thresholds: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ButtonBinding:
    key_index: int
    plugin: str | None = None
    component: str | None = None
    span: int = 1
    session_id: str | None = None
    action: str | None = None
    action_params: dict[str, Any] = field(default_factory=dict)
    window_binding: dict[str, str] | None = None
    # "#rrggbb" for the key's accent line and title, instead of the provider's.
    accent: str | None = None


@dataclass(frozen=True)
class ControlConfig:
    schema_version: int
    bridge: BridgeSettings
    stream_deck: Mk2Settings
    plugins: dict[str, dict[str, Any]]
    buttons: tuple[ButtonBinding, ...]
    session_window_bindings: dict[str, dict[str, str]] = field(default_factory=dict)


def default_control_config() -> ControlConfig:
    """Return the initial MK.2 layout without provider credentials."""

    buttons: list[ButtonBinding] = []
    for index in range(MK2_KEY_COUNT):
        if index == 0:
            buttons.append(
                ButtonBinding(index, "opencode", "session", span=5, action="select_session")
            )
        else:
            buttons.append(ButtonBinding(index))
    return ControlConfig(
        schema_version=SCHEMA_VERSION,
        bridge=BridgeSettings(),
        stream_deck=Mk2Settings(),
        plugins={
            "opencode": {
                "enabled": True,
                "endpoints": ["http://127.0.0.1:4096"],
                "password_env": None,
            },
            "ollama_quota": {
                "cookie_value": "",
                "settings_url": "https://ollama.com/settings",
                "cookie_name": "__Secure-session",
                "request_timeout_seconds": 15.0,
                "refresh_interval_seconds": 60.0,
            },
            "codex": {"enabled": False, "codex_path": None, "codex_home": None},
            "cliproxy_quota": {
                "enabled": False,
                "base_url": "",
                "management_key": None,
                "management_key_env": None,
                "allow_insecure_http": False,
                "providers": ["codex", "claude"],
            },
            "http_quota": {"enabled": False, "sources": []},
            "claude_code": {
                "enabled": False,
                "claude_path": None,
                "claude_home": None,
                "plugin_dir": None,
                "max_sessions": 15,
            },
            "antigravity": {
                "enabled": False,
                "agy_path": None,
                "agy_home": None,
                "max_sessions": 15,
            },
        },
        buttons=tuple(buttons),
        session_window_bindings={},
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlConfigError(f"{label} must be a mapping")
    return value


def _construct(cls: type[Any], raw: dict[str, Any], label: str) -> Any:
    fields = cls.__dataclass_fields__
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise ControlConfigError(f"Unknown setting(s) in {label}: {', '.join(unknown)}")
    try:
        return cls(**raw)
    except TypeError as exc:
        raise ControlConfigError(f"Invalid {label}: {exc}") from exc


def _parse_buttons(raw: Any) -> tuple[ButtonBinding, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ControlConfigError("buttons must be a list with exactly 15 entries")
    bindings: list[ButtonBinding] = []
    for index, value in enumerate(raw):
        binding = _construct(
            ButtonBinding, _mapping(value, f"buttons[{index}]"), f"buttons[{index}]"
        )
        bindings.append(binding)
    return tuple(bindings)


def _migrate_legacy_session_row(buttons: tuple[ButtonBinding, ...]) -> tuple[ButtonBinding, ...]:
    """Read pre-span five-key Session rows as the current five-key zone.

    Earlier bridge.yaml files represented the first five physical keys as five
    independent dynamic Session bindings.  The span model assigns those five
    slots from one root binding, so retaining the legacy interpretation would
    show the newest Session five times.  Migrate in memory; writing remains an
    explicit dashboard Save operation.
    """

    legacy_row = buttons[:5]
    if len(legacy_row) != 5 or not all(
        binding.key_index == index
        and binding.plugin == "opencode"
        and binding.component == "session"
        and binding.span == 1
        and binding.session_id is None
        and binding.action == "select_session"
        for index, binding in enumerate(legacy_row)
    ):
        return buttons
    migrated = [replace(legacy_row[0], span=5)]
    migrated.extend(
        replace(
            binding,
            plugin=None,
            component=None,
            span=1,
            session_id=None,
            action=None,
            action_params={},
            window_binding=None,
        )
        for binding in legacy_row[1:]
    )
    return tuple([*migrated, *buttons[5:]])


def validate_control_config(config: ControlConfig) -> None:
    if config.schema_version != SCHEMA_VERSION:
        raise ControlConfigError(f"schema_version must be {SCHEMA_VERSION}")
    if config.bridge.host != "127.0.0.1":
        raise ControlConfigError("bridge.host must be 127.0.0.1")
    if not isinstance(config.bridge.port, int) or not 1 <= config.bridge.port <= 65535:
        raise ControlConfigError("bridge.port must be an integer between 1 and 65535")
    if (
        not isinstance(config.bridge.refresh_interval_seconds, (int, float))
        or isinstance(config.bridge.refresh_interval_seconds, bool)
        or config.bridge.refresh_interval_seconds <= 0
    ):
        raise ControlConfigError("bridge.refresh_interval_seconds must be greater than 0")
    if config.stream_deck.model != "mk2":
        raise ControlConfigError("stream_deck.model must be mk2")
    if (
        not isinstance(config.stream_deck.brightness, int)
        or not 0 <= config.stream_deck.brightness <= 100
    ):
        raise ControlConfigError("stream_deck.brightness must be an integer between 0 and 100")
    if config.stream_deck.quota_display not in ("remaining", "used"):
        raise ControlConfigError("stream_deck.quota_display must be remaining or used")
    if not isinstance(config.stream_deck.session_accent_line, bool):
        raise ControlConfigError("stream_deck.session_accent_line must be true or false")
    thresholds = config.stream_deck.reset_thresholds
    if not isinstance(thresholds, dict):
        raise ControlConfigError("stream_deck.reset_thresholds must be a mapping")
    for service, windows in thresholds.items():
        if not isinstance(service, str) or not service or not isinstance(windows, dict):
            raise ControlConfigError("stream_deck.reset_thresholds maps a service to its windows")
        for window, percent in windows.items():
            if window not in RESET_THRESHOLD_WINDOWS:
                raise ControlConfigError(
                    f"stream_deck.reset_thresholds.{service}: windows are "
                    f"{', '.join(RESET_THRESHOLD_WINDOWS)}"
                )
            if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 90:
                raise ControlConfigError(
                    f"stream_deck.reset_thresholds.{service}.{window} must be 0-90"
                )
    colors = config.stream_deck.state_colors
    if not isinstance(colors, dict):
        raise ControlConfigError("stream_deck.state_colors must be a mapping")
    for state, color in colors.items():
        if state not in SESSION_STATE_NAMES:
            raise ControlConfigError(f"stream_deck.state_colors: unknown state {state}")
        if not isinstance(color, str) or not _HEX_COLOR.match(color):
            raise ControlConfigError(f"stream_deck.state_colors.{state} must be #rrggbb")
    serial = config.stream_deck.device_serial
    if serial is not None and (not isinstance(serial, str) or not serial.strip()):
        raise ControlConfigError("stream_deck.device_serial must be a non-empty string or null")
    if len(config.buttons) != MK2_KEY_COUNT:
        raise ControlConfigError("buttons must contain exactly 15 MK.2 key bindings")
    indices = [binding.key_index for binding in config.buttons]
    if sorted(indices) != list(range(MK2_KEY_COUNT)):
        raise ControlConfigError(
            "buttons must contain each MK.2 key_index from 0 through 14 exactly once"
        )
    if not isinstance(config.plugins, dict):
        raise ControlConfigError("plugins must be a mapping")
    for plugin_id, plugin_config in config.plugins.items():
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ControlConfigError("plugin ids must be non-empty strings")
        if not isinstance(plugin_config, dict):
            raise ControlConfigError(f"plugins.{plugin_id} must be a mapping")
    antigravity = config.plugins.get("antigravity")
    if antigravity is not None:
        try:
            validate_antigravity_config(antigravity)
        except AntigravityError as exc:
            message = str(exc)
            if message.startswith("antigravity."):
                message = f"plugins.{message}"
            raise ControlConfigError(message) from exc
    for binding in config.buttons:
        if binding.accent is not None and (
            not isinstance(binding.accent, str) or not _HEX_COLOR.match(binding.accent)
        ):
            raise ControlConfigError(f"buttons[{binding.key_index}].accent must be #rrggbb")
        if not isinstance(binding.span, int) or isinstance(binding.span, bool) or binding.span < 1:
            raise ControlConfigError(
                f"buttons[{binding.key_index}].span must be a positive integer"
            )
        if binding.plugin is None and binding.span > 1:
            raise ControlConfigError(
                f"buttons[{binding.key_index}].span > 1 requires a plugin"
            )
        if binding.component == "quota" and binding.span != 1:
            raise ControlConfigError(f"buttons[{binding.key_index}] quota keys use one key")
        if binding.key_index + binding.span > MK2_KEY_COUNT:
            raise ControlConfigError(
                f"buttons[{binding.key_index}].span {binding.span} exceeds the 15-key layout"
            )
        if binding.plugin is None and any(
            value is not None for value in (binding.component, binding.session_id, binding.action)
        ):
            raise ControlConfigError(
                f"buttons[{binding.key_index}] cannot define a component or action without a plugin"
            )
        if binding.plugin is None:
            continue
        if binding.plugin not in config.plugins:
            raise ControlConfigError(
                f"buttons[{binding.key_index}] references unknown plugin {binding.plugin}"
            )
        if not binding.component:
            raise ControlConfigError(
                f"buttons[{binding.key_index}] must define component when plugin is set"
            )
        if binding.component == "quota" and binding.action is None:
            # Display-only quota keys may carry their display options here.
            unknown = sorted(set(binding.action_params) - QUOTA_DISPLAY_PARAMS)
            if unknown:
                raise ControlConfigError(
                    f"buttons[{binding.key_index}] quota keys accept only "
                    f"{', '.join(sorted(QUOTA_DISPLAY_PARAMS))}, not {', '.join(unknown)}"
                )
            for name, value in binding.action_params.items():
                if not isinstance(value, str):
                    raise ControlConfigError(
                        f"buttons[{binding.key_index}].action_params.{name} must be text"
                    )
        elif binding.action_params and binding.action is None:
            raise ControlConfigError(
                f"buttons[{binding.key_index}] action_params require an action"
            )
        if binding.window_binding is not None:
            required = {"executable", "window_class", "title_pattern"}
            if set(binding.window_binding) != required or not all(binding.window_binding.values()):
                raise ControlConfigError(
                    f"buttons[{binding.key_index}].window_binding must define executable, "
                    "window_class, and title_pattern"
                )
    for session_id, window_binding in config.session_window_bindings.items():
        if not isinstance(session_id, str) or not session_id:
            raise ControlConfigError("session_window_bindings keys must be Session IDs")
        required = {"executable", "window_class", "title_pattern"}
        if (
            not isinstance(window_binding, dict)
            or set(window_binding) != required
            or not all(isinstance(value, str) and value for value in window_binding.values())
        ):
            raise ControlConfigError(
                f"session_window_bindings.{session_id} must define executable, "
                "window_class, and title_pattern"
            )


def load_control_config(path: Path | str) -> ControlConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ControlConfigError(f"Configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ControlConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    return parse_control_config(_mapping(raw, "root"))


def parse_control_config(data: dict[str, Any]) -> ControlConfig:
    allowed = {
        "schema_version",
        "bridge",
        "stream_deck",
        "plugins",
        "buttons",
        "session_window_bindings",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ControlConfigError(f"Unknown configuration section(s): {', '.join(unknown)}")
    config = ControlConfig(
        schema_version=data.get("schema_version", 0),
        bridge=_construct(BridgeSettings, _mapping(data.get("bridge", {}), "bridge"), "bridge"),
        stream_deck=_construct(
            Mk2Settings,
            _mapping(data.get("stream_deck", {}), "stream_deck"),
            "stream_deck",
        ),
        plugins=_mapping(data.get("plugins", {}), "plugins"),
        buttons=_migrate_legacy_session_row(_parse_buttons(data.get("buttons", []))),
        session_window_bindings=_mapping(
            data.get("session_window_bindings", {}), "session_window_bindings"
        ),
    )
    validate_control_config(config)
    return config


def control_config_data(config: ControlConfig) -> dict[str, Any]:
    """Return a serializable representation safe for the local configuration API."""

    return asdict(config)


def write_control_config(path: Path | str, config: ControlConfig) -> None:
    """Validate and atomically replace a local configuration file."""

    validate_control_config(config)
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary name, so two writers never share one file.
    fd, name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(yaml.safe_dump(control_config_data(config), sort_keys=False))
        temporary.replace(config_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
