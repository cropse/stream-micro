from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import cast

import pytest

from stream_micro import deck_bridge
from stream_micro.bridge import BridgeService
from stream_micro.control_config import default_control_config, write_control_config
from stream_micro.device import DeviceError


@pytest.mark.parametrize("unavailable", [False, True])
def test_runner_stop_releases_device_and_interrupts_retry(monkeypatch, unavailable):
    entered, released = Event(), Event()

    class Output:
        def __init__(self, _config):
            pass

        def __enter__(self):
            entered.set()
            if unavailable:
                raise DeviceError("unplugged")
            return self

        def __exit__(self, *_args):
            released.set()

        def set_key_callback(self, _callback):
            pass

    service = SimpleNamespace(config=default_control_config(), device_status={})
    runner = deck_bridge.Mk2BridgeRunner(cast(BridgeService, service))
    monkeypatch.setattr(deck_bridge, "Mk2DeckOutput", Output)
    monkeypatch.setattr(runner, "refresh", lambda _output: None)
    thread = Thread(target=runner.run, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        assert hasattr(runner, "stop"), "runner needs cooperative stop"
        runner.stop()
        thread.join(1)
        assert not thread.is_alive()
        assert unavailable or released.is_set()
    finally:
        if hasattr(runner, "stop"):
            runner.stop()


def test_runtime_configured_url_and_idempotent_stop(tmp_path: Path):
    from stream_micro import runtime

    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    instance = runtime.BridgeRuntime(path)
    assert instance.dashboard_url == "http://127.0.0.1:8765"
    assert instance.stop()
    assert instance.stop()
    assert not instance.is_running


def test_runtime_timeout_retains_ownership_and_blocks_restart(tmp_path, monkeypatch):
    from stream_micro import runtime

    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    instance = runtime.BridgeRuntime(path)
    released = Event()
    thread = Thread(target=lambda: released.wait(5), daemon=True)
    thread.start()
    instance._threads = [thread]
    try:
        assert not instance.stop(timeout=0.01)
        with pytest.raises(RuntimeError, match="not stopped"):
            instance.start()
    finally:
        released.set()
        thread.join(1)
        assert instance.stop()


def test_runtime_url_stays_bound_when_config_changes(tmp_path):
    from stream_micro import runtime

    path = tmp_path / "bridge.yaml"
    write_control_config(path, default_control_config())
    instance = runtime.BridgeRuntime(path)
    path.write_text("invalid: [", encoding="utf-8")
    assert instance.dashboard_url == "http://127.0.0.1:8765"
