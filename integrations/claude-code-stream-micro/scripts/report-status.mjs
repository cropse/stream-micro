// Settings written by the Bridge pass --stream-micro-url=... (works in any
// shell); the environment variable stays for hand-made setups.
const argument = (name) => {
  const prefix = `--${name}=`;
  const found = process.argv.find((item) => item.startsWith(prefix));
  return found ? found.slice(prefix.length).replace(/^"|"$/g, "") : undefined;
};

const chunks = [];

for await (const chunk of process.stdin) {
  chunks.push(chunk);
}

try {
  const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const payload = {
    session_id: input.session_id,
    cwd: input.cwd,
    hook_event_name: input.hook_event_name,
    source: input.source,
    session_title: input.session_title,
    prompt: typeof input.prompt === "string" ? input.prompt.slice(0, 200) : undefined,
    notification_type: input.notification_type,
  };
  const endpoint = argument("stream-micro-url")
    ?? process.env.STREAM_MICRO_CLAUDE_HOOK_URL
    ?? "http://127.0.0.1:8765/api/hooks/claude-code";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
} catch {
  // Status reporting must never interrupt or alter a Claude Code Session.
}
