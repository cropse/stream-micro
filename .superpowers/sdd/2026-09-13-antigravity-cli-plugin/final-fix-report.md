# Antigravity CLI Final Review Fix Report

Date: 2026-09-13

Binding design: `docs/superpowers/specs/2026-09-13-antigravity-cli-plugin-design.md`

Implementation plan: `docs/superpowers/plans/2026-09-13-antigravity-cli-plugin.md`

## Finding 1: Companion Destination and Quota Boundary

- `report-status.mjs` now parses the configured destination before reading or posting status data.
- Only plain HTTP destinations whose parsed hostname is `127.0.0.1`, `[::1]`, or `localhost` are accepted. HTTPS, remote hosts, malformed URLs, and custom unsafe destinations emit `stream-micro: unavailable` and exit successfully.
- Quota is reconstructed into a new object. Bucket IDs must match a bounded 1-64 character ASCII identifier contract. Each retained bucket contains only a finite `remaining_fraction` from 0 through 1 and a timezone-bearing, parseable ISO reset timestamp.
- Raw nested fields and invalid buckets are discarded rather than forwarded.
- Deterministic behavior coverage invokes the Node companion against a local capture server and verifies remote rejection and nested quota stripping.

## Finding 2: Configuration Validation Before Persistence

- `validate_control_config` now delegates `plugins.antigravity` to `validate_antigravity_config` for every parsed or written configuration, independent of `enabled`.
- Antigravity validation failures are translated to `ControlConfigError` with a `plugins.antigravity.<field>` prefix where applicable.
- `PUT /api/config` therefore returns HTTP 422 before `write_control_config` runs.
- API coverage verifies an invalid disabled `max_sessions` receives the exact field-specific error and leaves the configuration file byte-for-byte unchanged.

## Finding 3: Bounded Lifecycle State

- `_live_sessions`, `_live_states`, and `_completed` are bounded to configured `max_sessions`.
- After each accepted update, the oldest live record is evicted first by `(updated_at, conversation_id)`, giving deterministic tie behavior.
- All associated state and completion markers are removed together.
- Tests cover oldest-record eviction and completion-marker cleanup.

## Finding 4: Dashboard Configuration Controls

- Added an Antigravity provider card following the existing Codex and Claude Code card patterns.
- The card updates `enabled`, `agy_path`, `agy_home`, and `max_sessions` in `plugins.antigravity`.
- It displays health, conversation count, quota bars, and the companion privacy note.
- Frontend contract coverage verifies the card is mounted and each supported field updates the Antigravity configuration record.

## Finding 5: Health Behavior

- Any accepted live status makes an enabled provider connected even when the CLI is unavailable.
- Cache-only health remains connected only when both a recognized cache and a usable CLI are available.
- Health detail independently reports cache, live companion, and quota availability, and adds the missing-CLI explanation when disconnected for that reason.
- An instantiated disabled plugin reports `Disabled`.
- The Bridge currently constructs and publishes only enabled provider instances. Publishing disabled Antigravity health would require changing the registry contract for all providers and could alter manifest/button behavior. That broad change was omitted as permitted by the finding; disabled detail remains correct at the plugin boundary and dashboard configuration clearly shows the disabled toggle state.

## Finding 6: Safe Resume

- Resume still launches exactly `agy --conversation <conversation-id>` with `shell=False` and a new console.
- A stored workspace is used only while it remains an existing directory. Otherwise launch proceeds with `cwd=None`.
- Caller parameters cannot supply or override `cwd`; selection derives it only from the current plugin snapshot.
- Tests cover the direct launcher, caller-provided `cwd` rejection by omission, and selection after an accepted live workspace is deleted.

## Finding 7: Deterministic Closed Port

- The companion failure test no longer assumes TCP port 1 is closed.
- It binds an ephemeral loopback port and keeps the reservation socket open while Node attempts the request, deterministically preventing a listener from accepting the connection.

## Verification

- Focused Antigravity/config/server/frontend tests: 71 passed.
- Full Python suite: 223 passed, 1 unrelated deprecation warning.
- Ruff: `All checks passed!`
- Pyright: `0 errors, 0 warnings, 0 informations`.
- Node syntax: `node --check integrations/antigravity-stream-micro/scripts/report-status.mjs` passed.
- Node behavior: remote rejection, loopback forwarding, quota reconstruction, malformed input, and deterministically unavailable endpoint tests passed through pytest subprocess coverage.
- Frontend: `npm run build` passed.
- `git diff --check` passed with only Git's Windows line-ending notices.

## Concerns

- The existing Starlette TestClient dependency emits a deprecation warning about the httpx integration. It is unrelated to Antigravity behavior and does not fail tests.
- Disabled provider snapshots remain unavailable through the current enabled-only registry architecture, as documented under finding 5.

## Commit

Commit: `fix: resolve Antigravity final review findings` (this report is included in that commit).
