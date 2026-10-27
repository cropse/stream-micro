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


class _ImmediateRuntime:
    is_running = False

    def __init__(self, path):
        self.path = path

    def start(self):
        pass

    def stop(self):
        return True


def test_created_config_message_follows_language(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", _ImmediateRuntime)
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "zh-TW")

    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: 已建立 Bridge 設定檔：" in capsys.readouterr().out


def test_created_config_message_stays_english_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", _ImmediateRuntime)
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "en")

    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: created Bridge configuration at" in capsys.readouterr().out


def test_frozen_serve_defaults_bridge_config_next_to_exe(tmp_path, monkeypatch) -> None:
    from stream_micro import cli

    seen: list[Path] = []

    class Runtime:
        def __init__(self, config_path):
            seen.append(Path(config_path))

        def start(self):
            pass

        is_running = False
        error = None

        def stop(self):
            pass

    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", Runtime)
    assert cli.main(["serve"]) == 0
    assert seen == [tmp_path / "bridge.yaml"]


def test_explicit_bridge_config_survives_frozen(tmp_path, monkeypatch) -> None:
    from stream_micro import cli

    seen: list[Path] = []

    class Runtime:
        def __init__(self, config_path):
            seen.append(Path(config_path))

        def start(self):
            pass

        is_running = False
        error = None

        def stop(self):
            pass

    explicit = tmp_path / "custom.yaml"
    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", Runtime)
    assert cli.main(["serve", "--bridge-config", str(explicit)]) == 0
    assert seen == [explicit]


def test_frozen_without_command_enters_tray(monkeypatch, tmp_path) -> None:
    from stream_micro import cli

    called: list[Path] = []
    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr(
        "stream_micro.tray.main", lambda config_path: called.append(Path(config_path)) or 0
    )
    assert cli.main([]) == 0
    assert called == [tmp_path / "bridge.yaml"]
