# Development

[← Back to the product guide](../README.md) ·
[Provider setup and advanced configuration](configuration.md)

## Local setup

Run from the repository root:

```powershell
uv sync --dev
cd frontend
npm ci
npm run build
cd ..
uv run stream-micro serve
```

The Bridge serves the Dashboard at `http://127.0.0.1:8765` by default. It creates
and uses `bridge.yaml`, leaving the legacy Ollama `config.yaml` untouched.

## Hot reload

Start the Bridge with Python reload enabled:

```powershell
uv run stream-micro serve --reload
```

For Dashboard hot reload, run the following in a second terminal, starting
from the repository root:

```powershell
cd frontend
npm run dev
```

Open the URL Vite prints (normally `http://127.0.0.1:5173`). API requests are
proxied to the Bridge. `serve` and `serve --reload` run without a tray icon.

## Integration boundaries

Plugins expose a manifest, configuration validation, a status snapshot, and a
strict allowlist of actions. OpenCode, Codex, and Claude Code can each supply
Session keys. `ollama_quota` is a separate display-only plugin.

OpenCode discovery checks configured and listening loopback endpoints only.
When OpenChamber Desktop runs, its authenticated local `/api` proxy is the
preferred source so the Deck and OpenChamber use the same OpenCode server.
Stream Micro does not manage the OpenCode server lifecycle.

Provider setup, platform limitations, cached quota behaviour, and transport
requirements are documented in [Configuration](configuration.md). Companion
implementation and manual setup details live alongside their scripts:

- [Claude Code companion](../integrations/claude-code-stream-micro/README.md)
- [Antigravity CLI companion](../integrations/antigravity-stream-micro/README.md)

## Configuration API

The Dashboard reads and saves the versioned configuration through the local
Bridge API. Saved credentials are not returned to the browser.

When writing `PUT /api/config` directly for an `http_quota` source, send back
`api_key_configured: true` from the read response with a blank key to retain
the saved key. Without that marker, the key is cleared, as with the Ollama
cookie. Changing a source's host clears its saved key and `api_key_env`.

For CLIProxyAPI, changing `base_url` clears the saved management key and
`management_key_env`. The companion-settings write endpoint accepts only the
Bridge's own page.

## Verification

Run backend checks from the repository root:

```powershell
uv run pytest
uv run ruff check src tests
uv run pyright src tests
```

Build the frontend:

```powershell
cd frontend
npm run build
```

For documentation-only changes, check local Markdown links, image paths, and
`git diff --check`; runtime behaviour is unchanged.
