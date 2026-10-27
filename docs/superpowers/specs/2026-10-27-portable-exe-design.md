# 可攜 exe 打包發佈

## 為什麼

現行使用方式需要 checkout、uv、Node.js，先跑安裝與建置指令才能透過 start.vbs 啟動，是開發者流程。打包成可攜 exe 後，使用者體驗變成「下載、解壓、雙擊」：不安裝、不需要環境；設定跟著 exe 走，更新只要蓋上新的 zip。使用者已選擇可攜形態而非安裝器。

## 已確認的需求

- 可攜 zip：解壓到任意資料夾，雙擊 `StreamMicro.exe` 進系統匣；無安裝步驟，exe 本體不需要 Python／uv／Node。唯一例外：Claude Code 整合的 companion 以使用者本機的 Node 執行（裝了 Claude Code 的機器必有 Node），exe 不另外打包 JS runtime。
- 路徑不固定：exe 放哪個資料夾都正確執行；整包複製到別台機器、別的資料夾也能用。
- 設定跟著 exe 走：`bridge.yaml`、`.state/`、`bridge.log` 放 exe 同資料夾。
- zip 只含程式檔，永遠不含設定檔：新 zip 解壓蓋到原資料夾即完成更新，設定原封不動。
- 第一版更新方式就是「下載新 zip 蓋上」，不做 app 內自動更新。
- 登入自動啟動沿用現有機制（匣選單勾選 → Startup 資料夾的 .vbs），內容改指向 exe 的絕對路徑；行為與今天一致：搬資料夾後要重新勾一次；現有所有權檢查（內容不同即拒改）不變。
- Claude Code companion 完整支援（與原始碼版行為一致）：`~/.claude/settings.json` 內嵌腳本絕對路徑，exe 搬家後 companion 失效，到 Dashboard 重裝一次即可（與自動啟動「搬家重勾」同類）。

## 實作選擇（由實作者提出）

- PyInstaller，windowed（無 console）「一資料夾」模式。單檔模式每次啟動要解壓暫存目錄、較慢且較易被防毒誤判。exe 進入點直接進 tray（等同 `stream-micro tray`）；`serve --reload` 不需在凍結環境支援。
- 以 `sys.frozen` 區分凍結環境；相關分支的解析根目錄以參數注入，測試不必真的打包 exe 即可驗證行為。
- 程式改動 ①：`server.py` 的 dashboard_dir 在凍結環境讀打包進 exe 的 frontend/dist 靜態檔；原始碼環境維持現行 `parents[2]` 解析不變。
- 程式改動 ②：`tray.py` 與 `windows_startup.py` 的 StartupRegistration 在凍結環境指向 `sys.executable`（exe 本身，不需參數）、跳過 pythonw 存在性檢查，失敗訊息不再叫使用者跑 uv sync；原始碼環境維持 pythonw 不變。
- 程式改動 ③：CLI 的 `--bridge-config` 預設路徑在凍結環境解析成「exe 同資料夾」，不隨當前工作目錄。
- 程式改動 ④：`claude_setup.py` 的 INTEGRATION_DIR 在凍結環境解析成打包進 exe 的 integrations 腳本；`device.py` 的 hidapi.dll 搜尋路徑在凍結環境指向 exe 同資料夾，沿用現有 `add_dll_directory` 機制。
- 打包內容：hidapi.dll、streamdeck 套件資料、frontend/dist 靜態檔、integrations/claude-code-stream-micro 腳本與 plugin 檔案、Pillow／pystray／hid 等依賴；exe 圖示沿用系統匣圖示的 Pillow 畫法，在建置腳本輸出多尺寸 .ico。
- exe 名稱 `StreamMicro.exe`；版號取自 pyproject.toml；zip 名 `stream-micro-<version>-win64.zip`，內容只有打包資料夾。
- 建置流程一個腳本：frontend `npm ci && npm run build` → PyInstaller → zip。原始碼使用流程（serve、dev、start.vbs）完全不變；start.vbs／start.bat 不放進 zip。

## 驗收

- exe 能從含空白或中文的路徑正確啟動；系統匣、Deck、Dashboard 行為與原始碼模式一致；單實例檢查仍以 bridge.yaml 路徑 key 運作。
- 第一次啟動後，`bridge.yaml`、`.state/`、`bridge.log` 生成在 exe 同資料夾。
- 新 zip 解壓蓋到同資料夾，設定與 `.state` 不受影響，程式更新。
- 勾選登入自動啟動後登出再登入，系統匣自動出現；搬資料夾後手動啟動正常，自動啟動項需重新勾選（與今天行為一致）。
- 在有 Claude Code 與 Node 的機器上，exe 啟用 Claude Code 整合後 companion 正常回報（hook 與狀態列）；exe 搬家後 Dashboard 能顯示 companion 需重裝並一鍵修復。
- `uv run pytest`、`ruff check`、`pyright` 全數通過；打包不得破壞原始碼開發流程。

## 不在範圍內

App 內自動更新器（檢查新版、下載、換檔）、程式碼簽章、安裝器、防毒誤判處理、孤兒 Startup 項目清理、凍結環境的 `serve --reload`、非 Windows 平台。

## 留給實作者

- PyInstaller spec 細節（hidden imports、exclude 清單），以實際打包一次的錯誤調整。
- .ico 的生成細節與建置腳本放根目錄或 scripts/。
- 自動啟動 .vbs 在凍結環境的內容細節（無參數直接執行 exe）。