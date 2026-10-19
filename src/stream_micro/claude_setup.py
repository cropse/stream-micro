"""Install the Claude Code companion hook and status line for this Bridge.

Both live in Claude Code's user settings (``~/.claude/settings.json``) and
call the bundled scripts in ``integrations/claude-code-stream-micro``.  Only
entries that run those scripts with our ``--stream-micro-url`` argument are
ever added, updated or removed; the user's own hooks and settings are kept as
they are.  Every write first copies the file to a backup next to it.

Commands are ``node "<script>" --stream-micro-url=...``: plain enough for the
shell Claude Code uses on Windows (Git Bash) and for ``cmd``, with no
environment-variable prefix.  Paths with characters a shell would expand are
refused rather than quoted.

Another tool's status line is kept only with consent: chained (it still draws
the line and ours only reports quota) or replaced.  Removing ours gives a
chained status line back exactly as it was.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

INTEGRATION_DIR = Path(__file__).resolve().parents[2] / "integrations" / "claude-code-stream-micro"
HOOK_SCRIPT = "report-status.mjs"
QUOTA_SCRIPT = "report-quota.mjs"
# The events the bundled plugin listens to (hooks/hooks.json).
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PermissionRequest",
    "Notification",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
PARTS = frozenset({"hooks", "statusline"})
# Another tool's status line, kept next to settings.json while ours is chained.
CHAIN_FILE = "stream-micro-statusline-next.json"
HOOK_PATH = "/api/hooks/claude-code"
QUOTA_PATH = "/api/hooks/claude-code/quota"
URL_ARG = "--stream-micro-url="
NEXT_ARG = "--stream-micro-next="
# Earlier installs passed the URL in an environment variable.
LEGACY_MARKERS = ("STREAM_MICRO_CLAUDE_HOOK_URL=", "STREAM_MICRO_CLAUDE_QUOTA_URL=")
# Characters a shell could expand or that would end the quoting.
_UNSAFE_PATH = re.compile(r"[\"'`$!%\\\n\r]")


# One change at a time: two Dashboard tabs must not each read the file and
# write back their own copy over the other's change.
_CHANGE_LOCK = threading.Lock()


class ClaudeSetupError(RuntimeError):
    """Raised when Claude Code's settings cannot be read or changed safely."""


def _script(name: str) -> str:
    return (INTEGRATION_DIR / "scripts" / name).as_posix()


def _safe(path: str) -> str:
    if _UNSAFE_PATH.search(path):
        raise ClaudeSetupError(
            f"{path} contains characters a shell would expand; move it to a plain path"
        )
    return path


def check_scripts() -> None:
    """The bundled scripts must exist where the commands point (a source checkout)."""

    for name in (HOOK_SCRIPT, QUOTA_SCRIPT):
        if not (INTEGRATION_DIR / "scripts" / name).is_file():
            raise ClaudeSetupError(
                f"The bundled script {name} was not found under {INTEGRATION_DIR}; "
                "run the Bridge from its source folder to install the companions"
            )
        _safe(_script(name))


def hook_command(port: int) -> str:
    url = f"http://127.0.0.1:{port}{HOOK_PATH}"
    return f'node "{_script(HOOK_SCRIPT)}" {URL_ARG}{url}'


def statusline_command(port: int, chain_file: Path | None = None) -> str:
    url = f"http://127.0.0.1:{port}{QUOTA_PATH}"
    chain = f' {NEXT_ARG}"{_safe(chain_file.as_posix())}"' if chain_file else ""
    return f'node "{_script(QUOTA_SCRIPT)}" {URL_ARG}{url}{chain}'


def _runs_script(command: str, script: str) -> bool:
    """The command names a file called exactly ``script`` (in any folder)."""

    pattern = r"(?:^|[\s\"'/\\])" + re.escape(script) + r"(?:$|[\s\"'])"
    return re.search(pattern, command) is not None


def _is_ours(command: object, script: str) -> bool:
    """Ours: runs our script with our URL argument (or the earlier variable)."""

    if not isinstance(command, str) or not _runs_script(command, script):
        return False
    return URL_ARG in command or any(marker in command for marker in LEGACY_MARKERS)


def _points_here(command: str, port: int, path: str) -> bool:
    if "http://127.0.0.1:" in command or "http://localhost:" in command:
        return f":{port}{path}" in command
    return port == 8765  # the scripts' default


def read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        raise ClaudeSetupError(f"{path} could not be read as JSON; fix or move it first") from exc
    if not isinstance(data, dict):
        raise ClaudeSetupError(f"{path} must hold a JSON object")
    return data


def _status(data: dict[str, Any]) -> dict[str, Any] | None:
    status = data.get("statusLine")
    return status if isinstance(status, dict) else None


def _status_command(data: dict[str, Any]) -> str | None:
    status = _status(data)
    command = status.get("command") if status else None
    return command if isinstance(command, str) and command else None


def inspect(path: Path, port: int) -> dict[str, Any]:
    """What is installed: our hook events and status line, and whether they reach this port."""

    data = read_settings(path)
    raw_hooks = data.get("hooks")
    hooks: dict[str, Any] = raw_hooks if isinstance(raw_hooks, dict) else {}
    found: set[str] = set()
    wrong_port = False
    for event in HOOK_EVENTS:
        entries = hooks.get(event)
        for entry in entries if isinstance(entries, list) else []:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            for hook in inner if isinstance(inner, list) else []:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(command, str) and _is_ours(command, HOOK_SCRIPT):
                    found.add(event)
                    # An earlier (environment-variable) install is upgraded on install.
                    stale = not _points_here(command, port, HOOK_PATH) or URL_ARG not in command
                    wrong_port |= stale
    if not found:
        hook_state = "missing"
    elif wrong_port:
        hook_state = "wrong_port"
    elif len(found) < len(HOOK_EVENTS):
        hook_state = "partial"
    else:
        hook_state = "ours"
    command = _status_command(data)
    if command and _is_ours(command, QUOTA_SCRIPT):
        fresh = _points_here(command, port, QUOTA_PATH) and URL_ARG in command
        line = "ours" if fresh else "wrong_port"
    else:
        line = "other" if command else "missing"
    try:
        check_scripts()
        problem = None
    except ClaudeSetupError as exc:
        problem = str(exc)
    return {
        "settings_path": str(path),
        "hooks": {"state": hook_state, "events": [e for e in HOOK_EVENTS if e in found]},
        "statusline": {"state": line, "chained": bool(command and CHAIN_FILE in command)},
        "hook_command": hook_command(port),
        "statusline_command": statusline_command(port),
        **({"error": problem} if problem else {}),
    }


def _without_ours(entries: object, script: str) -> list[Any]:
    kept: list[Any] = []
    for entry in entries if isinstance(entries, list) else []:
        inner = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(inner, list):
            kept.append(entry)
            continue
        others = [
            hook
            for hook in inner
            if not (isinstance(hook, dict) and _is_ours(hook.get("command"), script))
        ]
        if others:
            kept.append({**entry, "hooks": others})
    return kept


def _backup(path: Path, clock: datetime | None = None) -> str | None:
    """Copy the file aside under a name no earlier backup uses."""

    if not path.exists():
        return None
    stamp = (clock or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-stream-micro-{stamp}")
    count = 1
    while target.exists():
        count += 1
        target = path.with_name(f"{path.name}.bak-stream-micro-{stamp}-{count}")
    shutil.copy2(path, target)
    return str(target)


def _write(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if private:
            # Another tool's command may hold secrets: owner-only on POSIX; on
            # Windows the file keeps the user profile folder's permissions.
            with contextlib.suppress(OSError):
                os.chmod(name, 0o600)
        Path(name).replace(path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _chained_original(chain_file: Path) -> dict[str, Any] | None:
    if not chain_file.exists():
        return None
    saved = read_settings(chain_file)
    original = saved.get("statusLine")
    if isinstance(original, dict) and isinstance(original.get("command"), str):
        return original
    command = saved.get("command")  # an earlier sidecar kept only the command
    return {"type": "command", "command": command} if isinstance(command, str) else None


def install(
    path: Path,
    port: int,
    parts: set[str],
    *,
    replace_statusline: bool = False,
    chain_statusline: bool = False,
    clock: datetime | None = None,
) -> dict[str, Any]:
    """Add (or point at this port) our hook events and/or status line."""

    with _CHANGE_LOCK:
        return _install(
            path,
            port,
            parts,
            replace_statusline=replace_statusline,
            chain_statusline=chain_statusline,
            clock=clock,
        )


def _install(
    path: Path,
    port: int,
    parts: set[str],
    *,
    replace_statusline: bool,
    chain_statusline: bool,
    clock: datetime | None,
) -> dict[str, Any]:
    _check_parts(parts)
    check_scripts()
    data = read_settings(path)
    status = _status(data)
    current = _status_command(data)
    other = current is not None and not _is_ours(current, QUOTA_SCRIPT)
    if "statusline" in parts and other and not (replace_statusline or chain_statusline):
        raise ClaudeSetupError("Claude Code already runs another status line; chain or replace it")
    chain_file = path.with_name(CHAIN_FILE)
    commands: dict[str, str] = {"hooks": hook_command(port)}
    if "statusline" in parts:
        chained = (other and chain_statusline) or (
            not other and current is not None and CHAIN_FILE in current
        )
        commands["statusline"] = statusline_command(port, chain_file if chained else None)
    backup = _backup(path, clock)
    drop_chain = False
    if "hooks" in parts:
        raw_hooks = data.get("hooks")
        hooks: dict[str, Any] = dict(raw_hooks) if isinstance(raw_hooks, dict) else {}
        for event in HOOK_EVENTS:
            entries = _without_ours(hooks.get(event), HOOK_SCRIPT)
            entries.append(
                {"hooks": [{"type": "command", "command": commands["hooks"], "timeout": 3}]}
            )
            hooks[event] = entries
        data["hooks"] = hooks
    if "statusline" in parts:
        if other and chain_statusline:
            assert status is not None
            # Keep the whole original (padding, refreshInterval, ...) to give back later.
            _write(chain_file, {"statusLine": status}, private=True)
            data["statusLine"] = {**status, "type": "command", "command": commands["statusline"]}
        elif other:  # replaced on request
            drop_chain = True
            data["statusLine"] = {"type": "command", "command": commands["statusline"]}
        else:
            data["statusLine"] = {
                **(status or {}),
                "type": "command",
                "command": commands["statusline"],
            }
    _write(path, data)
    if drop_chain:  # only once the settings no longer point at it
        chain_file.unlink(missing_ok=True)
    return {**inspect(path, port), "backup": backup}


def remove(
    path: Path, port: int, parts: set[str], *, clock: datetime | None = None
) -> dict[str, Any]:
    """Take out only what we added; a chained status line gets its place back."""

    with _CHANGE_LOCK:
        return _remove(path, port, parts, clock=clock)


def _remove(path: Path, port: int, parts: set[str], *, clock: datetime | None) -> dict[str, Any]:
    _check_parts(parts)
    data = read_settings(path)
    backup = _backup(path, clock)
    raw_hooks = data.get("hooks")
    if "hooks" in parts and isinstance(raw_hooks, dict):
        hooks = {}
        for event, entries in raw_hooks.items():
            kept = _without_ours(entries, HOOK_SCRIPT)
            if kept:
                hooks[event] = kept
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks")
    current = _status_command(data)
    chain_file = path.with_name(CHAIN_FILE)
    drop_chain = False
    if "statusline" in parts and current and _is_ours(current, QUOTA_SCRIPT):
        original = _chained_original(chain_file)
        if original is not None:
            data["statusLine"] = original
        else:
            data.pop("statusLine")
        drop_chain = True
    _write(path, data)
    if drop_chain:  # a failed write above keeps it, so nothing is lost
        chain_file.unlink(missing_ok=True)
    return {**inspect(path, port), "backup": backup}


def _check_parts(parts: set[str]) -> None:
    if not parts or not parts <= PARTS:
        raise ClaudeSetupError("Choose hooks and/or statusline")
