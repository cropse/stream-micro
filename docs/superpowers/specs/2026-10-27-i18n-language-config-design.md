# 語言設定進 config 與 i18n（系統匣、CLI、README 雙語）

## 已確認的需求

使用者已在對話確認以下範圍：

- 語言選擇存進 `bridge.yaml`，config 是事實來源；Dashboard 切換語言時立即寫入 config，不再只是瀏覽器 localStorage。
- 選了中文時，系統匣必須跟著中文：選單與狀態提示全部一致——選英文→全英文，選中文→全中文。目前「選單寫死繁中、狀態寫死英文」的混雜一併修正。
- 切換語言後系統匣約 2 秒內自動跟上，不需重啟。
- CLI 的使用者可見訊息一併本地化。
- README 雙語：`README.md`（英文）與 `README.zh-TW.md`（繁中）兩份，頂部互相連結切換。
- 第一版只做 English 與 繁體中文。

## 現況（設計依據）

- `frontend/src/i18n.ts`：英文原文為來源字串、zh-TW 字典；語言只存 localStorage（`deck-bridge-lang`），後端無法得知。
- `src/stream_micro/tray.py`：選單寫死繁中（開啟 Dashboard、重新啟動 Bridge…），狀態提示寫死英文（`Stream Micro: Starting`、`Bridge running`…），與語言選擇完全無關。
- `src/stream_micro/cli.py`：英文訊息（`stream-micro: created Bridge configuration…`、`stopped`、tray 啟動失敗的 MessageBoxW 文案）。
- `src/stream_micro/control_config.py`：`parse_control_config` 對頂層欄位嚴格驗證（`schema_version`、`bridge`、`stream_deck`、`plugins`、`buttons`、`session_window_bindings`）；寫檔為 atomic replace。
- 前端以 `PUT /api/config` 整份存檔（`server.py` → `BridgeService.replace_config`），config 全量 round-trip。
- `TrayController.run` 每 2 秒醒來探測 Bridge 健康狀態。

## 實作選擇（由實作者提出）

### 1. Config schema：頂層 `language` 欄位

- 新增頂層 `language: "auto" | "en" | "zh-TW"`，預設 `"auto"`。放頂層（產品整體偏好，不塞進 `bridge` 的 host/port 群組）。
- `"auto"`：Dashboard 沿用現行瀏覽器語言偵測（`initialLang` 的 zh-(tw|hk|mo|hant) 規則）；後端（系統匣、CLI）用 Windows 使用者 UI 語言（`ctypes.windll.kernel32.GetUserDefaultUILanguage()`，Hant/zh-TW/zh-HK/zh-MO → zh-TW，否則 en）。如此從未改過設定的繁中使用者維持現有自動偵測行為，且系統匣語言與之作對齊。
- 明確選了 `en` 或 `zh-TW`：前後端一律跟隨，config 為事實來源。
- `schema_version` 維持 1：舊檔缺 `language` 解析為 `"auto"`，新檔的 `language` 對未更新前的程式碼會報 Unknown configuration section，但本程式前後端同機同時更新，不處理版本交錯。
- `parse_control_config` 的 allowed 集合加入 `language`；值驗證只能是三選一，其他值報 `ControlConfigError`。

### 2. 前端（`frontend/src/main.tsx`、`i18n.ts`）

- `type Config` 加 `language?: "auto" | "en" | "zh-TW"`。
- 載入：`initialLang`（localStorage → 瀏覽器偵測）維持，用來避免 config 載入前的閃爍；config 載入後，語言以 `config.language` 為準——`"auto"` 沿用 `initialLang()` 的結果（localStorage 優先於瀏覽器偵測，與現行行為完全一致，既有使用者的偏好升級後不回退），明確值才覆蓋。localStorage 仍照舊寫入，僅作為下次頁面載入的初始種子。
- 切換（`switchLang`）：除了現行 `setLang`（立即重繪 UI），同時以**已儲存的 config（savedConfig）+ 新語言**立即 `PUT /api/config`，不經過「Save」流程。以 savedConfig 為基底而非目前的草稿，是為了：語言切換不會順便套用尚未按 Save 的其他修改，也不會因草稿填到一半無法通過驗證而存不進去。PUT 成功後更新 savedConfig 與本地草稿 config 的 `language`（避免之後按 Save 用舊值把 language 蓋回去），草稿的 dirty 狀態維持不變；PUT 失敗時以既有 notice 顯示錯誤、UI 語言仍照切。
- 語言切換按鈕維持 en ↔ zh-TW 二元切換，寫入明確值；`"auto"` 只作為未動過設定的預設，一旦明確選過就不再回到 auto。

### 3. 後端 i18n 模組（`src/stream_micro/i18n.py`）

- 與前端同一套做法：英文原文為鍵，`ZH_TW` 字典，`t(text, lang)`；未翻譯的字串 fallback 英文。
- `resolve_language(config) -> "en" | "zh-TW"`：明確值直接用；`"auto"` 走 Windows UI 語言（ctypes；非 Windows 平台一律 en）。
- 字典只收系統匣與 CLI 需要的字串（十來條），不引入 gettext/babel 等依賴。

### 4. 系統匣（`src/stream_micro/tray.py`）

- 啟動時讀 `bridge.yaml` 決定語言；主迴圈每 2 秒醒來時重讀 config（atomic write，讀取安全；Bridge 停止時也讀得到，不依賴 API）。語言變了：重建選單（pystray 的 menu 更新）並改用新字典產生之後的狀態字串；狀態列當下顯示中的 title 不強制重譯，下一次 present 自然用新語言。
- 選單項目改為字鍵（Open Dashboard / Restart Bridge / Stop Bridge / Run at log in / Exit——現行寫死繁中改為 t() 出字），加上「登入自動啟動」勾選狀態文字。
- 狀態字串（Starting、Stopping Bridge、Starting Bridge、Bridge stopped、Bridge running、Bridge stopped or not responding、Operation failed; see bridge.log）與 tooltip 前綴 `Stream Micro: ` 都經 `t()`。

### 5. CLI（`src/stream_micro/cli.py`）

- 使用者可見訊息經 `t()`：`created Bridge configuration at …`、`stopped`、最外層錯誤行 `stream-micro: {exc}`（exc 內容本身不翻譯——多為 ConfigError 開發者導向文字）、tray 啟動失敗的 MessageBoxW 標題與內文。
- 語言從 `bridge.yaml` 讀；檔案不存在或讀取失敗時以 `auto` 解析（Windows UI 語言）。
- argparse 的 help/usage、log 檔內容不翻譯（開發者導向）。

### 6. README 雙語

- `README.md` 頂部（標題與副標下）加切換行：`English | [繁體中文](README.zh-TW.md)`。
- 新增 `README.zh-TW.md`：完整繁中翻譯，同結構、同圖片相對路徑（從 repo root 相對路徑不變）、同指令內容；頂部 `[English](README.md) | 繁體中文`。
- 兩份內容必須一起修改，行為描述以英文版為準；中文版連往 `integrations/*/README.md` 的連結保留英文原文。
- README 內文既有的「support for English and Traditional Chinese」描述不變。

### 7. 測試

- `tests/test_control_config.py`：`language` 合法三值、非法值報錯、缺欄預設 `"auto"`、含 `language` 的 round-trip。
- `tests/test_i18n.py`（新）：`t()` 對照與 fallback、`resolve_language` 明確值與 auto 分支（mock ctypes）。
- `tests/test_tray.py`：選單與狀態字串隨語言、config 變更後 2 秒內跟上（沿現有 fake 架構）。
- `tests/test_server.py`：`GET/PUT /api/config` round-trip 帶 `language`。
- 前端依專案現況以 `npm run build`（tsc）驗證。

## 不在範圍內

- 第三種以上語言（枚舉與字典要再加）。
- `integrations/*/README.md`、`docs/`、`PRD.md`、`AGENT.md` 的翻譯。
- Stream Deck 按鍵上的狀態縮寫（RUN/SUB/WAIT/DONE/ERR/IDLE/OFF）與數字格式——按鍵顯示為短縮寫設計，與 Dashboard 語言脫鉤。
- API 錯誤 detail、log 檔內容、argparse help 等開發者導向文字。
- OpenCode/Claude Code 等外部工具的 session 標題與資料內容（來源是什麼就顯示什麼）。