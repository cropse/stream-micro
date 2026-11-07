# stream-micro

**你的 AI session 與用量，在 Stream Deck 上一目瞭然。**

[English](README.md) | 繁體中文

Stream Micro 把 **Elgato Stream Deck MK.2** 變成 AI 工具的本機控制面板。
哪些 session 正在執行、哪些在等你，一眼掌握；按一顆實體鍵就能回到某個
session，也能隨時盯著剩餘額度。

![Stream Micro running on an Elgato Stream Deck MK.2](docs/images/readme-cover.png)

在本機 Dashboard 裡設定你的按鍵，有即時預覽，並支援
**英文與繁體中文**。

![Stream Micro Dashboard with key layout preview and settings](docs/images/config-page.png)

## 你可以做什麼

- **掌握 session 動態** — OpenCode、Codex、Claude Code 與 Antigravity CLI 的
  session 顯示在按鍵上，工作中、等待中、已完成各有專屬狀態。
- **回到 session** — 按實體鍵，透過對應整合選取或恢復該 session。
- **固定重要的 session** — 按住 Session 鍵兩秒即可釘選；再按住一次取消釘選。
- **關注額度與餘額** — 顯示用量區間、重置時間與支援的 API 帳戶餘額，
  不必在各家服務的頁面之間切換。
- **打造自己的版面** — 在 Dashboard 選擇 session 區域、額度帳戶、按鍵顏色
  與 session 狀態顏色。
- **一切在本機** — Dashboard 在你自己的電腦上執行，Stream Micro 絕不會
  替你啟動或停止 OpenCode。

## 系統需求

- **Elgato Stream Deck MK.2**（15 顆按鍵，5 × 3）。其他 Stream Deck 機型
  不支援。
- **Windows**：系統匣啟動器與 CLI session 恢復流程都在 Windows 上運作，
  部分 session 狀態偵測也僅限 Windows。
- [uv](https://docs.astral.sh/uv/) 與 [Node.js with npm](https://nodejs.org/)
  用於初次設定。Python 套件需要 Python 3.10 以上。
- 至少一種支援的 AI 工具或額度來源。OpenCode session 需要你既有的
  OpenCode server 保持執行；有 OpenChamber Desktop 時會自動探索。

## 開始使用

### 1. 一次性設定

下載或複製本儲存庫後，在其資料夾內用 PowerShell 執行：

```powershell
uv sync --dev
cd frontend
npm ci
npm run build
cd ..
```

### 2. 啟動 Stream Micro

在 Windows 上雙擊 **`start.vbs`** 可無命令列視窗啟動。系統匣會出現
Stream Deck 造型的圖示；點它開啟 Dashboard。Windows 可能把圖示收在
**^** 裡。

也可以帶著命令列執行：

```powershell
uv run stream-micro serve
```

開啟 **<http://127.0.0.1:8765>**。第一次執行會建立 `bridge.yaml` 保存
你的設定。啟動器不會安裝依賴或編譯 Dashboard，請先完成步驟 1。

### 3. 選擇服務與按鍵

1. 開啟「設定」，啟用你使用的服務與額度來源。Claude Code 的卡片可以
   直接幫你安裝配套整合；Antigravity CLI 需要
   [status-line companion](integrations/antigravity-stream-micro/README.md)。
2. 開啟「按鍵」，選一顆鍵並指定它的服務與顯示元件。Session 區域可以
   跨多顆按鍵；額度鍵可以顯示指定的帳號。
3. 選好顏色後按「儲存」，版面就會套用到 Deck 上。

瀏覽器裡的預覽只用來編輯設定：它**絕不會執行按鍵動作**。
只有按下實體 Stream Deck 才會。

## 日常使用

### Session 按鍵

短按 Session 鍵回到該 session。按住**兩秒**把它釘選在目前位置；
被釘選的 session 有橘色外框。再按住一次取消釘選。初次安裝時按鍵
尚未指派，不會自動加入 OpenCode，也不會為它預留按鍵。
需要它時，到「設定 → 新增服務」加入，再配置 session 按鍵，並保持
OpenCode server 執行。既有使用者儲存的服務與按鍵設定會保留。

狀態色帶告訴你哪裡需要留意：

- **RUN** — 工作中。
- **SUB** — 背景代理或指令還在執行。
- **WAIT** — 等你輸入或核准。
- **DONE** — 最近完成。
- **ERR** — 失敗。
- **IDLE** — 開啟中，沒有進行中的回合。
- **OFF** — 沒有偵測到執行中的程序。

狀態的支援程度取決於服務與其整合方式。

### 額度按鍵

額度鍵可顯示最多兩個用量區間（例如 **5H** 與 **7D**）加上重置時間。
預設的數字與長條顯示**剩餘**：綠色代表充裕、橘色代表剩 30% 以下、
紅色代表剩 10% 以下。灰色的數字是來源目前連不上時，最後一次讀到的值。

想看已用了多少？到「設定」把「額度按鍵顯示」改成「顯示已用」。
支援的餘額來源會顯示剩餘金額而非百分比。

### 系統匣

右鍵點系統匣圖示可開啟 Dashboard、重新啟動或停止 Bridge、啟用
**登入自動啟動**，或結束。

- **停止 Bridge** 保留系統匣圖示，之後可以重新啟動。
- **結束** 會停止 Bridge 並釋放 Deck。
- **登入自動啟動**預設**關閉**。搬移專案資料夾或虛擬環境前請先停用。

Bridge 有回應時圖示是綠色、啟動或停止中是黃色、停止或無回應是紅色。
單純的服務故障不會讓圖示變紅。

## 可攜 Windows 執行檔

下載 Windows zip 解壓到任意資料夾，雙擊 `StreamMicro.exe` 即可使用。
設定（`bridge.yaml`）、日誌（`bridge.log`）與額度狀態（`.state/`，第一次
被插件使用時建立）都放在執行檔旁。更新時：先從系統匣選單**結束**
Stream Micro，把新 zip 解壓蓋到原資料夾再重新啟動；zip 永遠不含設定檔。
搬移資料夾後，請重新勾選**登入自動啟動**；若你有使用 Claude companion，
也請到 Dashboard 重新安裝。

從原始碼建置執行檔：

```powershell
uv sync --dev --group packaging
uv run --group packaging python build_exe.py
```

原始碼開發流程（`serve` 與 `start.vbs`）不受影響。

## 協助與延伸閱讀

- [Provider setup and advanced configuration](docs/configuration.md) — 整合
  需求、額度來源、YAML 範例與安全注意事項。
- [Troubleshooting](docs/configuration.md#troubleshooting) — 啟動、連接埠、
  日誌與無法使用的服務。
- [Development](docs/development.md) — 本機開發、熱重載、整合契約與
  驗證指令。
