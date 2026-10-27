# 語言設定進 config 與 i18n 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 語言選擇存進 `bridge.yaml`，系統匣選單與狀態提示、CLI 訊息跟隨語言出字，README 提供中英雙語。

**Architecture:** config 頂層新增 `language`（`"auto" | "en" | "zh-TW"`，預設 `"auto"`）。後端新增 i18n 模組（英文原文為鍵 + zh-TW 字典），系統匣每 2 秒重讀 config 自動跟上語言變更；前端切換語言時以已儲存的 config（savedConfig）為基底立即 PUT，不動草稿。

**Tech Stack:** Python 3.10+ / FastAPI / pystray / pytest；React 19 + TypeScript + Vite；Markdown。

**Spec:** `docs/superpowers/specs/2026-10-27-i18n-language-config-design.md`（計畫以其為據，執行者兩份都讀）

## Global Constraints

- `language` 值只能是 `"auto" | "en" | "zh-TW"`，預設 `"auto"`；非法值報 `ControlConfigError("language must be one of: auto, en, zh-TW")`。
- `schema_version` 維持 1；舊 `bridge.yaml` 缺 `language` 一律解析為 `"auto"`。
- 後端 i18n 不引入新依賴（無 gettext/babel）。
- 專有名詞不翻譯：Stream Micro、Bridge、Dashboard、Stream Deck MK.2、OpenCode、Codex、Claude Code、Antigravity CLI、OpenChamber、`bridge.log`、`bridge.yaml`、uv、npm。
- argparse help/usage、log 檔內容、API 錯誤 detail 維持英文。
- Stream Deck 按鍵狀態縮寫（RUN/SUB/WAIT/DONE/ERR/IDLE/OFF）不翻譯。
- zh-TR 文案一律台灣慣用繁體中文（不用簡體字、中國大陸用語）。
- 測試跑法：`uv run pytest <file> -v`；前端驗證：`frontend/` 下 `npm run build`；commit 風格 conventional commits（feat:/fix:/docs:，英文）。

## Review Focus

1. **舊 `bridge.yaml`（無 `language` 欄位）**：解析不報錯且預設 `"auto"`，既有使用者升級零影響 → Task 1 測試釘住。
2. **`language` 值非法（手改 yaml 打錯）**：整份 config 載入報 `ControlConfigError`（與其他欄位同等的嚴格驗證），不得靜默吞掉 → Task 1 測試釘住。
3. **`bridge.yaml` 缺檔/壞檔時 tray 與 CLI 讀語言**：`language_from_config` 回 `"auto"`，不得拋例外 → Task 2 測試釘住。
4. **系統匣語言切換後的 dedup**：`_last_status` 在語言變更時必須重置，否則 `check_health` 不會重送新語言的 title → Task 4 測試釘住。
5. **前端有未存草稿時切語言**：PUT 以 savedConfig 為基底，草稿不外送、dirty 不變、之後 Save 不會把 language 蓋回舊值 → Task 5 手動驗證步驟釘住。

---

### Task 1: Config 頂層 `language` 欄位

**Files:**
- Modify: `src/stream_micro/control_config.py`（`ControlConfig` dataclass、`parse_control_config`、`validate_control_config`）
- Test: `tests/test_control_config.py`、`tests/test_server.py`（config round-trip 帶 `language`）

**Interfaces:**
- Produces: `ControlConfig.language: str`（dataclass 欄位，預設 `"auto"`，放在 `session_window_bindings` 之後）；`parse_control_config` 接受頂層 `"language"` 鍵；`control_config_data()`（asdict）自動序列化它。後續所有任務依賴此欄位存在，前端 `GET /api/config` 也因此回傳它。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_control_config.py` 以 `control_config_data(default_control_config())` 回傳的 dict 為基底建測試資料（round-trip 語意最乾淨）：

```python
def test_language_accepts_valid_values():
    base = control_config_data(default_control_config())
    for value in ("auto", "en", "zh-TW"):
        data = {**base, "language": value}
        assert parse_control_config(data).language == value

def test_language_defaults_to_auto_when_missing():
    base = control_config_data(default_control_config())
    base.pop("language")
    assert parse_control_config(base).language == "auto"

def test_language_rejects_invalid_value():
    base = control_config_data(default_control_config())
    with pytest.raises(ControlConfigError, match="language must be one of: auto, en, zh-TW"):
        parse_control_config({**base, "language": "fr"})

def test_language_round_trips_through_serialization():
    config = replace(default_control_config(), language="zh-TW")
    assert parse_control_config(control_config_data(config)).language == "zh-TW"
```

另在 `tests/test_server.py` 沿既有 client 模式（fixture 同其他測試）加：

```python
def test_config_round_trip_preserves_language(tmp_path):
    service = BridgeService(tmp_path / "bridge.yaml")
    app = create_app(str(tmp_path / "bridge.yaml"), service=service, close_service=False)
    client = TestClient(app)
    data = client.get("/api/config").json()
    data["language"] = "zh-TW"
    assert client.put("/api/config", json=data).status_code == 200
    assert client.get("/api/config").json()["language"] == "zh-TW"
```

（client/fixture 的確切寫法照該檔既有測試；斷言重點是 PUT 後再 GET `language` 仍是 `"zh-TW"`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_control_config.py tests/test_server.py -v`
Expected: FAIL（`ControlConfig` 沒有 `language`；`parse_control_config` 報 Unknown configuration section: language）

- [ ] **Step 3: Implement in `src/stream_micro/control_config.py`**

三處：dataclass 加 `language: str = "auto"`（放 `session_window_bindings` 之後，維持「有預設值的欄位在後」）；`parse_control_config` 的 `allowed` 集合加 `"language"`，建構時 `language=data.get("language", "auto")`；`validate_control_config` 加值檢查（不在三選一就 raise，訊息如 Global Constraints）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_control_config.py tests/test_server.py -v`
Expected: PASS（含既有測試）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/control_config.py tests/test_control_config.py tests/test_server.py
git commit -m "feat: add language setting to Bridge configuration"
```

---

### Task 2: 後端 i18n 模組

**Files:**
- Create: `src/stream_micro/i18n.py`
- Create: `tests/test_i18n.py`

**Interfaces:**
- Produces（Task 3、Task 4 消費）：
  - `t(text: str, language: str) -> str` — `language` 是已解析的 `"en" | "zh-TW"`；查無鍵回傳原文。
  - `resolve_language(language: str, *, ui_language: int | None = None) -> str` — 明確值原樣回傳；`"auto"`：有注入 `ui_language` 用它，否則用 `windows_ui_language()`；zh-Hant LANGID → `"zh-TW"`，其餘 → `"en"`。
  - `windows_ui_language() -> int | None` — Windows 用 `ctypes.windll.kernel32.GetUserDefaultUILanguage()`；非 Windows 或任何例外回 `None`（測試用 monkeypatch 覆寫，不碰真機）。
  - `language_from_config(config_path: Path) -> str` — `load_control_config(path).language`；任何例外（`ControlConfigError`、`OSError`、YAML）回 `"auto"`。

- [ ] **Step 1: Write the failing tests**

```python
def test_t_returns_translation_and_falls_back():
    assert t("Bridge running", "zh-TW") == "Bridge 執行中"
    assert t("Bridge running", "en") == "Bridge running"
    assert t("not in dictionary", "zh-TW") == "not in dictionary"

def test_resolve_language_explicit_passthrough():
    assert resolve_language("en") == "en"
    assert resolve_language("zh-TW") == "zh-TW"

def test_resolve_language_auto_uses_zh_hant_langids():
    for langid in (0x0404, 0x0C04, 0x1404):  # zh-TW, zh-HK, zh-MO
        assert resolve_language("auto", ui_language=langid) == "zh-TW"
    for langid in (0x0409, 0x0804):  # en-US, zh-CN
        assert resolve_language("auto", ui_language=langid) == "en"

def test_resolve_language_auto_without_detection_is_english(monkeypatch):
    monkeypatch.setattr(i18n, "windows_ui_language", lambda: None)
    assert resolve_language("auto") == "en"

def test_language_from_config_reads_and_falls_back(tmp_path):
    config = replace(default_control_config(), language="zh-TW")
    path = tmp_path / "bridge.yaml"
    write_control_config(path, config)
    assert language_from_config(path) == "zh-TW"
    assert language_from_config(tmp_path / "missing.yaml") == "auto"
    bad = tmp_path / "bad.yaml"
    bad.write_text("{not: valid: yaml", encoding="utf-8")
    assert language_from_config(bad) == "auto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: FAIL（模組不存在）

- [ ] **Step 3: Implement `src/stream_micro/i18n.py`**

結構照 `frontend/src/i18n.ts` 的做法：英文原文為鍵的 `ZH_TW: dict[str, str]`、`t()` 查字典。zh-TW 文案如下（選單五條沿用現行寫死的繁中，其餘為本計畫定稿）：

```python
ZH_TW = {
    "Open Dashboard": "開啟 Dashboard",
    "Restart Bridge": "重新啟動 Bridge",
    "Stop Bridge": "停止 Bridge",
    "Run at log in": "登入自動啟動",
    "Exit": "結束",
    "Starting": "啟動中",
    "Stopping Bridge": "正在停止 Bridge",
    "Starting Bridge": "正在啟動 Bridge",
    "Bridge stopped": "Bridge 已停止",
    "Bridge running": "Bridge 執行中",
    "Bridge stopped or not responding": "Bridge 已停止或沒有回應",
    "Operation failed; see bridge.log": "操作失敗；請查看 bridge.log",
    "created Bridge configuration at {path}": "已建立 Bridge 設定檔：{path}",
    "stopped": "已停止",
    "Stream Micro could not start. Check bridge.log and run uv sync --dev in the project folder, then try again.":
        "Stream Micro 無法啟動。請查看 bridge.log，在專案資料夾執行 uv sync --dev 後再試一次。",
}
```

`resolve_language` 的 LANGID 對照：`0x0404/0x0C04/0x1404` → `"zh-TW"`，其餘 `"en"`。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/i18n.py tests/test_i18n.py
git commit -m "feat: add backend i18n for tray and CLI strings"
```

---

### Task 3: CLI 訊息本地化

**Files:**
- Modify: `src/stream_micro/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `t`、`resolve_language`、`language_from_config`（Task 2）。`cli.py` 以 `from .i18n import ...` 匯入，測試以 `monkeypatch.setattr("stream_micro.cli.resolve_language", ...)` 固定語言（不依賴測試機的 Windows locale）。

- [ ] **Step 1: Write the failing tests**

沿用 `tests/test_cli.py` 既有模式（monkeypatch `stream_micro.runtime.BridgeRuntime`，見 `test_serve_creates_separate_bridge_configuration`）加 `capsys`：

```python
def test_created_config_message_follows_language(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "zh-TW")
    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: 已建立 Bridge 設定檔：" in capsys.readouterr().out

def test_created_config_message_stays_english_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "en")
    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: created Bridge configuration at" in capsys.readouterr().out
```

（兩個測試的 monkeypatch lambda 都收 `**_kw`，簽名對上 Task 2 的 keyword-only `ui_language`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL（訊息仍是英文）

- [ ] **Step 3: Implement in `src/stream_micro/cli.py`**

- `main()` 的 serve/tray 分支前：`lang = resolve_language(language_from_config(args.bridge_config))`；`--demo` 與 legacy `run` 分支不動。
- 建檔訊息：`print(f"stream-micro: {t('created Bridge configuration at {path}', lang).format(path=args.bridge_config)}")`。
- tray 啟動失敗的 MessageBoxW 內文改 `t("Stream Micro could not start. ...", lang)`（標題 `"Stream Micro"` 不譯）。
- `except KeyboardInterrupt`：`print(f"stream-micro: {t('stopped', resolve_language(language_from_config(args.bridge_config)))}")`（此分支可能早於 `lang` 定義，直接重算）。
- `except (ConfigError, ...)` 的 `stream-micro: {exc}` 行不譯（exc 內容是開發者導向）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS（含既有兩個測試）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/cli.py tests/test_cli.py
git commit -m "feat: localize CLI messages"
```

---

### Task 4: 系統匣本地化與自動跟隨語言

**Files:**
- Modify: `src/stream_micro/tray.py`
- Test: `tests/test_tray.py`

**Interfaces:**
- Consumes: `t`、`resolve_language`、`language_from_config`（Task 2）。
- Produces: `TrayController.__init__(self, runtime, present, open_url, *, probe=probe_bridge, language="en", language_provider=None, on_language=None)`（新參數皆 keyword-only、有預設，既有測試建構式不破）；屬性 `language: str`（已解析值）；`set_language(language: str) -> None`（變更時更新並把 `_last_status` 重置為 None）。`main()` 傳入以 config 檔為來源的 provider。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_tray.py` 沿用既有 `Runtime` fake 與 views-list 模式：

```python
def test_status_strings_follow_language():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: False, language="zh-TW")
    controller.handle("stop")
    assert views[-1] == ("red", "Bridge 已停止")
    controller.set_language("en")
    controller.check_health()
    assert views[-1] == ("red", "Bridge stopped or not responding")

def test_language_change_represents_unchanged_status():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: True, language="en")
    controller.handle("restart")  # ends green "Bridge running"
    seen = len(views)
    controller.set_language("zh-TW")
    controller.check_health()  # 狀態沒變，但語言變了 → 必須重送
    assert views[seen] == ("green", "Bridge 執行中")

def test_run_loop_follows_language_provider():
    runtime = Runtime()
    languages = []
    current = {"lang": "en"}
    controller = tray.TrayController(runtime, lambda *_: None, lambda _: None,
                                     probe=lambda _url: True,
                                     language="en",
                                     language_provider=lambda: current["lang"],
                                     on_language=languages.append)
    controller.commands.put("exit")
    current["lang"] = "zh-TW"
    controller.run(lambda: None)
    assert controller.language == "zh-TW"
    assert languages == ["zh-TW"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tray.py -v`
Expected: FAIL（`TrayController` 無 `language` 參數）

- [ ] **Step 3: Implement in `src/stream_micro/tray.py`**

- `handle`／`check_health` 的字串全部改 `t(text, self.language)`：Starting、Stopping Bridge、Starting Bridge、Bridge stopped、Bridge running、Bridge stopped or not responding、Operation failed; see bridge.log（鍵同 Task 2 字典）。
- `set_language`：不同才動作；更新 `self.language`、`self._last_status = None`。
- `run()` 每圈循環開頭：`if self.language_provider is not None: self.set_language(self.language_provider())`，`set_language` 內變更時呼叫 `on_language(new)`（有的話）。
- `main()`：`controller.language = resolve_language(language_from_config(config_path))`；`language_provider = lambda: resolve_language(language_from_config(config_path))`、`on_language` 呼叫 `icon.update_menu()`。
- 選單：MenuItem 的 text 改 callable——`pystray.MenuItem(lambda _item: t("Open Dashboard", controller.language), enqueue("open"), default=True)` 等五項（含 `checked` 的登入自動啟動），`icon.update_menu()` 會以新語言重繪。
- icon 初始 title 的 `"Stream Micro: Starting"`：text 部分改 `t("Starting", controller.language)`；前綴 `"Stream Micro: "` 不譯。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tray.py -v`
Expected: PASS（含既有全部測試——dedup、restart/stop/exit、probe 例外等）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/tray.py tests/test_tray.py
git commit -m "feat: localize tray menu and status with live language follow"
```

---

### Task 5: 前端 config 驅動語言與切換即存

**Files:**
- Modify: `frontend/src/main.tsx`（`type Config`、`switchLang`、config 載入後套用語言）
- Modify: `frontend/src/i18n.ts`（無需改動；沿用 `setLang`/`getLang`）

**Interfaces:**
- Consumes: `GET /api/config` 回傳含 `language`（Task 1）；既有 `api<T>(path, options)` helper 與 `setLang`/`getLang`。
- Produces: `type Config` 增 `language?: "auto" | "en" | "zh-TW"`。

（本 repo 前端無單元測試框架（`npm test` = `npm run build`），本任務以 tsc 建置與手動驗證代替 failing-test 循環。）

- [ ] **Step 1: Implement in `frontend/src/main.tsx`**

- `type Config` 加 `language?: "auto" | "en" | "zh-TW"`。
- `switchLang` 改 async：

```tsx
const switchLang = async () => {
  const next: Lang = lang === "en" ? "zh-TW" : "en";
  setLang(next);
  setLangState(next);
  if (!savedConfig) return;
  try {
    const saved = await api<Config>("config", { method: "PUT", body: JSON.stringify({ ...savedConfig, language: next }) });
    setSavedConfig(saved);
    setConfig((prev) => (prev ? { ...prev, language: next } : prev));
  } catch (error) {
    setNotice(error instanceof Error ? error.message : t("Could not save configuration"));
  }
};
```

（注意：PUT 以 `savedConfig` 為基底——草稿不外送；成功後草稿的 `language` 也更新，之後 Save 不會蓋回舊值；`dirty` 狀態完全不動。）

- config 載入處（`refresh` 中 `setConfig` 的位置）：`if (nextConfig.language === "en" || nextConfig.language === "zh-TW") { if (getLang() !== nextConfig.language) { setLang(nextConfig.language); setLangState(nextConfig.language); } }`。`"auto"` 不覆蓋 `initialLang` 的結果（維持現行 localStorage → 瀏覽器偵測）。
- `switchLang` 的 onClick 改 `() => void switchLang()`。

- [ ] **Step 2: Build to verify**

Run（在 `frontend/`）: `npm run build`
Expected: tsc 與 vite build 通過，無型別錯誤

- [ ] **Step 3: Manual verification（有真機時）**

1. `uv run stream-micro serve`，開 Dashboard，切到某服務改一個值（不按 Save）。
2. 切語言 → UI 即時換語言、未存提示仍在（dirty 未動）、重新整理後語言維持新值、`bridge.yaml` 的 `language` 已更新。
3. 回 Dashboard 按存下的 Save → 其他修改套用且 `language` 未被蓋回。
4. 系統匣選單與提示在 ~2 秒內跟著換語言。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat: persist Dashboard language in Bridge configuration"
```

---

### Task 6: README 雙語

**Files:**
- Modify: `README.md`（副標行下加語言切換行）
- Create: `README.zh-TW.md`

**Interfaces:**
- Consumes: 無（獨立任務，但 zh-TW 文案語彙須與 Task 2 字典一致——Bridge、Dashboard 等專有名詞不譯）。

- [ ] **Step 1: Add the language switcher to `README.md`**

在副標（`**Your AI sessions and usage, at a glance on your Stream Deck.**`）下方空一行加：

```markdown
English | [繁體中文](README.zh-TW.md)
```

- [ ] **Step 2: Create `README.zh-TW.md`**

- 開頭：`# stream-micro`（產品名不譯）、繁中副標、切換行 `[English](README.md) | 繁體中文`。
- 逐節對應翻譯 `README.md` 全部小節；圖片相對路徑、指令區塊、資料夾/檔名、連結目標原樣保留（從 repo root 相對路徑兩份相同）。
- 連往 `integrations/*/README.md` 的連結文字保留英文原字。
- 文案是台灣慣用繁體中文；行為描述以英文版為準，翻譯不得改動任何承諾（如「Windows only」的範圍）。
- README 既有「support for English and Traditional Chinese」的描述照翻。

- [ ] **Step 3: Verify links and images**

Run: `git diff --stat README.md` 與目視檢查 `README.zh-TW.md` 中所有相對連結與圖片路徑在 repo 中存在。
Expected: 兩份 README 的圖片路徑都指向 `docs/images/` 下既有檔案

- [ ] **Step 4: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: add Traditional Chinese README"
```

---

### Task 7: 全套驗證

**Files:**
- 無新變更（只驗證；若發現跨任務回歸，修在哪個任務的檔案就歸那個 commit）

**Interfaces:**
- Consumes: 全部任務。

- [ ] **Step 1: Run the full backend suite**

Run: `uv run pytest -v`
Expected: 全綠（注意 `tests/test_server.py` 既有的 config round-trip 測試——GET 回的 config 現在含 `language`，PUT 回去必須照樣 200）

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: 無發現

- [ ] **Step 3: Frontend build**

Run（在 `frontend/`）: `npm run build`
Expected: 通過

- [ ] **Step 4: 若全綠，工作完成；若有紅，修到綠再收**