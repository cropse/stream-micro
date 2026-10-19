"""Loopback-only FastAPI application for Deck Bridge."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .bridge import BridgeError, BridgeService
from .claude_setup import ClaudeSetupError
from .control_config import ControlConfigError
from .device import DeviceError, discover_mk2_devices
from .plugins.antigravity import AntigravityError
from .plugins.claude_code import ClaudeCodeError
from .plugins.codex import CodexError
from .plugins.opencode import OpenCodeError

_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def own_page_only(request: Request) -> None:
    """Refuse a browser request that comes from another site or a rebound name.

    Browsers send Origin with every PUT; it must be this page's own, and the
    Host a loopback name, so a page that rebinds its DNS name to 127.0.0.1
    cannot change settings. Requests without Origin (scripts, tests) pass.
    """

    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    name = host[1 : host.find("]")] if host.startswith("[") else host.split(":")[0]
    if name not in _LOOPBACK_NAMES or origin not in (f"http://{host}", f"https://{host}"):
        raise HTTPException(
            status_code=403, detail="Only this Bridge's own dashboard may change its settings"
        )


def create_app(config_path: Path | str, *, service: BridgeService | None = None) -> FastAPI:
    service = service or BridgeService(config_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Deck Bridge", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.bridge = service

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return service.public_config_data()

    @app.put("/api/config")
    def put_config(data: dict[str, Any], request: Request) -> dict[str, Any]:
        own_page_only(request)
        try:
            return service.replace_config(data)
        except ControlConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/plugins")
    def get_plugins() -> list[dict[str, Any]]:
        return service.manifests()

    @app.get("/api/plugins/snapshots")
    def get_plugin_snapshots() -> dict[str, dict[str, Any]]:
        """Return display-ready data keyed by provider ID."""

        return service.snapshots_data()

    @app.get("/api/devices")
    def get_devices() -> list[dict[str, str | int | None]]:
        try:
            return discover_mk2_devices()
        except DeviceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/status")
    def get_status(include_plugins: bool = True) -> dict[str, Any]:
        return service.status_data(include_plugins=include_plugins)

    @app.get("/api/opencode/servers")
    def get_opencode_servers() -> list[dict[str, str]]:
        try:
            return service.opencode_servers()
        except (BridgeError, OpenCodeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/opencode/sessions")
    def get_opencode_sessions() -> list[dict[str, Any]]:
        try:
            return service.opencode_sessions()
        except (BridgeError, OpenCodeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/opencode/sessions/{session_id}/select")
    def select_opencode_session(session_id: str) -> dict[str, str]:
        """Use OpenChamber Desktop to select the exact OpenCode Session."""

        try:
            return service.select_session_in_openchamber(session_id)
        except (BridgeError, CodexError, OpenCodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/hooks/claude-code")
    def post_claude_code_hook(data: dict[str, Any]) -> dict[str, str | bool | None]:
        """Accept loopback lifecycle events from the bundled Claude Code hook."""

        try:
            return service.handle_claude_code_hook(data)
        except (BridgeError, ClaudeCodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def same_origin_only(request: Request) -> None:
        """Writing Claude Code's settings needs our own page: an Origin (always
        sent by browsers) for this loopback Host, and a header a plain
        cross-site form cannot set."""

        if request.headers.get("origin") is None or request.headers.get("x-stream-micro") != "1":
            raise HTTPException(status_code=403, detail="Only the Deck Bridge page may do this")
        own_page_only(request)

    @app.get("/api/claude-code/setup")
    def get_claude_setup() -> dict[str, Any]:
        return service.claude_setup_status()

    @app.post("/api/claude-code/setup")
    def post_claude_setup(data: dict[str, Any], request: Request) -> dict[str, Any]:
        same_origin_only(request)
        parts = data.get("parts")
        try:
            return service.change_claude_setup(
                str(data.get("action")),
                set(parts) if isinstance(parts, list) else set(),
                data.get("replace_statusline") is True,
                data.get("chain_statusline") is True,
            )
        except ClaudeSetupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/hooks/claude-code/quota")
    def post_claude_code_quota(data: dict[str, Any]) -> dict[str, str | bool | None]:
        """Accept quota fields from a local Claude Code status-line command."""

        try:
            return service.handle_claude_code_quota(data)
        except (BridgeError, ClaudeCodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/hooks/antigravity/status")
    def post_antigravity_status(data: dict[str, Any]) -> dict[str, str | bool | None]:
        try:
            return service.handle_antigravity_status(data)
        except (BridgeError, AntigravityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/opencode/sessions/{session_id}/capture-window")
    def capture_session_window(session_id: str) -> dict[str, str]:
        try:
            return service.capture_session_window(session_id)
        except BridgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/opencode/sessions/{session_id}/confirm-window")
    def confirm_session_window(session_id: str) -> dict[str, Any]:
        try:
            return service.confirm_session_window(session_id)
        except BridgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/opencode/sessions/{session_id}/focus-window")
    def focus_session_window(session_id: str) -> dict[str, bool]:
        try:
            service.focus_session_window(session_id)
            return {"ok": True}
        except BridgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/actions")
    def post_action(data: dict[str, Any]) -> Any:
        plugin = data.get("plugin")
        action = data.get("action")
        params = data.get("params", {})
        if (
            not isinstance(plugin, str)
            or not isinstance(action, str)
            or not isinstance(params, dict)
        ):
            raise HTTPException(status_code=422, detail="plugin, action, and params are required")
        try:
            return service.invoke(plugin, action, params)
        except (BridgeError, AntigravityError, ClaudeCodeError, CodexError, OpenCodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/deck/keys/{key_index}/press")
    def post_deck_key_press(key_index: int) -> dict[str, Any]:
        try:
            return service.press_key(key_index)
        except (BridgeError, AntigravityError, ClaudeCodeError, CodexError, OpenCodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/events")
    async def get_events() -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            while True:
                payload = json.dumps(service.status_data(), default=str)
                yield f"event: status\ndata: {payload}\n\n"
                await asyncio.sleep(service.config.bridge.refresh_interval_seconds)

        return StreamingResponse(stream(), media_type="text/event-stream")

    dashboard_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dashboard_dir / "index.html").is_file():
        # Windows registry entries can map .js to text/plain, which browsers reject
        # for module scripts.
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/css", ".css")
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    return app
