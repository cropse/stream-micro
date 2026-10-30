import httpx

from stream_micro.bridge import BridgeService
from stream_micro.control_config import default_control_config, write_control_config
from stream_micro.openchamber import (
    OpenChamberDesktop,
    discover_openchamber_desktop,
    select_openchamber_session,
    session_deep_link,
)
from stream_micro.plugins.opencode import OpenCodePlugin


def test_session_deep_link_preserves_session_and_directory() -> None:
    assert session_deep_link(session_id="ses_abc", directory="C:\\work space\\project") == (
        "openchamber://session/ses_abc?dir=C%3A%5Cwork%20space%5Cproject"
    )


def test_select_session_dispatches_registered_openchamber_protocol(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("stream_micro.openchamber.os.startfile", opened.append)

    select_openchamber_session(session_id="ses_abc", directory=None)

    assert opened == ["openchamber://session/ses_abc"]


def test_discovery_uses_openchambers_authenticated_opencode_proxy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "http://127.0.0.1:57123/health":
            return httpx.Response(
                200,
                json={
                    "openchamberVersion": "1.17.0",
                    "isOpenCodeReady": True,
                    "openCodePort": 1209,
                },
            )
        return httpx.Response(404)

    desktop = discover_openchamber_desktop(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        port_provider=lambda: {1209, 57123},
    )

    assert desktop == OpenChamberDesktop(
        url="http://127.0.0.1:57123",
        opencode_proxy_url="http://127.0.0.1:57123/api",
        version="1.17.0",
    )


def test_bridge_prefers_the_discovered_openchamber_session_source(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "bridge.yaml"
    config = default_control_config()
    config.plugins["opencode"]["enabled"] = True
    write_control_config(config_path, config)
    desktop = OpenChamberDesktop(
        url="http://127.0.0.1:57123",
        opencode_proxy_url="http://127.0.0.1:57123/api",
        version="1.17.0",
    )
    monkeypatch.setattr("stream_micro.bridge.discover_openchamber_desktop", lambda: desktop)

    service = BridgeService(config_path)

    plugin = service.plugins["opencode"]
    assert isinstance(plugin, OpenCodePlugin)
    assert plugin.config["endpoints"][0] == desktop.opencode_proxy_url
