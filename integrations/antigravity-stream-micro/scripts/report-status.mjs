const statusUrl =
  process.env.STREAM_MICRO_ANTIGRAVITY_STATUS_URL ??
  "http://127.0.0.1:8765/api/hooks/antigravity/status";
const quotaBucketId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

async function readInput() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return JSON.parse(input);
}

try {
  const destination = new URL(statusUrl);
  if (
    destination.protocol !== "http:" ||
    !["127.0.0.1", "[::1]", "localhost"].includes(destination.hostname)
  ) {
    throw new TypeError("status destination must be loopback HTTP");
  }
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
    quota: rawQuota,
  } = input;
  const quota = {};
  if (rawQuota !== null && typeof rawQuota === "object" && !Array.isArray(rawQuota)) {
    for (const [bucketId, bucket] of Object.entries(rawQuota)) {
      if (!quotaBucketId.test(bucketId) || bucket === null || typeof bucket !== "object" || Array.isArray(bucket)) continue;
      const { remaining_fraction, reset_time } = bucket;
      if (
        typeof remaining_fraction !== "number" ||
        !Number.isFinite(remaining_fraction) ||
        remaining_fraction < 0 ||
        remaining_fraction > 1 ||
        typeof reset_time !== "string" ||
        !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(reset_time) ||
        Number.isNaN(Date.parse(reset_time))
      ) continue;
      quota[bucketId] = { remaining_fraction, reset_time };
    }
  }
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
