# Antigravity CLI Plugin Design

## Goal

Add an `antigravity` provider to stream-micro that exposes Antigravity CLI conversations as Stream Deck session keys, shows lifecycle and quota state, and safely resumes a selected conversation in a new Windows console.

The first release supports Antigravity CLI only. It does not inspect or automate Antigravity Desktop, Remote Control, terminal UI output, or the Desktop application's private SQLite data.

## Scope and Constraints

- The provider is optional and disabled by default.
- The provider uses only the installed `agy` CLI, its documented session-resume behavior, a documented or explicitly configured companion hook/status-line mechanism, and a validated local session metadata cache.
- A key press can resume only a session present in the provider's current snapshot.
- The Bridge must execute `agy --continue <session-id>` with `shell=False`, in a new Windows console, using the session's validated workspace directory as `cwd`.
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
    companion_dir: null
    max_sessions: 15
```

Validation accepts only these keys:

- `enabled`: optional boolean.
- `agy_path`: optional non-empty executable path or command name.
- `agy_home`: optional non-empty directory path used to locate documented CLI metadata.
- `companion_dir`: optional non-empty directory path used when launching a session with the bundled companion enabled.
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

The plugin owns three independent sources of display state:

1. Validated CLI metadata supplies recent sessions: stable session ID, display title, workspace directory, and last-updated time.
2. The loopback lifecycle endpoint supplies current state for a session: `RUNNING`, `WAITING`, `COMPLETED`, or `ERROR`.
3. The loopback quota endpoint supplies normalized, display-safe quota signals.

Snapshot construction reads cached CLI metadata, overlays lifecycle records by session ID, sorts by most recent known update, and caps the result at `max_sessions`. Lifecycle state wins over default idle state. A completed state remains visible until the user selects the session, after which it returns to idle until another lifecycle event arrives.

Provider health is connected when `agy` is available and at least one recognized metadata source or accepted lifecycle record is present. The detail explains whether metadata, live companion updates, and quota data are available. If the plugin is disabled, health reads `Disabled`.

## Session Metadata Boundary

Antigravity CLI's local cache location and record shape must be treated as an adapter boundary rather than inferred throughout the provider.

A narrow metadata reader:

- uses the configured `agy_home`, otherwise the documented/default Antigravity CLI home;
- locates only known, non-secret session metadata files;
- bounds file size and record count before parsing;
- validates identifier, title, workspace path, and timestamp types;
- ignores malformed records and returns no sessions for an unrecognized schema;
- never reads token stores, credential files, conversation transcripts, or Antigravity Desktop `state.vscdb` files.

The exact on-disk format is verified against a locally installed supported `agy` version before implementation. If the CLI later provides an official machine-readable conversation command, it replaces this reader without changing the plugin contract.

## Lifecycle and Quota Companion

Add a bundled companion integration directory analogous to the Claude Code integration. Its configuration is opt-in through `companion_dir` and documented setup instructions.

The companion sends only schema-validated loopback POST requests:

- `POST /api/plugins/antigravity/event`
- `POST /api/plugins/antigravity/quota`

Lifecycle payload requirements:

- `session_id`: a conservative valid Antigravity session identifier.
- `event`: one allowlisted lifecycle event mapped to `RUNNING`, `WAITING`, `COMPLETED`, or `ERROR`.
- Optional non-sensitive title and `cwd` may update a live-only session record.

Quota payload requirements:

- Optional `session_id` with the same validation.
- Normalized display signals only, such as numeric percentage, short window label, and ISO-8601 reset time.
- All percentages must be finite values between 0 and 100.

The HTTP handlers resolve the registered plugin, reject disabled or missing plugins, reject unknown fields and invalid values, and return no sensitive detail. They remain loopback-only under the existing Bridge server boundary.

The companion does not transmit prompt text, model responses, artifacts, command output, credentials, cookie values, OAuth data, tokens, or raw provider responses.

## Session Selection

`invoke("select_session", {"session_id": value})` must:

1. Validate the action and session ID.
2. Confirm the ID exists in the current snapshot.
3. Resolve `agy_path` as a direct executable or Windows shim without using a shell.
4. Start a new console with `agy --continue <session-id>`.
5. Use the known workspace directory when it exists; otherwise omit `cwd` rather than accepting a caller-provided path.
6. Include the configured companion directory only through the documented Antigravity CLI integration mechanism.
7. Clear a completed indicator for the selected session and return a small success object.

Unknown actions, missing binaries, invalid sessions, and launch failures raise `AntigravityError`. The action API will report the failure without executing a fallback command.

## Bridge, Renderer, and Dashboard Integration

Register the provider through the existing plugin registry/configuration flow. Update the control configuration validation, Bridge status aggregation, renderer provider labels, API schemas, and frontend provider controls so `antigravity` is selectable anywhere OpenCode, Codex, and Claude Code are selectable.

Extend `bridge.example.yaml` and the README with:

- Antigravity CLI installation prerequisite.
- Plugin configuration example.
- Companion installation/configuration instructions.
- Explanation that the integration is CLI-only.
- Safe-degradation behavior and no-credential boundary.

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
