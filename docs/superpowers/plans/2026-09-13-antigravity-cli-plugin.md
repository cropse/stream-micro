# Antigravity CLI Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CLI-only Antigravity provider that displays the latest conversation for each workspace, live state, and quota on Stream Micro and safely resumes a selected conversation in a new Windows console.

**Architecture:** The provider reads Antigravity CLI's documented `cache/last_conversations.json`, deriving titles strictly from workspace folder names. A configured Antigravity `statusLine` script receives documented structured JSON and forwards a minimal whitelisted status/quota payload to one loopback Bridge endpoint. Session selection accepts only cached/live conversation IDs and runs `agy --conversation <id>` without a shell.

**Tech Stack:** Python 3.10, FastAPI, PyYAML, pytest, Ruff, Pyright, Node.js, React/Vite.

**Spec:** `docs/superpowers/specs/2026-09-13-antigravity-cli-plugin-design.md`

## Global Constraints

- Support Antigravity CLI only, version `1.2.2` validated on Windows.
- Read only `~/.gemini/antigravity-cli/cache/last_conversations.json`; do not read `history.jsonl`, conversation databases, Desktop storage, logs, token stores, transcripts, or credentials.
- Use workspace folder names as session titles. Never send or display prompts, transcript paths, email, model metadata, credentials, tokens, artifacts, raw quota input, or command output.
- Use only `select_session`; execute `agy --conversation <conversation-id>` with `shell=False`, `CREATE_NEW_CONSOLE`, and a validated workspace `cwd`.
- Use one loopback-only `POST /api/hooks/antigravity/status` endpoint; reject unknown, malformed, or sensitive fields with HTTP 422.
- Preserve safe degradation when `agy`, cache, workspace, status-line companion, or quota is unavailable.
- Preserve the MK.2 15-key layout.

---

## File Structure

- Create `src/stream_micro/plugins/antigravity.py`: cache parser, payload validator, status/quota mapper, snapshot builder, safe Windows launch helper.
- Create `tests/test_antigravity_plugin.py`: focused provider, config, cache, payload, launch, and registration tests.
- Modify `src/stream_micro/control_config.py`: add disabled Antigravity defaults.
- Modify `src/stream_micro/bridge.py`: register the provider and delegate status input.
- Modify `src/stream_micro/server.py`: provide the loopback status endpoint and error handling.
- Modify `tests/test_server.py`: exercise the endpoint and rejection behavior.
- Modify `src/stream_micro/control_renderer.py` and `tests/test_control_renderer.py`: add Antigravity provider styling.
- Create `integrations/antigravity-stream-micro/scripts/report-status.mjs`: accept documented status-line JSON and forward only whitelisted fields.
- Create `integrations/antigravity-stream-micro/README.md`: exact `statusLine` configuration and privacy boundary.
- Modify `bridge.example.yaml` and `README.md`: provider config, CLI-only setup, and safe degradation documentation.

### Task 1: Establish the Safe Cache and Plugin Contract

**Files:**
- Create: `src/stream_micro/plugins/antigravity.py`
- Create: `tests/test_antigravity_plugin.py`
- Modify: `src/stream_micro/control_config.py:59-98`

**Interfaces:**
- Produces `AntigravityError(RuntimeError)`.
- Produces `AntigravityCacheSession(id: str, directory: str, updated_at: float)`.
- Produces `validate_antigravity_config(config: dict[str, object]) -> None`.
- Produces `read_last_conversations(path: Path, *, max_sessions: int = 15) -> tuple[AntigravityCacheSession, ...]`.
- Produces `AntigravityPlugin(config: dict[str, Any], *, launcher: Callable[[str, str | None], None] | None = None, clock: Callable[[], float] = time.time)`.

- [ ] **Step 1: Write failing config and cache-reader tests**

```python
import json
from pathlib import Path

import pytest

from stream_micro.plugins.antigravity import (
    AntigravityError,
    read_last_conversations,
    validate_antigravity_config,
)

@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"enabled": "yes"}, "boolean"),
        ({"agy_path": ""}, "agy_path"),
        ({"agy_home": []}, "agy_home"),
        ({"max_sessions": 16}, "max_sessions"),
        ({"command": "cmd.exe"}, "Unknown"),
    ],
)
def test_antigravity_config_rejects_invalid_values(config, message):
    with pytest.raises(AntigravityError, match=message):
        validate_antigravity_config(config)

def test_last_conversations_maps_workspaces_to_sessions(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "last_conversations.json"
    cache.write_text(json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}), encoding="utf-8")

    assert read_last_conversations(cache) == (
        AntigravityCacheSession("de5c0341-9e8a-40d3-b93f-e87bc874a460", str(project), 0.0),
    )

def test_last_conversations_ignores_non_absolute_paths_and_invalid_ids(tmp_path: Path):
    cache = tmp_path / "last_conversations.json"
    cache.write_text(json.dumps({"relative": "good-id", str(tmp_path): "not valid id!"}), encoding="utf-8")

    assert read_last_conversations(cache) == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_antigravity_plugin.py -k "config_rejects or last_conversations" -v`

Expected: FAIL during collection because the module does not exist.

- [ ] **Step 3: Implement config and bounded cache parsing**

Implement exactly these settings:

```python
_ALLOWED_KEYS = frozenset({"enabled", "agy_path", "agy_home", "max_sessions"})
MAX_VISIBLE_SESSIONS = 15
DEFAULT_AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"
```

Add the default control config:

```python
"antigravity": {
    "enabled": False,
    "agy_path": None,
    "agy_home": None,
    "max_sessions": 15,
},
```

The cache parser reads a bounded JSON file only, accepts a JSON object of absolute existing directory paths to UUID-like conversation IDs, creates `AntigravityCacheSession(id, directory, 0.0)`, sorts deterministically by normalized directory, and never opens any related history or database file.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `uv run pytest tests/test_antigravity_plugin.py -k "config_rejects or last_conversations" -v`

Expected: PASS.

- [ ] **Step 5: Commit the cache contract**

```bash
git add src/stream_micro/plugins/antigravity.py src/stream_micro/control_config.py tests/test_antigravity_plugin.py
git commit -m "feat: add Antigravity cache config"
```

### Task 2: Implement Status-Line Mapping, Snapshots, and Safe Resume

**Files:**
- Modify: `src/stream_micro/plugins/antigravity.py`
- Modify: `tests/test_antigravity_plugin.py`

**Interfaces:**
- Consumes `AntigravityCacheSession` and `read_last_conversations` from Task 1.
- Produces `map_status_payload(payload: Mapping[str, object]) -> tuple[SessionState, dict[str, str | float | int | None]]`.
- Produces `resolve_agy_command(agy_path: str) -> list[str]`.
- Produces `launch_antigravity_session(conversation_id: str, directory: str | None, *, agy_path: str = "agy") -> None`.
- Produces `AntigravityPlugin.handle_status(payload: dict[str, Any]) -> dict[str, str | bool | None]`.

- [ ] **Step 1: Write failing status mapping and snapshot tests**

```python
from stream_micro.plugins.base import SessionState
from stream_micro.plugins.antigravity import AntigravityPlugin, map_status_payload

def test_status_payload_prioritizes_confirmation_and_normalizes_quota():
    state, signals = map_status_payload(
        {
            "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
            "cwd": "C:/work/project",
            "agent_state": "working",
            "tool_confirmation_pending": True,
            "task_count": 1,
            "quota": {"gemini-weekly": {"remaining_fraction": 0.75, "reset_time": "2026-09-14T00:00:00Z"}},
        }
    )
    assert state is SessionState.WAITING
    assert signals == {
        "primary_percent": 25.0,
        "primary_label": "GEMINI-WEEKLY",
        "primary_reset_at": "2026-09-14T00:00:00+00:00",
    }

def test_plugin_uses_workspace_name_without_reading_prompt(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(project): "de5c0341-9e8a-40d3-b93f-e87bc874a460"}), encoding="utf-8"
    )
    plugin = AntigravityPlugin({"enabled": True, "agy_home": str(tmp_path)}, launcher=lambda _id, _cwd: None)

    snapshot = plugin.snapshot()

    assert snapshot.sessions[0].title == "project"
    assert snapshot.sessions[0].directory == str(project)
    assert snapshot.sessions[0].state is SessionState.IDLE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_antigravity_plugin.py -k "status_payload or workspace_name" -v`

Expected: FAIL because status mapping and snapshots are incomplete.

- [ ] **Step 3: Implement strict status input and snapshots**

Accept only these top-level status payload fields: `conversation_id`, `cwd`, `agent_state`, `tool_confirmation_pending`, `task_count`, and `quota`.

Map state in this order: `tool_confirmation_pending=True` to `WAITING`; otherwise `thinking`, `working`, `tool_use`, `initializing`, or `task_count > 0` to `RUNNING`; otherwise `IDLE`. Reject missing/invalid IDs, non-absolute `cwd`, invalid state, negative task count, malformed quota, unknown fields, and invalid ISO dates.

Store only ID, workspace, state, and update time. Do not retain any other input. Overlay live records over cache records; a live-only record title is `Path(cwd).name`. When a previously `RUNNING` or `WAITING` record receives an idle update, expose `COMPLETED` until selected; otherwise expose `IDLE`. Convert each quota `remaining_fraction` to `used_percent = (1 - fraction) * 100`, sort bucket IDs, map the first two to `primary` and `secondary`, and uppercase/truncate labels to 12 ASCII characters.

- [ ] **Step 4: Run status and snapshot tests to verify they pass**

Run: `uv run pytest tests/test_antigravity_plugin.py -k "status_payload or workspace_name" -v`

Expected: PASS.

- [ ] **Step 5: Write failing strict selection and Windows launch tests**

```python
from unittest.mock import patch

def test_selection_resumes_only_a_current_session(tmp_path: Path):
    plugin = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)
    with pytest.raises(AntigravityError, match="no longer exists"):
        plugin.invoke("select_session", {"session_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460"})

def test_windows_launch_uses_official_specific_conversation_flag(tmp_path: Path):
    with (
        patch("stream_micro.plugins.antigravity.os.name", "nt"),
        patch("stream_micro.plugins.antigravity.resolve_agy_command", return_value=["agy.exe"]),
        patch("stream_micro.plugins.antigravity.subprocess.Popen") as popen,
    ):
        launch_antigravity_session("de5c0341-9e8a-40d3-b93f-e87bc874a460", str(tmp_path), agy_path="agy")

    assert popen.call_args.args[0] == ["agy.exe", "--conversation", "de5c0341-9e8a-40d3-b93f-e87bc874a460"]
    assert popen.call_args.kwargs["cwd"] == str(tmp_path)
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["creationflags"] != 0
```

- [ ] **Step 6: Run the selection tests to verify they fail**

Run: `uv run pytest tests/test_antigravity_plugin.py -k "selection_resumes or windows_launch" -v`

Expected: FAIL because invocation and launcher are incomplete.

- [ ] **Step 7: Implement safe action dispatch and process launch**

Implement only `select_session`. Look up `session_id` in the current snapshot before launching. Resolve `.cmd`/`.bat` with `COMSPEC /d /s /c`, `.ps1` with non-interactive PowerShell, and direct executables otherwise. Validate ID and `Path(directory).is_dir()`, use `CREATE_NEW_CONSOLE` and `shell=False`, and run exactly:

```python
[*resolve_agy_command(agy_path), "--conversation", conversation_id]
```

Clear the completed marker after successful selection. Never use `--continue` because it resumes whichever session is newest in the workspace rather than the selected conversation.

- [ ] **Step 8: Run all plugin tests to verify they pass**

Run: `uv run pytest tests/test_antigravity_plugin.py -v`

Expected: PASS.

- [ ] **Step 9: Commit the provider**

```bash
git add src/stream_micro/plugins/antigravity.py tests/test_antigravity_plugin.py
git commit -m "feat: add Antigravity session provider"
```

### Task 3: Register the Provider and Expose the Loopback Status Endpoint

**Files:**
- Modify: `src/stream_micro/bridge.py:25-30,87-110,412-422`
- Modify: `src/stream_micro/server.py:16-21,92-108,132-153`
- Modify: `tests/test_antigravity_plugin.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes `AntigravityPlugin`, `AntigravityError`, and `handle_status` from Task 2.
- Produces `BridgeService.handle_antigravity_status(payload: dict[str, Any]) -> dict[str, str | bool | None]`.
- Produces `POST /api/hooks/antigravity/status`.

- [ ] **Step 1: Write failing Bridge and API tests**

```python
def test_bridge_registers_antigravity_only_when_enabled(tmp_path: Path):
    data = control_config_data(default_control_config())
    data["plugins"]["opencode"]["enabled"] = False
    data["plugins"]["ollama_quota"]["enabled"] = False
    data["plugins"]["antigravity"]["enabled"] = True
    path = tmp_path / "bridge.yaml"
    write_control_config(path, parse_control_config(data))
    service = BridgeService(path)
    try:
        assert isinstance(service.plugins["antigravity"], AntigravityPlugin)
    finally:
        service.close()

def test_antigravity_status_endpoint_updates_snapshot(tmp_path: Path):
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["antigravity"] = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)
        response = client.post(
            "/api/hooks/antigravity/status",
            json={"conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460", "cwd": str(tmp_path), "agent_state": "working", "tool_confirmation_pending": False, "task_count": 0, "quota": {}},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "running"
```

- [ ] **Step 2: Run Bridge and API tests to verify they fail**

Run: `uv run pytest tests/test_antigravity_plugin.py -k bridge_registers tests/test_server.py -k antigravity_status -v`

Expected: FAIL because the provider is not registered and the route does not exist.

- [ ] **Step 3: Register and delegate provider status**

In `BridgeService._build_plugins`, create `AntigravityPlugin` only when `plugins.antigravity.enabled` is true. Add this delegate:

```python
def handle_antigravity_status(self, payload: dict[str, Any]) -> dict[str, str | bool | None]:
    plugin = self._plugin("antigravity")
    if not isinstance(plugin, AntigravityPlugin):
        raise BridgeError("Antigravity plugin is not enabled")
    return plugin.handle_status(payload)
```

Add this FastAPI route and include `AntigravityError` in generic action/key exception tuples:

```python
@app.post("/api/hooks/antigravity/status")
def post_antigravity_status(data: dict[str, Any]) -> dict[str, str | bool | None]:
    try:
        return service.handle_antigravity_status(data)
    except (BridgeError, AntigravityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **Step 4: Add and run unknown-field rejection coverage**

```python
def test_antigravity_status_endpoint_rejects_sensitive_fields(tmp_path: Path):
    with app_with_plugin(tmp_path) as client:
        service = cast(Any, client.app).state.bridge
        service.plugins["antigravity"] = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)
        response = client.post(
            "/api/hooks/antigravity/status",
            json={"conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460", "cwd": str(tmp_path), "agent_state": "idle", "tool_confirmation_pending": False, "task_count": 0, "quota": {}, "transcript_path": "secret"},
        )

    assert response.status_code == 422
```

Run: `uv run pytest tests/test_antigravity_plugin.py tests/test_server.py -k antigravity -v`

Expected: PASS.

- [ ] **Step 5: Commit registration and routing**

```bash
git add src/stream_micro/bridge.py src/stream_micro/server.py tests/test_antigravity_plugin.py tests/test_server.py
git commit -m "feat: add Antigravity status endpoint"
```

### Task 4: Add Provider Rendering and Documentation

**Files:**
- Modify: `src/stream_micro/control_renderer.py:18-35`
- Modify: `tests/test_control_renderer.py`
- Modify: `bridge.example.yaml:10-34`
- Modify: `README.md:50-78`

**Interfaces:**
- Consumes generic provider snapshots from Tasks 2-3.
- Produces Antigravity provider style `("ANTIGRAVITY", "#17142c", "#6f5cff")`.

- [ ] **Step 1: Write and run a failing renderer assertion**

Use the existing test module's renderer function and image helper:

```python
def test_antigravity_sessions_use_the_provider_style():
    image = render_session_key(
        AgentSession("de5c0341-9e8a-40d3-b93f-e87bc874a460", "project", SessionState.RUNNING, "project"),
        plugin_id="antigravity",
    )
    assert _contains_expected_provider_color(image, "#6f5cff")
```

Run: `uv run pytest tests/test_control_renderer.py -k antigravity -v`

Expected: FAIL because the provider uses the fallback style.

- [ ] **Step 2: Add provider style and verify it**

Add:

```python
"antigravity": ("ANTIGRAVITY", "#17142c", "#6f5cff"),
```

Run: `uv run pytest tests/test_control_renderer.py -k antigravity -v`

Expected: PASS.

- [ ] **Step 3: Document default configuration and setup**

Add to `bridge.example.yaml`:

```yaml
  antigravity:
    enabled: false
    agy_path: null
    agy_home: null
    max_sessions: 15
```

Add a README section that states: CLI-only integration; default Windows binary `%LOCALAPPDATA%\agy\bin\agy.exe`; one latest session per workspace; `agy --conversation <id>` restores an exact selected session; setup requires the bundled `statusLine` script; no prompts/history/transcripts/credentials are read or sent; and absent CLI/cache/status data safely shows unavailable.

- [ ] **Step 4: Run contract and renderer regressions**

Run: `uv run pytest tests/test_control_config.py tests/test_control_renderer.py tests/test_frontend_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the control surface changes**

```bash
git add src/stream_micro/control_renderer.py tests/test_control_renderer.py bridge.example.yaml README.md
git commit -m "feat: expose Antigravity in control surface"
```

### Task 5: Build the Official Status-Line Companion

**Files:**
- Create: `integrations/antigravity-stream-micro/scripts/report-status.mjs`
- Create: `integrations/antigravity-stream-micro/README.md`
- Modify: `tests/test_antigravity_plugin.py`

**Interfaces:**
- Consumes the documented Antigravity CLI 1.2.2 `statusLine` JSON fields and `POST /api/hooks/antigravity/status` from Task 3.
- Produces a script that sends only the plugin's accepted field set and exits 0 on reporting failure.

- [ ] **Step 1: Add a plugin payload fixture test**

Create `integrations/antigravity-stream-micro/fixtures/status.json` with only:

```json
{
  "conversation_id": "de5c0341-9e8a-40d3-b93f-e87bc874a460",
  "cwd": "C:\\work\\project",
  "agent_state": "working",
  "tool_confirmation_pending": false,
  "task_count": 0,
  "quota": {"gemini-weekly": {"remaining_fraction": 0.75, "reset_time": "2026-09-14T00:00:00Z"}}
}
```

Test it:

```python
def test_companion_fixture_matches_plugin_contract():
    payload = json.loads(Path("integrations/antigravity-stream-micro/fixtures/status.json").read_text())
    plugin = AntigravityPlugin({"enabled": True}, launcher=lambda _id, _cwd: None)
    assert plugin.handle_status(payload)["accepted"] is True
```

- [ ] **Step 2: Run fixture test to verify it fails**

Run: `uv run pytest tests/test_antigravity_plugin.py::test_companion_fixture_matches_plugin_contract -v`

Expected: FAIL because the fixture does not exist.

- [ ] **Step 3: Implement the non-blocking status script**

Read one JSON object from stdin. Permit only `cwd`, `conversation_id`, `agent_state`, `tool_confirmation_pending`, `task_count`, and `quota`; discard every other status-line field, including `transcript_path`, `model`, `email`, context token values, and VCS data. POST the minimized JSON with a three-second `AbortSignal.timeout` to:

```js
process.env.STREAM_MICRO_ANTIGRAVITY_STATUS_URL ?? "http://127.0.0.1:8765/api/hooks/antigravity/status"
```

Write a compact non-sensitive status line to stdout. Catch JSON and network errors and exit code 0 so the script never blocks the CLI.

- [ ] **Step 4: Write the exact configuration guide**

Document this `~/.gemini/antigravity-cli/settings.json` block, replacing the path as needed:

```json
{
  "statusLine": {
    "type": "command",
    "command": "node \"C:/path/to/stream-micro/integrations/antigravity-stream-micro/scripts/report-status.mjs\"",
    "stack_with_default": true
  }
}
```

Document `STREAM_MICRO_ANTIGRAVITY_STATUS_URL` for a non-default loopback port. State exactly which fields are forwarded and which documented status-line fields are discarded.

- [ ] **Step 5: Verify companion contract and syntax**

Run:

```powershell
uv run pytest tests/test_antigravity_plugin.py::test_companion_fixture_matches_plugin_contract -v
node --check integrations/antigravity-stream-micro/scripts/report-status.mjs
```

Expected: both commands pass.

- [ ] **Step 6: Commit the companion**

```bash
git add integrations/antigravity-stream-micro tests/test_antigravity_plugin.py README.md
git commit -m "feat: add Antigravity status-line companion"
```

### Task 6: Full Verification

**Files:**
- Modify only the source and focused test that a verification failure proves defective.

- [ ] **Step 1: Run all Python tests**

Run: `uv run pytest`

Expected: PASS.

- [ ] **Step 2: Run lint and type checks**

Run:

```powershell
uv run ruff check src tests
uv run pyright src tests
```

Expected: both commands exit 0.

- [ ] **Step 3: Build the Dashboard**

Run: `npm run build`

Working directory: `frontend`

Expected: exits 0.

- [ ] **Step 4: Verify safe degradation and final diff**

Run:

```powershell
uv run pytest tests/test_antigravity_plugin.py tests/test_server.py -k "antigravity and (disabled or missing or rejects)" -v
git diff --check
git status --short
git log --oneline -10
```

Expected: targeted tests pass; no whitespace errors; no Desktop/parser or secret-bearing files are added.

- [ ] **Step 5: Commit only verification fixes if any were necessary**

```bash
git add src tests frontend integrations README.md bridge.example.yaml
git commit -m "test: verify Antigravity integration"
```
