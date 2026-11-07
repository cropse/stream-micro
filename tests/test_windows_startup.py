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


def test_frozen_registration_runs_only_the_executable(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    exe = tmp_path / "工具 空 白" / "StreamMicro.exe"
    registration = StartupRegistration(
        exe.parent / ".." / exe.parent.name / "bridge.yaml",
        tmp_path / "missing-pythonw.exe", tmp_path,
        frozen=True, executable=exe,
    )
    registration.set_enabled(True)  # pythonw 不存在也不該被擋
    assert registration.is_enabled()
    content = registration.path.read_text(encoding="utf-16")
    assert "StreamMicro.exe" in content
    assert "--bridge-config" not in content
    run_line = next(line for line in content.splitlines() if line.startswith("sh.Run "))
    quoted_executable = '"' + str(exe.resolve()).replace('"', '""') + '"'
    vbs_command = '"' + quoted_executable.replace('"', '""') + '"'
    assert run_line == f"sh.Run {vbs_command}, 0, False"
    registration.set_enabled(False)
    assert not registration.path.exists()


def test_frozen_registration_preserves_custom_config(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    exe = tmp_path / "工具 空 白" / "StreamMicro.exe"
    config = tmp_path / "自訂 設定" / "bridge.yaml"
    registration = StartupRegistration(
        config, tmp_path / "missing-pythonw.exe", tmp_path,
        frozen=True, executable=exe,
    )
    assert not registration.is_enabled()
    registration.set_enabled(True)
    assert registration.is_enabled()
    content = registration.path.read_text(encoding="utf-16")
    assert str(exe.resolve()) in content
    assert "--bridge-config" in content
    assert str(config.resolve()) in content
    registration.set_enabled(True)
    registration.set_enabled(False)
    assert not registration.is_enabled()
    registration.path.write_text("foreign", encoding="utf-16")
    with pytest.raises(RuntimeError):
        registration.set_enabled(True)
    with pytest.raises(RuntimeError):
        registration.set_enabled(False)
    assert registration.path.read_text(encoding="utf-16") == "foreign"


def test_frozen_registration_refuses_foreign_entry(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    registration = StartupRegistration(
        tmp_path / "bridge.yaml", tmp_path / "pythonw.exe", tmp_path,
        frozen=True, executable=tmp_path / "StreamMicro.exe",
    )
    registration.path.write_text("foreign", encoding="utf-16")
    with pytest.raises(RuntimeError):
        registration.set_enabled(True)
    assert registration.path.read_text(encoding="utf-16") == "foreign"
