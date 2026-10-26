"""OpenCode local-server adapter.

The adapter deliberately uses only documented, safe server endpoints. Window
focus and Stream Deck rendering stay in the Bridge layer.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any

import httpx

from .base import AgentSession, PluginHealth, PluginManifest, PluginSnapshot, SessionState


class OpenCodeError(RuntimeError):
    pass


LIVE_STATE_SESSION_LIMIT = 5

# Messages and tool parts older than this are considered abandoned — the
# session crashed, timed out, or was killed without OpenCode writing
# ``time.completed`` or updating the tool ``state.status``.
STALE_THRESHOLD_SECONDS = 120


@dataclass(frozen=True)
class OpenCodeEndpoint:
    url: str
    api_version: int = 1


def listening_loopback_ports(netstat_output: str) -> set[int]:
    """Extract Windows TCP loopback listening ports without network scanning."""

    ports: set[int] = set()
    for line in netstat_output.splitlines():
        columns = line.split()
        if len(columns) < 4 or columns[0].upper() != "TCP" or columns[3].upper() != "LISTENING":
            continue
        address = columns[1]
        match = re.match(r"(?:127\.0\.0\.1|\[::1\]):(\d+)$", address)
        if match:
            ports.add(int(match.group(1)))
    return ports


def default_loopback_ports() -> set[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return listening_loopback_ports(result.stdout)


class OpenCodePlugin:
    manifest = PluginManifest(
        id="opencode",
        name="OpenCode",
        components=("session",),
        actions=("select_session",),
    )

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.Client | None = None,
        port_provider: Callable[[], Iterable[int]] = default_loopback_ports,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.validate_config(config)
        self.config = config
        self._client = client or httpx.Client(timeout=3.0)
        self._port_provider = port_provider
        self._time_provider = time_provider
        self.endpoint: OpenCodeEndpoint | None = None
        self._recently_busy: set[str] = set()
        self._completed: set[str] = set()

    def validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config.get("enabled", True), bool):
            raise OpenCodeError("opencode.enabled must be true or false")
        endpoints = config.get("endpoints", [])
        if not isinstance(endpoints, list) or not all(isinstance(item, str) for item in endpoints):
            raise OpenCodeError("opencode.endpoints must be a list of URLs")
        password_env = config.get("password_env")
        if password_env is not None and (not isinstance(password_env, str) or not password_env):
            raise OpenCodeError(
                "opencode.password_env must be a non-empty environment variable name or null"
            )

    def _candidate_urls(self) -> list[str]:
        configured = [url.rstrip("/") for url in self.config.get("endpoints", [])]
        discovered = [f"http://127.0.0.1:{port}" for port in self._port_provider()]
        return list(dict.fromkeys([*configured, *discovered]))

    def discover(self) -> tuple[OpenCodeEndpoint, ...]:
        found: list[OpenCodeEndpoint] = []
        for url in self._candidate_urls():
            # Desktop already supplies an /api proxy. Direct V2 servers need
            # the /api prefix; never turn a proxy into /api/api.
            api_url = url if url.endswith("/api") else f"{url}/api"
            try:
                response = self._client.get(f"{api_url}/info")
                body = response.json()
                if (
                    response.is_success
                    and isinstance(body, dict)
                    and isinstance(body.get("version"), str)
                    and type(body.get("pid")) is int
                    and isinstance(body.get("urls"), list)
                    and isinstance(body.get("paths"), dict)
                ):
                    found.append(OpenCodeEndpoint(api_url, api_version=2))
                    continue
            except (httpx.HTTPError, ValueError):
                pass
            try:
                response = self._client.get(f"{url}/global/health")
                body = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if response.is_success and isinstance(body, dict) and body.get("healthy") is True:
                found.append(OpenCodeEndpoint(url))
        self.endpoint = found[0] if found else None
        return tuple(found)

    def _require_endpoint(self) -> OpenCodeEndpoint:
        if self.endpoint is None:
            self.discover()
        if self.endpoint is None:
            raise OpenCodeError("No active OpenCode server found on this computer")
        return self.endpoint

    @staticmethod
    def _state(value: Any) -> SessionState:
        """Map an OpenCode ``SessionStatus.type`` string to a ``SessionState``.

        OpenCode v1.18.x reports three status types on ``/session/status``:
        ``idle``, ``busy``, and ``retry``.  ``busy`` means the agent is actively
        generating; ``retry`` means the upstream provider is retrying after an
        error (treated as ERROR); ``idle`` means no turn is in flight.
        """

        normalized = str(value or "idle").lower()
        if normalized == "busy":
            return SessionState.RUNNING
        if normalized == "retry":
            return SessionState.ERROR
        return SessionState.IDLE

    def _pending_session_ids(self, endpoint: OpenCodeEndpoint) -> set[str]:
        """Return session ids that have an unanswered permission or question.

        ``/permission`` and ``/question`` list pending approval/interaction
        requests.  A session with at least one pending request is WAITING for
        user input — the Deck key should show amber.

        On OpenCode v1.18.7+ the v1 endpoints may return empty for v2
        questions/permissions, so we also check the per-session v2 endpoints
        ``/api/session/{id}/question`` and ``/api/session/{id}/permission``.
        """

        pending: set[str] = set()
        for path in ("/permission", "/question"):
            try:
                response = self._client.get(f"{endpoint.url}{path}")
                response.raise_for_status()
                items = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("sessionID"), str):
                    pending.add(item["sessionID"])
        return pending

    def _pending_session_ids_v2(
        self, endpoint: OpenCodeEndpoint, session_ids: list[str]
    ) -> set[str]:
        """Check v2 per-session question/permission endpoints for pending items."""

        pending: set[str] = set()
        for sid in session_ids:
            paths = (
                (f"/session/{sid}/form", f"/session/{sid}/permission")
                if endpoint.api_version == 2
                else (f"/api/session/{sid}/question", f"/api/session/{sid}/permission")
            )
            for path in paths:
                try:
                    response = self._client.get(f"{endpoint.url}{path}")
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError):
                    continue
                items = body.get("data") if isinstance(body, dict) else body
                if isinstance(items, list) and items:
                    pending.add(sid)
                    break
        return pending

    def _active_session_ids(
        self, endpoint: OpenCodeEndpoint, session_ids: list[str]
    ) -> tuple[set[str], set[str]]:
        """Return ``(active, waiting)`` session-id sets from message polling.

        On OpenCode v1.18.7+ ``/session/status`` returns an empty map during
        tool execution (it only reports ``busy`` during LLM token generation).
        The reliable running indicator is the message list:

        - An assistant message whose ``time.completed`` is absent means the
          agent is actively generating or executing tools.
        - A tool part with ``state.status`` of ``running`` or ``pending`` means
          a tool call is in flight.

        A tool call whose ``tool`` name is ``question`` or ``permission`` is
        waiting for user input, not actively working — those session ids go into
        the ``waiting`` set so the Deck key shows amber instead of blue.

        **Staleness guard.**  When a session crashes, times out, or is killed
        mid-turn, OpenCode never writes ``time.completed`` and any tool part
        stays stuck at ``state.status='running'``.  Without a guard the Deck
        key would show RUNNING forever.  We treat messages and tool parts
        older than ``STALE_THRESHOLD_SECONDS`` as abandoned and do not count
        them as active.

        One ``GET /session/:id/message?limit=1`` call per session is cheap
        (5 sessions × 5 s interval = 1 req/s) and authoritative.
        """

        active: set[str] = set()
        waiting: set[str] = set()
        now = self._time_provider()
        for sid in session_ids:
            try:
                response = self._client.get(
                    f"{endpoint.url}/session/{sid}/message", params={"limit": 1}
                )
                response.raise_for_status()
                messages = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if not isinstance(messages, list) or not messages:
                continue
            msg = messages[-1] if isinstance(messages[-1], dict) else None
            if not msg:
                continue
            info = msg.get("info")
            if not isinstance(info, dict) or info.get("role") != "assistant":
                continue
            # Check tool parts FIRST — a question/permission tool in
            # running/pending state means the session is WAITING for user
            # input, even though time.completed is absent (the turn is
            # still open while the user hasn't answered).
            parts = msg.get("parts")
            classified = False
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict) or part.get("type") != "tool":
                        continue
                    state = part.get("state")
                    status = state.get("status") if isinstance(state, dict) else None
                    if status in ("running", "pending"):
                        # Staleness guard: if the tool started more than
                        # STALE_THRESHOLD_SECONDS ago it was abandoned by a
                        # crash or kill and should not count as active.
                        part_time = state.get("time") if isinstance(state, dict) else None
                        tool_start = (
                            part_time.get("start")
                            if isinstance(part_time, dict)
                            else None
                        )
                        if (
                            isinstance(tool_start, (int, float))
                            and now - tool_start / 1000 > STALE_THRESHOLD_SECONDS
                        ):
                            continue
                        tool_name = str(part.get("tool", ""))
                        if tool_name in ("question", "permission"):
                            waiting.add(sid)
                        else:
                            active.add(sid)
                        classified = True
                        break
            if classified:
                continue
            # Fall back to time/finish heuristics for sessions without
            # an explicit running/pending tool part.
            time_field = info.get("time")
            completed = time_field.get("completed") if isinstance(time_field, dict) else None
            if completed is not None:
                # Message has a completed timestamp — the turn ended
                # normally.  ``finish`` is ``None`` while the assistant
                # is still streaming, ``""`` when the turn ended without
                # tool-calls (e.g. a plain text reply or an abort), and a
                # non-empty string like ``"tool-calls"`` when tools were
                # called.  An empty-string ``finish`` with ``completed``
                # set means the session is NOT running.
                continue
            # ``completed`` is absent.  If the message was created more
            # than STALE_THRESHOLD_SECONDS ago, the session crashed or
            # was killed mid-turn — don't count it as active.
            created = time_field.get("created") if isinstance(time_field, dict) else None
            if (
                isinstance(created, (int, float))
                and now - created / 1000 > STALE_THRESHOLD_SECONDS
            ):
                continue
            # Message is recent and has no completed timestamp — the
            # assistant is actively generating.
            active.add(sid)
        return active, waiting

    def _v2_sessions(self, endpoint: OpenCodeEndpoint) -> tuple[list[Any], dict[str, Any]]:
        """Read the global, cursor-paginated V2 feed and authoritative drains."""
        items: list[Any] = []
        params = {"limit": "100", "order": "desc", "parentID": "null"}
        seen_cursors: set[str] = set()
        while True:
            response = self._client.get(f"{endpoint.url}/session", params=params)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise OpenCodeError("OpenCode returned an unexpected session response")
            items.extend(body["data"])
            cursor = body.get("cursor")
            if not isinstance(cursor, dict):
                raise OpenCodeError("OpenCode returned an invalid session cursor")
            next_cursor = cursor.get("next")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise OpenCodeError("OpenCode returned an invalid or repeated session cursor")
            seen_cursors.add(next_cursor)
            params["cursor"] = next_cursor
        response = self._client.get(f"{endpoint.url}/session/active")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise OpenCodeError("OpenCode returned an unexpected active session response")
        statuses = {
            sid: {"type": "busy"} for sid, state in body["data"].items()
            if isinstance(state, dict) and state.get("type") == "running"
        }
        # The root-only feed omits delegated sessions. Resolve only active
        # descendants and their ancestors, not the entire historical child feed.
        by_id = {item["id"]: item for item in items
                 if isinstance(item, dict) and isinstance(item.get("id"), str)}
        delegated_pending = self._pending_session_ids_v2(endpoint, list(statuses))
        pending_checked = set(statuses)
        aggregated: dict[str, Any] = {}
        for sid in statuses:
            current = sid
            visited: set[str] = set()
            while current not in visited:
                visited.add(current)
                item = by_id.get(current)
                if item is None:
                    response = self._client.get(f"{endpoint.url}/session/{current}")
                    if response.status_code == 404:
                        break  # A drain can disappear between the two requests.
                    response.raise_for_status()
                    body = response.json()
                    item = body.get("data") if isinstance(body, dict) else None
                    if not isinstance(item, dict) or item.get("id") != current:
                        raise OpenCodeError("OpenCode returned an unexpected session response")
                    by_id[current] = item
                if current not in pending_checked:
                    delegated_pending |= self._pending_session_ids_v2(endpoint, [current])
                    pending_checked.add(current)
                parent = item.get("parentID")
                if not isinstance(parent, str) or not parent:
                    state = "waiting" if visited & delegated_pending else "busy"
                    if aggregated.get(current, {}).get("type") != "waiting":
                        aggregated[current] = {"type": state}
                    break
                current = parent
        return items, aggregated

    def sessions(self) -> tuple[AgentSession, ...]:
        endpoint = self._require_endpoint()
        if endpoint.api_version == 2:
            try:
                raw_sessions, statuses = self._v2_sessions(endpoint)
            except (httpx.HTTPError, ValueError) as exc:
                raise OpenCodeError(f"Could not read OpenCode sessions: {exc}") from exc
        else:
            raw_sessions, statuses = self._legacy_sessions(endpoint)
        return self._session_states(endpoint, raw_sessions, statuses)

    def _legacy_sessions(self, endpoint: OpenCodeEndpoint) -> tuple[list[Any], dict[str, Any]]:
        try:
            # `/session` is scoped by the server's current workspace on newer
            # OpenCode versions.  The experimental list is the cross-project
            # feed and carries the global last-update ordering we need.
            sessions_response = self._client.get(f"{endpoint.url}/experimental/session")
            if sessions_response.status_code == 404:
                sessions_response = self._client.get(f"{endpoint.url}/session")
            statuses_response = self._client.get(f"{endpoint.url}/session/status")
            sessions_response.raise_for_status()
            statuses_response.raise_for_status()
            raw_sessions = sessions_response.json()
            statuses = statuses_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenCodeError(f"Could not read OpenCode sessions: {exc}") from exc
        if not isinstance(raw_sessions, list) or not isinstance(statuses, dict):
            raise OpenCodeError("OpenCode returned an unexpected session response")
        return raw_sessions, statuses

    def _session_states(
        self, endpoint: OpenCodeEndpoint, raw_sessions: list[Any], statuses: dict[str, Any]
    ) -> tuple[AgentSession, ...]:
        visible_sessions: list[Any] = []
        for item in raw_sessions:
            if isinstance(item, dict):
                time = item.get("time")
                if isinstance(time, dict) and time.get("archived") is not None:
                    continue
                metadata = item.get("metadata")
                chamber = metadata.get("openchamber") if isinstance(metadata, dict) else None
                work = chamber.get("work") if isinstance(chamber, dict) else None
                # OpenChamber's explicit Done marker is distinct from a turn
                # finishing. Keep done work in OpenChamber history, not Deck slots.
                if isinstance(work, dict) and work.get("state") == "done":
                    continue
            visible_sessions.append(item)
        raw_sessions = visible_sessions
        pending = self._pending_session_ids(endpoint) if endpoint.api_version == 1 else set()
        live_ids: set[str] = set()
        result: list[AgentSession] = []
        candidate_ids: list[str] = []
        for item in sorted(raw_sessions, key=_updated_at, reverse=True):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if isinstance(item.get("parentID"), str) and item["parentID"]:
                continue
            candidate_ids.append(item["id"])
        visible_candidate_ids = candidate_ids[:LIVE_STATE_SESSION_LIMIT]
        pending_candidate_ids = visible_candidate_ids
        if endpoint.api_version == 2:
            # A pinned/older root may still own a drain and be awaiting input.
            pending_candidate_ids = list(dict.fromkeys([
                *visible_candidate_ids, *(sid for sid in candidate_ids if sid in statuses)
            ]))
        if pending_candidate_ids:
            pending |= self._pending_session_ids_v2(endpoint, pending_candidate_ids)
        # Always poll the last message of each session.  On OpenCode v1.18.7+
        # /session/status returns {} during tool execution — it only reports
        # "busy" during LLM token generation.  Message-based detection is the
        # authoritative signal for "agent is working".
        active_ids, waiting_ids = (
            self._active_session_ids(endpoint, visible_candidate_ids)
            if visible_candidate_ids and endpoint.api_version == 1
            else (set(), set())
        )
        pending |= waiting_ids
        if endpoint.api_version == 2:
            pending |= {sid for sid, state in statuses.items() if state.get("type") == "waiting"}
        for item in sorted(raw_sessions, key=_updated_at, reverse=True):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if isinstance(item.get("parentID"), str) and item["parentID"]:
                continue
            session_id = item["id"]
            live_ids.add(session_id)
            status_value = statuses.get(session_id)
            if isinstance(status_value, dict):
                status_value = status_value.get("type") or status_value.get("status")
            location = item.get("location")
            directory = (
                location.get("directory") if isinstance(location, dict) else item.get("directory")
            )
            project = _project_folder(directory, item.get("projectID"))
            if session_id in pending:
                state = SessionState.WAITING
                if endpoint.api_version == 2:
                    self._recently_busy.add(session_id)
                self._completed.discard(session_id)
            else:
                raw_state = self._state(status_value)
                if raw_state is SessionState.IDLE and session_id in active_ids:
                    raw_state = SessionState.RUNNING
                if raw_state is SessionState.RUNNING:
                    self._recently_busy.add(session_id)
                    self._completed.discard(session_id)
                    state = raw_state
                elif raw_state is SessionState.ERROR:
                    self._recently_busy.discard(session_id)
                    self._completed.discard(session_id)
                    state = raw_state
                elif session_id in self._recently_busy:
                    self._recently_busy.discard(session_id)
                    self._completed.add(session_id)
                    state = SessionState.COMPLETED
                else:
                    state = raw_state
            result.append(
                AgentSession(
                    id=session_id,
                    title=str(item.get("title") or "Untitled session"),
                    state=state,
                    project=project,
                    directory=directory if isinstance(directory, str) else None,
                )
            )
        self._recently_busy &= live_ids
        self._completed &= live_ids
        return tuple(result)

    def snapshot(self) -> PluginSnapshot:
        try:
            endpoint = self._require_endpoint()
            return PluginSnapshot(PluginHealth(True, "Connected", endpoint.url), self.sessions())
        except OpenCodeError as exc:
            return PluginSnapshot(PluginHealth(False, str(exc)), ())

    def mark_viewed(self, session_id: str) -> None:
        """Clear the COMPLETED flag after the user acknowledges the session."""
        self._completed.discard(session_id)

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        endpoint = self._require_endpoint()
        if action == "select_session":
            session_id = _required_string(params, "session_id")
            self.mark_viewed(session_id)
            response = self._client.post(
                f"{endpoint.url}/tui/select-session", json={"sessionID": session_id}
            )
        else:
            raise OpenCodeError(f"Unsupported OpenCode action: {action}")
        try:
            response.raise_for_status()
            return response.json() if response.content else {"ok": True}
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenCodeError(f"OpenCode action failed: {exc}") from exc


def _required_string(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OpenCodeError(f"{name} is required")
    return value


def _project_folder(directory: Any, project_id: Any) -> str | None:
    """Return the human-readable project folder that belongs on a Deck key."""

    if isinstance(directory, str) and directory.strip():
        name = PureWindowsPath(directory.rstrip("\\/")).name
        if name:
            return name
    return str(project_id) if isinstance(project_id, str) and project_id else None


def _updated_at(session: Any) -> int:
    if not isinstance(session, dict):
        return 0
    time = session.get("time")
    value = time.get("updated") if isinstance(time, dict) else None
    return value if isinstance(value, int) else 0
