# stream-micro

Local Agent Control Surface for **Elgato Stream Deck MK.2 only** (15 keys,
5 × 3). It discovers a locally running OpenCode Server, renders the first five
Sessions on the first row, and provides a local Dashboard at the same address.

## Run

```powershell
uv sync --dev
cd frontend; npm ci; npm run build; cd ..
uv run stream-micro serve
```

Open `http://127.0.0.1:8765`. The first run writes `bridge.yaml`; it is the
Dashboard's versioned source of truth and does not replace the legacy Ollama
`config.yaml`.

## Development hot reload

Run the Bridge with Python reload enabled:

```powershell
uv run stream-micro serve --reload
```

For Dashboard hot reload, run this in a second terminal and open the URL it
prints (normally `http://127.0.0.1:5173`). API requests are proxied to Bridge.

```powershell
cd frontend; npm run dev
```

OpenCode discovery only checks configured and listening **loopback** endpoints.
The configured fallback endpoint is `http://127.0.0.1:4096`. When OpenChamber
Desktop is running, Bridge automatically prefers its local authenticated proxy
so both surfaces use the same OpenCode Server.

## Session-key behavior

- The first row contains five Session keys.
- Short-pressing a Session key selects its matching Session.
- Holding a Session key for 2 seconds pins that Session to its current slot;
  the key gets an orange frame and stays in place while the other Sessions
  reorder. Hold the key again to unpin it.
- When OpenChamber Desktop is running, Bridge auto-discovers its local control
  server and reads Sessions through its authenticated `/api` proxy. This keeps
  the Deck list and the OpenChamber UI on the same OpenCode Server.
- Under the provider's line, the strip of a Session key shows its state.
  Bright strips need a look: `RUN` (working), `SUB` (background agents still
  working), `WAIT` (needs your input or approval), `DONE` (finished recently),
  `ERR` (failed). Dark green strips are quiet: `IDLE` (open, no active turn)
  and `OFF` (no live process).

## Quota keys

Quota keys show up to two windows (for example `5H` and `7D`) with a big
number, a bar, and when a window resets: `TODAY 15:11` or `THU 20:25`. That is
the first (5-hour) window, unless one is used up: then that one, or of several
used up, the one that refills last. By default the number is what is **left**, so a full green bar
means plenty of quota; the colour turns amber at 30% left and red at 10%.
Grey numbers are the last values seen while their source is unreachable.
Choose *Quota keys show → What is used* in the Dashboard's Configuration page,
or set it in `bridge.yaml`:

```yaml
stream_deck:
  quota_display: used  # remaining (default) | used
```

## Plugins

Plugins expose a manifest, configuration validation, a status snapshot, and a
strict allowlist of actions. OpenCode, Codex, and Claude Code can each supply
Session keys. `ollama_quota` is a separate display-only plugin; enable it in
`bridge.yaml` only if you retain the legacy Ollama configuration.

### Claude Code Sessions and quota

Enable `plugins.claude_code` in the Dashboard or `bridge.yaml`. The provider
reads recent Session IDs and titles from the local Claude Code history without
copying credentials.

Session state works without any setup: the Bridge reads the live Claude process
registry (`~/.claude/sessions`, busy/idle, matched by process start time; a
Session whose process is gone shows `OFF`) and the tail of each Session
transcript. A finished turn whose background subagents, or background
`codex`/`opencode`/`agy` (Antigravity CLI)/`gemini` commands, have not reported
back shows `SUB`. Open Sessions are listed before closed ones. Checking whether
a process still runs is implemented for Windows; elsewhere states come from
transcripts and hooks, and closed Sessions are not marked `OFF`.
Sessions hosted by the Claude desktop app are left out: they are not terminal
Sessions, and resuming one in a console would fork it.

The bundled companion hook adds exact lifecycle events, notably `WAIT` when
Claude asks for approval:

```powershell
claude --plugin-dir integrations/claude-code-stream-micro
```

Set `plugins.claude_code.plugin_dir` to that integration folder so a physical
Session-key press resumes the exact Session with the hook enabled. The action
uses Claude Code's official `--resume <session-id>` CLI option and opens it in a
new Windows console. The hook reports to the loopback-only Bridge endpoint and
silently does nothing when Bridge is stopped.

Claude Code exposes Pro/Max subscription usage through its status-line input.
Point `~/.claude/settings.json` at the bundled `scripts/report-quota.mjs` as
shown in the companion integration README. After the first API response, the
Bridge displays the official 5-hour and 7-day usage percentages and reset times
on Claude Code quota keys and in the Dashboard.

## Verify

```powershell
uv run pytest
uv run ruff check src tests
uv run pyright src tests
cd frontend; npm run build
```
