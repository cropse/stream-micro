"""Check the Claude Code companion hook and status line for this Bridge.

Both live in Claude Code's user settings (``~/.claude/settings.json``) and
call the bundled scripts in ``integrations/claude-code-stream-micro``.  This
module only reads that file: it reports whether our entries are there and
whether they point at this Bridge's port, and builds the commands to add.

Commands are ``node "<script>" --stream-micro-url=...``: plain enough for the
shell Claude Code uses on Windows (Git Bash) and for ``cmd``, with no
environment-variable prefix.  Paths with characters a shell would expand are
refused rather than quoted.
"""

from __future__ import annotations

import json
import re
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
# Another tool's status line, kept next to settings.json while ours is chained.
CHAIN_FILE = "stream-micro-statusline-next.json"
HOOK_PATH = "/api/hooks/claude-code"
QUOTA_PATH = "/api/hooks/claude-code/quota"
URL_ARG = "--stream-micro-url="
# Earlier installs passed the URL in an environment variable.
LEGACY_MARKERS = ("STREAM_MICRO_CLAUDE_HOOK_URL=", "STREAM_MICRO_CLAUDE_QUOTA_URL=")
# Characters a shell could expand or that would end the quoting.
_UNSAFE_PATH = re.compile(r"[\"'`$!%\\\n\r]")


class ClaudeSetupError(RuntimeError):
    """Raised when Claude Code's settings cannot be read safely."""


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


def statusline_command(port: int) -> str:
    url = f"http://127.0.0.1:{port}{QUOTA_PATH}"
    return f'node "{_script(QUOTA_SCRIPT)}" {URL_ARG}{url}'


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
