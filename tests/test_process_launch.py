"""Guard every production subprocess entry point against accidental consoles."""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from stream_micro.plugins.antigravity import AntigravityPlugin
from stream_micro.plugins.claude_code import ClaudeCodePlugin

ROOT = Path(__file__).parents[1]
INTERACTIVE_LAUNCHERS = {
    ("plugins/claude_code.py", "launch_claude_session"),
    ("plugins/antigravity.py", "launch_antigravity_session"),
}
PROCESS_CALLS = {
    "Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
}


class ProcessCalls(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function = "<module>"
        self.calls: list[tuple[str, ast.Call]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        before, self.function = self.function, node.name
        self.generic_visit(node)
        self.function = before

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in PROCESS_CALLS
        ):
            self.calls.append((self.function, node))
        self.generic_visit(node)


def test_all_background_subprocess_calls_use_shared_hidden_launch_policy() -> None:
    package = ROOT / "src" / "stream_micro"
    found_interactive = set()
    found_background = set()
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Aliases and alternate process APIs must be reviewed rather than bypassing this guard.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(item.name == "subprocess" and item.asname for item in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module != "subprocess", f"Use classified subprocess calls: {path}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and (node.func.value.id, node.func.attr)
                    in {("os", "system"), ("os", "popen"), ("asyncio", "create_subprocess_exec"),
                        ("asyncio", "create_subprocess_shell")}
                ), f"Unclassified child-process launcher: {path}:{node.lineno}"
        visitor = ProcessCalls()
        visitor.visit(tree)
        for function, call in visitor.calls:
            location = (path.relative_to(package).as_posix(), function)
            if location in INTERACTIVE_LAUNCHERS:
                assert any(item.arg == "creationflags" for item in call.keywords)
                assert "CREATE_NEW_CONSOLE" in ast.unparse(tree)
                found_interactive.add(location)
                continue
            assert any(
                item.arg is None
                and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name)
                and item.value.func.id == "background_process_options"
                for item in call.keywords
            ), f"Background launch must hide its console: {path}:{call.lineno}"
            found_background.add(location)
    assert found_interactive == INTERACTIVE_LAUNCHERS
    assert ("plugins/codex.py", "start") in found_background
    assert ("plugins/opencode.py", "default_loopback_ports") in found_background


@pytest.mark.parametrize("provider", ["claude_code", "antigravity"])
def test_passive_provider_enable_and_refresh_never_launch_processes(
    tmp_path, monkeypatch, provider
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Enabling or refreshing a passive provider must not launch a process")

    for name in PROCESS_CALLS:
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "startfile", forbidden, raising=False)
    if provider == "claude_code":
        plugin = ClaudeCodePlugin({"enabled": True, "claude_home": str(tmp_path)})
        plugin.handle_hook({
            "session_id": "console-regression",
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
        })
    else:
        plugin = AntigravityPlugin({"enabled": True, "agy_home": str(tmp_path)})
        plugin.handle_status({
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": str(tmp_path),
            "agent_state": "working",
            "tool_confirmation_pending": False,
            "task_count": 1,
            "quota": {},
        })
    for _ in range(3):
        snapshot = plugin.snapshot()
        assert snapshot.health.connected
        assert snapshot.sessions


def test_claude_statusline_chain_hides_its_background_shell() -> None:
    script = (ROOT / "integrations/claude-code-stream-micro/scripts/report-quota.mjs").read_text(
        encoding="utf-8"
    )
    assert "windowsHide: true" in script
    assert 'spawnSync(process.env.SHELL, ["-c", command], options)' in script
    assert "spawnSync(command, { ...options, shell: true })" in script
