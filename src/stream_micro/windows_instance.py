"""Per-user, per-config Windows instance guard; never identifies owners by port."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path


def config_key(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()[:24]


class SingleInstance:
    def __init__(self, config_path: Path) -> None:
        self.acquired = False
        self._handle = None
        self._name = "Local\\StreamMicro-" + config_key(Path.home()) + config_key(config_path)

    def __enter__(self) -> SingleInstance:
        from ctypes import wintypes

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel.CreateMutexW(None, False, self._name)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        return self

    def __exit__(self, *_args: object) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
