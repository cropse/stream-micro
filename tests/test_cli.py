from __future__ import annotations

from pathlib import Path

from stream_micro import cli
from stream_micro.control_config import load_control_config


def test_serve_creates_separate_bridge_configuration(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "bridge.yaml"
    calls: list[tuple[object, str, int]] = []

    class Runtime:
        is_running = False

        def __init__(self, path):
            self.path = path

        def start(self):
            config = load_control_config(self.path).bridge
            calls.append((self, config.host, config.port))

        def stop(self):
            return True

    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", Runtime)

    assert cli.main(["serve", "--bridge-config", str(config_path)]) == 0
    assert config_path.exists()
    assert load_control_config(config_path).stream_deck.model == "mk2"
    assert len(calls) == 1
    assert calls[0][1:] == ("127.0.0.1", 8765)


def test_serve_reload_uses_factory_and_watches_python_sources(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "bridge.yaml"
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(application: object, **kwargs: object) -> None:
        calls.append((application, kwargs))

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert cli.main(["serve", "--reload", "--bridge-config", str(config_path)]) == 0
    assert calls == [
        (
            "stream_micro.cli:create_reload_app",
            {
                "factory": True,
                "reload": True,
                "reload_dirs": [str(Path("src").resolve())],
                "host": "127.0.0.1",
                "port": 8765,
            },
        )
    ]
