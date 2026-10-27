"""Opt-in current-user Startup registration, with exact ownership checks."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from . import paths
from .windows_instance import config_key


def startup_directory() -> Path:
    # CSIDL_STARTUP: current user's Startup known folder (including redirection).
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 7, None, 0, buffer)
    if result != 0:
        raise OSError("Cannot locate current-user Startup folder")
    return Path(buffer.value)


class StartupRegistration:
    def __init__(self, config_path: Path, pythonw: Path,
                 startup_dir: Path | None = None, *, frozen: bool | None = None,
                 executable: Path | None = None) -> None:
        self.config_path = config_path.resolve()
        self.pythonw = pythonw.resolve()
        self.frozen = paths.is_frozen() if frozen is None else frozen
        directory = startup_dir if startup_dir is not None else startup_directory()
        self.path = directory / ("StreamMicro-" + config_key(config_path) + ".vbs")
        if self.frozen:
            exe = (executable if executable is not None else Path(sys.executable)).resolve()
            arguments = [str(exe)]
            if self.config_path != (exe.parent / "bridge.yaml").resolve():
                arguments.extend(["--bridge-config", str(self.config_path)])
            command = subprocess.list2cmdline(arguments)
        else:
            command = subprocess.list2cmdline([
                str(self.pythonw), "-m", "stream_micro.cli", "tray",
                "--bridge-config", str(self.config_path),
            ])
        def quoted(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'
        self._contents = (
            "' Stream Micro owned startup registration\r\n"
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f"sh.CurrentDirectory = {quoted(str(self.config_path.parent))}\r\n"
            f"sh.Run {quoted(command)}, 0, False\r\n"
        )

    def is_enabled(self) -> bool:
        try:
            return self.path.read_text(encoding="utf-16").replace("\r\n", "\n") == (
                self._contents.replace("\r\n", "\n"))
        except (OSError, UnicodeError):
            return False

    def set_enabled(self, enabled: bool) -> None:
        if self.path.exists() and not self.is_enabled():
            raise RuntimeError("Startup entry belongs to another installation; left unchanged")
        if enabled:
            if not self.pythonw.is_file() and not self.frozen:
                raise FileNotFoundError("pythonw.exe not found; run uv sync first")
            if not self.path.exists():
                with self.path.open("x", encoding="utf-16", newline="") as output:
                    output.write(self._contents)
        elif self.path.exists():
            self.path.unlink()
