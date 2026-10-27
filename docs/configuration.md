# Provider setup and advanced configuration

[← Back to the product guide](../README.md)

Use the Dashboard's **Configuration** page to enable providers and **Buttons**
to assign session zones or quota accounts to keys. Most users do not need to
edit YAML. This guide covers integration requirements and advanced options.

## Configuration files

The first launch writes `bridge.yaml`. It is the Dashboard's versioned source
of truth and does not replace the legacy Ollama `config.yaml`.

Credentials saved through the Dashboard are not returned to the browser.
For quota sources that support them, environment-variable references let you
keep keys outside the configuration file.

## OpenCode and OpenChamber

Keep your existing OpenCode server running. Discovery checks only configured
and listening **loopback** endpoints; the configured fallback is
`http://127.0.0.1:4096`.

When OpenChamber Desktop is running, Stream Micro automatically prefers its
local authenticated proxy so the Deck and OpenChamber show sessions from the
same OpenCode server. Stream Micro never starts or stops OpenCode.

## Claude Code

Enable `plugins.claude_code` in Configuration or `bridge.yaml`. Stream Micro
reads recent session IDs and titles from local Claude Code history without
copying credentials. Claude desktop app sessions are excluded: resuming one
in a console would fork it.

### Companion setup

The Dashboard's Claude Code card checks the quota status line and the `WAIT`
hook in `~/.claude/settings.json`, their Bridge port, and when each last
reported. It can install, fix, or remove them for this Bridge's port.

- The settings file is backed up first.
- Only entries running the bundled scripts are touched.
- An existing status line is kept unless you choose to chain ours after it
  (which still draws the line) or replace it.
- Either companion can be declined.

After Claude Code's first API response, the status-line companion supplies
Pro/Max 5-hour and 7-day usage percentages and reset times to quota keys and
the Dashboard.

For manual setup, endpoint overrides, and a one-session launch, see the
[Claude Code companion guide](../integrations/claude-code-stream-micro/README.md).
Set `plugins.claude_code.plugin_dir` to that integration folder to include
the hook when a physical key resumes a session.

### Session-state details

Basic state detection needs no companion: Stream Micro reads the live Claude
process registry (`~/.claude/sessions`, matched by process start time) and the
tail of each session transcript. Open sessions appear before closed ones.

A finished turn with outstanding background subagents or background
`codex` / `opencode` / `agy` / `gemini` commands shows `SUB`. The companion
adds exact lifecycle events, notably `WAIT` for approval requests.

Process checks are implemented for Windows. Elsewhere, states come from
transcripts and hooks, and closed sessions are not marked `OFF`.

A physical Session-key press uses the official `claude --resume <session-id>`
option in a new Windows console. The hook reports only to the loopback Bridge
and silently does nothing when the Bridge is stopped.

## Antigravity CLI

This integration supports **the CLI only**. Enable `plugins.antigravity` in
Configuration or `bridge.yaml` and install the bundled
[Antigravity status-line companion](../integrations/antigravity-stream-micro/README.md)
for live state and quota.

On Windows the default binary is `%LOCALAPPDATA%\agy\bin\agy.exe`. Stream Micro
shows the latest cached session for each workspace. Pressing its key opens a
new Windows console with `agy --conversation <id>` to restore that selection.

No prompts, history, transcripts, or credentials are read or sent by this
integration. Missing CLI, cache, or status data makes the provider unavailable
without disrupting the Bridge.

## Quota display and colours

Quota keys show up to two windows. The reset time refers to the first window
(normally 5-hour), unless a window is exhausted: then it refers to that window,
or the latest reset among several exhausted windows.

By default the display shows remaining quota. To show used quota instead,
change **Quota keys show → What is used** or set:

```yaml
stream_deck:
  quota_display: used  # remaining (default) | used
```

Window labels use short names such as `5H`, `7D`, and `MO`. In **Buttons** you
can choose a key's accent colour (shared across a session zone), change each
session state's colour, and hide the accent line on Session keys. Quota keys
always keep their accent line. The preview updates immediately; the Deck
updates after **Save**.

```yaml
stream_deck:
  state_colors: {completed: "#ffffff", waiting: "#ffc15a"}
  session_accent_line: true
buttons:
  - key_index: 5
    accent: "#e28b67"
```

## CLIProxyAPI pool quota (optional)

Disabled by default. Enable `cliproxy_quota` in Configuration to show Codex and
Claude quota for accounts pooled in a self-hosted
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) server. In **Buttons**,
choose one of its accounts as the quota key's **Account**. One proxy is
supported per Bridge.

```yaml
plugins:
  cliproxy_quota:
    enabled: true
    base_url: https://proxy.example:8317
    management_key: null          # set in Dashboard; stored, never shown
    providers: [codex, claude]
```

### Credentials and transport

- The Bridge asks the proxy's management API for accounts and requests usage
  through `/v0/management/api-call` with `$TOKEN$`. Account tokens never leave
  the proxy; the Bridge stores only the management key.
- The upstream usage endpoints (`chatgpt.com/backend-api/wham/usage` and
  `api.anthropic.com/api/oauth/usage`) are **not documented public APIs**.
  See the [quota-access research note](research/claude-code-quota-access.md).
- The management key is stored like the Ollama cookie and is never returned
  to the Dashboard. Use `management_key_env` to read it from an environment
  variable instead.
- Plain HTTP to another machine requires `allow_insecure_http: true` and
  exposes the key in transit. This opt-in exists for self-hosted proxies
  without TLS; the Dashboard warns about it. Usage API sources below do not
  allow this exception.
- Changing `base_url` clears both the saved key and `management_key_env`;
  enter them again for the new proxy.
- Last-seen values are kept in `.state/` and shown in grey while unreachable.

## Usage API quota and balances (optional)

Disabled by default. Enable `http_quota` in Configuration to read usage JSON
from an HTTPS service, with one source per account. Choose the source as a
quota key's **Account** and optionally set a **Label** in Buttons.

### Presets

- `openrouter`: an API key's spending limit.
- `openrouter_credits`: account credits; OpenRouter may require a management key.
- `ollama`: Ollama Cloud included monthly credits through the documented
  `/api/balance`, using an ollama.com API key. This is an alternative to the
  legacy `ollama_quota` session cookie.

### Balances

Supported presets: `deepseek`, `moonshot` (platform.moonshot.ai, USD),
`moonshot_cn` (platform.moonshot.cn, CNY), `siliconflow` (siliconflow.cn, CNY),
`siliconflow_intl` (siliconflow.com, USD), and `novita`.

These use documented balance APIs with ordinary API keys, free to read. A key
shows the money left (for example `$3.66`, footer `BALANCE`). The bar compares
it with the balance after the latest top-up: an increase counts as a top-up
and becomes the full bar. Set `budget` on a source for a fixed full-bar amount.

### Custom sources

Use JSON paths such as `data.limit`, `items.0.used`, or
`limits[type=TOKENS,unit=3].percentage` (the first list item whose fields match).
Each of up to two rows reads:

- A used or remaining percentage (`fraction: true` for a 0–1 value), or
- A used or remaining amount plus a `limit`.

Reset times are optional and accept ISO text, epoch seconds, or milliseconds.

```yaml
plugins:
  http_quota:
    enabled: true
    sources:
      - id: openrouter
        preset: openrouter
        api_key: null             # set in Dashboard; stored, never shown
      - id: my-service
        label: MY API
        url: https://api.example.com/v1/usage
        api_key_env: MY_API_KEY   # or save the key through the Dashboard
        auth_scheme: Bearer       # "" sends the bare key; auth_header renames the header
        windows:
          - label: 5H
            used_percent: data.five_hour.used_percent
            reset_at: data.five_hour.resets_at
          - label: WEEK
            used: data.week.used
            limit: data.week.limit
```

### Credentials and cached readings

Keys are stored like the Ollama cookie and never returned to the Dashboard;
`api_key_env` reads one from an environment variable instead. URLs must use
HTTPS (plain HTTP is allowed only to this machine) and must not contain keys.
Moving a source to another host clears both its saved key and `api_key_env`;
enter them again for the new host.

Last-seen values are kept in `.state/` next to `bridge.yaml`. Unreachable
sources show them in grey. A cached window whose reset time has passed is
hidden rather than shown as refilled.

For direct configuration API updates and the secret-preservation contract,
see [Development](development.md#configuration-api).

## Legacy Ollama quota

`ollama_quota` is a separate display-only plugin. Enable it in `bridge.yaml`
only if you retain the legacy Ollama configuration. For new setups, consider
the `http_quota` Ollama preset above instead.

## Troubleshooting

### The tray icon is missing or red

Check Windows' **^** hidden-icons area first. Green means the Bridge's HTTP
server responds; yellow means starting/stopping; red means stopped or
unavailable. External provider outages alone do not turn it red.

For an explicit console launch of the tray application, run from the project
folder after installation:

```powershell
.venv\Scripts\python.exe -m stream_micro.cli tray --bridge-config bridge.yaml
```

Configuration or dependency errors before tray creation appear in a Windows
error dialog. Logs are written next to `bridge.yaml` as `bridge.log`, rotated
at 5 MiB with one backup, `bridge.log.1`.

### The port is already in use

Repeated tray launches for the same configuration only open the Dashboard.
If a different process (including a manually launched `serve`) occupies the
port, startup fails safely: no process is killed or adopted. Close that
instance yourself before restarting from the tray.

`start.bat` is a compatibility shortcut to the same windowless tray launcher,
not a separate console Bridge. Neither launcher installs packages or builds
the frontend. There is no automatic crash restart in this version.

### A provider or quota source is unavailable

Check that it is enabled in Configuration and its tool or server is running.
For Claude Code, check the companion status on its Dashboard card. For
Antigravity, check the status-line companion setup. Grey quota readings are
cached values, not a live connection.

### Updating dependencies fails on Windows

Close Stream Micro before running `uv sync` if Windows reports that
`stream-micro.exe` is in use. Disable start at login before moving the checkout
or virtual environment; re-enable it afterward from the tray menu. The startup
entry is per-user and does not need administrator privileges.
