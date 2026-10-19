from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    # The turn has ended but background subagents or agents still work for it.
    BACKGROUND = "background"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    components: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class AgentSession:
    id: str
    title: str
    state: SessionState
    project: str | None = None
    directory: str | None = None


@dataclass(frozen=True)
class PluginHealth:
    connected: bool
    detail: str
    endpoint: str | None = None


@dataclass(frozen=True)
class PluginSnapshot:
    health: PluginHealth
    sessions: tuple[AgentSession, ...] = ()
    signals: dict[str, str | float | int | None] | None = None


class BridgePlugin(Protocol):
    manifest: PluginManifest

    def validate_config(self, config: dict[str, Any]) -> None: ...

    def snapshot(self) -> PluginSnapshot: ...

    def invoke(self, action: str, params: dict[str, Any]) -> Any: ...
