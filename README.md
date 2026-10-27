# stream-micro

Local Agent Control Surface for **Elgato Stream Deck MK.2 only** (15 keys,
5 × 3). It discovers a locally running OpenCode Server, renders the first five
Sessions on the first row, and provides a local Dashboard at the same address.
The Dashboard speaks English or Traditional Chinese (the 中文 / EN button in
the top bar; it starts in Chinese for Traditional Chinese browsers and
remembers the choice in this browser).

## Run

```powershell
uv sync --dev
cd frontend; npm ci; npm run build; cd ..
uv run stream-micro serve
```

Open `http://127.0.0.1:8765`. The first run writes `bridge.yaml`; it is the
Dashboard's versioned source of truth and does not replace the legacy Ollama
`config.yaml`.

## Windows system tray

After the setup/build commands above, double-click **`start.vbs`** for a
windowless launch. `start.bat` is only a compatibility shortcut to the same
windowless tray launcher; it no longer starts a separate console Bridge.
Neither launcher installs packages or builds the frontend.
For an explicit troubleshooting console launch:

```powershell
.venv\Scripts\python.exe -m stream_micro.cli tray --bridge-config bridge.yaml
```

The MK.2-shaped tray icon is green when this Bridge's HTTP server responds,
yellow while starting/stopping, and red when stopped or unavailable. Hover for
status; single-click opens Dashboard using the configured host/port. External
provider outages do not by themselves turn the icon red. Windows may put the
icon under **^**; use Windows taskbar settings to keep it visible.

Right-click for **開啟 Dashboard**, **重新啟動 Bridge**, **停止 Bridge**,
**登入自動啟動**, and **結束**. Stop keeps the tray icon so you can restart;
Exit stops the Bridge and releases the Deck before removing the icon.
Login startup is **off by default**; checking the menu installs only this
configuration's launcher in your current user's Startup folder (no admin).
Uncheck it before moving the checkout or virtual environment.

Repeated tray launches for the same configuration only open Dashboard.
If another process (including a manually launched `serve`) occupies the port,
startup fails safely: **no process is killed or adopted**. Close that instance
yourself before restarting Bridge from the tray. There is no automatic crash
restart in this version, and this application never starts/stops OpenCode.

Tray logs are written beside `bridge.yaml` to `bridge.log`, rotated at 5 MiB
with one backup, `bridge.log.1`. Configuration or dependency failures before
tray creation are shown in a Windows error dialog. `serve` and `serve --reload`
remain available for development without a tray icon. Close the application
before running `uv sync` if Windows reports that `stream-micro.exe` is in use.

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

Every key names its windows the same short way (`5H`, `7D`, `MO`) on the
number's row, and keys draw their labels and numbers at one size whatever the
digits, so an `80%` key and a `100%` key line up.

### Colours

Every key carries its provider's colour as a line along the top edge; on a
Session key the state strip sits right under it. In Buttons you can pick a
key's colour (a Session zone shares one), change the colour of any Session
state (`RUN`, `SUB`, `WAIT`, `DONE`, `IDLE`, `ERR`, `OFF`), and turn the line
off for Session keys; quota keys always keep it. The preview changes at once,
the Deck after Save:

```yaml
stream_deck:
  state_colors: {completed: "#ffffff", waiting: "#ffc15a"}
  session_accent_line: true
buttons:
  - key_index: 5
    accent: "#e28b67"
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

The Dashboard's Claude Code card checks both companions (the quota status
line and the `WAIT` hook): whether they are in `~/.claude/settings.json`,
whether they point at this Bridge's port, and when each last reported. One
click installs, fixes or removes them for this Bridge's port. Only entries
that run the bundled scripts are touched, the file is backed up first, and
another tool's status line is kept unless you chain ours after it (it still
draws the line) or replace it. Either part can be declined. The endpoint that
writes the file accepts only the Bridge's own page.

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

### Antigravity CLI Sessions

The Antigravity integration supports the CLI only. On Windows, its default
binary path is `%LOCALAPPDATA%\agy\bin\agy.exe`. Enable
`plugins.antigravity` in the Dashboard or `bridge.yaml`; Stream Micro shows one
latest cached session per workspace. Selecting a Session key opens a new
Windows console and restores that exact selection with
`agy --conversation <id>`.

Setup requires the bundled Antigravity `statusLine` companion script so live
state and quota can reach Bridge. Stream Micro reads or sends no prompts,
history, transcripts, or credentials. If the CLI, cache, or status data is
absent, the provider safely shows as unavailable.

### CLIProxyAPI pool quota (optional)

Disabled by default and configured in the Dashboard's Configuration page.
`cliproxy_quota` shows the Codex and Claude quota of every account pooled in a
self-hosted [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
server; on a quota key, pick one of its accounts as the **Account** in
Buttons. One proxy per Bridge.

- The Bridge asks the proxy's management API for its accounts and has the
  proxy call each account's usage endpoint (`/v0/management/api-call` with
  `$TOKEN$`), so **account tokens never leave the proxy**; the Bridge stores
  only the management key.
- Those usage endpoints (`chatgpt.com/backend-api/wham/usage`,
  `api.anthropic.com/api/oauth/usage`) are **not documented public APIs**;
  see the note in `docs/research/claude-code-quota-access.md`.
- The management key is stored like the Ollama cookie and never returned to
  the Dashboard; `management_key_env` reads it from an environment variable
  instead. Plain `http://` to another machine needs
  `allow_insecure_http: true`, because the key would cross the network
  unencrypted. This differs from usage-API sources, which always require
  `https://` off-machine: a self-hosted proxy on a home network often has no
  TLS, so the opt-in exists and the Dashboard shows a warning. A changed
  `base_url` keeps neither the saved key nor `management_key_env`: enter them
  again for the new proxy.
- Last-seen numbers are kept in `.state/` and shown in grey while the proxy
  is unreachable.

```yaml
plugins:
  cliproxy_quota:
    enabled: true
    base_url: https://proxy.example:8317
    management_key: null          # set it in the Dashboard (stored, never shown)
    providers: [codex, claude]
```

### Usage API quota (optional)

Disabled by default and configured in the Dashboard's Configuration page.
`http_quota` reads quota from any service that reports usage as JSON over
HTTPS, one source per account; on a quota key, pick the source as its
**Account** (and an optional **Label**) in Buttons.

- **Presets** need only a key: `openrouter` (an API key's spending limit),
  `openrouter_credits` (account credits; OpenRouter may require a management
  key) and `ollama` (Ollama Cloud's included monthly credits from the
  documented `/api/balance`, with an ollama.com API key; an easier
  alternative to the legacy `ollama_quota` session cookie).
- **Balances** (documented, ordinary API keys, free to read): `deepseek`,
  `moonshot` (platform.moonshot.ai, USD), `moonshot_cn` (platform.moonshot.cn,
  CNY), `siliconflow` (siliconflow.cn, CNY), `siliconflow_intl`
  (siliconflow.com, USD) and `novita`. These services report only what is
  left, so the key shows the money left (`$3.66`, footer `BALANCE`) and the
  bar compares it with the balance after the latest top-up: a balance that
  goes up counts as a top-up and becomes the full bar. Set `budget` on a
  source to use a fixed amount as the full bar instead.
- **Custom** sources name where the numbers are with JSON paths such as
  `data.limit`, `items.0.used` or `limits[type=TOKENS,unit=3].percentage`
  (the first list item whose fields match). A row reads a used or remaining
  percentage (`fraction: true` for 0–1), or a used or remaining amount with a
  `limit`, plus an optional reset time (ISO text or epoch seconds or
  milliseconds). Up to two rows per key.
- Keys are stored like the Ollama cookie and never returned to the Dashboard;
  `api_key_env` reads one from an environment variable instead. URLs must use
  `https://` (plain HTTP only to this machine) and may not carry keys. When
  saving through `PUT /api/config` yourself, send back the
  `api_key_configured: true` you read with a blank key to keep the saved one;
  without it the key is cleared, as for the Ollama cookie. A source moved to
  another host keeps neither its saved key nor its `api_key_env`: enter them
  again for the new host.
- Last-seen numbers are kept in `.state/` next to `bridge.yaml`; while a source
  is unreachable its keys show them in grey, and a window whose reset time has
  passed is hidden rather than shown as refilled.

```yaml
plugins:
  http_quota:
    enabled: true
    sources:
      - id: openrouter
        preset: openrouter
        api_key: null             # set it in the Dashboard (stored, never shown)
      - id: my-service
        label: MY API
        url: https://api.example.com/v1/usage
        api_key_env: MY_API_KEY   # or read the key from an environment variable
        auth_scheme: Bearer       # "" sends the bare key; auth_header renames the header
        windows:
          - label: 5H
            used_percent: data.five_hour.used_percent
            reset_at: data.five_hour.resets_at
          - label: WEEK
            used: data.week.used
            limit: data.week.limit
```

## Verify

```powershell
uv run pytest
uv run ruff check src tests
uv run pyright src tests
cd frontend; npm run build
```
