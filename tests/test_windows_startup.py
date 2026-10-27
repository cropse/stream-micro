from pathlib import Path

import pytest


def test_startup_opt_in_round_trip_and_foreign_file(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    pythonw = tmp_path / "空 白" / "pythonw.exe"
    pythonw.parent.mkdir()
    pythonw.touch()
    config = pythonw.parent / "bridge.yaml"
    startup = tmp_path / "Startup"
    startup.mkdir()
    registration = StartupRegistration(config, pythonw, startup)
    assert not registration.is_enabled()
    registration.set_enabled(True)
    assert registration.is_enabled()
    registration.set_enabled(True)
    registration.set_enabled(False)
    assert not list(startup.iterdir())
    registration.path.write_text("foreign", encoding="utf-16")
    with pytest.raises(RuntimeError):
        registration.set_enabled(True)
    with pytest.raises(RuntimeError):
        registration.set_enabled(False)
    assert registration.path.read_text(encoding="utf-16") == "foreign"


def test_startup_missing_interpreter_writes_nothing(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    registration = StartupRegistration(tmp_path / "bridge.yaml", tmp_path / "missing.exe", tmp_path)
    with pytest.raises(FileNotFoundError):
        registration.set_enabled(True)
    assert not registration.path.exists()
