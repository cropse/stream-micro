# stream-micro agent guide

## Product direction

`stream-micro` is a local Stream Deck Bridge. It turns configurable data and
agent state into custom Stream Deck key displays and routes physical key presses
to allowlisted developer workflows.

The sole supported target for the current product phase is **Elgato Stream Deck
MK.2**: 15 visual keys in a 5 × 3 layout. Do not add Mini, XL, Plus, mobile, or
Codex Micro hardware support unless the product scope explicitly changes.

The current Ollama quota display is one example adapter, not the product
boundary. Keep the Bridge extensible for Cloud Codex, Gemini, CI, calendars,
local tools, and other sources.

## Architecture boundary

```
Frontend configuration UI
        <-> local Python HTTP API <-> versioned config file
                                         <-> Python Bridge <-> Stream Deck
```

- The frontend is a configuration client. It reads, edits, validates, and
  previews configuration through the local API. It does not access credentials,
  local files, or Stream Deck hardware directly.
- The config file is the shared contract and source of truth between the UI and
  the Python Bridge. Prefer a versioned schema and backwards-compatible
  migrations as it grows.
- The Python Bridge owns hardware discovery, rendering, polling/subscriptions,
  action execution, retries, reconnection, and safe error display.
- Do not require a hosted frontend for local control. A locally served UI and
  API must remain usable without external network access.

## Configuration model

Keep configuration grouped by responsibility:

- `bridge`: schema version, logging, refresh/retry behavior, local API settings.
- `stream_deck`: model, serial selection, layout, brightness, reset behavior.
- `sources`: provider-specific connections and display-ready signals (for
  example Codex agents, Gemini tasks, CI results, calendar events, or Ollama
  quota).
- `buttons`: one record per key with display content, source binding, display
  rules, colors, refresh policy, and optional action binding.
- `actions`: named, allowlisted actions such as opening a Codex thread,
  focusing an agent, opening a project/file, sending an approved prompt, or
  triggering a local workflow.

Do not store browser-only UI state in the shared configuration unless it is a
user preference that should persist across sessions.

## Security and control rules

- Credentials and provider tokens stay in local configuration or the OS secret
  store; never expose them to the browser, previews, logs, fixtures, or source
  control.
- The frontend must call a local API for configuration changes. It must never
  write `config.yaml` directly from browser code.
- Treat key actions as privileged. Validate action IDs and parameters against
  an allowlist before the Bridge executes them.
- Preserve the existing same-origin cookie protections for Ollama and apply the
  same principle to future provider adapters.

## Planned local API

Use the following endpoint shape as the baseline when adding the UI backend:

- `GET /api/config` — read the normalized configuration.
- `PUT /api/config` — validate and atomically save configuration.
- `GET /api/devices` — discover compatible Stream Deck devices.
- `GET /api/status` — Bridge, source, and device health for the UI.
- `POST /api/actions/test` — safely test an allowlisted action or key mapping.

Keep this API local by default. Any remote-control capability requires explicit
authentication and a separate security review.

## Working conventions

- `stream-micro serve` is the control-surface entrypoint. It creates and uses
  `bridge.yaml`, leaving the legacy Ollama `config.yaml` untouched.
- New installations start with unassigned keys and OpenCode disabled. Users
  can add OpenCode and assign Session zones themselves; preserve saved layouts.
  Local discovery may health-check configured loopback endpoints and
  listening loopback ports, but must not scan the LAN. A Session key always
  selects its matching OpenChamber Desktop Session through its official
  deep-link protocol; it does not require a per-Session Windows binding. When
  OpenChamber Desktop is running, its loopback `/api` proxy is the preferred
  OpenCode Session source: do not read its private, token-protected raw
  OpenCode port directly.

- Preserve the existing Python package in `src/stream_micro`; keep the UI in
  `frontend/`.
- Add tests whenever a config schema, validation rule, renderer behavior, or
  hardware failure path changes.
- Keep demo mode free of credentials and hardware requirements.
- Make changes in small, compatible steps; do not couple future cloud adapters
  to the current Ollama scraper implementation.
- Treat the MK.2's 15-key layout as the default configuration and preview
  contract. Device discovery must validate the selected device before applying
  a saved layout.
