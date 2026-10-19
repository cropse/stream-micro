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

let input;
try {
  input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
} catch {
  process.exit(0);
}

const formatPercent = (window) => {
  const value = window?.used_percentage;
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)}%`
    : "--";
};

const fiveHour = formatPercent(input.rate_limits?.five_hour);
const sevenDay = formatPercent(input.rate_limits?.seven_day);

console.log(`CLAUDE · 5H ${fiveHour} · 7D ${sevenDay}`);

const quotaWindow = (window) => {
  if (!window || typeof window !== "object") return undefined;
  return {
    used_percentage: window.used_percentage,
    resets_at: window.resets_at,
  };
};
const payload = {
  session_id: input.session_id,
  rate_limits: {
    five_hour: quotaWindow(input.rate_limits?.five_hour),
    seven_day: quotaWindow(input.rate_limits?.seven_day),
  },
};
const endpoint = argument("stream-micro-url")
  ?? process.env.STREAM_MICRO_CLAUDE_QUOTA_URL
  ?? "http://127.0.0.1:8765/api/hooks/claude-code/quota";
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 1200);
try {
  await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  });
} catch {
  // Quota reporting must never interrupt or alter a Claude Code Session.
} finally {
  clearTimeout(timeout);
}
