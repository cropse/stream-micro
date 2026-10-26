"""Official OpenChamber Desktop Session navigation."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .windows_focus import WindowFocusError, focus_app_windows

DEFAULT_DESKTOP_PORT = 57123


class OpenChamberError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenChamberDesktop:
    """A local OpenChamber Desktop control plane and its authenticated API proxy."""

    url: str
    opencode_proxy_url: str
    version: str


def _loopback_ports() -> Iterable[int]:
    """Reuse the Bridge's local-only listener discovery without a LAN scan."""

    from .plugins.opencode import default_loopback_ports

    return default_loopback_ports()


def discover_openchamber_desktop(
    *,
    client: httpx.Client | None = None,
    port_provider: Callable[[], Iterable[int]] = _loopback_ports,
) -> OpenChamberDesktop | None:
    """Find OpenChamber Desktop and its authenticated OpenCode proxy on loopback.

    OpenChamber owns an authenticated OpenCode Server.  Its ``/api`` proxy is
    the correct endpoint for the Bridge: the raw OpenCode port requires a token
    that must remain private to OpenChamber.
    """

    ports: list[int] = [DEFAULT_DESKTOP_PORT]
    with suppress(OSError, RuntimeError):
        ports.extend(port for port in port_provider() if isinstance(port, int))
    candidates = tuple(dict.fromkeys(port for port in ports if 0 < port < 65536))

    owns_client = client is None
    probe = client or httpx.Client(timeout=0.35)
    try:
        for port in candidates:
            url = f"http://127.0.0.1:{port}"
            try:
                response = probe.get(f"{url}/health")
                body = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if not response.is_success or not isinstance(body, dict):
                continue
            version = body.get("openchamberVersion")
            if not isinstance(version, str) or not version:
                continue
            if body.get("isOpenCodeReady") is not True:
                continue
            return OpenChamberDesktop(
                url=url,
                opencode_proxy_url=f"{url}/api",
                version=version,
            )
    finally:
        if owns_client:
            probe.close()
    return None


def session_deep_link(*, session_id: str, directory: str | None = None) -> str:
    """Build OpenChamber's documented native Session navigation URL."""

    normalized_id = session_id.strip()
    if not normalized_id:
        raise OpenChamberError("OpenChamber session ID is required")
    url = f"openchamber://session/{quote(normalized_id, safe='')}"
    if directory and directory.strip():
        url = f"{url}?dir={quote(directory.strip(), safe='')}"
    return url


def select_openchamber_session(*, session_id: str, directory: str | None = None) -> None:
    """Ask the registered OpenChamber Desktop app to focus a Session."""

    if os.name != "nt":
        raise OpenChamberError("OpenChamber Desktop Session switching is available only on Windows")
    try:
        os.startfile(session_deep_link(session_id=session_id, directory=directory))
    except OSError as exc:
        raise OpenChamberError(
            "Could not open OpenChamber. Install or start OpenChamber Desktop first."
        ) from exc
    # The deep link switches the Session inside the app but Windows may deny
    # the app's own focus attempt (foreground lock). Force the window front.
    with suppress(WindowFocusError):
        focus_app_windows(("OpenChamber.exe",))
