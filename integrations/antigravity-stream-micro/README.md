# Stream Micro Status-Line Companion for Antigravity CLI

This companion sends Antigravity CLI 1.2.2 status-line state to the local Stream
Micro Bridge. It is not a plugin launcher and does not add a `hooks.json` file.

## Setup

Install Node.js, then add this block to
`~/.gemini/antigravity-cli/settings.json`, replacing the repository path as
needed:

```json
{
  "statusLine": {
    "type": "command",
    "command": "node \"C:/path/to/stream-micro/integrations/antigravity-stream-micro/scripts/report-status.mjs\"",
    "stack_with_default": true
  }
}
```

The default Bridge endpoint is
`http://127.0.0.1:8765/api/hooks/antigravity/status`. If the Bridge uses a
different loopback port, set `STREAM_MICRO_ANTIGRAVITY_STATUS_URL` in the
environment that starts Antigravity CLI, for example:

```powershell
$env:STREAM_MICRO_ANTIGRAVITY_STATUS_URL = "http://127.0.0.1:9876/api/hooks/antigravity/status"
```

## Privacy Boundary

The script forwards only these documented status-line fields:

- `conversation_id`
- `cwd`
- `agent_state`
- `tool_confirmation_pending`
- `task_count`
- `quota`

It discards all other status-line input, including `transcript_path`, `model`,
`email`, context-token values, and VCS data. It does not send prompts,
transcripts, model responses, artifacts, command output, credentials, cookies,
OAuth data, tokens, or raw provider responses.

The script has a three-second reporting timeout and exits successfully when
input cannot be parsed or the Bridge cannot be reached, so it cannot block the
Antigravity CLI.
