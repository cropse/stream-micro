"""Window foreground forcing for app-switching deep links.

Windows denies ``SetForegroundWindow`` from background processes while the
foreground lock is engaged (user typed/clicked another window within the
``ForegroundLockTimeout``).  A synthetic ALT keypress makes the calling
thread the "last input" thread and grants the foreground right, which is how
the Stream Deck key press brings the switched-to app window to the front
even while the user is typing elsewhere.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class WindowFocusError(RuntimeError):
    pass


def _enum_windows_find(
    user32: ctypes.WinDLL,
    kernel32: ctypes.WinDLL,
    executable_names: tuple[str, ...],
) -> list[int]:
    """Return visible top-level windows owned by one of ``executable_names``."""

    lowered = tuple(name.casefold() for name in executable_names)
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def check(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return True
        process = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process:
            return True
        try:
            executable = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(executable))
            if not kernel32.QueryFullProcessImageNameW(process, 0, executable, ctypes.byref(size)):
                return True
        finally:
            kernel32.CloseHandle(process)
        name = executable.value.rsplit("\\", 1)[-1].casefold()
        if name in lowered:
            found.append(hwnd)
        return True

    callback = callback_type(check)
    user32.EnumWindows(callback, 0)
    return found


def focus_app_windows(executable_names: tuple[str, ...]) -> None:
    """Bring the first visible window of one app to the foreground.

    Works while the Windows foreground lock is engaged because the synthetic
    ALT keypress makes this thread the last-input thread, granting the
    ``SetForegroundWindow`` right that plain calls from a background process
    would be denied.
    """

    if not executable_names:
        return
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found = _enum_windows_find(user32, kernel32, executable_names)
    if not found:
        raise WindowFocusError(
            f"No visible window for {', '.join(executable_names)} is currently open"
        )
    _force_foreground(user32, found[0])


def _force_foreground(user32: ctypes.WinDLL, hwnd: int) -> None:
    """Focus one window even under the Windows foreground lock."""

    # Synthetic ALT press/release grants this thread the foreground right.
    user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
    user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        raise WindowFocusError("Windows did not allow the app window to be focused")
