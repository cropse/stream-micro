# Stream Micro Bridge for Claude Code

This companion plugin sends Claude Code lifecycle events to the local Stream
Micro Bridge. It sends only the Session ID, working directory, event type,
notification type, optional Session title, and at most 200 characters of the
submitted prompt. Failures are ignored so the integration cannot block Claude
Code.

The easiest way to install it for every Session is the Claude Code card in the
Bridge Dashboard (Configuration): it writes the hook and the status line into
`~/.claude/settings.json` with the Bridge's port, and can remove them again.

For a one-session check, start Claude Code with:

```powershell
claude --plugin-dir integrations/claude-code-stream-micro
```

The Bridge listens on
`http://127.0.0.1:8765/api/hooks/claude-code` by default. Set
`STREAM_MICRO_CLAUDE_HOOK_URL` before starting Claude Code (or pass
`--stream-micro-url=<url>` to the script, as the Dashboard's installer does) if
the Bridge uses a different loopback port.

## Quota

Claude Code exposes Pro/Max account usage only to a configured status-line
command. Add this to `~/.claude/settings.json`, changing the path to this
repository when needed:

```json
{
  "statusLine": {
    "type": "command",
    "command": "node \"C:/path/to/stream-micro/integrations/claude-code-stream-micro/scripts/report-quota.mjs\""
  }
}
```

The script displays a compact `5H` / `7D` summary inside Claude Code and sends
only the Session ID and rate-limit fields to the loopback Bridge. Quota data
appears after the first API response in a Claude Code Session. Set
`STREAM_MICRO_CLAUDE_QUOTA_URL` if the Bridge uses a different loopback port.
