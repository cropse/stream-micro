import { useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { getLang, isZh, locale, setLang, t, tDetail, type Lang } from "./i18n";

type Button = {
  key_index: number;
  plugin: string | null;
  component: string | null;
  span: number;
  session_id: string | null;
  action: string | null;
  action_params: Record<string, unknown>;
  window_binding: Record<string, string> | null;
  accent?: string | null;
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
type ClaudeCodeConfig = { enabled?: boolean; claude_path?: string | null; claude_home?: string | null; plugin_dir?: string | null; max_sessions?: number};
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
  stream_deck: { brightness: number; device_serial: string | null; quota_display?: QuotaDisplay; state_colors?: Record<string, string>; session_accent_line?: boolean };
  plugins: Record<string, PluginConfig>;
  buttons: Button[];
};
type Session = { id: string; title: string; state: string; project: string | null; directory?: string | null };
type SetupState = "missing" | "other" | "wrong_port" | "partial" | "ours";
type ClaudeSetup = {
  settings_path: string;
  error?: string;
  hooks?: { state: SetupState; events: string[] };
  statusline?: { state: SetupState; chained: boolean };
  hook_command?: string;
  statusline_command?: string;
  last_hook_at?: number | null;
  last_statusline_at?: number | null;
  port: number;
  backup?: string | null;
};
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
    ...options,
    headers: { "content-type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error((await response.json().catch(() => ({}))).detail || "Bridge unavailable");
  }
  return response.json();
}

function displayName(value: string | null | undefined) {
  return value ? t(value.replace(/_/g, " ").replace(/\b\w/g, (letter: string) => letter.toUpperCase())) : t("Not configured");
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

// Session state words: the same as on the keys (RUN, SUB, WAIT, ...).
function stateText(state: string) {
  return STATE_WORDS[state] ?? state.toUpperCase();
}

// Dark text on a light colour, light text on a dark one (as on the keys).
function textOn(color: string) {
  const [red, green, blue] = [1, 3, 5].map((at) => parseInt(color.slice(at, at + 2), 16));
  if ([red, green, blue].some((value) => Number.isNaN(value))) return "#08131f";
  return 0.299 * red + 0.587 * green + 0.114 * blue > 140 ? "#08131f" : "#f1f5f9";
}

function sessionStateChip(state: string, custom?: Record<string, string>) {
  const color = custom?.[state];
  if (color) return { background: color, color: textOn(color) };
  return { background: sessionStateColor(state), color: DARK_SESSION_STATES.has(state) ? "#dcfce7" : "#08131f" };
}

// The accent each provider draws on its keys (control_renderer._PROVIDER_STYLES).
const PROVIDER_ACCENTS: Record<string, string> = {
  claude_code: "#e28b67",
  codex: "#48e0b4",
  opencode: "#8aa7ff",
  ollama_quota: "#b69cff",
  http_quota: "#f2b880",
};
const COLOR_SWATCHES = ["#4a9eff", "#8aa7ff", "#b794ff", "#48e0b4", "#4de57a", "#166534", "#ffc15a", "#f2b880", "#e28b67", "#ff7983", "#ffffff", "#8292aa"];

function ColorPicker({ value, isDefault, onPick, onReset, label }: {
  value: string;
  isDefault: boolean;
  onPick: (color: string) => void;
  onReset: () => void;
  label: string;
}) {
  const current = value.toLowerCase();
  return <div className="color-picker" role="group" aria-label={label}>
    {COLOR_SWATCHES.map((color) => <button key={color} type="button" className={`swatch${current === color ? " is-on" : ""}`} style={{ background: color }} aria-label={color} aria-pressed={current === color} onClick={() => onPick(color)} />)}
    <label className="swatch swatch-custom" title={t("Custom colour")}>
      <input type="color" value={value} aria-label={t("Custom colour")} onChange={(event) => onPick(event.target.value)} />
    </label>
    {!isDefault && <button type="button" className="link-button" onClick={onReset}>{t("Reset to default colour")}</button>}
  </div>;
}

// Each provider's key background (control_renderer._PROVIDER_STYLES).
const PROVIDER_BACKGROUNDS: Record<string, string> = {
  claude_code: "#251811",
  codex: "#0b1f1c",
  opencode: "#142038",
  ollama_quota: "#181a2b",
  http_quota: "#14181f",
};

const SESSION_STATE_ORDER = ["running", "background", "waiting", "completed", "idle", "error", "disconnected"];
const STATE_WORDS: Record<string, string> = { idle: "IDLE", running: "RUN", background: "SUB", waiting: "WAIT", completed: "DONE", error: "ERR", disconnected: "OFF" };

function formatReset(value: string | number | boolean | null | undefined) {
  if (!value || typeof value !== "string") return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  const when = parsed.toLocaleString(locale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const minutes = Math.round((parsed.valueOf() - Date.now()) / 60000);
  if (minutes <= 0 || minutes >= 24 * 60) return when;
  const hours = Math.floor(minutes / 60);
  return hours ? t("{when} (in {h} h {m} min)", { when, h: hours, m: minutes % 60 }) : t("{when} (in {m} min)", { when, m: minutes });
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
  return { value, text: t(display === "used" ? "{value}% used" : "{value}% left", { value: Math.round(value) }) };
}

const STALE_COLOR = "#6f8099";

// The keys' short window names (control_renderer._SHORT_WINDOW_LABELS).
const SHORT_WINDOW_LABELS: Record<string, string> = { WEEK: "7D", WEEKLY: "7D", WK: "7D", MONTH: "MO", MONTHLY: "MO" };
function shortLabel(label: string) {
  return SHORT_WINDOW_LABELS[label.toUpperCase()] ?? label;
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
  const primaryLabel = generic ? shortLabel(String(signals.primary_label ?? t("Primary"))) : t("Session");
  const secondaryLabel = generic ? shortLabel(String(signals.secondary_label ?? t("Secondary"))) : t("Weekly");
  const rows = [
    { key: "primary", label: primaryLabel, used: primary, reset: primaryReset },
    { key: "secondary", label: secondaryLabel, used: secondary, reset: secondaryReset },
  ].filter((row): row is typeof row & { used: number } => typeof row.used === "number");
  return <div className={`ollama-usage-grid${stale ? " is-stale" : ""}`}>
    {stale && <small className="stale-note">{typeof signals.seen_at === "string" ? t("Last seen {time} — the source is not reachable now.", { time: formatReset(signals.seen_at) }) : t("Last seen — the source is not reachable now.")}</small>}
    {rows.length === 0 && <small className="usage-empty">{t("No quota numbers yet.")}</small>}
    {rows.map((row) => <div key={row.key} className="usage-bar">
      <div className="usage-bar-label"><span>{row.label}</span><b style={{ color: tint(row.used) }}>{moneyText(signals[`${row.key}_amount_left`], signals[`${row.key}_currency`]) ?? quotaShown(row.used, display).text}</b></div>
      <div className="usage-bar-track"><div className="usage-bar-fill" style={{ width: `${quotaShown(row.used, display).value}%`, background: tint(row.used) }} /></div>
      {typeof row.reset === "string" && row.reset ? <small>{t("Resets:")} {formatReset(row.reset)}</small> : null}
    </div>)}
  </div>;
}

// What the user can do about a provider problem, when we know.
const FIX_HINTS: [RegExp, string][] = [
  [/No cookie value configured/, "Paste the cookie above, then Save."],
  [/cookie is expired or invalid/, "Sign in at ollama.com again and paste a new cookie (see How to get the cookie)."],
  [/Claude Code was not found/, "Install Claude Code, or set its executable under Advanced settings."],
  [/^key rejected$/, "Paste the key again; it may have been revoked."],
  [/^needs a key$/, "Paste the API key above, then Save."],
  [/no quota numbers at the configured paths/, "Check the paths under Advanced settings against the service's reply."],
  [/window \d+ needs used_percent/, "Open that source under Configuration and fill in where its number is."],
  [/needs one or two windows/, "Open that source under Configuration and add a row with where its number is."],
];

function fixHint(detail: string | null | undefined) {
  const found = detail ? FIX_HINTS.find(([pattern]) => pattern.test(detail)) : undefined;
  return found ? t(found[1]) : null;
}

function Health({ health }: { health?: PluginHealth }) {
  if (!health) return null;
  const hint = health.connected ? null : fixHint(health.detail);
  return <div className={`ollama-health ${health.connected ? "is-connected" : "is-error"}`}>
    <span className="health-dot" />
    <span>{tDetail(health.detail)}{hint && <small className="fix-hint">{hint}</small>}</span>
  </div>;
}

// Disabled providers fold down to their title and switch.
function cardClass(base: string, enabled: boolean | undefined) {
  return `${base}${enabled ? "" : " is-off"}`;
}

type Billing = "monthly" | "payg";

function BillingBadge({ billing }: { billing: Billing }) {
  return <span className={`billing-badge billing-${billing}`}>{billing === "monthly" ? t("Monthly plan") : t("Pay as you go")}</span>;
}

// Heading of a quota card: what it is, how it is billed, and how to take it away.
// Turning a service off or removing it goes through its row in the list,
// which asks first and offers Undo until Save.
function QuotaCardHead({ kicker, title, billing }: {
  kicker: string;
  title: string;
  billing: Billing;
}) {
  return <div className="quota-card-head">
    <div><p>{kicker}</p><h2>{title}</h2></div>
    <div className="quota-card-tools">
      <BillingBadge billing={billing} />
    </div>
  </div>;
}

// Quota of a subscription whose Agent card lives under "Agent chats" (Claude, Codex).
function PlanQuotaCard({ kicker, title, snapshot, display, note }: {
  kicker: string;
  title: string;
  snapshot?: PluginSnapshot;
  display: QuotaDisplay;
  note: string;
}) {
  return <section className="panel provider-card quota-card">
    <QuotaCardHead kicker={kicker} title={title} billing="monthly" />
    {snapshot?.signals ? <UsageBars signals={snapshot.signals} generic display={display} /> : <small className="usage-empty">{t("No quota numbers yet.")}</small>}
    <p className="ollama-hint">{note}</p>
  </section>;
}

function OllamaQuotaCard({ config, snapshot, updateConfig }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
}) {
  const pluginConfig = config?.plugins?.ollama_quota as OllamaQuotaConfig | undefined;
  const hasKeySource = ((config?.plugins?.http_quota as HttpQuotaConfig | undefined)?.sources ?? []).some((source) => source.preset === "ollama");
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

  return <section className="panel ollama-card quota-card">
    <QuotaCardHead kicker={t("OLLAMA · SESSION COOKIE")} title="Ollama Cloud" billing="monthly" />
    {hasKeySource && <p className="fix-note">{t("An Ollama source with an API key is set up below; this cookie card can be turned off.")} <button type="button" className="link-button" onClick={() => setField("enabled", false)}>{t("Turn off")}</button></p>}
    <div className="ollama-cookie-field">
      <label>{t("Session cookie value")}
        <input
          type="password"
          placeholder={pluginConfig?.cookie_configured ? t("Cookie configured — enter a new value to replace it") : t("Paste ollama.com __Secure-session cookie value")}
          value={pluginConfig?.cookie_value ?? ""}
          onChange={(event) => setField("cookie_value", event.target.value)}
        />
      </label>
      <p className="ollama-hint">{pluginConfig?.cookie_configured ? t("The saved cookie stays in the local Bridge and is never returned to this page.") : isZh() ? <>登入 ollama.com，打開開發者工具 → Application → Cookies，複製 <code>__Secure-session</code> 的值。</> : <>Sign in at ollama.com, open DevTools → Application → Cookies, copy the <code>__Secure-session</code> value.</>}</p>
      <details className="ollama-advanced">
        <summary>{t("How to get the cookie")}</summary>
        {isZh() ? <>
        <ol className="cookie-steps">
          <li>用 Chrome 或 Edge 登入 <code>ollama.com</code>（要顯示額度的那個帳號）。</li>
          <li>按 <code>F12</code> 打開開發者工具，選上方的 <b>Application</b> 分頁（Firefox 是 <b>Storage</b>）。</li>
          <li>左邊 <b>Cookies</b> 底下點 <code>https://ollama.com</code>。</li>
          <li>找到 <code>__Secure-session</code>，複製它的 <b>Value</b>，貼到上面再按「儲存變更」。</li>
          <li>同一列的 <b>Expires / Max-Age</b> 就是它能用到什麼時候。在 ollama.com 登出會讓它提早失效；失效時這張卡片會提示你，再貼一次新的就好。</li>
        </ol>
        </> : <>
        <ol className="cookie-steps">
          <li>Sign in at <code>ollama.com</code> in Chrome or Edge with the account to show.</li>
          <li>Press <code>F12</code> to open DevTools and choose the <b>Application</b> tab (in Firefox: <b>Storage</b>).</li>
          <li>Under <b>Cookies</b>, select <code>https://ollama.com</code>.</li>
          <li>Find <code>__Secure-session</code> and copy its <b>Value</b>; paste it above and Save.</li>
          <li>The <b>Expires / Max-Age</b> column there shows until when it works. Signing out of ollama.com ends it early; when it stops working this card says so, and you paste a new one.</li>
        </ol>
        </>}
      </details>
    </div>
    <details className="ollama-advanced">
      <summary>{t("Advanced settings")}</summary>
      <label>{t("Settings URL")}
        <input type="text" value={pluginConfig?.settings_url ?? "https://ollama.com/settings"} onChange={(event) => setField("settings_url", event.target.value)} />
      </label>
      <label>{t("Cookie name")}
        <input type="text" value={pluginConfig?.cookie_name ?? "__Secure-session"} onChange={(event) => setField("cookie_name", event.target.value)} />
      </label>
      <label>{t("Request timeout (seconds)")}
        <input type="number" min={1} value={pluginConfig?.request_timeout_seconds ?? 15} onChange={(event) => setField("request_timeout_seconds", Number(event.target.value))} />
      </label>
      <label>{t("Refresh interval (seconds)")}
        <input type="number" min={1} value={pluginConfig?.refresh_interval_seconds ?? 60} onChange={(event) => setField("refresh_interval_seconds", Number(event.target.value))} />
      </label>
    </details>
    <Health health={health} />
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
  return <section className={cardClass("panel ollama-card codex-card", pluginConfig?.enabled)}>
    <p className="card-keep">{t("CODEX APP SERVER")}</p>
    <h2 className="card-keep">{t("Chats")}</h2>
    <label className="card-keep toggle-field"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> {t("Enable Codex provider")}</label>
    {!pluginConfig?.enabled && <p className="card-keep off-note">{t("Off. Turn it on to set it up; changes apply after Save.")}</p>}
    <details className="ollama-advanced">
      <summary>{t("Advanced settings")}</summary>
      <label>{t("Codex executable")}
        <input type="text" placeholder={t("codex (use PATH)")} value={pluginConfig?.codex_path ?? ""} onChange={(event) => setField("codex_path", event.target.value || null)} />
      </label>
      <label>CODEX_HOME
        <input type="text" placeholder={t("Use current Codex home")} value={pluginConfig?.codex_home ?? ""} onChange={(event) => setField("codex_home", event.target.value || null)} />
      </label>
    </details>
    <Health health={snapshot?.health} />
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>{t("recent Codex chats available to session keys")}</span></div>
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
  return <section className={cardClass("panel provider-card claude-code-card", pluginConfig?.enabled)}>
    <p className="card-keep">{t("CLAUDE CODE")}</p>
    <h2 className="card-keep">{t("Sessions")}</h2>
    <label className="card-keep toggle-field claude-code-toggle"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> {t("Enable Claude Code provider")}</label>
    {!pluginConfig?.enabled && <p className="card-keep off-note">{t("Off. Turn it on to set it up; changes apply after Save.")}</p>}
    <details className="ollama-advanced">
      <summary>{t("Advanced settings")}</summary>
      <label>{t("Claude executable")}
        <input type="text" placeholder={t("claude (use PATH)")} value={pluginConfig?.claude_path ?? ""} onChange={(event) => setField("claude_path", event.target.value || null)} />
      </label>
      <label>{t("Claude data folder")}
        <input type="text" placeholder={t("Use current ~/.claude folder")} value={pluginConfig?.claude_home ?? ""} onChange={(event) => setField("claude_home", event.target.value || null)} />
      </label>
      <label>{t("Companion plugin folder")}
        <input type="text" placeholder={t("Optional path to the bundled integration")} value={pluginConfig?.plugin_dir ?? ""} onChange={(event) => setField("plugin_dir", event.target.value || null)} />
      </label>
      <label>{t("Recent Sessions")}
        <input type="number" min={1} max={15} value={pluginConfig?.max_sessions ?? 15} onChange={(event) => setField("max_sessions", Number(event.target.value))} />
      </label>
    </details>
    <Health health={snapshot?.health} />
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>{t("recent Claude Code Sessions available to button zones")}</span></div>
    <p className="provider-note">{t("Session state comes from the running Claude process and its transcript, so it works without setup; the companion hook (installed above) adds an exact WAIT when Claude needs your approval. SUB means the turn ended but subagents or other agents still work for it. Quota appears after Claude Code's status line receives its first API response.")}</p>
  </section>;
}

// Known usage APIs; the Bridge fills in URL and fields for these (http_quota PRESETS).
// `base` names a new source; `billing` picks its section on the Configuration page.
const USAGE_PRESETS: { id: string; name: string; hint: string; base: string; billing: Billing }[] = [
  { id: "deepseek", name: "DeepSeek balance", hint: "A DeepSeek API key (platform.deepseek.com → API keys). Reading the balance costs nothing.", base: "deepseek", billing: "payg" },
  { id: "moonshot", name: "Moonshot balance (international)", hint: "An API key from platform.moonshot.ai (USD account). Reading the balance costs nothing.", base: "moonshot", billing: "payg" },
  { id: "moonshot_cn", name: "Moonshot balance (China)", hint: "An API key from platform.moonshot.cn (CNY account). Reading the balance costs nothing.", base: "moonshot-cn", billing: "payg" },
  { id: "siliconflow", name: "SiliconFlow balance (China)", hint: "An API key from cloud.siliconflow.cn (CNY account). Reading the balance costs nothing.", base: "siliconflow", billing: "payg" },
  { id: "siliconflow_intl", name: "SiliconFlow balance (international)", hint: "An API key from cloud.siliconflow.com (USD account). Reading the balance costs nothing.", base: "siliconflow-intl", billing: "payg" },
  { id: "novita", name: "Novita balance", hint: "An API key from novita.ai (Key Management). Reading the balance costs nothing.", base: "novita", billing: "payg" },
  { id: "ollama", name: "Ollama Cloud credits", hint: "An ollama.com API key (Settings → Keys). Reading the balance uses no credits; no cookie needed.", base: "ollama", billing: "monthly" },
  { id: "openrouter", name: "OpenRouter key limit", hint: "Any OpenRouter API key that has a spending limit.", base: "openrouter", billing: "payg" },
  { id: "openrouter_credits", name: "OpenRouter credits", hint: "Account credits; OpenRouter may require a management key.", base: "openrouter-credits", billing: "payg" },
  { id: "custom", name: "Custom JSON API", hint: "Any HTTPS endpoint that returns usage as JSON.", base: "custom", billing: "payg" },
];

const AGENT_PLUGINS = new Set(["opencode", "codex", "claude_code"]);

function usagePreset(id: string | null | undefined) {
  return USAGE_PRESETS.find((item) => item.id === (id || "custom")) ?? USAGE_PRESETS[USAGE_PRESETS.length - 1];
}
const PATH_LABELS: Record<keyof UsageWindow, string> = {
  label: "Label",
  used_percent: "Used % path",
  remaining_percent: "Remaining % path",
  used: "Used amount path",
  remaining: "Remaining amount path",
  limit: "Limit path",
  reset_at: "Reset time path",
  fraction: "Fraction",
};
const WINDOW_MODES: { id: keyof UsageWindow; name: string; fields: (keyof UsageWindow)[] }[] = [
  { id: "used_percent", name: "Used %", fields: ["used_percent"] },
  { id: "remaining_percent", name: "Remaining %", fields: ["remaining_percent"] },
  { id: "used", name: "Used / limit", fields: ["used", "limit"] },
  { id: "remaining", name: "Remaining / limit", fields: ["remaining", "limit"] },
];

// A row the Bridge accepts: a percentage path, or an amount path with a limit.
function rowHasNumber(window: UsageWindow) {
  const filled = (value: unknown) => value !== undefined && value !== null && String(value).trim() !== "";
  return filled(window.used_percent) || filled(window.remaining_percent) || (filled(window.limit) && (filled(window.used) || filled(window.remaining)));
}

function windowMode(window: UsageWindow): keyof UsageWindow {
  if (window.remaining_percent !== undefined) return "remaining_percent";
  if (window.used !== undefined) return "used";
  if (window.remaining !== undefined) return "remaining";
  return "used_percent";
}

// One card per usage-API source, for the sources billed as `billing`.
function UsageSourceCards({ config, snapshot, updateConfig, billing, only, savedIds }: {
  config: Config | null;
  snapshot?: PluginSnapshot;
  updateConfig: (change: (current: Config) => Config) => void;
  billing?: Billing;
  only?: string;
  savedIds?: Set<string>;
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
  return <>
    {sources.map((source, index) => {
      const preset = usagePreset(source.preset);
      if (only !== undefined ? source.id !== only : preset.billing !== billing) return null;
      const custom = preset.id === "custom";
      const windows = source.windows ?? [];
      const account = snapshot?.accounts?.find((item) => item.id === source.id);
      const advanced = <>
        <label>URL
          <input type="text" placeholder={custom ? "https://api.example.com/usage" : t("Preset default")} value={source.url ?? ""} onChange={(event) => setSource(index, { url: event.target.value || null })} />
        </label>
        <p className="ollama-hint">{t("Never put a key in the URL. Pointing a source at another host asks for its key again.")}</p>
        <div className="usage-row">
          <label>{t("Key header")}
            <input type="text" placeholder="Authorization" value={source.auth_header ?? ""} onChange={(event) => setSource(index, { auth_header: event.target.value || null })} />
          </label>
          <label>{t("Key format")}
            <select value={source.auth_scheme === "" ? "bare" : source.auth_scheme ? "bearer" : "default"} onChange={(event) => setSource(index, { auth_scheme: event.target.value === "bare" ? "" : event.target.value === "bearer" ? "Bearer" : null })}>
              <option value="default">{custom ? t("Bearer <key> (default)") : t("Preset default")}</option>
              <option value="bearer">Bearer &lt;key&gt;</option>
              <option value="bare">{t("<key> only")}</option>
            </select>
          </label>
        </div>
        <label>{t("Key from environment variable")}
          <input type="text" placeholder={t("Optional, e.g. OPENROUTER_API_KEY")} value={source.api_key_env ?? ""} onChange={(event) => setSource(index, { api_key_env: event.target.value || null })} />
        </label>
        <p className="ollama-hint">{isZh() ? <>{custom ? "數字在 JSON 回應裡的位置" : "新增的列會取代範本的列"}：路徑寫法例如 <code>data.limit</code>、<code>items.0.used</code> 或 <code>limits[type=TOKENS,unit=3].percentage</code>。重置時間可以是 ISO 文字，或 epoch 秒／毫秒。</> : <>{custom ? "Where the numbers are in the JSON reply" : "Rows replace the preset's rows when added"}: paths like <code>data.limit</code>, <code>items.0.used</code> or <code>limits[type=TOKENS,unit=3].percentage</code>. Reset times may be ISO text or epoch seconds/milliseconds.</>}</p>
        {[0, 1].map((at) => {
          const window = windows[at];
          if (!window) {
            return at === windows.length ? <button key={at} type="button" className="link-button" onClick={() => setWindow(index, at, { label: at === 0 ? "5H" : "WEEK", used_percent: "" })}>{at === 0 ? t("Add top row") : t("Add bottom row")}</button> : null;
          }
          const mode = windowMode(window);
          const fields = WINDOW_MODES.find((item) => item.id === mode)?.fields ?? ["used_percent"];
          return <fieldset key={at} className="usage-window">
            <legend>{at === 0 ? t("Top row") : t("Bottom row")} · <button type="button" className="link-button" onClick={() => setWindows(index, windows.filter((_, other) => other !== at))}>{t("Remove")}</button></legend>
            <div className="usage-row">
              <label>{t("Label")}
                <input type="text" maxLength={8} placeholder="5H" value={window.label ?? ""} onChange={(event) => setWindow(index, at, { label: event.target.value })} />
              </label>
              <label>{t("Numbers")}
                <select value={mode} onChange={(event) => setWindowMode(index, at, event.target.value as keyof UsageWindow)}>
                  {WINDOW_MODES.map((item) => <option key={item.id} value={item.id}>{t(item.name)}</option>)}
                </select>
              </label>
            </div>
            {fields.map((field) => <label key={field}>{t(PATH_LABELS[field])}
              <input type="text" value={String(window[field] ?? "")} onChange={(event) => setWindow(index, at, { [field]: field === "limit" && /^\d+(\.\d+)?$/.test(event.target.value.trim()) ? Number(event.target.value) : event.target.value })} />
            </label>)}
            {(mode === "used_percent" || mode === "remaining_percent") && <label className="toggle-field"><input type="checkbox" checked={window.fraction ?? false} onChange={(event) => setWindow(index, at, { fraction: event.target.checked })} /> {t("Value is a fraction (0–1)")}</label>}
            <label>{t("Reset time path")}
              <input type="text" placeholder={t("Optional")} value={window.reset_at ?? ""} onChange={(event) => setWindow(index, at, { reset_at: event.target.value })} />
            </label>
          </fieldset>;
        })}
      </>;
      return <section key={index} className="panel provider-card usage-source quota-card">
        <QuotaCardHead kicker={t(preset.name).toUpperCase()} title={source.label || source.id || t("New source")} billing={preset.billing} />
        {account && <small className={account.error ? "source-status is-error" : "source-status"}>{account.error ? `${tDetail(account.error)}${fixHint(account.error) ? ` — ${fixHint(account.error)}` : ""}` : account.signals?.stale ? t("Last seen, not current") : t("Current")}</small>}
        {account?.signals && <UsageBars signals={account.signals} generic display={display} />}
        <p className="ollama-hint source-label-field">{t("Then choose this source as the Account of a Usage API quota key in Buttons.")}</p>
        <label className="source-label-field">{t("Label")}
          <input type="text" maxLength={16} placeholder={t("Shown on the key")} value={source.label ?? ""} onChange={(event) => setSource(index, { label: event.target.value || null })} />
        </label>
        <label>{t("API key")}
          <input
            type="password"
            placeholder={source.api_key_configured ? t("Key saved — enter a new value to replace it") : t("Paste the API key")}
            value={source.api_key ?? ""}
            onChange={(event) => setSource(index, { api_key: event.target.value })}
          />
        </label>
        <p className="ollama-hint">{t(preset.hint)} {source.api_key_configured && <button type="button" className="link-button" onClick={() => setSource(index, { api_key: "", api_key_configured: false })}>{t("Forget saved key")}</button>}</p>
        {custom && !windows.some(rowHasNumber) && <p className="fix-note">{t("Fill in where at least one number is in the reply (a row below); the Bridge cannot save this source without it.")}</p>}
        {custom ? advanced : <details className="ollama-advanced"><summary>{t("Advanced settings")}</summary>{advanced}</details>}
        <div className="usage-row usage-footer">
          <label>{t("Source ID (the key's Account)")}
            <input type="text" maxLength={32} value={source.id} readOnly={source.api_key_configured === true || savedIds?.has(source.id) === true} title={source.api_key_configured === true || savedIds?.has(source.id) ? t("Saved sources keep their ID; change the Label instead") : undefined} onChange={(event) => setSource(index, { id: event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} />
          </label>
        </div>
      </section>;
    })}
  </>;
}

// -- Claude Code setup check ------------------------------------------------------
const HOOK_EVENT_NAMES = ["SessionStart", "UserPromptSubmit", "PermissionRequest", "Notification", "Stop", "StopFailure", "SessionEnd"];

function sinceText(at: number | null | undefined) {
  if (!at) return "";
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - at) / 60));
  if (minutes < 1) return t("just now");
  if (minutes < 60) return t("{n} min ago", { n: minutes });
  return t("{n} h ago", { n: Math.round(minutes / 60) });
}

// Parts someone chose not to install (kept in this browser): no more reminders.
const SETUP_SKIP_KEY = "stream-micro.claude-setup-skipped";
function readSkipped(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(SETUP_SKIP_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}
function writeSkipped(parts: string[]) {
  try { localStorage.setItem(SETUP_SKIP_KEY, JSON.stringify(parts)); } catch { /* private window */ }
}

// Something to fix: a part neither installed (even if not reporting yet) nor declined.
function setupNeedsWork(setup: ClaudeSetup | null, skipped: string[]) {
  if (!setup) return false;
  if (setup.error) return true;
  return (["statusline", "hooks"] as const).some((part) => !skipped.includes(part) && (setup[part]?.state ?? "missing") !== "ours");
}

function ClaudeSetupCheck({ setup, onChange, skipped, onSkip }: {
  setup: ClaudeSetup | null;
  onChange: (body: { action: "install" | "remove"; parts: string[]; replace_statusline?: boolean; chain_statusline?: boolean }) => Promise<string | null>;
  skipped: string[];
  onSkip: (part: string, skip: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  if (!setup) return null;
  if (setup.error) return <div className="setup-check"><p className="fix-note">{tDetail(setup.error)}</p></div>;
  const run = async (body: Parameters<typeof onChange>[0]) => {
    setBusy(true);
    setConfirmRemove(null);
    const error = await onChange(body);
    setBusy(false);
    setMessage(error ? { ok: false, text: error } : { ok: true, text: body.action === "install" ? t("Written to {path} (the old file is backed up). Start a new Claude Code session for it to take effect.", { path: setup.settings_path }) : t("Removed from {path}.", { path: setup.settings_path }) });
  };
  const line = (part: "statusline" | "hooks") => {
    const state = (part === "statusline" ? setup.statusline?.state : setup.hooks?.state) ?? "missing";
    const last = part === "statusline" ? setup.last_statusline_at : setup.last_hook_at;
    const title = part === "statusline" ? t("Quota report (status line)") : t("Approval reminder WAIT (hook)");
    const purpose = part === "statusline" ? t("Lets Claude's 5-hour and weekly quota show on the Deck.") : t("Lets Session keys show WAIT while Claude waits for your approval.");
    let icon = "⬜";
    let text = t("Not installed");
    let actions: React.ReactNode = <button type="button" className="primary small" disabled={busy} onClick={() => void run({ action: "install", parts: [part] })}>{t("Install")}</button>;
    if (state === "other") {
      icon = "⚠️";
      text = t("Claude Code already uses another status line.");
      actions = <>
        <button type="button" className="primary small" disabled={busy} onClick={() => void run({ action: "install", parts: [part], chain_statusline: true })}>{t("Keep it and add quota (recommended)")}</button>
        <button type="button" className="small" disabled={busy} onClick={() => void run({ action: "install", parts: [part], replace_statusline: true })}>{t("Replace it")}</button>
      </>;
    } else if (state === "wrong_port") {
      icon = "⛔";
      text = t("Set to another port; this Bridge listens on {port}.", { port: setup.port });
      actions = <button type="button" className="primary small" disabled={busy} onClick={() => void run({ action: "install", parts: [part] })}>{t("Fix it")}</button>;
    } else if (state === "partial") {
      icon = "⚠️";
      text = t("Only part of it is installed.");
      actions = <button type="button" className="primary small" disabled={busy} onClick={() => void run({ action: "install", parts: [part] })}>{t("Fix it")}</button>;
    } else if (state === "ours") {
      icon = last ? "✅" : "⏳";
      text = last ? t("Working, last report {when}", { when: sinceText(last) }) : t("Installed. Waiting for the first report: start a new Claude Code session.");
      if (part === "statusline" && setup.statusline?.chained) text += ` ${t("(after your own status line)")}`;
      actions = null;
    }
    const installed = state === "ours" || state === "wrong_port" || state === "partial";
    if (!installed && skipped.includes(part)) {
      return <li className="setup-line setup-skipped">
        <span className="setup-icon" aria-hidden="true">➖</span>
        <div className="setup-text"><b>{title}</b><span>{t("Not wanted; no reminders.")}</span><small>{purpose}</small></div>
        <div className="setup-actions"><button type="button" className="link-button" onClick={() => onSkip(part, false)}>{t("I want it after all")}</button></div>
      </li>;
    }
    return <li className={`setup-line setup-${state}${state === "ours" && last ? " is-ok" : ""}`}>
      <span className="setup-icon" aria-hidden="true">{icon}</span>
      <div className="setup-text"><b>{title}</b><span>{text}</span><small>{purpose}</small></div>
      <div className="setup-actions">
        {actions}
        {!installed && <button type="button" className="link-button" onClick={() => onSkip(part, true)}>{t("I don't need this")}</button>}
        {installed && (confirmRemove === part
          ? <span className="confirm-line">{t("Remove it from Claude Code's settings?")}<button type="button" className="danger small" disabled={busy} onClick={() => void run({ action: "remove", parts: [part] })}>{t("Remove")}</button><button type="button" className="link-button" onClick={() => setConfirmRemove(null)}>{t("Cancel")}</button></span>
          : <button type="button" className="link-button" onClick={() => setConfirmRemove(part)}>{t("Remove")}</button>)}
      </div>
    </li>;
  };
  const snippet = JSON.stringify({
    statusLine: { type: "command", command: setup.statusline_command },
    hooks: Object.fromEntries(HOOK_EVENT_NAMES.map((event) => [event, [{ hooks: [{ type: "command", command: setup.hook_command, timeout: 3 }] }]])),
  }, null, 2);
  return <div className="setup-check">
    <div className="setup-head"><b>{t("Claude Code setup")}</b><small>{t("Both run small scripts from this Bridge while Claude Code works; they only report to this computer.")}</small></div>
    <ul className="setup-lines">{line("statusline")}{line("hooks")}</ul>
    {message && <p className={message.ok ? "setup-message" : "setup-message is-error"} role="status">{message.text}</p>}
    <details className="ollama-advanced">
      <summary>{t("Do it yourself")}</summary>
      <p className="ollama-hint">{t("Add these to {path} (merge with what is there), then start a new Claude Code session.", { path: setup.settings_path })}</p>
      <pre className="setup-snippet">{snippet}</pre>
      <button type="button" className="small" onClick={() => void navigator.clipboard?.writeText(snippet)}>{t("Copy")}</button>
    </details>
  </div>;
}

// -- Connected services --------------------------------------------------------
// The Configuration page lists only the services in use; "+ Add service" offers
// the rest. A service is a plugin, or one usage-API source.
type ServiceGroup = "agent" | "monthly" | "payg";
type ServiceStatus = "ok" | "stale" | "error" | "pending" | "loading" | "off";
type ServiceItem = {
  key: string;
  group: ServiceGroup;
  name: string;
  sub?: string;
  billing?: Billing;
  status: ServiceStatus;
  summary: string;
  problem?: string;
  dirty: boolean;
  leaving: boolean;
  warn?: string;
  // Added in this session and never saved: removing it simply drops it.
  fresh: boolean;
  boundKeys: number;
  detail: ReactNode;
  turnOff?: () => void;
  undo?: () => void;
  removeLabel: string;
};
type ServiceChoice = { id: string; group: ServiceGroup; name: string; needs: string; billing?: Billing; connected: boolean; pick: () => string };

const PLUGIN_SERVICES: { id: string; group: ServiceGroup; name: string; billing?: Billing; needs: string }[] = [
  { id: "claude_code", group: "agent", name: "Claude Code", needs: "Nothing: reads Claude Code on this computer." },
  { id: "codex", group: "agent", name: "Codex", needs: "Nothing: uses the codex installed on this computer." },
  { id: "opencode", group: "agent", name: "OpenCode", needs: "A running OpenCode server." },
  { id: "ollama_quota", group: "monthly", name: "Ollama Cloud (session cookie)", billing: "monthly", needs: "A cookie copied from the browser. The API key choice is easier." },
];
const SERVICE_GROUPS: [ServiceGroup, string][] = [["agent", "Agent chats"], ["monthly", "Monthly plans"], ["payg", "Pay as you go and custom"]];

function servicePanelId(key: string) {
  return `svc-${key.replace(/[^a-z0-9_-]/gi, "-")}`;
}

// The most used window decides the summary: what is closest to running out.
const CURRENCY_SIGNS: Record<string, string> = { CNY: "¥", USD: "$", EUR: "€", JPY: "¥" };

function moneyText(amount: unknown, currency: unknown) {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return null;
  const code = typeof currency === "string" ? currency : "";
  const text = Math.abs(amount).toFixed(2);
  // A debt reads -$1.23; anything that rounds to zero is just $0.00.
  const minus = amount < 0 && Number(text) !== 0 ? "-" : "";
  return `${minus}${CURRENCY_SIGNS[code] ?? (code ? `${code} ` : "")}${text}`;
}

function moneyLeft(signals: QuotaSignals | null | undefined) {
  const text = moneyText(signals?.primary_amount_left, signals?.primary_currency);
  return text ? t("{amount} left", { amount: text }) : "";
}

function quotaSummary(signals: QuotaSignals | null | undefined, display: QuotaDisplay) {
  if (!signals) return "";
  const used = ["primary_percent", "secondary_percent", "session_percent", "weekly_percent"]
    .map((name) => signals[name])
    .filter((value): value is number => typeof value === "number");
  return used.length ? quotaShown(Math.max(...used), display).text : "";
}

function ServiceRow({ item, open, onToggle }: { item: ServiceItem; open: boolean; onToggle: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const id = servicePanelId(item.key);
  const statusText = { ok: t("Working"), stale: t("Last seen, not current"), error: t("Needs attention"), pending: t("Starts after Save"), loading: t("Reading…"), off: t("Turned off after Save") }[item.status];
  const moveFocus = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const heads = [...document.querySelectorAll<HTMLButtonElement>(".service-row-head")];
    const at = heads.indexOf(event.currentTarget);
    const target = { ArrowDown: heads[at + 1], ArrowUp: heads[at - 1], Home: heads[0], End: heads[heads.length - 1] }[event.key];
    if (target) { event.preventDefault(); target.focus(); }
  };
  // Esc closes the row, but not while typing in one of its fields.
  const closeOnEscape = (event: React.KeyboardEvent<HTMLLIElement>) => {
    if (event.key !== "Escape" || !open) return;
    if ((event.target as HTMLElement).closest("input, textarea, select")) return;
    onToggle();
    (event.currentTarget.querySelector(".service-row-head") as HTMLElement | null)?.focus();
  };
  return <li className={`service-row status-${item.status}${open ? " is-open" : ""}${item.leaving ? " is-leaving" : ""}`} onKeyDown={closeOnEscape}>
    <button type="button" className="service-row-head" aria-expanded={open} aria-controls={open ? id : undefined} onClick={onToggle} onKeyDown={moveFocus}>
      <span className="service-dot" aria-hidden="true" />
      <span className="service-name"><b>{item.name}</b>{item.sub && <small>{item.sub}</small>}<span className="sr-only">{statusText}</span></span>
      <span className="service-tags">
        {item.billing && <BillingBadge billing={item.billing} />}
        {item.warn && !item.leaving && <span className="tag tag-warn">{item.warn}</span>}
        {item.leaving ? <span className="tag tag-leaving">{t("Turned off after Save")}</span> : item.dirty && <span className="tag tag-unsaved">{t("Unsaved")}</span>}
      </span>
      <span className="service-summary">{item.problem ? <em>{item.problem}</em> : item.summary || (item.status === "pending" ? t("Starts after Save") : item.status === "loading" ? t("Reading…") : "")}</span>
      <span className="service-chevron" aria-hidden="true">›</span>
    </button>
    {open && <div id={id} role="region" aria-label={item.name} className="service-detail">
      {item.leaving ? <p className="fix-note">{t("This service goes away when you Save.")} {item.undo && <button type="button" className="link-button" onClick={item.undo}>{t("Undo")}</button>}</p> : <>
        {item.detail}
        <div className="service-danger">
          {confirming ? <span className="confirm-line">
            {item.boundKeys > 0 ? t("{n} key(s) use it and will show no data.", { n: item.boundKeys }) + " " : ""}{item.fresh ? t("It was never saved, so what you entered is dropped.") : t("It goes away when you Save; until then you can undo.")}
            <button type="button" className="danger" onClick={() => { setConfirming(false); item.turnOff?.(); }}>{item.removeLabel}</button>
            <button type="button" className="link-button" onClick={() => setConfirming(false)}>{t("Cancel")}</button>
          </span> : <button type="button" className="link-button danger-link" onClick={() => setConfirming(true)}>{item.removeLabel}</button>}
        </div>
      </>}
    </div>}
  </li>;
}

function AddServiceDialog({ choices, onPick, onClose }: { choices: ServiceChoice[]; onPick: (choice: ServiceChoice) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>(".add-quota-item:not([disabled])")?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !ref.current) return;
      // Keep Tab inside the dialog.
      const items = [...ref.current.querySelectorAll<HTMLElement>("button:not([disabled])")];
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !ref.current.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  return <div className="dialog-backdrop" onClick={onClose}>
    <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="add-service-title" ref={ref} onClick={(event) => event.stopPropagation()}>
      <div className="dialog-head">
        <h2 id="add-service-title">{t("Add a service")}</h2>
        <button type="button" className="link-button" aria-label={t("Close")} onClick={onClose}>✕</button>
      </div>
      {SERVICE_GROUPS.map(([group, label]) => {
        const items = choices.filter((choice) => choice.group === group);
        if (!items.length) return null;
        return <section key={group} className="dialog-group">
          <h3>{t(label)}</h3>
          <div className="add-quota-list">
            {items.map((choice) => <button key={choice.id} type="button" className="add-quota-item" disabled={choice.connected} onClick={() => onPick(choice)}>
              <span><b>{t(choice.name)}</b>{choice.connected ? <span className="tag tag-connected">{t("Connected")}</span> : choice.billing && <BillingBadge billing={choice.billing} />}</span>
              <small>{t(choice.needs)}</small>
            </button>)}
          </div>
        </section>;
      })}
    </div>
  </div>;
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
  return <section className={cardClass("panel provider-card opencode-card", pluginConfig?.enabled)}>
    <p className="card-keep">{t("OPENCODE SERVERS")}</p>
    <h2 className="card-keep">{t("Sessions")}</h2>
    <label className="card-keep toggle-field opencode-toggle"><input type="checkbox" checked={pluginConfig?.enabled ?? false} onChange={(event) => setField("enabled", event.target.checked)} /> {t("Enable OpenCode provider")}</label>
    {!pluginConfig?.enabled && <p className="card-keep off-note">{t("Off. Turn it on to set it up; changes apply after Save.")}</p>}
    <label>{t("Server endpoints")}
      <textarea
        rows={3}
        placeholder={t("One endpoint per line")}
        value={(pluginConfig?.endpoints ?? []).join("\n")}
        onChange={(event) => setField("endpoints", event.target.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean))}
      />
    </label>
    <label>{t("Password environment variable")}
      <input type="text" placeholder={t("Optional")} value={pluginConfig?.password_env ?? ""} onChange={(event) => setField("password_env", event.target.value || null)} />
    </label>
    <Health health={snapshot?.health} />
    <div className="codex-session-summary"><b>{snapshot?.sessions?.length ?? 0}</b><span>{t("OpenCode sessions available to button zones")}</span></div>
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
  // The configuration as last saved, to mark unsaved services.
  const [savedConfig, setSavedConfig] = useState<Config | null>(null);
  const [claudeSetup, setClaudeSetup] = useState<ClaudeSetup | null>(null);
  const [setupSkipped, setSetupSkipped] = useState<string[]>(readSkipped);
  const skipSetup = (part: string, skip: boolean) => setSetupSkipped((current) => {
    const next = skip ? [...new Set([...current, part])] : current.filter((item) => item !== part);
    writeSkipped(next);
    return next;
  });
  const [openServices, setOpenServices] = useState<Set<string>>(new Set());
  const [focusService, setFocusService] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const refreshInFlightRef = useRef(false);
  const [notice, setNotice] = useState(t("Connecting to local Bridge..."));
  const [dragSource, setDragSource] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [lang, setLangState] = useState<Lang>(getLang());
  const switchLang = () => {
    const next: Lang = lang === "en" ? "zh-TW" : "en";
    setLang(next);
    setLangState(next);
  };
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);

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
      if (replaceLocalConfig || !dirtyRef.current) {
        setConfig(nextConfig);
        setSavedConfig(nextConfig);
      }
      setSnapshots(nextSnapshots);
      setManifests(nextManifests);
      api<ClaudeSetup>("claude-code/setup").then(setClaudeSetup, () => setClaudeSetup(null));
      setStatus(nextStatus);
      if (!dirtyRef.current) setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : t("Bridge unavailable"));
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
  };

  const selectedBinding = config?.buttons.find((button) => button.key_index === editingKey) ?? null;
  const quotaDisplay = config?.stream_deck.quota_display ?? "remaining";
  const stateColors = config?.stream_deck.state_colors ?? {};
  const sessionLine = config?.stream_deck.session_accent_line ?? true;
  const setDeckOption = (name: "session_accent_line", value: boolean) => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, [name]: value } }));
  const [editingState, setEditingState] = useState<string | null>(null);
  const resetStateColors = () => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, state_colors: {} } }));
  const setStateColor = (state: string, color: string | null) => updateConfig((current) => {
    const next = { ...(current.stream_deck.state_colors ?? {}) };
    if (color) next[state] = color; else delete next[state];
    return { ...current, stream_deck: { ...current.stream_deck, state_colors: next } };
  });
  const pluginOn = (id: string) => Boolean((config?.plugins?.[id] as { enabled?: boolean } | undefined)?.enabled);
  const turnOn = (id: string) => updateConfig((current) => ({ ...current, plugins: { ...current.plugins, [id]: { ...current.plugins[id], enabled: true } } }));
  const addUsageSource = (presetId: string) => {
    // Never reuse a saved id: its key would follow the new source.
    const taken = new Set([...sourcesIn(config), ...sourcesIn(savedConfig)].map((source) => source.id));
    const base = usagePreset(presetId).base;
    let id = base;
    for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
    const source: UsageSource = presetId === "custom" ? { id, preset: "custom", api_key: "", windows: [{ label: "5H", used_percent: "" }] } : { id, preset: presetId, api_key: "" };
    updateConfig((current) => {
      const http = (current.plugins.http_quota ?? {}) as HttpQuotaConfig;
      return { ...current, plugins: { ...current.plugins, http_quota: { ...http, enabled: true, sources: [...(http.sources ?? []), source] } } };
    });
    return id;
  };
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  // Writes Claude Code's settings: our own page only (the Bridge checks).
  const changeClaudeSetup = async (body: { action: "install" | "remove"; parts: string[]; replace_statusline?: boolean; chain_statusline?: boolean }) => {
    try {
      const next = await api<ClaudeSetup>("claude-code/setup", { method: "POST", headers: { "content-type": "application/json", "x-stream-micro": "1" }, body: JSON.stringify(body) });
      setClaudeSetup(next);
      return null;
    } catch (error) {
      return error instanceof Error ? tDetail(error.message) : t("Could not change Claude Code's settings");
    }
  };
  const discard = () => {
    setConfirmDiscard(false);
    dirtyRef.current = false;
    setDirty(false);
    setNotice("");
    void refresh(true);
  };
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  // A service just added opens, scrolls into view and focuses its first field.
  useEffect(() => {
    if (!focusService) return;
    const panel = document.getElementById(servicePanelId(focusService));
    if (!panel) return;
    panel.scrollIntoView({ block: "center", behavior: "smooth" });
    const field = panel.querySelector<HTMLElement>('input[type="password"], input:not([type="checkbox"]):not([readonly]):not([type="range"]), textarea');
    (field ?? panel.closest(".service-row")?.querySelector<HTMLElement>(".service-row-head"))?.focus();
    setFocusService(null);
  }, [focusService, config, openServices]);
  const toggleService = (key: string) => setOpenServices((current) => current.has(key) ? new Set() : new Set([key]));
  const pluginOnIn = (from: Config | null, id: string) => Boolean((from?.plugins?.[id] as { enabled?: boolean } | undefined)?.enabled);
  const sourcesIn = (from: Config | null) => ((from?.plugins?.http_quota as HttpQuotaConfig | undefined)?.sources ?? []);
  const keysUsing = (pluginId: string, account?: string) => (config?.buttons ?? []).filter((button) => button.plugin === pluginId && (account === undefined || button.action_params.account === account)).length;
  const turnOff = (id: string) => updateConfig((current) => ({ ...current, plugins: { ...current.plugins, [id]: { ...current.plugins[id], enabled: false } } }));
  const setSourcesTo = (next: UsageSource[]) => updateConfig((current) => ({ ...current, plugins: { ...current.plugins, http_quota: { ...current.plugins.http_quota, sources: next } } }));
  const services: ServiceItem[] = [];
  // Every service this Dashboard knows, started or not: the Bridge lists only
  // providers that are running, and a new one starts after Save.
  for (const spec of PLUGIN_SERVICES) {
    const on = pluginOnIn(config, spec.id);
    const wasOn = pluginOnIn(savedConfig, spec.id);
    if (!on && !wasOn) continue;
    const snapshot = snapshots[spec.id];
    const health = snapshot?.health;
    const signals = snapshot?.signals;
    const sessions = snapshot?.sessions?.length ?? 0;
    const parts: string[] = [];
    if (spec.group === "agent") parts.push(t("{n} chats", { n: sessions }));
    const quota = quotaSummary(signals, quotaDisplay);
    if (quota) parts.push(quota);
    if (signals?.stale && typeof signals.seen_at === "string") parts.push(t("last read {time}", { time: formatReset(signals.seen_at) }));
    const pending = on && !wasOn;
    const status: ServiceStatus = !on ? "off" : pending ? "pending" : health && !health.connected && !signals?.stale ? "error" : signals?.stale ? "stale" : "ok";
    const detail = {
      claude_code: <><ClaudeSetupCheck setup={claudeSetup} onChange={changeClaudeSetup} skipped={setupSkipped} onSkip={skipSetup} /><PlanQuotaCard kicker="" title="" snapshot={snapshot} display={quotaDisplay} note={t("Claude plan: 5-hour and weekly limits.")} /><ClaudeCodeCard config={config} snapshot={snapshot} updateConfig={updateConfig} /></>,
      codex: <><PlanQuotaCard kicker="" title="" snapshot={snapshot} display={quotaDisplay} note={t("ChatGPT plan of the account signed in to the local codex.")} /><CodexCard config={config} snapshot={snapshot} updateConfig={updateConfig} /></>,
      opencode: <OpenCodeCard config={config} snapshot={snapshot} updateConfig={updateConfig} />,
      ollama_quota: <OllamaQuotaCard config={config} snapshot={snapshot} updateConfig={updateConfig} />,
    }[spec.id];
    services.push({
      key: `plugin:${spec.id}`,
      group: spec.group,
      name: t(spec.name),
      billing: spec.billing,
      status,
      summary: parts.join(" · "),
      warn: spec.id === "claude_code" && on && setupNeedsWork(claudeSetup, setupSkipped) ? t("Setup incomplete") : undefined,
      problem: status === "error" ? tDetail(health?.detail) : undefined,
      dirty: JSON.stringify(config?.plugins?.[spec.id]) !== JSON.stringify(savedConfig?.plugins?.[spec.id]),
      leaving: !on,
      fresh: pending,
      boundKeys: keysUsing(spec.id),
      detail,
      turnOff: () => turnOff(spec.id),
      undo: () => turnOn(spec.id),
      removeLabel: t("Turn off"),
    });
  }
  const currentSources = sourcesIn(config);
  // Sources of a provider that was off are not "connected": no rows for them.
  const savedSources = pluginOnIn(savedConfig, "http_quota") ? sourcesIn(savedConfig) : [];
  const httpOn = pluginOnIn(config, "http_quota");
  // Rows of current sources are keyed by position, so editing an id keeps the
  // row open; removed saved sources follow, keyed by their saved id.
  const rows: { key: string; current?: UsageSource; saved?: UsageSource }[] = [
    ...(httpOn ? currentSources : []).map((source, index) => ({ key: `source:#${index}`, current: source, saved: savedSources.find((item) => item.id === source.id) })),
    ...savedSources.filter((source) => !(httpOn ? currentSources : []).some((item) => item.id === source.id)).map((source) => ({ key: `source:saved:${source.id}`, saved: source })),
  ];
  for (const { key, current, saved } of rows) {
    const source = current ?? saved;
    if (!source) continue;
    const sourceId = source.id;
    const preset = usagePreset(source.preset);
    const account = snapshots.http_quota?.accounts?.find((item) => item.id === sourceId);
    const pending = Boolean(current) && !saved;
    const status: ServiceStatus = !current ? "off" : pending ? "pending" : !account ? "loading" : account.error ? "error" : account.signals?.stale ? "stale" : "ok";
    services.push({
      key,
      group: preset.billing,
      name: t(preset.name),
      sub: source.label || source.id,
      billing: preset.billing,
      status,
      summary: [moneyLeft(account?.signals) || quotaSummary(account?.signals, quotaDisplay), account?.signals?.stale && typeof account.signals.seen_at === "string" ? t("last read {time}", { time: formatReset(account.signals.seen_at) }) : ""].filter(Boolean).join(" · "),
      problem: status === "error" && account?.error ? tDetail(account.error) : undefined,
      dirty: JSON.stringify(current) !== JSON.stringify(saved),
      leaving: !current,
      fresh: pending,
      boundKeys: keysUsing("http_quota", sourceId),
      detail: <UsageSourceCards config={config} snapshot={snapshots.http_quota} updateConfig={updateConfig} only={sourceId} savedIds={new Set(sourcesIn(savedConfig).map((item) => item.id))} />,
      turnOff: () => { setSourcesTo(currentSources.filter((item) => item !== current)); setOpenServices(new Set()); },
      undo: saved ? () => updateConfig((cfg) => ({ ...cfg, plugins: { ...cfg.plugins, http_quota: { ...cfg.plugins.http_quota, enabled: true, sources: [...sourcesIn(cfg), saved] } } })) : undefined,
      removeLabel: t("Remove"),
    });
  }
  const serviceChoices: ServiceChoice[] = [
    ...PLUGIN_SERVICES.map((spec) => ({
      id: spec.id, group: spec.group, name: spec.name, needs: spec.needs, billing: spec.billing,
      connected: pluginOnIn(config, spec.id),
      pick: () => { turnOn(spec.id); return `plugin:${spec.id}`; },
    })),
    ...USAGE_PRESETS.map((preset) => ({
      id: `http:${preset.id}`, group: preset.billing as ServiceGroup, name: preset.name, billing: preset.billing, connected: false,
      needs: (preset.id === "custom" ? t("A URL that returns usage as JSON, a key, and where the numbers are.") : t("Paste an API key."))
        + (sourcesIn(config).filter((source) => (source.preset || "custom") === preset.id).length ? " " + t("You already have {n}; this adds another account.", { n: sourcesIn(config).filter((source) => (source.preset || "custom") === preset.id).length }) : ""),
      pick: () => { addUsageSource(preset.id); return `source:#${currentSources.length}`; },
    })),
  ];
  const pickService = (choice: ServiceChoice) => {
    const key = choice.pick();
    setAdding(false);
    setOpenServices(new Set([key]));
    setFocusService(key);
  };
  // Provider names as each Plugin gives them ("Usage API quota", not "Http Quota").
  const pluginName = (id: string | null | undefined) => {
    const name = manifests.find((manifest) => manifest.id === id)?.name;
    return name ? t(name) : displayName(id);
  };
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
      setNotice(t("Moved key {from} to key {to}. Save to apply.", { from: dragSource + 1, to: keyIndex + 1 }));
    } else {
      setNotice(t("Cannot drop here — span zone would overflow or overlap."));
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
      setSavedConfig(saved);
      dirtyRef.current = false;
      setDirty(false);
      setNotice(t("Saved. The physical Deck updates on its next refresh."));
    } catch (error) {
      const reason = error instanceof Error ? error.message : "";
      const hint = fixHint(reason);
      setNotice(reason ? `${t("Not saved:")} ${tDetail(reason)}${hint ? ` ${hint}` : ""}` : t("Could not save configuration"));
    }
  };

  return <main className={`app-shell${dirty ? " has-unsaved" : ""}`}>
    <header className="topbar">
      <div className="brand"><span className="brand-mark">DB</span><div><p>{t("LOCAL CONTROL SURFACE")}</p><h1>Deck Bridge</h1></div></div>
      <div className="topbar-right">
        <span className={`connection ${status?.device.connected ? "online" : "offline"}`}><i /> {status?.device.connected ? t("MK.2 connected") : t("Deck offline")}</span>
        <button type="button" className="lang-switch" title={t("Switch language")} aria-label={t("Switch language")} onClick={switchLang}>{lang === "en" ? "中文" : "EN"}</button>
        <button onClick={() => void refresh(true)}>{t("Refresh")}</button>
        <button className="primary" disabled={!dirty} onClick={() => void save()}>{dirty ? t("Save changes") : t("Saved")}</button>
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
        <span>{t("Buttons")}</span>
        <small>{t("Deck layout and actions")}</small>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === "configuration"}
        className={activeTab === "configuration" ? "is-active" : ""}
        onClick={() => setActiveTab("configuration")}
      >
        <span>{t("Configuration")}</span>
        <small>{t("Hardware, providers and quota")}</small>
      </button>
    </nav>
    {activeTab === "buttons" ? <div className="workspace" role="tabpanel" aria-label="Button configuration">
      <section className="deck-panel panel">
        <div className="section-heading">
          <div><p>{t("MK.2 BUTTON LAYOUT")}</p><h2>{t("Choose a key to edit")}</h2><span>{t("This browser preview never runs a key action. Only the physical Stream Deck does.")}</span></div>
          <div className="session-count"><b>{totalSessions}</b><span>{t("provider sessions")}</span></div>
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
            const provider = pluginName(pluginId);
            const label = session?.title ?? (quota ? quota.title || provider : isChild ? t("Session slot") : displayName(button?.component ?? null));
            const sublabel = session ? `${provider} · ${session.project ?? t("No project")}` : isChild ? `${provider} · ${t("slot {n}", { n: index - (parent?.key_index ?? 0) + 1 })}` : quota && !quota.title ? t("Quota key") : pluginId ? provider : t("Unassigned");
            const quotaDisplay = config?.stream_deck.quota_display ?? "remaining";
            const state = session ? stateText(session.state) : (quota ? (quota.problem ? tDetail(quota.problem) : `${quota.amount ?? quotaShown(quota.used ?? 0, quotaDisplay).text}${quota.stale ? ` ${t("(last seen)")}` : ""}`) : snapshot && !snapshot.health.connected ? tDetail(snapshot.health.detail) : button?.action ? displayName(button.action) : t("No action"));
            const stateColor = !session && quota && !quota.problem ? quota.stale ? STALE_COLOR : pctColor(quota.used) : undefined;
            const isPinned = status?.pinned_sessions?.[String(index)]?.plugin === pluginId;
            const styleId = pluginId ?? "";
            const accent = (parent ?? button)?.accent || PROVIDER_ACCENTS[styleId] || undefined;
            const base = pluginId && (session || isQuota || isChild) ? PROVIDER_BACKGROUNDS[styleId] : undefined;
            const tileStyle = {
              ...(base ? { background: base } : {}),
              // Quota and other keys always keep the line; Session keys may drop it.
              ...(accent && (!session || sessionLine) ? { boxShadow: `inset 0 3px ${accent}` } : { boxShadow: "none" }),
            };
            return <button
              style={tileStyle}
              key={index}
              type="button"
              draggable
              title={`${t("Edit key {n}: {label}", { n: index + 1, label })}${isPinned ? ` ${t("(pinned)")}` : ""} — ${t("drag to rearrange")}`}
              className={`deck-key ${session ? "session-key" : "utility-key"} provider-${pluginId ?? "none"} ${editingKey === index ? "is-editing" : ""} ${isPinned ? "is-pinned" : ""} ${isParent && parent && parent.span > 1 ? "is-zone-parent" : ""} ${isChild ? "is-zone-child" : ""} ${dragSource === index ? "is-dragging" : ""} ${dragOver === index && dragSource !== null && dragSource !== index ? "is-drop-target" : ""}`}
              aria-pressed={editingKey === index}
              onClick={() => setEditingKey(index)}
              onDragStart={() => handleDragStart(index)}
              onDragEnd={handleDragEnd}
              onDragOver={(event) => handleDragOver(event, index)}
              onDragLeave={handleDragLeave}
              onDrop={() => handleDrop(index)}
            >{session && <span className="state-band" style={{ ...sessionStateChip(session.state, stateColors), top: sessionLine ? 3 : 0 }}>{STATE_WORDS[session.state] ?? session.state.toUpperCase()}</span>}<span className="key-index">{index + 1}</span><strong style={quota && accent ? { color: accent } : undefined}>{label}</strong><small>{sublabel}</small>{!session && <em style={stateColor ? { color: stateColor } : undefined}>{state}</em>}<span className="drag-handle" aria-hidden="true">⠿</span></button>;
          })}
        </div></div>
        <div className="state-palette">
          <div className="state-palette-row">
            <span className="state-palette-title">{t("Session state colours")}</span>
            {SESSION_STATE_ORDER.map((state) => <button key={state} type="button" className={`state-band-sample${editingState === state ? " is-editing" : ""}`} style={sessionStateChip(state, stateColors)} aria-expanded={editingState === state} onClick={() => setEditingState(editingState === state ? null : state)}>{STATE_WORDS[state]}</button>)}
            {Object.keys(stateColors).length > 0 && <button type="button" className="link-button" onClick={resetStateColors}>{t("Reset all to default colours")}</button>}
          </div>
          {editingState ? <ColorPicker label={STATE_WORDS[editingState]} value={stateColors[editingState] ?? sessionStateColor(editingState)} isDefault={!stateColors[editingState]} onPick={(color) => setStateColor(editingState, color)} onReset={() => setStateColor(editingState, null)} />
            : <small className="state-palette-hint">{t("Click a state to change its colour. The preview changes at once; the Deck after Save.")}</small>}
          <div className="state-palette-row">
            <label className="toggle-field compact-toggle"><input type="checkbox" checked={sessionLine} onChange={(event) => setDeckOption("session_accent_line", event.target.checked)} /> {t("Top line on Session keys")}</label>
            <small className="state-palette-hint">{t("Quota keys always keep their top line.")}</small>
          </div>
          <div className="deck-legend"><span><i className="legend-dot editing-dot" /> {t("Editing key")}</span><span><i className="legend-dot pinned-dot" /> {t("Pinned Session")}</span></div>
        </div>
      </section>

      <aside className="detail-column">
        <section className="panel key-editor">
          <p>{t("KEY CONFIGURATION")}</p>
          <h2>{t("Key {n}", { n: editingKey + 1 })}</h2>
          <span className="editor-hint">{t("Changes stay in this page until you choose Save. Clicking this preview never opens OpenChamber, Codex, or Claude Code.")}</span>
          <label>{t("Plugin")}
            <select value={selectedBinding?.plugin ?? ""} onChange={(event) => changePlugin(event.target.value)}>
              <option value="">{t("No plugin (unassigned)")}</option>
              <optgroup label={t("Agents (chats and their quota)")}>{manifests.filter((plugin) => AGENT_PLUGINS.has(plugin.id)).map((plugin) => <option key={plugin.id} value={plugin.id}>{t(plugin.name)}</option>)}</optgroup>
              <optgroup label={t("Other quota sources")}>{manifests.filter((plugin) => !AGENT_PLUGINS.has(plugin.id)).map((plugin) => <option key={plugin.id} value={plugin.id}>{t(plugin.name)}</option>)}</optgroup>
            </select>
          </label>
          <label>{t("Display component")}
            <select disabled={!selectedManifest} value={selectedBinding?.component ?? ""} onChange={(event) => changeComponent(event.target.value)}>
              {!selectedManifest && <option value="">{t("Choose a plugin first")}</option>}
              {selectedManifest?.components.map((component) => <option key={component} value={component}>{displayName(component)}</option>)}
            </select>
          </label>
          {selectedBinding?.component === "session" && (
            <label>{t("Number of keys (span)")}
              <input type="number" min={1} max={15 - editingKey} value={selectedBinding?.span ?? 1} onChange={(event) => changeSpan(Number(event.target.value))} />
            </label>
          )}
          {selectedBinding?.component === "quota" ? (() => {
            const quotaSnapshot = selectedBinding.plugin ? snapshots[selectedBinding.plugin] : undefined;
            const accounts = quotaSnapshot?.accounts ?? [];
            const savedAccount = typeof selectedBinding.action_params.account === "string" ? selectedBinding.action_params.account : "";
            const known = findAccount(quotaSnapshot, savedAccount);
            return <>
              {(accounts.length > 0 || savedAccount) && <label>{t("Account")}
                <select value={known?.id ?? savedAccount} onChange={(event) => setQuotaOption("account", event.target.value)}>
                  <option value="">{t("Choose an account")}</option>
                  {accounts.map((account) => <option key={account.id} value={account.id}>{account.title} · {account.id}{account.signals?.stale ? ` ${t("(last seen)")}` : ""}</option>)}
                  {savedAccount && !known && <option value={savedAccount}>{savedAccount} {t("(not found right now)")}</option>}
                </select>
              </label>}
              <label>{t("Label")}
                <input type="text" maxLength={16} placeholder={known?.title ?? pluginName(selectedBinding.plugin)} value={typeof selectedBinding.action_params.label === "string" ? selectedBinding.action_params.label : ""} onChange={(event) => setQuotaOption("label", event.target.value)} />
              </label>
              <p className="editor-hint">{t("Quota keys only display usage; pressing them does nothing.")}</p>
            </>;
          })() : <label>{t("Physical-key action")}
            <select disabled={!selectedManifest} value={selectedBinding?.action ?? ""} onChange={(event) => updateBinding({ action: event.target.value || null, action_params: {} })}>
              <option value="">{t("No action")}</option>
              {selectedManifest?.actions.map((action) => <option key={action} value={action}>{displayName(action)}</option>)}
            </select>
          </label>}
          {(() => {
            const owner = spanZones.get(editingKey) ?? selectedBinding;
            if (!owner?.plugin) return null;
            const fallback = PROVIDER_ACCENTS[owner.plugin] ?? "#8292aa";
            const setAccent = (color: string | null) => updateConfig((current) => ({ ...current, buttons: current.buttons.map((button) => button.key_index === owner.key_index ? { ...button, accent: color } : button) }));
            return <div className="accent-field">
              <span className="field-label">{t("Key colour")}</span>
              <ColorPicker label={t("Key colour")} value={owner.accent ?? fallback} isDefault={!owner.accent} onPick={setAccent} onReset={() => setAccent(null)} />
              <small>{owner.component === "session" ? t("The line on top of the key, above the state strip.") : t("The line on top of the key and its title.")}{owner.key_index !== editingKey ? ` ${t("Shared by every key of this Session zone.")}` : ""}</small>
            </div>;
          })()}
          <div className="binding-explainer">
            {selectedBinding?.plugin === "opencode" && selectedBinding.component === "session" ? <>
              <b>{t("Session zone (span {n})", { n: selectedBinding.span })}</b><span>{isZh() ? t("This key and the keys to its right show live Sessions. Each physical press focuses that slot's Session in OpenChamber.") : <>This key and {(selectedBinding.span ?? 1) - 1} key{(selectedBinding.span ?? 1) > 2 ? "s" : ""} to its right show live Sessions. Each physical press focuses that slot's Session in OpenChamber.</>}</span>
              <small>{selectedBinding.action === "select_session" ? t("Physical press: focus this slot's Session in OpenChamber.") : t("Physical press: no Session focus action configured.")}</small>
            </> : selectedBinding?.plugin === "codex" && selectedBinding.component === "session" ? <>
              <b>{t("Codex chat zone (span {n})", { n: selectedBinding.span })}</b><span>{t("This zone follows Codex's most recently active local chats, using live turn status and Codex-specific key styling.")}</span>
              <small>{selectedBinding.action === "select_session" ? t("Physical press: open this exact chat in the ChatGPT desktop app.") : t("Physical press: no Codex chat action configured.")}</small>
            </> : selectedBinding?.plugin === "claude_code" && selectedBinding.component === "session" ? <>
              <b>{t("Claude Code Session zone (span {n})", { n: selectedBinding.span })}</b><span>{t("This zone follows open and recent Claude Code Sessions; open ones come first.")}</span>
              <small>{selectedBinding.action === "select_session" ? t("Physical press: resume this exact Session in a new console.") : t("Physical press: no Claude Code Session action configured.")}</small>
            </> : selectedBinding?.plugin ? <><b>{displayName(selectedBinding.component)}</b><span>{t("This key renders the selected Plugin component when that Plugin supplies it.")}</span><small>{t("Physical action: {action}", { action: displayName(selectedBinding.action) })}</small></> : <><b>{t("Unassigned key")}</b><span>{t("Choose a Plugin and Component to define what this key displays.")}</span><small>{t("No physical action is configured.")}</small></>}
          </div>
          {selectedBinding?.component === "session" && <p className="pin-hint">{t("Short-press the physical key to open its Session. Hold it for 2 seconds to pin that Session to this slot; hold it again to unpin. An orange frame marks a pinned slot.")}</p>}
          {selectedBinding?.plugin && selectedBinding.component === "session" && isZoneParent(editingKey) && (selectedBinding.span ?? 1) === 1 && (() => {
            const assigned = selectedBinding.session_id ? selectedProviderSessions.find((session) => session.id === selectedBinding.session_id) : null;
            return <div className="session-picker">
              <div className="session-picker-head">
                <p>{t("SESSION ASSIGNMENT")}</p>
                {selectedBinding.session_id && <button type="button" className="ghost-btn" onClick={() => updateBinding({ session_id: null })}>{assigned ? t("Clear session") : t("Session not found — clear")}</button>}
              </div>
              {selectedBinding.session_id && !assigned && <p className="session-not-found">{t("Session not found. It may have closed. Clear the assignment to pick a new one.")}</p>}
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
                      <strong>{session.title || t("Untitled session")}</strong>
                      <small>{session.project ?? t("No project")}</small>
                    </span>
                    <em className="state-chip" style={sessionStateChip(session.state, stateColors)}>{stateText(session.state)}</em>
                  </button>
                ))}
                {selectedProviderSessions.length === 0 && <p className="session-list-empty">{t("No live {name} sessions available.", { name: displayName(selectedBinding.plugin) })}</p>}
              </div>
            </div>;
          })()}
        </section>
      </aside>
    </div> : <section className="configuration-page" role="tabpanel" aria-label="Bridge configuration">
      <section className="panel hardware-bar" aria-label="Stream Deck">
        <div className="hardware-status">
          <i className={`service-dot ${status?.device.connected ? "dot-ok" : "dot-off"}`} aria-hidden="true" />
          <div><b>Stream Deck MK.2</b><span>{status?.device.connected ? t("Ready") : tDetail(status?.device.detail) || t("Waiting")}</span></div>
        </div>
        <label>{t("Brightness")}<input type="range" min="0" max="100" value={config?.stream_deck.brightness ?? 40} onChange={(event) => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, brightness: Number(event.target.value) } }))} /></label>
        <label>{t("Quota keys show")}
          <select value={config?.stream_deck.quota_display ?? "remaining"} onChange={(event) => updateConfig((current) => ({ ...current, stream_deck: { ...current.stream_deck, quota_display: event.target.value as QuotaDisplay } }))}>
            <option value="remaining">{t("What is left (full green bar = plenty)")}</option>
            <option value="used">{t("What is used")}</option>
          </select>
        </label>
      </section>
      <section className="panel services-panel" aria-labelledby="services-title">
        <div className="services-head">
          <div><h2 id="services-title">{t("Connected services")}</h2><span>{t("Click a service to see its settings. Keys stay in the local Bridge and are never shown here.")}</span></div>
          <button ref={addButtonRef} type="button" className="primary" onClick={() => setAdding(true)}>{t("+ Add service")}</button>
        </div>
        {services.length > 0 && <div className="service-legend" aria-hidden="true">
          <span className="status-ok"><i className="service-dot" />{t("Working")}</span>
          <span className="status-loading"><i className="service-dot" />{t("Reading…")}</span>
          <span className="status-stale"><i className="service-dot" />{t("Last seen, not current")}</span>
          <span className="status-error"><i className="service-dot" />{t("Needs attention")}</span>
          <span className="status-pending"><i className="service-dot" />{t("Starts after Save")}</span>
        </div>}
        {services.length === 0 ? <div className="services-empty">
          <h3>{t("No services connected yet")}</h3>
          <p>{t("Connect the AI tools you use; their chats and quota can then show on the Deck.")}</p>
          <button type="button" className="primary" onClick={() => setAdding(true)}>{t("+ Connect your first service")}</button>
          <div className="quick-add"><span>{t("Common:")}</span>{serviceChoices.filter((choice) => choice.group === "agent" && !choice.connected).map((choice) => <button key={choice.id} type="button" onClick={() => pickService(choice)}>{t(choice.name)}</button>)}</div>
        </div> : SERVICE_GROUPS.map(([group, label]) => {
          const items = services.filter((item) => item.group === group);
          if (!items.length) return null;
          return <div key={group} className="service-group">
            <h3 className="service-group-label">{t(label)}</h3>
            <ul className="service-list" aria-label={t(label)}>
              {items.map((item) => <ServiceRow key={item.key} item={item} open={openServices.has(item.key)} onToggle={() => toggleService(item.key)} />)}
            </ul>
          </div>;
        })}
      </section>
      {adding && <AddServiceDialog choices={serviceChoices} onPick={pickService} onClose={() => { setAdding(false); addButtonRef.current?.focus(); }} />}
    </section>}
    {dirty && <div className="unsaved-bar" role="status">
      <span>{services.some((item) => item.dirty || item.leaving) ? t("{n} unsaved change(s). Save to apply them to the Deck.", { n: services.filter((item) => item.dirty || item.leaving).length }) : t("You have unsaved changes. Save to apply them to the Deck.")}</span>
      {confirmDiscard ? <div><span>{t("Drop every unsaved change?")}</span><button type="button" className="danger" onClick={discard}>{t("Discard")}</button><button type="button" onClick={() => setConfirmDiscard(false)}>{t("Cancel")}</button></div>
        : <div><button type="button" onClick={() => setConfirmDiscard(true)}>{t("Discard")}</button><button type="button" className="primary" onClick={() => void save()}>{t("Save changes")}</button></div>}
    </div>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
