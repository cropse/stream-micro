const statusUrl =
  process.env.STREAM_MICRO_ANTIGRAVITY_STATUS_URL ??
  "http://127.0.0.1:8765/api/hooks/antigravity/status";

async function readInput() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return JSON.parse(input);
}

try {
  const input = await readInput();
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    throw new TypeError("status input must be an object");
  }

  const {
    conversation_id,
    cwd,
    agent_state,
    tool_confirmation_pending,
    task_count,
    quota,
  } = input;
  const response = await fetch(statusUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      conversation_id,
      cwd,
      agent_state,
      tool_confirmation_pending,
      task_count,
      quota,
    }),
    signal: AbortSignal.timeout(3000),
  });

  process.stdout.write(`stream-micro: ${response.ok ? "reported" : "unavailable"}\n`);
} catch {
  process.stdout.write("stream-micro: unavailable\n");
}
