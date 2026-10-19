// Dashboard language: English (the default and the source text) or Traditional
// Chinese. Strings are looked up by their English text, so anything without a
// translation simply stays English. The choice is a browser-only preference.

export type Lang = "en" | "zh-TW";

const STORAGE_KEY = "deck-bridge-lang";

function initialLang(): Lang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "zh-TW") return saved;
  } catch {
    // Storage can be unavailable (private window, blocked site data).
  }
  // Only Traditional Chinese locales start in Chinese; everyone else in English.
  const language = navigator.language?.toLowerCase() ?? "";
  return /^zh-(tw|hk|mo|hant)/.test(language) ? "zh-TW" : "en";
}

let current: Lang = initialLang();

export function getLang(): Lang {
  return current;
}

export function setLang(lang: Lang) {
  current = lang;
  document.documentElement.lang = lang;
  try {
    window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // The choice then lasts only for this page view.
  }
}

export const isZh = () => current === "zh-TW";

/** Locale for dates and numbers in the current language. */
export const locale = () => (current === "zh-TW" ? "zh-TW" : "en-US");

const ZH: Record<string, string> = {
  "Show the reset early": "提早顯示重置時間",
  "When a weekly or monthly limit has this much or less left, a key's bottom line shows when that limit resets: amber for weekly, red for monthly. The 5-hour limit always switches at 0%.": "每週或每月額度剩到這個比例（或更少）時，按鍵最下面那行改顯示它的重置時間：週額度是橘色，月額度是紅色。5 小時額度固定在 0% 才切換。",
  "For this service only. Shared follows the setting at the top of Configuration.": "只套用到這個服務。「共用」會跟著設定頁最上面的值。",
  "Weekly (7D) at or below": "每週（7D）剩不到",
  "Monthly (MO) at or below": "每月（MO）剩不到",
  "Shared ({value})": "共用（{value}）",
  Off: "關閉",
  "Open that source under Configuration and fill in where its number is.": "到設定頁打開那個來源，填上數字在回應裡的位置。",
  "Open that source under Configuration and add a row with where its number is.": "到設定頁打開那個來源，加一列並填上數字的位置。",
  "Fill in where at least one number is in the reply (a row below); the Bridge cannot save this source without it.": "至少要在下面填一列「數字在回應裡的位置」，不然 Bridge 無法儲存這個來源。",
  "Not saved:": "沒有儲存：",
  "Session state comes from the running Claude process and its transcript, so it works without setup; the companion hook (installed above) adds an exact WAIT when Claude needs your approval. SUB means the turn ended but subagents or other agents still work for it. Quota appears after Claude Code's status line receives its first API response.": "Session 狀態是從執行中的 Claude 程式和對話記錄判斷的，不用另外設定；裝了上面的 hook，Claude 等你核准時會準確顯示 WAIT。SUB 表示這一輪已結束，但子代理或其他代理還在工作。額度在 Claude Code 狀態列收到第一個 API 回應後出現。",
  "Optional, e.g. OPENROUTER_API_KEY": "選填，例如 OPENROUTER_API_KEY",
  "Written to {path} (the old file is backed up). Start a new Claude Code session for it to take effect.": "已寫入 {path}（舊檔已備份）。新開一個 Claude Code session 後生效。",
  "Both run small scripts from this Bridge while Claude Code works; they only report to this computer.": "兩者都是 Claude Code 運作時跑 Bridge 的一個小程式，只回報給這台電腦。",
  "The line on top of the key, above the state strip.": "按鍵最上面那條線，在狀態色帶上方。",
  "Physical press: resume this exact Session in a new console.": "實體按鍵：在新的 console 繼續這個 Session。",
  "Then choose this source as the Account of a Usage API quota key in Buttons.": "接著到「按鍵」頁，把這個來源選為額度鍵的「帳號」。",
  "Quota key": "額度鍵",
  "Used % path": "已用 % 的路徑",
  "Remaining % path": "剩餘 % 的路徑",
  "Used amount path": "已用量的路徑",
  "Remaining amount path": "剩餘量的路徑",
  "Limit path": "上限的路徑",
  "Usage API quota": "用量 API 額度",
  "Ollama quota": "Ollama 額度",
  "Claude Code": "Claude Code",
  "Codex": "Codex",
  "OpenCode": "OpenCode",
  // Header and tabs
  "LOCAL CONTROL SURFACE": "本機控制面板",
  "MK.2 connected": "MK.2 已連線",
  "Deck offline": "Deck 未連線",
  Refresh: "重新整理",
  "Save changes": "儲存變更",
  Saved: "已儲存",
  Buttons: "按鍵",
  "Deck layout and actions": "Deck 版面與動作",
  Configuration: "設定",
  "Hardware, providers and quota": "硬體、資料來源與額度",
  "Switch language": "切換語言",

  // Notices
  "Connecting to local Bridge...": "正在連線到本機 Bridge…",
  "Bridge unavailable": "連不到 Bridge",
  "Moved key {from} to key {to}. Save to apply.": "已把第 {from} 鍵移到第 {to} 鍵，按「儲存變更」套用。",
  "Cannot drop here — span zone would overflow or overlap.": "不能放在這裡：多鍵區塊會超出邊界或跟其他區塊重疊。",
  "Saved. The physical Deck updates on its next refresh.": "已儲存，實體 Deck 下次更新時會套用。",
  "Could not save configuration": "設定無法儲存",

  // Deck preview
  "MK.2 BUTTON LAYOUT": "MK.2 按鍵配置",
  "Choose a key to edit": "選一顆鍵來編輯",
  "This browser preview never runs a key action. Only the physical Stream Deck does.": "這裡只是預覽，點了不會執行任何動作；只有實體 Stream Deck 會執行。",
  "provider sessions": "個 session",
  "{name} quota": "{name} 額度",
  "Session slot": "Session 位置",
  "No project": "沒有專案",
  "slot {n}": "第 {n} 格",
  Unassigned: "未指定",
  "(last seen)": "（舊資料）",
  "No action": "沒有動作",
  "Edit key {n}: {label}": "編輯第 {n} 鍵：{label}",
  "(pinned)": "（已釘選）",
  "drag to rearrange": "可拖曳調整位置",
  "Editing key": "編輯中的鍵",
  "Pinned Session": "已釘選的 Session",
  "Not configured": "未設定",


  // Key editor
  "KEY CONFIGURATION": "按鍵設定",
  "Key {n}": "第 {n} 鍵",
  "Changes stay in this page until you choose Save. Clicking this preview never opens OpenChamber, Codex, or Claude Code.": "按「儲存變更」之前，修改只留在這個頁面。點預覽不會開啟 OpenChamber、Codex 或 Claude Code。",
  Plugin: "資料來源",
  "No plugin (unassigned)": "不使用（未指定）",
  "Display component": "顯示內容",
  "Choose a plugin first": "請先選資料來源",
  "Number of keys (span)": "佔用幾顆鍵",
  Account: "帳號",
  "Choose an account": "請選擇帳號",
  "(not found right now)": "（目前找不到）",
  Label: "標籤",
  "Quota keys only display usage; pressing them does nothing.": "額度鍵只顯示用量，按下去不會有動作。",
  "Physical-key action": "實體鍵動作",
  "Session zone (span {n})": "Session 區塊（{n} 顆鍵）",
  "This key and the keys to its right show live Sessions. Each physical press focuses that slot's Session in OpenChamber.": "這顆鍵和右邊的鍵會顯示即時 Session。按實體鍵會在 OpenChamber 切到該格的 Session。",
  "Physical press: focus this slot's Session in OpenChamber.": "按實體鍵：在 OpenChamber 切到這格的 Session。",
  "Physical press: no Session focus action configured.": "按實體鍵：沒有設定切換 Session 的動作。",
  "Codex chat zone (span {n})": "Codex 對話區塊（{n} 顆鍵）",
  "This zone follows Codex's most recently active local chats, using live turn status and Codex-specific key styling.": "這個區塊會顯示 Codex 最近的本機對話，包含即時狀態和 Codex 專用的按鍵樣式。",
  "Physical press: open this exact chat in the ChatGPT desktop app.": "按實體鍵：在 ChatGPT 桌面版打開這個對話。",
  "Physical press: no Codex chat action configured.": "按實體鍵：沒有設定開啟 Codex 對話的動作。",
  "Claude Code Session zone (span {n})": "Claude Code Session 區塊（{n} 顆鍵）",
  "This zone follows open and recent Claude Code Sessions; open ones come first.": "這個區塊會顯示開著和最近的 Claude Code Session，開著的排前面。",
  "Physical press: no Claude Code Session action configured.": "按實體鍵：沒有設定開啟 Claude Code Session 的動作。",
  "This key renders the selected Plugin component when that Plugin supplies it.": "這顆鍵會顯示所選資料來源提供的內容。",
  "Physical action: {action}": "實體鍵動作：{action}",
  "Unassigned key": "未指定的鍵",
  "Choose a Plugin and Component to define what this key displays.": "選擇資料來源和顯示內容，決定這顆鍵要顯示什麼。",
  "No physical action is configured.": "沒有設定實體鍵動作。",
  "Short-press the physical key to open its Session. Hold it for 2 seconds to pin that Session to this slot; hold it again to unpin. An orange frame marks a pinned slot.": "短按實體鍵會打開它的 Session。長按 2 秒會把這個 Session 釘在這格，再長按一次取消。釘選的格子有橘色外框。",
  "SESSION ASSIGNMENT": "指定 Session",
  "Clear session": "清除指定",
  "Session not found — clear": "找不到 Session，清除",
  "Session not found. It may have closed. Clear the assignment to pick a new one.": "找不到這個 Session，可能已經關閉。清除後再選一個新的。",
  "Untitled session": "未命名 Session",
  "No live {name} sessions available.": "目前沒有 {name} 的即時 Session。",
  // Plugin names (from the Bridge) and their key captions
  Session: "Session",

  // Configuration page
  Ready: "就緒",
  Waiting: "等待中",
  Brightness: "亮度",
  "Quota keys show": "額度鍵顯示",
  "What is left (full green bar = plenty)": "剩餘量（整條綠色＝還很多）",
  "What is used": "已用量",
  "Advanced settings": "進階設定",
  "Refresh interval (seconds)": "更新間隔（秒）",
  "Request timeout (seconds)": "請求逾時（秒）",
  "Key from environment variable": "從環境變數讀金鑰",
  "Forget saved key": "清除已存的金鑰",
  "Key saved — enter a new value to replace it": "已存金鑰，輸入新值會取代",
  Optional: "選填",

  // Usage bars
  Primary: "上排",
  Secondary: "下排",
  Weekly: "每週",
  "Resets:": "重置：",
  "Last seen {time} — the source is not reachable now.": "最後更新 {time}，目前連不到來源。",
  "Last seen — the source is not reachable now.": "目前連不到來源，顯示的是舊資料。",

  // OpenCode
  "OPENCODE SERVERS": "OpenCode 伺服器",
  Sessions: "Session",
  "Enable OpenCode provider": "啟用 OpenCode",
  "Server endpoints": "伺服器位址",
  "One endpoint per line": "一行一個位址",
  "Password environment variable": "密碼的環境變數",
  "OpenCode sessions available to button zones": "個 OpenCode session 可放到按鍵",

  // Codex
  "CODEX APP SERVER": "Codex App Server",
  "Enable Codex provider": "啟用 Codex",
  "Codex executable": "Codex 執行檔",
  "codex (use PATH)": "codex（從 PATH 找）",
  "Use current Codex home": "使用目前的 Codex 資料夾",
  "recent Codex chats available to session keys": "個最近的 Codex 對話可放到按鍵",

  // Claude Code
  "CLAUDE CODE": "Claude Code",
  "Enable Claude Code provider": "啟用 Claude Code",
  "Claude executable": "Claude 執行檔",
  "claude (use PATH)": "claude（從 PATH 找）",
  "Claude data folder": "Claude 資料夾",
  "Use current ~/.claude folder": "使用目前的 ~/.claude 資料夾",
  "Companion plugin folder": "搭配的 plugin 資料夾",
  "Optional path to the bundled integration": "選填，專案附的整合套件路徑",
  "Recent Sessions": "最近 Session 數量",
  "recent Claude Code Sessions available to button zones": "個最近的 Claude Code Session 可放到按鍵",

  // Ollama
  "Session cookie value": "Session cookie 值",
  "Cookie configured — enter a new value to replace it": "已設定 cookie，輸入新值會取代",
  "Paste ollama.com __Secure-session cookie value": "貼上 ollama.com 的 __Secure-session cookie 值",
  "The saved cookie stays in the local Bridge and is never returned to this page.": "已存的 cookie 只留在本機 Bridge，不會再傳回這個頁面。",
  "Settings URL": "設定頁網址",
  "Cookie name": "Cookie 名稱",
  "How to get the cookie": "怎麼取得 cookie？",



  // Usage APIs
  "OpenRouter key limit": "OpenRouter 金鑰上限",
  "OpenRouter credits": "OpenRouter 帳戶餘額",
  "Custom JSON API": "自訂 JSON API",
  "Any OpenRouter API key that has a spending limit.": "任何有設定花費上限的 OpenRouter API 金鑰。",
  "Account credits; OpenRouter may require a management key.": "帳戶餘額；OpenRouter 可能要求管理用金鑰。",
  "Ollama Cloud credits": "Ollama Cloud 每月額度",
  // Configuration page sections
  "Top line on Session keys": "Session 鍵顯示頂端線",
  "Quota keys always keep their top line.": "額度鍵一定有頂端線。",
  "Not wanted; no reminders.": "不需要，不再提醒。",
  "I want it after all": "改成要裝",
  "I don't need this": "我不需要",
  "just now": "剛剛",
  "{n} min ago": "{n} 分鐘前",
  "{n} h ago": "{n} 小時前",
  "Removed from {path}.": "已從 {path} 移除。",
  "Quota report (status line)": "額度回報（狀態列）",
  "Approval reminder WAIT (hook)": "等待核准提醒 WAIT（hook）",
  "Lets Claude's 5-hour and weekly quota show on the Deck.": "讓 Claude 的 5 小時、每週額度顯示在 Deck 上。",
  "Lets Session keys show WAIT while Claude waits for your approval.": "Claude 在等你核准時，Session 鍵會顯示 WAIT。",
  "Not installed": "尚未安裝",
  "Install": "安裝",
  "Claude Code already uses another status line.": "Claude Code 已經在用別的狀態列。",
  "Keep it and add quota (recommended)": "保留它，再加上額度回報（建議）",
  "Replace it": "直接取代",
  "Set to another port; this Bridge listens on {port}.": "設定送到別的 port，但 Bridge 在 {port}。",
  "Fix it": "一鍵修好",
  "Only part of it is installed.": "只裝了一部分。",
  "Working, last report {when}": "正常，最近一次回報：{when}",
  "Installed. Waiting for the first report: start a new Claude Code session.": "已安裝，等第一次回報：新開一個 Claude Code session。",
  "(after your own status line)": "（接在你原本的狀態列後面）",
  "Remove it from Claude Code's settings?": "要從 Claude Code 設定移除嗎？",
  "Claude Code setup": "Claude Code 設定檢查",
  "Do it yourself": "自己動手設定",
  "Add these to {path} (merge with what is there), then start a new Claude Code session.": "把下面這段加進 {path}（跟原本的內容合併），再新開一個 Claude Code session。",
  "Copy": "複製",
  "Could not change Claude Code's settings": "無法修改 Claude Code 的設定",
  "Setup incomplete": "設定未完成",
  "Custom colour": "自訂顏色",
  "Reset to default colour": "改回預設色",
  "Reset all to default colours": "全部改回預設色",
  "Click a state to change its colour. The preview changes at once; the Deck after Save.": "點一個狀態來換顏色。預覽會馬上變，實體 Deck 在「儲存變更」後套用。",
  "The line on top of the key and its title.": "按鍵上方那條線和標題的顏色。",
  "Key colour": "按鍵代表色",
  "Shared by every key of this Session zone.": "這個 Session 區塊的每顆鍵共用這個顏色。",
  "Session state colours": "Session 狀態顏色",
  "Moonshot balance (international)": "Moonshot 餘額（國際版）",
  "Moonshot balance (China)": "Moonshot 餘額（中國版）",
  "SiliconFlow balance (China)": "SiliconFlow 餘額（中國版）",
  "SiliconFlow balance (international)": "SiliconFlow 餘額（國際版）",
  "Novita balance": "Novita 餘額",
  "An API key from platform.moonshot.ai (USD account). Reading the balance costs nothing.": "platform.moonshot.ai 的 API 金鑰（美元帳戶）。查餘額不扣錢。",
  "An API key from platform.moonshot.cn (CNY account). Reading the balance costs nothing.": "platform.moonshot.cn 的 API 金鑰（人民幣帳戶）。查餘額不扣錢。",
  "An API key from cloud.siliconflow.cn (CNY account). Reading the balance costs nothing.": "cloud.siliconflow.cn 的 API 金鑰（人民幣帳戶）。查餘額不扣錢。",
  "An API key from cloud.siliconflow.com (USD account). Reading the balance costs nothing.": "cloud.siliconflow.com 的 API 金鑰（美元帳戶）。查餘額不扣錢。",
  "An API key from novita.ai (Key Management). Reading the balance costs nothing.": "novita.ai 的 API 金鑰（Key Management）。查餘額不扣錢。",
  "{amount} left": "剩 {amount}",
  "{value}% left": "剩 {value}%",
  "{value}% used": "已用 {value}%",
  "DeepSeek balance": "DeepSeek 餘額",
  "A DeepSeek API key (platform.deepseek.com → API keys). Reading the balance costs nothing.": "DeepSeek 的 API 金鑰（platform.deepseek.com → API keys）。查餘額不扣錢。",
  "Reading…": "讀取中…",
  "It was never saved, so what you entered is dropped.": "它還沒存過，移除後剛輸入的內容會一起丟掉。",
  "Drop every unsaved change?": "要丟掉所有未儲存的變更嗎？",
  "+ Add service": "＋ 新增服務",
  "+ Connect your first service": "＋ 連接第一個服務",
  "A running OpenCode server.": "需要一個在跑的 OpenCode 伺服器。",
  "Add a service": "新增服務",
  "Cancel": "取消",
  "ChatGPT plan of the account signed in to the local codex.": "本機 codex 登入帳號的 ChatGPT 方案額度。",
  "Claude plan: 5-hour and weekly limits.": "Claude 方案：5 小時與每週額度。",
  "Click a service to see its settings. Keys stay in the local Bridge and are never shown here.": "點一個服務看它的設定。金鑰只存在本機 Bridge，不會顯示在這裡。",
  "Common:": "常用：",
  "Connect the AI tools you use; their chats and quota can then show on the Deck.": "接上你在用的 AI 工具，它們的對話和額度就能顯示在 Deck 上。",
  "Connected services": "已連接的服務",
  "Discard": "捨棄變更",
  "It goes away when you Save; until then you can undo.": "按「儲存變更」後才會生效，之前都可以復原。",
  "Needs attention": "需要處理",
  "No services connected yet": "還沒有連接任何服務",
  "Nothing: reads Claude Code on this computer.": "免設定：讀這台電腦上的 Claude Code。",
  "Nothing: uses the codex installed on this computer.": "免設定：用這台電腦安裝的 codex。",
  "Starts after Save": "儲存後開始讀取",
  "This service goes away when you Save.": "按「儲存變更」後這個服務就會移除。",
  "Turned off after Save": "儲存後停用",
  "Undo": "復原",
  "Unsaved": "未儲存",
  "Working": "正常",
  "You already have {n}; this adds another account.": "你已經有 {n} 個，這會再加一個帳號。",
  "You have unsaved changes. Save to apply them to the Deck.": "有未儲存的變更，儲存後才會套用到 Deck。",
  "last read {time}": "上次讀取 {time}",
  "{n} chats": "{n} 個對話",
  "{n} key(s) use it and will show no data.": "有 {n} 顆按鍵用到它，之後會顯示沒有資料。",
  "{n} unsaved change(s). Save to apply them to the Deck.": "有 {n} 項未儲存的變更，儲存後才會套用到 Deck。",
  "Monthly plan": "月費",
  "Pay as you go": "按量計費",
  "Turn off": "停用",
  "OLLAMA · SESSION COOKIE": "OLLAMA · 登入 COOKIE",
  "An Ollama source with an API key is set up below; this cookie card can be turned off.": "下面已經有用 API 金鑰的 Ollama 來源，這張 cookie 卡可以停用。",
  "Chats": "對話",
  "New source": "新來源",
  "Close": "關閉",
  "Agents (chats and their quota)": "Agent（對話與它的額度）",
  "Other quota sources": "其他額度來源",
  "Agent chats": "Agent 對話",
  "Monthly plans": "月費方案",
  "Pay as you go and custom": "按量計費與自訂",
  "Paste an API key.": "貼上 API 金鑰。",
  "A URL that returns usage as JSON, a key, and where the numbers are.": "要填回傳用量 JSON 的網址、金鑰，以及數字在哪裡。",
  "Ollama Cloud (session cookie)": "Ollama Cloud（登入 cookie）",
  "A cookie copied from the browser. The API key choice is easier.": "要從瀏覽器複製 cookie。選 API 金鑰的那項比較簡單。",
  "An ollama.com API key (Settings → Keys). Reading the balance uses no credits; no cookie needed.": "ollama.com 的 API 金鑰（Settings → Keys）。查餘額不會扣額度，也不用 cookie。",
  "Any HTTPS endpoint that returns usage as JSON.": "任何用 JSON 回報用量的 HTTPS 網址。",
  "Used %": "已用 %",
  "Remaining %": "剩餘 %",
  "Used / limit": "已用／上限",
  "Remaining / limit": "剩餘／上限",
  "Preset default": "使用範本預設",
  "Bearer <key> (default)": "Bearer <金鑰>（預設）",
  "<key> only": "只放 <金鑰>",
  "Key header": "金鑰放在哪個標頭",
  "Never put a key in the URL. Pointing a source at another host asks for its key again.": "不要把金鑰放進網址。來源改指向別的主機時，需要重新貼上金鑰。",
  "Key format": "金鑰格式",
  "Add top row": "新增上排",
  "Add bottom row": "新增下排",
  "Top row": "上排",
  "Bottom row": "下排",
  Remove: "移除",
  Numbers: "數字類型",
  "Value is a fraction (0–1)": "數值是 0–1 的比例",
  "Reset time path": "重置時間的路徑",
  "Shown on the key": "顯示在按鍵上",
  "API key": "API 金鑰",
  "Paste the API key": "貼上 API 金鑰",
  "Source ID (the key's Account)": "來源 ID（按鍵的「帳號」）",
  "Saved sources keep their ID; change the Label instead": "已儲存的來源不能改 ID，要改名請改「標籤」",
  "Last seen, not current": "舊資料，不是最新",
  Current: "最新",

  // Usability hints
  "No quota numbers yet.": "還沒有額度數字。",
  "Off. Turn it on to set it up; changes apply after Save.": "未啟用。打開開關就能設定，按「儲存變更」後生效。",
  "Paste the cookie above, then Save.": "在上方貼上 cookie，再按「儲存變更」。",
  "Sign in at ollama.com again and paste a new cookie (see How to get the cookie).": "重新登入 ollama.com，貼上新的 cookie（看「怎麼取得 cookie？」）。",
  "Install Claude Code, or set its executable under Advanced settings.": "安裝 Claude Code，或在「進階設定」指定執行檔位置。",
  "Paste the key again; it may have been revoked.": "重新貼上金鑰，原本的可能已被撤銷。",
  "Paste the API key above, then Save.": "在上方貼上 API 金鑰，再按「儲存變更」。",
  "Check the paths under Advanced settings against the service's reply.": "對照服務的回應，檢查「進階設定」裡的路徑。",

  // Quota previews and messages from the Bridge
  Connected: "已連線",
  "{when} (in {h} h {m} min)": "{when}（還有 {h} 小時 {m} 分）",
  "{when} (in {m} min)": "{when}（還有 {m} 分）",
};

// Messages from the Bridge that carry numbers.
const ZH_PATTERNS: [RegExp, (...groups: string[]) => string][] = [
  [/^http_quota source (\S+) window (\d+) needs used_percent, remaining_percent, or limit with used or remaining$/, (id, n) => `來源「${id}」第 ${n} 列還沒填數字的位置`],
  [/^http_quota source (\S+) needs one or two windows$/, (id) => `來源「${id}」還沒有任何一列數字`],
  [/^(\d+) recent Sessions, (\d+) open$/, (n, open) => `${n} 個最近的 Session，${open} 個開著`],
  [/^(\d+) recent Sessions; waiting for live hooks$/, (n) => `${n} 個最近的 Session；等待即時 hook`],
  [/^Live hooks connected; (\d+) recent Sessions$/, (n) => `即時 hook 已連線；${n} 個最近的 Session`],
  [/^(\d+) source\(s\) current$/, (n) => `${n} 個來源都是最新`],
  [/^(\d+) of (\d+) source\(s\) current$/, (ok, n) => `${n} 個來源中 ${ok} 個是最新`],
  [/^HTTP (\d+)$/, (code) => `HTTP ${code} 錯誤`],
  [/^unreachable: (.*)$/, (why) => `連不到：${why}`],
];

/** Translate dashboard text; `{name}` placeholders are filled from `vars`. */
export function t(text: string, vars?: Record<string, string | number>): string {
  let out = current === "zh-TW" ? ZH[text] ?? text : text;
  for (const [key, value] of Object.entries(vars ?? {})) out = out.split(`{${key}}`).join(String(value));
  return out;
}

/** Translate a status message from the Bridge, part by part ("A; B"); unknown text stays English. */
export function tDetail(text: string | null | undefined): string {
  if (!text) return "";
  if (current !== "zh-TW") return text;
  // Whole messages first: some contain "; " themselves.
  for (const [pattern, render] of ZH_PATTERNS) {
    const match = text.match(pattern);
    if (match) return render(...match.slice(1));
  }
  let rest = text;
  for (const [english, chinese] of Object.entries(ZH)) {
    if (english.includes("; ") && rest.includes(english)) rest = rest.split(english).join(chinese);
  }
  return rest
    .split("; ")
    .map((part) => {
      if (ZH[part]) return ZH[part];
      for (const [pattern, render] of ZH_PATTERNS) {
        const match = part.match(pattern);
        if (match) return render(...match.slice(1));
      }
      return part;
    })
    .join("；");
}
