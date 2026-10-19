import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Button = {
  key_index: number;
  plugin: string | null;
  component: string | null;
  span: number;
  session_id: string | null;
  action: string | null;
  action_params: Record<string, unknown>;
  window_binding: Record<string, string> | null;
};
type OllamaQuotaConfig = {
  enabled?: boolean;
  cookie_value?: string;
  cookie_configured?: boolean;
  settings_url?: string;
  cookie_name?: string;
  request_timeout_seconds?: number;
  refresh_interval_seconds?: number;
};
type CodexConfig = { enabled?: boolean; codex_path?: string | null; codex_home?: string | null };
type ClaudeCodeConfig = { enabled?: boolean; claude_path?: string | null; claude_home?: string | null; plugin_dir?: string | null; max_sessions?: number };
type UsageWindow = {
  label?: string;
  used_percent?: string;
  remaining_percent?: string;
  used?: string;
  remaining?: string;
  limit?: string | number;  // a plain number is a fixed amount
  fraction?: boolean;
  reset_at?: string;
};
type UsageSource = {
  id: string;
  label?: string | null;
  preset?: string;
  url?: string | null;
  api_key?: string | null;
  api_key_configured?: boolean;
  api_key_env?: string | null;
  auth_header?: string | null;
  auth_scheme?: string | null;
  windows?: UsageWindow[];
};
type HttpQuotaConfig = { enabled?: boolean; sources?: UsageSource[]; refresh_interval_seconds?: number };
type OpenCodeConfig = { enabled?: boolean; endpoints?: string[]; password_env?: string | null };
type PluginConfig = { enabled?: boolean; [key: string]: unknown };
type QuotaDisplay = "remaining" | "used";
type Config = {
  stream_deck: { brightness: number; device_serial: string | null; quota_display?: QuotaDisplay };
  plugins: Record<string, PluginConfig>;
  buttons: Button[];
};
type Session = { id: string; title: string; state: string; project: string | null; directory?: string | null };
type PluginManifest = { id: string; name: string; components: string[]; actions: string[] };
type PluginHealth = { connected: boolean; detail: string; endpoint?: string | null };
type QuotaSignals = Record<string, string | number | boolean | null>;
type QuotaAccount = { id: string; title: string; provider: string; signals?: QuotaSignals | null; error?: string | null };
type PluginSnapshot = {
  health: PluginHealth;
  sessions?: Session[];
  signals?: QuotaSignals | null;
  accounts?: QuotaAccount[];
};

// The account a quota key stores, by id.
function findAccount(snapshot: PluginSnapshot | undefined, key: unknown) {
  if (typeof key !== "string" || !key) return undefined;
  const wanted = key.toLowerCase();
  return snapshot?.accounts?.find((account) => account.id.toLowerCase() === wanted);
}

// What a quota key will show, decided the same way as the Deck renderer
// (quota_source in control_renderer.py): account choice, errors, last-seen data.
function quotaPreview(button: Button | undefined, snapshot: PluginSnapshot | undefined) {
  const customLabel = typeof button?.action_params.label === "string" ? button.action_params.label : "";
  const accountKey = button?.action_params.account;
  let signals: QuotaSignals | null = snapshot?.signals ?? null;
  let problem: string | null = null;
  let title = "";
  if (!snapshot) {
    problem = "No data";
  } else if ((snapshot.accounts?.length ?? 0) > 0 || accountKey) {
    const account = findAccount(snapshot, accountKey);
    if (typeof accountKey !== "string" || !accountKey) problem = "Choose an account";
    else if (!account) problem = snapshot.health.connected ? "Account not found" : snapshot.health.detail;
    else {
      title = account.title;
      signals = account.signals ?? null;
      if (!signals) problem = account.error || "No quota data";
    }
  } else if (!snapshot.health.connected && !signals?.stale) {
    problem = snapshot.health.detail;
  }
  const used = [signals?.primary_percent, signals?.session_percent, signals?.secondary_percent, signals?.weekly_percent]
    .find((value): value is number => typeof value === "number");
  if (!problem && used === undefined) problem = "No quota data";
  const amount = moneyText(signals?.primary_amount_left, signals?.primary_currency);
  return { title: customLabel || title, problem, used, stale: Boolean(signals?.stale), amount };
}
type BridgeStatus = {
  selected_session_id?: string | null;
  selected_session_ids?: Record<string, string>;
  pinned_sessions?: Record<string, Session & { plugin: string }>;
  device: { connected: boolean; detail: string; key_count?: number };
};

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error((await response.json().catch(() => ({}))).detail || "Bridge unavailable");
  }
  return response.json();
}

function displayName(value: string | null | undefined) {
  return value ? value.replace(/_/g, " ").replace(/\b\w/g, (letter: string) => letter.toUpperCase()) : "Not configured";
}

// Same colours as the physical keys: bright = needs a look, dark green = quiet.
const SESSION_STATE_COLORS: Record<string, string> = {
  idle: "#166534",
  running: "#4a9eff",
  background: "#b794ff",
  waiting: "#ffc15a",
  completed: "#ffffff",
  error: "#ff7983",
  disconnected: "#166534",
};
const DARK_SESSION_STATES = new Set(["idle", "disconnected"]);

function sessionStateColor(state: string) {
  return SESSION_STATE_COLORS[state] ?? "#71819a";
}

function sessionStateChip(state: string) {
  return { background: sessionStateColor(state), color: DARK_SESSION_STATES.has(state) ? "#dcfce7" : "#08131f" };
}

function formatReset(value: string | number | boolean | null | undefined) {
  if (!value || typeof value !== "string") return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

// Providers report how much is *used*; colour always follows what is left.
function pctColor(usedPct: number | null | undefined) {
  if (usedPct == null) return "#71819a";
  if (usedPct >= 90) return "#ef4444";
  if (usedPct >= 70) return "#f59e0b";
  return "#4de57a";
}

function quotaShown(usedPct: number, display: QuotaDisplay) {
  const used = Math.min(Math.max(usedPct, 0), 100);
  const value = display === "used" ? used : 100 - used;
  return { value, text: `${Math.round(value)}% ${display === "used" ? "used" : "left"}` };
}

const STALE_COLOR = "#6f8099";

// Balances show money left (control_renderer._money_text uses the same signs).
const CURRENCY_SIGNS: Record<string, string> = { CNY: "¥", USD: "$", EUR: "€", JPY: "¥" };
function moneyText(amount: unknown, currency: unknown) {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return null;
  const code = typeof currency === "string" ? currency : "";
  const text = Math.abs(amount).toFixed(2);
  // A debt reads -$1.23; anything that rounds to zero is just $0.00.
  const minus = amount < 0 && Number(text) !== 0 ? "-" : "";
  return `${minus}${CURRENCY_SIGNS[code] ?? (code ? `${code} ` : "")}${text}`;
}

function UsageBars({ signals, generic = false, display = "remaining" }: {
  signals: QuotaSignals;
  generic?: boolean;
  display?: QuotaDisplay;
}) {
  const primary = (generic ? signals.primary_percent : signals.session_percent) as number | null | undefined;
  const secondary = (generic ? signals.secondary_percent : signals.weekly_percent) as number | null | undefined;
  const primaryReset = generic ? signals.primary_reset_at : signals.session_reset_at;
  const secondaryReset = generic ? signals.secondary_reset_at : signals.weekly_reset_at;
  // Last-seen numbers from an unreachable source are grey, never green.
  const stale = Boolean(signals.stale);
  const tint = (used: number | null | undefined) => stale ? STALE_COLOR : pctColor(used);
  const primaryLabel = generic ? String(signals.primary_label ?? "Primary") : "Session";
  const secondaryLabel = generic ? String(signals.secondary_label ?? "Secondary") : "Weekly";
  return <div className={`ollama-usage-grid${stale ? " is-stale" : ""}`}>
    {stale && <small className="stale-note">Last seen{typeof signals.seen_at === "string" ? ` ${formatReset(signals.seen_at)}` : ""} — the source is not reachable now.</small>}
    <div className="usage-bar">
      <div className="usage-bar-label"><span>{primaryLabel}</span><b style={{ color: tint(primary) }}>{moneyText(signals.primary_amount_left, signals.primary_currency) ?? (primary != null ? quotaShown(primary, display).text : "—")}</b></div>
      <div className="usage-bar-track"><div className="usage-bar-fill" style={{ width: `${primary != null ? quotaShown(primary, display).value : 0}%`, background: tint(primary) }} /></div>
      <small>Resets: {formatReset(primaryReset)}</small>
    </div>
    <div className="usage-bar">
      <div className="usage-bar-label"><span>{secondaryLabel}</span><b style={{ color: tint(secondary) }}>{moneyText(signals.secondary_amount_left, signals.secondary_currency) ?? (secondary != null ? quotaShown(secondary, display).text : "—")}</b></div>
      <div className="usage-bar-track"><div className="usage-bar-fill" style={{ width: `${secondary != null ? quotaShown(secondary, display).value : 0}%`, background: tint(secondary) }} /></div>
      <small>Resets: {formatReset(secondaryReset)}</small>
    </div>
  </div>;
}

function OllamaQuotaCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.ollama_quota as OllamaQuotaConfig | undefined;
  const health = snapshot?.health;
  const signals = snapshot?.signals;

  const setField = (field: keyof OllamaQuotaConfig, value: string | number | boolean) => {
    updateConfig((current) => ({
      ...current,
      plugins: {
        ...current.plugins,
        ollama_quota: { ...current.plugins?.ollama_quota, [field]: value },
      },
    }));
  };

  return <section className="panel ollama-card">
    <p>OLLAMA USAGE</p>
    <h2>Quota</h2>
    <label className="toggle-field ollama-toggle"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> Enable Ollama Cloud quota provider</label>
    <div className="ollama-cookie-field">
      <label>Session cookie value
        <input
          type="password"
          placeholder={pluginConfig?.cookie_configured ? "Cookie configured — enter a new value to replace it" : "Paste ollama.com __Secure-session cookie value"}
          value={pluginConfig?.cookie_value ?? ""}
          onChange={(event) => setField("cookie_value", event.target.value)}
        />
      </label>
      <p className="ollama-hint">{pluginConfig?.cookie_configured ? "The saved cookie stays in the local Bridge and is never returned to this page." : <>Sign in at ollama.com, open DevTools → Application → Cookies, copy the <code>__Secure-session</code> value.</>}</p>
      <details className="ollama-advanced">
        <summary>How to get the cookie</summary>
        <ol className="cookie-steps">
          <li>Sign in at <code>ollama.com</code> in Chrome or Edge with the account to show.</li>
          <li>Press <code>F12</code> to open DevTools and choose the <b>Application</b> tab (in Firefox: <b>Storage</b>).</li>
          <li>Under <b>Cookies</b>, select <code>https://ollama.com</code>.</li>
          <li>Find <code>__Secure-session</code> and copy its <b>Value</b>; paste it above and Save.</li>
          <li>The <b>Expires / Max-Age</b> column there shows until when it works. Signing out of ollama.com ends it early; when it stops working this card says so, and you paste a new one.</li>
        </ol>
      </details>
    </div>
    <details className="ollama-advanced">
      <summary>Advanced settings</summary>
      <label>Settings URL
        <input type="text" value={pluginConfig?.settings_url ?? "https://ollama.com/settings"} onChange={(event) => setField("settings_url", event.target.value)} />
      </label>
      <label>Cookie name
        <input type="text" value={pluginConfig?.cookie_name ?? "__Secure-session"} onChange={(event) => setField("cookie_name", event.target.value)} />
      </label>
      <label>Request timeout (seconds)
        <input type="number" min={1} value={pluginConfig?.request_timeout_seconds ?? 15} onChange={(event) => setField("request_timeout_seconds", Number(event.target.value))} />
      </label>
      <label>Refresh interval (seconds)
        <input type="number" min={1} value={pluginConfig?.refresh_interval_seconds ?? 60} onChange={(event) => setField("refresh_interval_seconds", Number(event.target.value))} />
      </label>
    </details>
    {health && (
      <div className={`ollama-health ${health.connected ? "is-connected" : "is-error"}`}>
        <span className="health-dot" />
        <span>{health.detail}</span>
      </div>
    )}
    {signals && <UsageBars signals={signals} generic={typeof signals.primary_percent === "number"} display={config?.stream_deck.quota_display ?? "remaining"} />}
  </section>;
}

function CodexCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.codex as CodexConfig | undefined;
  const setField = (field: keyof CodexConfig, value: string | boolean | null) => {
    updateConfig((current) => ({
      ...current,
      plugins: {
        ...current.plugins,
        codex: { ...current.plugins.codex, [field]: value },
      },
    }));
  };
  return <section className="panel ollama-card codex-card">
    <p>CODEX APP SERVER</p>
    <h2>Chats &amp; quota</h2>
    <label className="toggle-field"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> Enable Codex provider</label>
    <details className="ollama-advanced">
      <summary>Advanced settings</summary>
      <label>Codex executable
        <input type="text" placeholder="codex (use PATH)" value={pluginConfig?.codex_path ?? ""} onChange={(event) => setField("codex_path", event.target.value || null)} />
      </label>
      <label>CODEX_HOME
        <input type="text" placeholder="Use current Codex home" value={pluginConfig?.codex_home ?? ""} onChange={(event) => setField("codex_home", event.target.value || null)} />
      </label>
    </details>
    {snapshot?.health && <div className={`ollama-health ${snapshot.health.connected ? "is-connected" : "is-error"}`}><span className="health-dot" /><span>{snapshot.health.detail}</span></div>}
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>recent Codex chats available to session keys</span></div>
    {snapshot?.signals && <UsageBars signals={snapshot.signals} generic display={config?.stream_deck.quota_display ?? "remaining"} />}
  </section>;
}

function ClaudeCodeCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.claude_code as ClaudeCodeConfig | undefined;
  const setField = (field: keyof ClaudeCodeConfig, value: string | number | boolean | null) => {
    updateConfig((current) => ({
      ...current,
      plugins: {
        ...current.plugins,
        claude_code: { ...current.plugins.claude_code, [field]: value },
      },
    }));
  };
  return <section className="panel provider-card claude-code-card">
    <p>CLAUDE CODE</p>
    <h2>Sessions &amp; quota</h2>
    <label className="toggle-field claude-code-toggle"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> Enable Claude Code provider</label>
    <details className="ollama-advanced">
      <summary>Advanced settings</summary>
      <label>Claude executable
        <input type="text" placeholder="claude (use PATH)" value={pluginConfig?.claude_path ?? ""} onChange={(event) => setField("claude_path", event.target.value || null)} />
      </label>
      <label>Claude data folder
        <input type="text" placeholder="Use current ~/.claude folder" value={pluginConfig?.claude_home ?? ""} onChange={(event) => setField("claude_home", event.target.value || null)} />
      </label>
      <label>Companion plugin folder
        <input type="text" placeholder="Optional path to the bundled integration" value={pluginConfig?.plugin_dir ?? ""} onChange={(event) => setField("plugin_dir", event.target.value || null)} />
      </label>
      <label>Recent Sessions
        <input type="number" min={1} max={15} value={pluginConfig?.max_sessions ?? 15} onChange={(event) => setField("max_sessions", Number(event.target.value))} />
      </label>
    </details>
    {snapshot?.health && <div className={`ollama-health ${snapshot.health.connected ? "is-connected" : "is-error"}`}><span className="health-dot" /><span>{snapshot.health.detail}</span></div>}
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>recent Claude Code Sessions available to button zones</span></div>
    {snapshot?.signals && <UsageBars signals={snapshot.signals} generic display={config?.stream_deck.quota_display ?? "remaining"} />}
    <p className="provider-note">Session state comes from the running Claude process and its transcript, so it works without setup; the companion hook adds an exact WAIT when Claude needs your approval. SUB means the turn ended but subagents or other agents still work for it. Quota appears after Claude Code's status line receives its first API response.</p>
  </section>;
}

// Known usage APIs; the Bridge fills in URL and fields for these (http_quota PRESETS).
const USAGE_PRESETS: { id: string; name: string; hint: string }[] = [
  { id: "openrouter", name: "OpenRouter key limit", hint: "Any OpenRouter API key that has a spending limit." },
  { id: "openrouter_credits", name: "OpenRouter credits", hint: "Account credits; OpenRouter may require a management key." },
  { id: "deepseek", name: "DeepSeek balance", hint: "A DeepSeek API key (platform.deepseek.com → API keys). Reading the balance costs nothing." },
  { id: "moonshot", name: "Moonshot balance (international)", hint: "An API key from platform.moonshot.ai (USD account). Reading the balance costs nothing." },
  { id: "moonshot_cn", name: "Moonshot balance (China)", hint: "An API key from platform.moonshot.cn (CNY account). Reading the balance costs nothing." },
  { id: "siliconflow", name: "SiliconFlow balance (China)", hint: "An API key from cloud.siliconflow.cn (CNY account). Reading the balance costs nothing." },
  { id: "siliconflow_intl", name: "SiliconFlow balance (international)", hint: "An API key from cloud.siliconflow.com (USD account). Reading the balance costs nothing." },
  { id: "novita", name: "Novita balance", hint: "An API key from novita.ai (Key Management). Reading the balance costs nothing." },
  { id: "ollama", name: "Ollama Cloud credits", hint: "An ollama.com API key (Settings → Keys). Reading the balance uses no credits; no cookie needed." },
  { id: "custom", name: "Custom JSON API", hint: "Any HTTPS endpoint that returns usage as JSON." },
];
const WINDOW_MODES: { id: keyof UsageWindow; name: string; fields: (keyof UsageWindow)[] }[] = [
  { id: "used_percent", name: "Used %", fields: ["used_percent"] },
  { id: "remaining_percent", name: "Remaining %", fields: ["remaining_percent"] },
  { id: "used", name: "Used / limit", fields: ["used", "limit"] },
  { id: "remaining", name: "Remaining / limit", fields: ["remaining", "limit"] },
];

function windowMode(window: UsageWindow): keyof UsageWindow {
  if (window.remaining_percent !== undefined) return "remaining_percent";
  if (window.used !== undefined) return "used";
  if (window.remaining !== undefined) return "remaining";
  return "used_percent";
}

function HttpQuotaCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.http_quota as HttpQuotaConfig | undefined;
  const sources = pluginConfig?.sources ?? [];
  const display = config?.stream_deck.quota_display ?? "remaining";
  const setFields = (fields: Partial<HttpQuotaConfig>) => {
    updateConfig((current) => ({
      ...current,
      plugins: { ...current.plugins, http_quota: { ...current.plugins.http_quota, ...fields } },
    }));
  };
  const setSources = (next: UsageSource[]) => setFields({ sources: next });
  const setSource = (index: number, fields: Partial<UsageSource>) => setSources(sources.map((source, at) => at === index ? { ...source, ...fields } : source));
  const freeId = (base: string) => {
    const taken = new Set(sources.map((source) => source.id));
    let id = base;
    for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
    return id;
  };
  const setWindows = (index: number, windows: UsageWindow[]) => setSource(index, { windows });
  const setWindow = (index: number, at: number, fields: Partial<UsageWindow>) => {
    const windows = [...(sources[index].windows ?? [])];
    windows[at] = { ...windows[at], ...fields };
    setWindows(index, windows);
  };
  // Switching what a row reads clears its path: the old one meant something else.
  const setWindowMode = (index: number, at: number, mode: keyof UsageWindow) => {
    const { used_percent: _used, remaining_percent: _left, used: _amount, remaining: _rest, limit, ...rest } = sources[index].windows?.[at] ?? {};
    const next: UsageWindow = { ...rest, [mode]: "" };
    if (mode === "used" || mode === "remaining") next.limit = limit ?? "";
    const windows = [...(sources[index].windows ?? [])];
    windows[at] = next;
    setWindows(index, windows);
  };
  return <section className="panel provider-card usage-card">
    <p>USAGE APIS</p>
    <h2>Quota from a usage API</h2>
    <label className="toggle-field"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setFields({ enabled: event.target.checked })} /> Enable usage API quota provider</label>
    <p className="ollama-hint">One source per account: pick the service and paste its key. Keys stay in the local Bridge and are never returned to this page. Then choose the source as the Account of a Usage Api quota key in Buttons.</p>
    {sources.map((source, index) => {
      const preset = USAGE_PRESETS.find((item) => item.id === (source.preset || "custom")) ?? USAGE_PRESETS[USAGE_PRESETS.length - 1];
      const custom = preset.id === "custom";
      const windows = source.windows ?? [];
      const account = snapshot?.accounts?.find((item) => item.id === source.id);
      const advanced = <>
        <label>URL
          <input type="text" placeholder={custom ? "https://api.example.com/usage" : "Preset default"} value={source.url ?? ""} onChange={(event) => setSource(index, { url: event.target.value || null })} />
        </label>
        <p className="ollama-hint">Never put a key in the URL. Pointing a source at another host asks for its key again.</p>
        <div className="usage-row">
          <label>Key header
            <input type="text" placeholder="Authorization" value={source.auth_header ?? ""} onChange={(event) => setSource(index, { auth_header: event.target.value || null })} />
          </label>
          <label>Key format
            <select value={source.auth_scheme === "" ? "bare" : source.auth_scheme ? "bearer" : "default"} onChange={(event) => setSource(index, { auth_scheme: event.target.value === "bare" ? "" : event.target.value === "bearer" ? "Bearer" : null })}>
              <option value="default">{custom ? "Bearer <key> (default)" : "Preset default"}</option>
              <option value="bearer">Bearer &lt;key&gt;</option>
              <option value="bare">&lt;key&gt; only</option>
            </select>
          </label>
        </div>
        <label>Key from environment variable
          <input type="text" placeholder="Optional, e.g. OPENROUTER_API_KEY" value={source.api_key_env ?? ""} onChange={(event) => setSource(index, { api_key_env: event.target.value || null })} />
        </label>
        <p className="ollama-hint">{custom ? "Where the numbers are in the JSON reply" : "Rows replace the preset's rows when added"}: paths like <code>data.limit</code>, <code>items.0.used</code> or <code>limits[type=TOKENS,unit=3].percentage</code>. Reset times may be ISO text or epoch seconds/milliseconds.</p>
        {[0, 1].map((at) => {
          const window = windows[at];
          if (!window) {
            return at === windows.length ? <button key={at} type="button" className="link-button" onClick={() => setWindow(index, at, { label: at === 0 ? "5H" : "WEEK", used_percent: "" })}>{at === 0 ? "Add top row" : "Add bottom row"}</button> : null;
          }
          const mode = windowMode(window);
          const fields = WINDOW_MODES.find((item) => item.id === mode)?.fields ?? ["used_percent"];
          return <fieldset key={at} className="usage-window">
            <legend>{at === 0 ? "Top row" : "Bottom row"} · <button type="button" className="link-button" onClick={() => setWindows(index, windows.filter((_, other) => other !== at))}>Remove</button></legend>
            <div className="usage-row">
              <label>Label
                <input type="text" maxLength={8} placeholder="5H" value={window.label ?? ""} onChange={(event) => setWindow(index, at, { label: event.target.value })} />
              </label>
              <label>Numbers
                <select value={mode} onChange={(event) => setWindowMode(index, at, event.target.value as keyof UsageWindow)}>
                  {WINDOW_MODES.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            </div>
            {fields.map((field) => <label key={field}>{`${field.replace("_", " ")} path`}
              <input type="text" value={String(window[field] ?? "")} onChange={(event) => setWindow(index, at, { [field]: field === "limit" && /^\d+(\.\d+)?$/.test(event.target.value.trim()) ? Number(event.target.value) : event.target.value })} />
            </label>)}
            {(mode === "used_percent" || mode === "remaining_percent") && <label className="toggle-field"><input type="checkbox" checked={window.fraction ?? false} onChange={(event) => setWindow(index, at, { fraction: event.target.checked })} /> Value is a fraction (0–1)</label>}
            <label>Reset time path
              <input type="text" placeholder="Optional" value={window.reset_at ?? ""} onChange={(event) => setWindow(index, at, { reset_at: event.target.value })} />
            </label>
          </fieldset>;
        })}
      </>;
      return <div key={index} className="account-item usage-source">
        <div className="usage-row">
          <label>Service
            <select value={preset.id} onChange={(event) => setSource(index, event.target.value === "custom" ? { preset: "custom", windows: windows.length ? windows : [{ label: "5H", used_percent: "" }] } : { preset: event.target.value, url: null, auth_header: null, auth_scheme: null, windows: [] })}>
              {USAGE_PRESETS.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>Label
            <input type="text" maxLength={16} placeholder="Shown on the key" value={source.label ?? ""} onChange={(event) => setSource(index, { label: event.target.value || null })} />
          </label>
        </div>
        <label>API key
          <input
            type="password"
            placeholder={source.api_key_configured ? "Key saved — enter a new value to replace it" : "Paste the API key"}
            value={source.api_key ?? ""}
            onChange={(event) => setSource(index, { api_key: event.target.value })}
          />
        </label>
        <p className="ollama-hint">{preset.hint} {source.api_key_configured && <button type="button" className="link-button" onClick={() => setSource(index, { api_key: "", api_key_configured: false })}>Forget saved key</button>}</p>
        {custom ? advanced : <details className="ollama-advanced"><summary>Advanced settings</summary>{advanced}</details>}
        <div className="usage-row usage-footer">
          <label>Source ID (the key's Account)
            <input type="text" maxLength={32} value={source.id} readOnly={source.api_key_configured === true} title={source.api_key_configured === true ? "Saved sources keep their ID; change the Label instead" : undefined} onChange={(event) => setSource(index, { id: event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} />
          </label>
          <button type="button" className="link-button" onClick={() => setSources(sources.filter((_, other) => other !== index))}>Remove source</button>
        </div>
        {account && <small>{account.error ? account.error : account.signals?.stale ? "Last seen, not current" : "Current"}</small>}
        {account?.signals && <UsageBars signals={account.signals} generic display={display} />}
      </div>;
    })}
    <button type="button" onClick={() => setSources([...sources, { id: freeId("openrouter"), preset: "openrouter", api_key: "" }])}>Add source</button>
    {snapshot?.health && <div className={`ollama-health ${snapshot.health.connected ? "is-connected" : "is-error"}`}><span className="health-dot" /><span>{snapshot.health.detail}</span></div>}
  </section>;
}

function OpenCodeCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.opencode as OpenCodeConfig | undefined;
  const setField = (field: keyof OpenCodeConfig, value: boolean | string[] | string | null) => {
    updateConfig((current) => ({
      ...current,
      plugins: {
        ...current.plugins,
        opencode: { ...current.plugins.opencode, [field]: value },
      },
    }));
  };
  return <section className="panel provider-card opencode-card">
    <p>OPENCODE SERVERS</p>
    <h2>Sessions</h2>
    <label className="toggle-field opencode-toggle"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> Enable OpenCode provider</label>
    <label>Server endpoints
      <textarea
        rows={3}
        placeholder="One endpoint per line"
        value={(pluginConfig?.endpoints ?? []).join("\n")}
        onChange={(event) => setField("endpoints", event.target.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean))}
      />
    </label>
    <label>Password environment variable
      <input type="text" placeholder="Optional" value={pluginConfig?.password_env ?? ""} onChange={(event) => setField("password_env", event.target.value || null)} />
    </label>
    {snapshot?.health && <div className={`ollama-health ${snapshot.health.connected ? "is-connected" : "is-error"}`}><span className="health-dot" /><span>{snapshot.health.detail}</span></div>}
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>OpenCode sessions available to button zones</span></div>
  </section>;
}

function App() {
  const [activeTab, setActiveTab] = useState<"buttons" | "configuration">("buttons");
  const [config, setConfig] = useState<Config | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, PluginSnapshot>>({});
  const [manifests, setManifests] = useState<PluginManifest[]>([]);
  const [status, setStatus] = useState<BridgeStatus | null>(null);
  const [editingKey, setEditingKey] = useState(0);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const refreshInFlightRef = useRef(false);
  const [notice, setNotice] = useState("Connecting to local Bridge...");
  const [dragSource, setDragSource] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  const refresh = async (replaceLocalConfig = false) => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    try {
      const [nextConfig, nextSnapshots, nextManifests, nextStatus] = await Promise.all([
        api<Config>("config"),
        api<Record<string, PluginSnapshot>>("plugins/snapshots"),
        api<PluginManifest[]>("plugins"),
        api<BridgeStatus>("status?include_plugins=false"),
      ]);
      if (replaceLocalConfig || !dirtyRef.current) setConfig(nextConfig);
      setSnapshots(nextSnapshots);
      setManifests(nextManifests);
      setStatus(nextStatus);
      if (!dirtyRef.current) setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Bridge unavailable");
    } finally {
      refreshInFlightRef.current = false;
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const updateConfig = (change: (current: Config) => Config) => {
    setConfig((current) => (current ? change(current) : current));
    dirtyRef.current = true;
    setDirty(true);
    setNotice("Configuration changed. Save to apply it to the physical Deck.");
  };

  const selectedBinding = config?.buttons.find((button) => button.key_index === editingKey) ?? null;
  const selectedManifest = manifests.find((manifest) => manifest.id === selectedBinding?.plugin) ?? null;
  const sessionsForPlugin = (pluginId: string | null | undefined) => pluginId ? snapshots[pluginId]?.sessions ?? [] : [];
  const totalSessions = Object.values(snapshots).reduce((total, snapshot) => total + (snapshot.sessions?.length ?? 0), 0);
  const selectedProviderSessions = sessionsForPlugin(selectedBinding?.plugin);

  // Build span zone lookup: key_index -> zone parent Button
  const spanZones: Map<number, Button> = new Map();
  for (const button of config?.buttons ?? []) {
    if (button.plugin && button.component === "session") {
      for (let i = button.key_index; i < button.key_index + Math.max(button.span, 1); i++) {
        spanZones.set(i, button);
      }
    }
  }

  const sessionFor = (keyIndex: number) => {
    const parent = spanZones.get(keyIndex);
    if (!parent) return null;
    const sessions = sessionsForPlugin(parent.plugin);
    const runtimePin = status?.pinned_sessions?.[String(keyIndex)];
    if (runtimePin?.plugin === parent.plugin) return runtimePin;
    if (parent.span === 1 && parent.session_id) {
      return sessions.find((s) => s.id === parent.session_id) ?? null;
    }
    const zoneIndices = Array.from(
      { length: Math.max(parent.span, 1) },
      (_, slot) => parent.key_index + slot,
    );
    const pinnedIds = new Set(
      zoneIndices
        .map((index) => status?.pinned_sessions?.[String(index)])
        .filter((pin): pin is Session & { plugin: string } => pin?.plugin === parent.plugin)
        .map((pin) => pin.id),
    );
    const available = sessions.filter((session) => !pinnedIds.has(session.id));
    const unpinnedSlot = zoneIndices
      .slice(0, keyIndex - parent.key_index)
      .filter((index) => status?.pinned_sessions?.[String(index)]?.plugin !== parent.plugin)
      .length;
    return available[unpinnedSlot] ?? null;
  };

  const isZoneParent = (keyIndex: number) => {
    const parent = spanZones.get(keyIndex);
    return parent !== undefined && parent.key_index === keyIndex;
  };

  const updateBinding = (change: Partial<Button>) => {
    updateConfig((current) => ({
      ...current,
      buttons: current.buttons.map((button) =>
        button.key_index === editingKey ? { ...button, ...change } : button,
      ),
    }));
  };

  // When span changes, auto-clear child keys and session_id
  const changeSpan = (newSpan: number) => {
    const span = Math.max(1, Math.min(15 - editingKey, newSpan));
    updateConfig((current) => ({
      ...current,
      buttons: current.buttons.map((button) => {
        if (button.key_index === editingKey) return { ...button, span, session_id: span > 1 ? null : button.session_id };
        if (button.key_index > editingKey && button.key_index < editingKey + span) {
          return { ...button, plugin: null, component: null, action: null, session_id: null, action_params: {}, window_binding: null, span: 1 };
        }
        return button;
      }),
    }));
  };

  // zoneParent(keyIndex): returns the zone parent Button for the given key, or null.
  const zoneParent = (keyIndex: number, buttons: Button[]): Button | null => {
    for (const b of buttons) {
      if (b.plugin && b.component === "session") {
        const span = Math.max(b.span, 1);
        if (keyIndex >= b.key_index && keyIndex < b.key_index + span) return b;
      }
    }
    return null;
  };

  // canDropZone: checks if source zone fits at target without overflow or overlap.
  const canDropZone = (
    sourceParent: Button | null,
    targetKey: number,
    buttons: Button[],
  ): boolean => {
    if (!sourceParent) return true; // single key always fits
    const span = Math.max(sourceParent.span, 1);
    if (targetKey + span > 15) return false; // overflow right edge
    // Check overlap with OTHER zones (not the source itself)
    for (const b of buttons) {
      if (!b.plugin || b.component !== "session") continue;
      if (b.key_index === sourceParent.key_index) continue;
      const bSpan = Math.max(b.span, 1);
      const bEnd = b.key_index + bSpan;
      const srcEnd = targetKey + span;
      if (targetKey < bEnd && srcEnd > b.key_index) return false; // overlap
    }
    return true;
  };

  // emptyButton: returns a cleared (unassigned) button at the given key_index.
  const emptyButton = (keyIndex: number): Button => ({
    key_index: keyIndex,
    plugin: null,
    component: null,
    span: 1,
    session_id: null,
    action: null,
    action_params: {},
    window_binding: null,
  });

  // swapKeys: swaps button configurations between sourceKey and targetKey,
  // handling span zones as atomic units. Returns new buttons array or null if invalid.
  const swapKeys = (sourceKey: number, targetKey: number, buttons: Button[]): Button[] | null => {
    if (sourceKey === targetKey) return null;
    const srcParent = zoneParent(sourceKey, buttons);
    const tgtParent = zoneParent(targetKey, buttons);

    // If either is a span zone, validate the swap fits
    if (srcParent && !canDropZone(srcParent, targetKey, buttons)) return null;
    if (tgtParent && !canDropZone(tgtParent, sourceKey, buttons)) return null;

    // Determine the source and target key ranges
    const srcStart = srcParent ? srcParent.key_index : sourceKey;
    const srcSpan = srcParent ? Math.max(srcParent.span, 1) : 1;
    const srcEnd = srcStart + srcSpan; // exclusive

    const tgtStart = tgtParent ? tgtParent.key_index : targetKey;
    const tgtSpan = tgtParent ? Math.max(tgtParent.span, 1) : 1;
    const tgtEnd = tgtStart + tgtSpan; // exclusive

    // If zones overlap (one contains the other), reject
    if (srcStart < tgtEnd && srcEnd > tgtStart && srcStart !== tgtStart) return null;
    if (srcStart === tgtStart) return null; // same zone

    // Build the new button configs
    const newButtons = buttons.map((b) => ({ ...b }));

    // Helper: get config for a key position (the parent's config or the individual button's)
    const getConfig = (key: number): Button => {
      const parent = zoneParent(key, buttons);
      if (parent && parent.key_index !== key) {
        // child key — config is empty (children are always cleared)
        return emptyButton(key);
      }
      return { ...buttons.find((b) => b.key_index === key)! };
    };

    const srcConfig = getConfig(sourceKey);
    const tgtConfig = getConfig(targetKey);

    // Place source config at target position
    // If source is a span zone parent, keep its span; adjust key_index
    if (srcParent) {
      newButtons[targetKey] = {
        ...srcConfig,
        key_index: targetKey,
        span: srcSpan,
        // span > 1: clear session_id (auto-assign by slot)
        session_id: srcSpan > 1 ? null : srcConfig.session_id,
      };
    } else {
      newButtons[targetKey] = { ...srcConfig, key_index: targetKey, span: 1 };
    }

    // Place target config at source position
    if (tgtParent) {
      newButtons[sourceKey] = {
        ...tgtConfig,
        key_index: sourceKey,
        span: tgtSpan,
        session_id: tgtSpan > 1 ? null : tgtConfig.session_id,
      };
    } else {
      newButtons[sourceKey] = { ...tgtConfig, key_index: sourceKey, span: 1 };
    }

    // Clear child keys in both zones (old source area and old target area)
    // Old source zone children (keys between srcStart+1 and srcEnd)
    for (let i = srcStart + 1; i < srcEnd; i++) {
      if (i !== sourceKey && i !== targetKey) {
        newButtons[i] = emptyButton(i);
      }
    }
    // Old target zone children
    for (let i = tgtStart + 1; i < tgtEnd; i++) {
      if (i !== sourceKey && i !== targetKey) {
        newButtons[i] = emptyButton(i);
      }
    }

    // Clear NEW child keys in moved source zone (around targetKey)
    if (srcParent && srcSpan > 1) {
      for (let i = targetKey + 1; i < targetKey + srcSpan; i++) {
        if (i !== sourceKey) {
          newButtons[i] = emptyButton(i);
        }
      }
    }
    // Clear NEW child keys in moved target zone (around sourceKey)
    if (tgtParent && tgtSpan > 1) {
      for (let i = sourceKey + 1; i < sourceKey + tgtSpan; i++) {
        if (i !== targetKey) {
          newButtons[i] = emptyButton(i);
        }
      }
    }

    // Ensure key_index matches array position for all buttons
    return newButtons.map((b, i) => ({ ...b, key_index: i }));
  };

  const handleDragStart = (keyIndex: number) => {
    setDragSource(keyIndex);
  };

  const handleDragEnd = () => {
    setDragSource(null);
    setDragOver(null);
  };

  const handleDragOver = (event: React.DragEvent, keyIndex: number) => {
    if (dragSource === null || dragSource === keyIndex) return;
    event.preventDefault();
    setDragOver(keyIndex);
  };

  const handleDragLeave = () => {
    setDragOver(null);
  };

  const handleDrop = (keyIndex: number) => {
    if (dragSource === null || dragSource === keyIndex) {
      setDragSource(null);
      setDragOver(null);
      return;
    }
    if (!config) {
      setDragSource(null);
      setDragOver(null);
      return;
    }
    const swapped = swapKeys(dragSource, keyIndex, config.buttons);
    if (swapped) {
      updateConfig((current) => ({ ...current, buttons: swapped }));
      setEditingKey(keyIndex);
      setNotice(`Moved key ${dragSource + 1} to key ${keyIndex + 1}. Save to apply.`);
    } else {
      setNotice("Cannot drop here — span zone would overflow or overlap.");
    }
    setDragSource(null);
    setDragOver(null);
  };

  const changePlugin = (pluginId: string) => {
    if (!pluginId) {
      updateBinding({
        plugin: null,
        component: null,
        span: 1,
        action: null,
        session_id: null,
        action_params: {},
        window_binding: null,
      });
      return;
    }
    const plugin = manifests.find((manifest) => manifest.id === pluginId);
    const component = plugin?.components[0] ?? null;
    updateBinding({
      plugin: pluginId,
      component,
      span: 1,
      // Quota keys only display; they never run a Session action.
      action: component === "quota" ? null : plugin?.actions[0] ?? null,
      session_id: null,
      action_params: {},
      window_binding: null,
    });
  };

  const changeComponent = (component: string) => {
    updateBinding({
      component: component || null,
      action: component === "quota" ? null : selectedManifest?.actions[0] ?? null,
      session_id: null,
      span: 1,
      action_params: {},
    });
  };

  // Merge one quota display option (account, label) into action_params.
  const setQuotaOption = (name: "account" | "label", value: string) => {
    const params = { ...(selectedBinding?.action_params ?? {}) };
    if (value) params[name] = value;
    else delete params[name];
    updateBinding({ action_params: params });
  };

  const save = async () => {
    if (!config || !dirtyRef.current) return;
    try {
      const saved = await api<Config>("config", { method: "PUT", body: JSON.stringify(config) });
      setConfig(saved);
      dirtyRef.current = false;
      setDirty(false);
      setNotice("Saved. The physical Deck updates on its next refresh.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save configuration");
    }
  };

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">DB</span><div><p>LOCAL CONTROL SURFACE</p><h1>Deck Bridge</h1></div></div>
      <div className="topbar-right">
        <span className={`connection ${status?.device.connected ? "online" : "offline"}`}><i /> {status?.device.connected ? "MK.2 connected" : "Deck offline"}</span>
        <button onClick={() => void refresh(true)}>Refresh</button>
        <button className="primary" disabled={!dirty} onClick={() => void save()}>{dirty ? "Save changes" : "Saved"}</button>
      </div>
    </header>
    {notice && <div className="notice">{notice}</div>}
    <nav className="page-tabs" role="tablist" aria-label="Deck Bridge sections">
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === "buttons"}
        className={activeTab === "buttons" ? "is-active" : ""}
        onClick={() => setActiveTab("buttons")}
      >
        <span>Buttons</span>
        <small>Deck layout and actions</small>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === "configuration"}
        className={activeTab === "configuration" ? "is-active" : ""}
        onClick={() => setActiveTab("configuration")}
      >
        <span>Configuration</span>
        <small>Hardware, providers and quota</small>
      </button>
    </nav>
    {activeTab === "buttons" ? <div className="workspace" role="tabpanel" aria-label="Button configuration">
      <section className="deck-panel panel">
        <div className="section-heading">
          <div><p>MK.2 BUTTON LAYOUT</p><h2>Choose a key to edit</h2><span>This browser preview never runs a key action. Only the physical Stream Deck does.</span></div>
          <div className="session-count"><b>{totalSessions}</b><span>provider sessions</span></div>
        </div>
        <div className="deck-frame"><div className="deck-grid">
          {Array.from({ length: 15 }, (_, index) => {
            const button = config?.buttons.find((item) => item.key_index === index);
            const session = sessionFor(index);
            const parent = spanZones.get(index);
            const pluginId = parent?.plugin ?? button?.plugin;
            const snapshot = pluginId ? snapshots[pluginId] : undefined;
            const isChild = parent !== undefined && parent.key_index !== index;
            const isParent = parent !== undefined && parent.key_index === index;
            const isQuota = button?.component === "quota";
            const quota = isQuota ? quotaPreview(button, snapshot) : undefined;
            const label = session?.title ?? (quota ? quota.title || `${displayName(pluginId)} quota` : isChild ? "Session slot" : displayName(button?.component ?? null));
            const sublabel = session ? `${displayName(pluginId)} · ${session.project ?? "No project"}` : isChild ? `${displayName(pluginId)} · slot ${index - (parent?.key_index ?? 0) + 1}` : pluginId ? displayName(pluginId) : "Unassigned";
            const quotaDisplay = config?.stream_deck.quota_display ?? "remaining";
            const state = session?.state ?? (quota ? quota.problem ?? `${quota.amount ?? quotaShown(quota.used ?? 0, quotaDisplay).text}${quota.stale ? " (last seen)" : ""}` : snapshot && !snapshot.health.connected ? snapshot.health.detail : button?.action ? displayName(button.action) : "No action");
            const stateColor = !session && quota && !quota.problem ? quota.stale ? STALE_COLOR : pctColor(quota.used) : undefined;
            const isPinned = status?.pinned_sessions?.[String(index)]?.plugin === pluginId;
            return <button
              key={index}
              type="button"
              draggable
              title={`Edit key ${index + 1}: ${label}${isPinned ? " (pinned)" : ""} — drag to rearrange`}
              className={`deck-key ${session ? "session-key" : "utility-key"} provider-${pluginId ?? "none"} ${editingKey === index ? "is-editing" : ""} ${isPinned ? "is-pinned" : ""} ${isParent && parent && parent.span > 1 ? "is-zone-parent" : ""} ${isChild ? "is-zone-child" : ""} ${dragSource === index ? "is-dragging" : ""} ${dragOver === index && dragSource !== null && dragSource !== index ? "is-drop-target" : ""}`}
              aria-pressed={editingKey === index}
              onClick={() => setEditingKey(index)}
              onDragStart={() => handleDragStart(index)}
              onDragEnd={handleDragEnd}
              onDragOver={(event) => handleDragOver(event, index)}
              onDragLeave={handleDragLeave}
              onDrop={() => handleDrop(index)}
            ><span className="key-index">{index + 1}</span><strong>{label}</strong><small>{sublabel}</small><em className={session ? "state-chip" : undefined} style={session ? sessionStateChip(session.state) : stateColor ? { color: stateColor } : undefined}>{state}</em><span className="drag-handle" aria-hidden="true">⠿</span></button>;
          })}
        </div></div>
        <div className="deck-legend"><span><i className="legend-dot editing-dot" /> Editing key</span><span><i className="legend-dot pinned-dot" /> Pinned Session</span><span><i className="legend-dot session-dot" /> Live Session display</span><span><i className="legend-dot" /> Other / unassigned</span></div>
      </section>

      <aside className="detail-column">
        <section className="panel key-editor">
          <p>KEY CONFIGURATION</p>
          <h2>Key {editingKey + 1}</h2>
          <span className="editor-hint">Changes stay in this page until you choose Save. Clicking this preview never opens OpenChamber, Codex, or Claude Code.</span>
          <label>Plugin
            <select value={selectedBinding?.plugin ?? ""} onChange={(event) => changePlugin(event.target.value)}>
              <option value="">No plugin (unassigned)</option>
              {manifests.map((plugin) => <option key={plugin.id} value={plugin.id}>{plugin.name}</option>)}
            </select>
          </label>
          <label>Display component
            <select disabled={!selectedManifest} value={selectedBinding?.component ?? ""} onChange={(event) => changeComponent(event.target.value)}>
              {!selectedManifest && <option value="">Choose a plugin first</option>}
              {selectedManifest?.components.map((component) => <option key={component} value={component}>{displayName(component)}</option>)}
            </select>
          </label>
          {selectedBinding?.component === "session" && (
            <label>Number of keys (span)
              <input type="number" min={1} max={15 - editingKey} value={selectedBinding?.span ?? 1} onChange={(event) => changeSpan(Number(event.target.value))} />
            </label>
          )}
          {selectedBinding?.component === "quota" ? (() => {
            const quotaSnapshot = selectedBinding.plugin ? snapshots[selectedBinding.plugin] : undefined;
            const accounts = quotaSnapshot?.accounts ?? [];
            const savedAccount = typeof selectedBinding.action_params.account === "string" ? selectedBinding.action_params.account : "";
            const known = findAccount(quotaSnapshot, savedAccount);
            return <>
              {(accounts.length > 0 || savedAccount) && <label>Account
                <select value={known?.id ?? savedAccount} onChange={(event) => setQuotaOption("account", event.target.value)}>
                  <option value="">Choose an account</option>
                  {accounts.map((account) => <option key={account.id} value={account.id}>{account.title} · {account.id}{account.signals?.stale ? " (last seen)" : ""}</option>)}
                  {savedAccount && !known && <option value={savedAccount}>{savedAccount} (not found right now)</option>}
                </select>
              </label>}
              <label>Label
                <input type="text" maxLength={16} placeholder={known?.title ?? `${displayName(selectedBinding.plugin)} quota`} value={typeof selectedBinding.action_params.label === "string" ? selectedBinding.action_params.label : ""} onChange={(event) => setQuotaOption("label", event.target.value)} />
              </label>
              <p className="editor-hint">Quota keys only display usage; pressing them does nothing.</p>
            </>;
          })() : <label>Physical-key action
            <select disabled={!selectedManifest} value={selectedBinding?.action ?? ""} onChange={(event) => updateBinding({ action: event.target.value || null, action_params: {} })}>
              <option value="">No action</option>
              {selectedManifest?.actions.map((action) => <option key={action} value={action}>{displayName(action)}</option>)}
            </select>
          </label>}
          <div className="binding-explainer">
            {selectedBinding?.plugin === "opencode" && selectedBinding.component === "session" ? <>
              <b>Session zone (span {selectedBinding.span})</b><span>This key and {(selectedBinding.span ?? 1) - 1} key{(selectedBinding.span ?? 1) > 2 ? "s" : ""} to its right show live Sessions. Each physical press focuses that slot's Session in OpenChamber.</span>
              <small>{selectedBinding.action === "select_session" ? "Physical press: focus this slot's Session in OpenChamber." : "Physical press: no Session focus action configured."}</small>
            </> : selectedBinding?.plugin === "codex" && selectedBinding.component === "session" ? <>
              <b>Codex chat zone (span {selectedBinding.span})</b><span>This zone follows Codex's most recently active local chats, using live turn status and Codex-specific key styling.</span>
              <small>{selectedBinding.action === "select_session" ? "Physical press: open this exact chat in the ChatGPT desktop app." : "Physical press: no Codex chat action configured."}</small>
            </> : selectedBinding?.plugin === "claude_code" && selectedBinding.component === "session" ? <>
              <b>Claude Code Session zone (span {selectedBinding.span})</b><span>This zone follows open and recent Claude Code Sessions; open ones come first.</span>
              <small>{selectedBinding.action === "select_session" ? "Physical press: resume this exact Session in a new console." : "Physical press: no Claude Code Session action configured."}</small>
            </> : selectedBinding?.plugin ? <><b>{displayName(selectedBinding.component)}</b><span>This key renders the selected Plugin component when that Plugin supplies it.</span><small>Physical action: {displayName(selectedBinding.action)}</small></> : <><b>Unassigned key</b><span>Choose a Plugin and Component to define what this key displays.</span><small>No physical action is configured.</small></>}
          </div>
          {selectedBinding?.component === "session" && <p className="pin-hint">Short-press the physical key to open its Session. Hold it for 2 seconds to pin that Session to this slot; hold it again to unpin. An orange frame marks a pinned slot.</p>}
          {selectedBinding?.plugin && selectedBinding.component === "session" && isZoneParent(editingKey) && (selectedBinding.span ?? 1) === 1 && (() => {
            const assigned = selectedBinding.session_id ? selectedProviderSessions.find((session) => session.id === selectedBinding.session_id) : null;
            return <div className="session-picker">
              <div className="session-picker-head">
                <p>SESSION ASSIGNMENT</p>
                {selectedBinding.session_id && <button type="button" className="ghost-btn" onClick={() => updateBinding({ session_id: null })}>{assigned ? "Clear session" : "Session not found — clear"}</button>}
              </div>
              {selectedBinding.session_id && !assigned && <p className="session-not-found">Session not found. It may have closed. Clear the assignment to pick a new one.</p>}
              <div className="session-list">
                {selectedProviderSessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    className={`session-item ${selectedBinding.session_id === session.id ? "is-selected" : ""}`}
                    onClick={() => updateBinding({ session_id: session.id })}
                  >
                    <span className="session-state-dot" style={{ background: sessionStateColor(session.state) }} />
                    <span className="session-item-main">
                      <strong>{session.title || "Untitled session"}</strong>
                      <small>{session.project ?? "No project"}</small>
                    </span>
                    <em className="state-chip" style={sessionStateChip(session.state)}>{session.state}</em>
                  </button>
                ))}
                {selectedProviderSessions.length === 0 && <p className="session-list-empty">No live {displayName(selectedBinding.plugin)} sessions available.</p>}
              </div>
            </div>;
          })()}
        </section>
      </aside>
    </div> : <section className="configuration-page" role="tabpanel" aria-label="Bridge configuration">
      <div className="configuration-heading">
        <p>BRIDGE CONFIGURATION</p>
        <h2>Hardware and providers</h2>
        <span>Connection settings and usage data live here, separate from the physical button layout.</span>
      </div>
      <div className="configuration-grid">
        <section className="panel device-card configuration-card">
          <p>PHYSICAL DECK</p>
          <h2>Stream Deck MK.2</h2>
          <div><b>{status?.device.connected ? "Ready" : "Waiting"}</b><span>{status?.device.detail || "Checking hardware"}</span></div>
          <label>Brightness<input type="range" min="0" max="100" value={config?.stream_deck.brightness ?? 40} onChange={(event) => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, brightness: Number(event.target.value) } }))} /></label>
          <label>Quota keys show
            <select value={config?.stream_deck.quota_display ?? "remaining"} onChange={(event) => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, quota_display: event.target.value as QuotaDisplay } }))}>
              <option value="remaining">What is left (full green bar = plenty)</option>
              <option value="used">What is used</option>
            </select>
          </label>
        </section>
        <OpenCodeCard config={config} snapshot={snapshots.opencode} updateConfig={updateConfig} />
        <CodexCard config={config} snapshot={snapshots.codex} updateConfig={updateConfig} />
        <ClaudeCodeCard config={config} snapshot={snapshots.claude_code} updateConfig={updateConfig} />
        <OllamaQuotaCard config={config} snapshot={snapshots.ollama_quota} updateConfig={updateConfig} />
        <HttpQuotaCard config={config} snapshot={snapshots.http_quota} updateConfig={updateConfig} />
      </div>
    </section>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
