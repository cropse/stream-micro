# Antigravity CLI Plugin Design

## Goal

Add an `antigravity` provider to stream-micro that exposes Antigravity CLI conversations as Stream Deck session keys, shows lifecycle and quota state, and safely resumes a selected conversation in a new Windows console.

The first release supports Antigravity CLI only. It does not inspect or automate Antigravity Desktop, Remote Control, terminal UI output, or the Desktop application's private SQLite data.

## Scope and Constraints

- The provider is optional and disabled by default.
- The provider uses only the installed `agy` CLI, its documented `cache/last_conversations.json` workspace cache, documented `--conversation` resume behavior, and documented status-line JSON input.
- A key press can resume only a session present in the provider's current snapshot.
- The Bridge must execute `agy --conversation <conversation-id>` with `shell=False`, in a new Windows console, using the session's validated workspace directory as `cwd`.
- The frontend continues to use the Bridge's local API. It never reads Antigravity files or credentials.
- Prompts, conversation contents, authentication material, and raw quota responses must never be sent to the Bridge, dashboard, logs, tests, or source control.
- Missing CLI binaries, unknown cache formats, invalid metadata, absent companion data, or inaccessible files produce a non-connected or partial snapshot. They never trigger Desktop database parsing or arbitrary command execution.

## Configuration

Add a `plugins.antigravity` record to the versioned Bridge configuration.

```yaml
plugins:
  antigravity:
    enabled: false
    agy_path: agy
    agy_home: null
    max_sessions: 15
```

Validation accepts only these keys:

- `enabled`: optional boolean.
- `agy_path`: optional non-empty executable path or command name.
- `agy_home`: optional non-empty directory path used to locate documented CLI state. Its default is `~/.gemini/antigravity-cli`.
- `max_sessions`: integer from 1 through the Stream Deck/dashboard session limit of 15.

There is intentionally no arbitrary command, arguments field, URL, token, or shell template.

## Provider Architecture

Create `src/stream_micro/plugins/antigravity.py` with `AntigravityPlugin`, `AntigravityError`, metadata records, parsing helpers, and safe Windows command-resolution/launch helpers.

The plugin manifest is:

```python
PluginManifest(
    id="antigravity",
    name="Antigravity",
    components=("session", "quota"),
    actions=("select_session",),
)
```

The plugin owns two independent sources of display state:

1. The documented CLI cache `~/.gemini/antigravity-cli/cache/last_conversations.json` maps absolute workspace paths to their most recent conversation IDs. Each cache entry becomes one session whose title and project are the workspace folder name; the cache does not expose prompts, conversation contents, or timestamps.
2. The documented `statusLine` command input is forwarded by the bundled companion to the loopback Bridge. It supplies `conversation_id`, `cwd`, `agent_state`, `tool_confirmation_pending`, `task_count`, and structured quota fields. The plugin maps those fields to state and normalized quota signals.

Snapshot construction reads the cache, overlays live status records by conversation ID, orders live records first and the remaining cached workspace sessions deterministically by normalized workspace path, and caps the result at `max_sessions`. A status transition from `RUNNING` or `WAITING` to idle becomes `COMPLETED`; otherwise idle remains `IDLE`. A completed state remains visible until the user selects the session, after which it returns to idle until another status-line update arrives.

Provider health is connected when `agy` is available and the cache is recognized, or a valid status-line update has been accepted. The detail explains whether cache, live companion updates, and quota data are available. If the plugin is disabled, health reads `Disabled`.

## Session Metadata Boundary

Antigravity CLI's documented workspace cache is the only on-disk session source.

A narrow cache reader:

- uses the configured `agy_home`, otherwise `~/.gemini/antigravity-cli`;
- reads only `cache/last_conversations.json`, a JSON map of absolute workspace paths to conversation IDs;
- bounds file size and entry count before parsing;
- validates identifiers and existing workspace paths;
- derives title and project from the workspace folder name, never from prompt data;
- ignores malformed entries and returns no sessions for an unrecognized document;
- never reads `history.jsonl`, conversation databases, token stores, credential files, transcripts, or Antigravity Desktop `state.vscdb` files.

## Lifecycle and Quota Companion

Add a bundled status-line companion integration. The user configures Antigravity CLI's documented `statusLine` command to run it. The companion receives the documented status-line JSON on stdin and sends only a schema-validated loopback POST request:

- `POST /api/hooks/antigravity/status`

The payload requirements are:

- `conversation_id`: a conservative valid Antigravity conversation identifier.
- `cwd`: a validated absolute workspace path.
- `agent_state`: one documented value, mapped to `RUNNING` for `thinking`, `working`, `tool_use`, or `initializing`, otherwise `IDLE`.
- `tool_confirmation_pending`: when true, maps to `WAITING` and takes precedence over `agent_state`.
- `task_count`: a non-negative integer. A positive count maps an otherwise idle session to `RUNNING`.
- Optional normalized quota windows formed from documented `quota` entries. Each includes finite `remaining_fraction` from 0 through 1 and a valid ISO-8601 `reset_time`; the plugin converts it to used percentage, short label, and reset time.

The HTTP handler resolves the registered plugin, rejects disabled or missing plugins, rejects unknown fields and invalid values, and returns no sensitive detail. It remains loopback-only under the existing Bridge server boundary.

The companion does not transmit prompts, transcript paths, model information, email, raw status JSON, model responses, artifacts, command output, credentials, cookie values, OAuth data, tokens, or raw provider responses.

## Session Selection

`invoke("select_session", {"session_id": value})` must:

1. Validate the action and session ID.
2. Confirm the ID exists in the current snapshot.
3. Resolve `agy_path` as a direct executable or Windows shim without using a shell.
4. Start a new console with `agy --conversation <conversation-id>`.
5. Use the known workspace directory when it exists; otherwise omit `cwd` rather than accepting a caller-provided path.
6. Clear a completed indicator for the selected session and return a small success object.

Unknown actions, missing binaries, invalid sessions, and launch failures raise `AntigravityError`. The action API will report the failure without executing a fallback command.

## Bridge, Renderer, and Dashboard Integration

Register the provider through the existing plugin registry/configuration flow. Update the control configuration validation, Bridge status aggregation, renderer provider labels, API schemas, and frontend provider controls so `antigravity` is selectable anywhere OpenCode, Codex, and Claude Code are selectable.

Extend `bridge.example.yaml` and the README with:

- Antigravity CLI installation prerequisite and default Windows executable location.
- Plugin configuration example.
- `statusLine` companion configuration instructions.
- Explanation that the integration is CLI-only and presents one most-recent conversation per workspace.
- Safe-degradation behavior and no-prompt/no-credential boundary.

## Error Handling

- Invalid plugin config prevents saving configuration with a field-specific error.
- A missing `agy` executable exposes disconnected health and a user-actionable detail; it does not crash the Bridge.
- Missing or unsupported metadata yields no cached sessions and a precise health detail.
- Invalid companion requests receive a 4xx response and cannot mutate state.
- A lifecycle record for an unknown session is retained only if it includes validated, display-safe live session information; otherwise it is ignored.
- Quota data remains absent until valid companion input is received. The plugin does not fabricate values.
- Launch failures surface as a failed allowlisted action; no process is retried automatically.

## Testing

Add focused tests covering:

- Configuration unknown-key, type, and bound validation.
- Session metadata parsing, sort order, malformed records, oversize/missing input, and unrecognized formats.
- Lifecycle event mapping and lifecycle/history merge behavior.
- Quota signal normalization and payload validation.
- Plugin health for disabled, CLI unavailable, cache unavailable, live-only, and connected cases.
- Loopback endpoint routing, successful valid requests, and rejection of invalid/unknown payload fields.
- `select_session` action validation, known-session restriction, Windows command resolution, console launch arguments, companion activation, and process-launch failures.
- Dashboard/control configuration contract and provider renderer coverage.

Run the existing Python test suite, Ruff, Pyright, and frontend build as final verification.

## Out of Scope

- Antigravity Desktop conversation discovery or switching.
- Parsing `state.vscdb`, Chromium storage, logs, or terminal screen content.
- Cloud/Remote Control APIs.
- Creating, sending prompts to, aborting, or approving Antigravity sessions from the Stream Deck.
- Any network listener beyond the Bridge's existing loopback server.
- Quota acquisition without a validated official companion source.
