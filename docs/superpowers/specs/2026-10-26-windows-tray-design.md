# Windows 系統匣常駐程式

## 已確認的需求

使用者已在對話確認以下第一版範圍：
- 背景執行，系統匣圖示，不留黑色命令列視窗；重複啟動不多開 Bridge。
- 點圖示開 Dashboard，網址取自 bridge.yaml，不寫死 port。
- 綠色代表 Bridge 有回應；黃色代表啟動／重啟中；紅色代表停止或故障。滑鼠提示顯示文字狀態；不把外部 provider 故障等同 Bridge 故障。
- 右鍵選單：開啟 Dashboard、重新啟動 Bridge、停止 Bridge、登入自動啟動、結束。
- 停止 Bridge 保留圖示，重新啟動可恢復；結束則整組停止並釋放 Stream Deck。
- 只停止自己管理的 Bridge，不根據 port 終止未知進程。
- 保留日誌；serve 保留作為開發入口。
- 第一版不自動故障重啟，不安裝 Windows Service，不管理或重啟 OpenCode server。

## 實作選擇（由實作者提出）

沿用 Python、Pillow，加入僅限 Windows 的 pystray 依賴。系統匣主執行緒管理同一進程內的 Uvicorn 與 Deck 執行緒，避免留下子進程。將既有 serve 的 runtime 抽成可明確 start/stop 的單元；Deck loop 必須支援中斷並在 service.close 前退出。

Windows named mutex 防止同一使用者同一份 config 重複啟動，重複啟動僅開既有 Dashboard；非本程式佔用 port 時顯示故障，不接管。健康檢查須結合自有 runtime 存活狀態與輕量 HTTP 回應，避免把他人的服務判定為成功。

系統匣 callback 僅排入序列化命令，不阻塞原生 UI thread；啟動／停止／重啟不可同時操作硬體。stop 先要求 Deck loop 停止並喚醒等待，等其釋放硬體後關閉服務。若操作逾時，顯示故障並禁止啟動第二份 runtime；不可假裝停止成功。

登入自啟用目前使用者 Startup 目錄的專用 launcher，不需管理員權限，預設關閉，僅切換選單時寫入。路徑必須完整引用並支援空白及中文字；不得覆寫其他程式的項目。

start.vbs 改為使用 .venv 的 pythonw 啟動 tray。保留 start.bat 作為有 console 的入口，但移除依 port 強殺行為；不在隱藏啟動中安裝依賴或編譯前端。部署前依 README 執行 uv sync 及 frontend build。

日誌使用 Python RotatingFileHandler，bridge.log，5 MiB，保留一份備份；啟動錯誤可診斷。圖示以 Pillow 畫 MK.2 5×3 按鍵意象。Windows 對單擊／雙擊的實際支援需人工驗證；採 pystray 預設動作開啟 Dashboard，若原生後端只提供雙擊須明確記錄，不偷偷改變承諾。

## 不在範圍內

打包 exe、Windows Service、背景 watchdog、自動故障重啟、外部 Bridge 接管、遠端管理、修改 OpenChamber 或其 OpenCode server。Windows 可能將圖示收進 ^ 選單，程式不強行更改 Windows 偏好。
