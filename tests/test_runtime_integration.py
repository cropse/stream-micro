import socket
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from stream_micro import runtime
from stream_micro.control_config import (
    control_config_data,
    default_control_config,
    parse_control_config,
    write_control_config,
)


def test_runtime_real_http_restart_and_port_conflict(tmp_path: Path, monkeypatch):
    events = []

    class Service:
        def __init__(self, _path):
            events.append("created")

        def close(self):
            events.append("closed")

    class Runner:
        def __init__(self, _service):
            from threading import Event
            self.done = Event()

        def run(self):
            self.done.wait()
            events.append("released")

        def stop(self):
            self.done.set()

    app = FastAPI()

    @app.get("/api/status")
    def status():
        return {"ok": True}

    monkeypatch.setattr(runtime, "BridgeService", Service)
    monkeypatch.setattr(runtime, "Mk2BridgeRunner", Runner)
    monkeypatch.setattr(runtime, "create_app", lambda *_a, **_k: app)
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        port = owner.getsockname()[1]
        data = control_config_data(default_control_config())
        data["bridge"]["port"] = port
        path = tmp_path / "bridge.yaml"
        write_control_config(path, parse_control_config(data))
        instance = runtime.BridgeRuntime(path)
        with pytest.raises(OSError):
            instance.start()
        assert events == []
        assert owner.getsockname()[1] == port
    for _ in range(2):
        try:
            instance.start()
            assert instance.is_running
            assert httpx.get(instance.dashboard_url + "/api/status", trust_env=False).json() == {
                "ok": True}
        finally:
            assert instance.stop()
        assert events[-2:] == ["released", "closed"]
        assert not instance.is_running
