# èªžè¨€è¨­å®šé€² config èˆ‡ i18n å¯¦ä½œè¨ˆç•«

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** èªžè¨€é¸æ“‡å­˜é€² `bridge.yaml`ï¼Œç³»çµ±åŒ£é¸å–®èˆ‡ç‹€æ…‹æç¤ºã€CLI è¨Šæ¯è·Ÿéš¨èªžè¨€å‡ºå­—ï¼ŒREADME æä¾›ä¸­è‹±é›™èªžã€‚

**Architecture:** config é ‚å±¤æ–°å¢ž `language`ï¼ˆ`"auto" | "en" | "zh-TW"`ï¼Œé è¨­ `"auto"`ï¼‰ã€‚å¾Œç«¯æ–°å¢ž i18n æ¨¡çµ„ï¼ˆè‹±æ–‡åŽŸæ–‡ç‚ºéµ + zh-TW å­—å…¸ï¼‰ï¼Œç³»çµ±åŒ£æ¯ 2 ç§’é‡è®€ config è‡ªå‹•è·Ÿä¸Šèªžè¨€è®Šæ›´ï¼›å‰ç«¯åˆ‡æ›èªžè¨€æ™‚ä»¥å·²å„²å­˜çš„ configï¼ˆsavedConfigï¼‰ç‚ºåŸºåº•ç«‹å³ PUTï¼Œä¸å‹•è‰ç¨¿ã€‚

**Tech Stack:** Python 3.10+ / FastAPI / pystray / pytestï¼›React 19 + TypeScript + Viteï¼›Markdownã€‚

**Spec:** `docs/superpowers/specs/2026-10-27-i18n-language-config-design.md`ï¼ˆè¨ˆç•«ä»¥å…¶ç‚ºæ“šï¼ŒåŸ·è¡Œè€…å…©ä»½éƒ½è®€ï¼‰

## Global Constraints

- `language` å€¼åªèƒ½æ˜¯ `"auto" | "en" | "zh-TW"`ï¼Œé è¨­ `"auto"`ï¼›éžæ³•å€¼å ± `ControlConfigError("language must be one of: auto, en, zh-TW")`ã€‚
- `schema_version` ç¶­æŒ 1ï¼›èˆŠ `bridge.yaml` ç¼º `language` ä¸€å¾‹è§£æžç‚º `"auto"`ã€‚
- å¾Œç«¯ i18n ä¸å¼•å…¥æ–°ä¾è³´ï¼ˆç„¡ gettext/babelï¼‰ã€‚
- å°ˆæœ‰åè©žä¸ç¿»è­¯ï¼šStream Microã€Bridgeã€Dashboardã€Stream Deck MK.2ã€OpenCodeã€Codexã€Claude Codeã€Antigravity CLIã€OpenChamberã€`bridge.log`ã€`bridge.yaml`ã€uvã€npmã€‚
- argparse help/usageã€log æª”å…§å®¹ã€API éŒ¯èª¤ detail ç¶­æŒè‹±æ–‡ã€‚
- Stream Deck æŒ‰éµç‹€æ…‹ç¸®å¯«ï¼ˆRUN/SUB/WAIT/DONE/ERR/IDLE/OFFï¼‰ä¸ç¿»è­¯ã€‚
- zh-TR æ–‡æ¡ˆä¸€å¾‹å°ç£æ…£ç”¨ç¹é«”ä¸­æ–‡ï¼ˆä¸ç”¨ç°¡é«”å­—ã€ä¸­åœ‹å¤§é™¸ç”¨èªžï¼‰ã€‚
- æ¸¬è©¦è·‘æ³•ï¼š`uv run pytest <file> -v`ï¼›å‰ç«¯é©—è­‰ï¼š`frontend/` ä¸‹ `npm run build`ï¼›commit é¢¨æ ¼ conventional commitsï¼ˆfeat:/fix:/docs:ï¼Œè‹±æ–‡ï¼‰ã€‚

## Review Focus

1. **èˆŠ `bridge.yaml`ï¼ˆç„¡ `language` æ¬„ä½ï¼‰**ï¼šè§£æžä¸å ±éŒ¯ä¸”é è¨­ `"auto"`ï¼Œæ—¢æœ‰ä½¿ç”¨è€…å‡ç´šé›¶å½±éŸ¿ â†’ Task 1 æ¸¬è©¦é‡˜ä½ã€‚
2. **`language` å€¼éžæ³•ï¼ˆæ‰‹æ”¹ yaml æ‰“éŒ¯ï¼‰**ï¼šæ•´ä»½ config è¼‰å…¥å ± `ControlConfigError`ï¼ˆèˆ‡å…¶ä»–æ¬„ä½åŒç­‰çš„åš´æ ¼é©—è­‰ï¼‰ï¼Œä¸å¾—éœé»˜åžæŽ‰ â†’ Task 1 æ¸¬è©¦é‡˜ä½ã€‚
3. **`bridge.yaml` ç¼ºæª”/å£žæª”æ™‚ tray èˆ‡ CLI è®€èªžè¨€**ï¼š`language_from_config` å›ž `"auto"`ï¼Œä¸å¾—æ‹‹ä¾‹å¤– â†’ Task 2 æ¸¬è©¦é‡˜ä½ã€‚
4. **ç³»çµ±åŒ£èªžè¨€åˆ‡æ›å¾Œçš„ dedup**ï¼š`_last_status` åœ¨èªžè¨€è®Šæ›´æ™‚å¿…é ˆé‡ç½®ï¼Œå¦å‰‡ `check_health` ä¸æœƒé‡é€æ–°èªžè¨€çš„ title â†’ Task 4 æ¸¬è©¦é‡˜ä½ã€‚
5. **å‰ç«¯æœ‰æœªå­˜è‰ç¨¿æ™‚åˆ‡èªžè¨€**ï¼šPUT ä»¥ savedConfig ç‚ºåŸºåº•ï¼Œè‰ç¨¿ä¸å¤–é€ã€dirty ä¸è®Šã€ä¹‹å¾Œ Save ä¸æœƒæŠŠ language è“‹å›žèˆŠå€¼ â†’ Task 5 æ‰‹å‹•é©—è­‰æ­¥é©Ÿé‡˜ä½ã€‚

---

### Task 1: Config é ‚å±¤ `language` æ¬„ä½

**Files:**
- Modify: `src/stream_micro/control_config.py`ï¼ˆ`ControlConfig` dataclassã€`parse_control_config`ã€`validate_control_config`ï¼‰
- Test: `tests/test_control_config.py`ã€`tests/test_server.py`ï¼ˆconfig round-trip å¸¶ `language`ï¼‰

**Interfaces:**
- Produces: `ControlConfig.language: str`ï¼ˆdataclass æ¬„ä½ï¼Œé è¨­ `"auto"`ï¼Œæ”¾åœ¨ `session_window_bindings` ä¹‹å¾Œï¼‰ï¼›`parse_control_config` æŽ¥å—é ‚å±¤ `"language"` éµï¼›`control_config_data()`ï¼ˆasdictï¼‰è‡ªå‹•åºåˆ—åŒ–å®ƒã€‚å¾ŒçºŒæ‰€æœ‰ä»»å‹™ä¾è³´æ­¤æ¬„ä½å­˜åœ¨ï¼Œå‰ç«¯ `GET /api/config` ä¹Ÿå› æ­¤å›žå‚³å®ƒã€‚

- [x] **Step 1: Write the failing tests**

åœ¨ `tests/test_control_config.py` ä»¥ `control_config_data(default_control_config())` å›žå‚³çš„ dict ç‚ºåŸºåº•å»ºæ¸¬è©¦è³‡æ–™ï¼ˆround-trip èªžæ„æœ€ä¹¾æ·¨ï¼‰ï¼š

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

å¦åœ¨ `tests/test_server.py` æ²¿æ—¢æœ‰ client æ¨¡å¼ï¼ˆfixture åŒå…¶ä»–æ¸¬è©¦ï¼‰åŠ ï¼š

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

ï¼ˆclient/fixture çš„ç¢ºåˆ‡å¯«æ³•ç…§è©²æª”æ—¢æœ‰æ¸¬è©¦ï¼›æ–·è¨€é‡é»žæ˜¯ PUT å¾Œå† GET `language` ä»æ˜¯ `"zh-TW"`ã€‚ï¼‰

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_control_config.py tests/test_server.py -v`
Expected: FAILï¼ˆ`ControlConfig` æ²’æœ‰ `language`ï¼›`parse_control_config` å ± Unknown configuration section: languageï¼‰

- [x] **Step 3: Implement in `src/stream_micro/control_config.py`**

ä¸‰è™•ï¼šdataclass åŠ  `language: str = "auto"`ï¼ˆæ”¾ `session_window_bindings` ä¹‹å¾Œï¼Œç¶­æŒã€Œæœ‰é è¨­å€¼çš„æ¬„ä½åœ¨å¾Œã€ï¼‰ï¼›`parse_control_config` çš„ `allowed` é›†åˆåŠ  `"language"`ï¼Œå»ºæ§‹æ™‚ `language=data.get("language", "auto")`ï¼›`validate_control_config` åŠ å€¼æª¢æŸ¥ï¼ˆä¸åœ¨ä¸‰é¸ä¸€å°± raiseï¼Œè¨Šæ¯å¦‚ Global Constraintsï¼‰ã€‚

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_control_config.py tests/test_server.py -v`
Expected: PASSï¼ˆå«æ—¢æœ‰æ¸¬è©¦ï¼‰

- [x] **Step 5: Commit**

```bash
git add src/stream_micro/control_config.py tests/test_control_config.py tests/test_server.py
git commit -m "feat: add language setting to Bridge configuration"
```

---

### Task 2: å¾Œç«¯ i18n æ¨¡çµ„

**Files:**
- Create: `src/stream_micro/i18n.py`
- Create: `tests/test_i18n.py`

**Interfaces:**
- Producesï¼ˆTask 3ã€Task 4 æ¶ˆè²»ï¼‰ï¼š
  - `t(text: str, language: str) -> str` â€” `language` æ˜¯å·²è§£æžçš„ `"en" | "zh-TW"`ï¼›æŸ¥ç„¡éµå›žå‚³åŽŸæ–‡ã€‚
  - `resolve_language(language: str, *, ui_language: int | None = None) -> str` â€” æ˜Žç¢ºå€¼åŽŸæ¨£å›žå‚³ï¼›`"auto"`ï¼šæœ‰æ³¨å…¥ `ui_language` ç”¨å®ƒï¼Œå¦å‰‡ç”¨ `windows_ui_language()`ï¼›zh-Hant LANGID â†’ `"zh-TW"`ï¼Œå…¶é¤˜ â†’ `"en"`ã€‚
  - `windows_ui_language() -> int | None` â€” Windows ç”¨ `ctypes.windll.kernel32.GetUserDefaultUILanguage()`ï¼›éž Windows æˆ–ä»»ä½•ä¾‹å¤–å›ž `None`ï¼ˆæ¸¬è©¦ç”¨ monkeypatch è¦†å¯«ï¼Œä¸ç¢°çœŸæ©Ÿï¼‰ã€‚
  - `language_from_config(config_path: Path) -> str` â€” `load_control_config(path).language`ï¼›ä»»ä½•ä¾‹å¤–ï¼ˆ`ControlConfigError`ã€`OSError`ã€YAMLï¼‰å›ž `"auto"`ã€‚

- [x] **Step 1: Write the failing tests**

```python
def test_t_returns_translation_and_falls_back():
    assert t("Bridge running", "zh-TW") == "Bridge åŸ·è¡Œä¸­"
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

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: FAILï¼ˆæ¨¡çµ„ä¸å­˜åœ¨ï¼‰

- [x] **Step 3: Implement `src/stream_micro/i18n.py`**

çµæ§‹ç…§ `frontend/src/i18n.ts` çš„åšæ³•ï¼šè‹±æ–‡åŽŸæ–‡ç‚ºéµçš„ `ZH_TW: dict[str, str]`ã€`t()` æŸ¥å­—å…¸ã€‚zh-TW æ–‡æ¡ˆå¦‚ä¸‹ï¼ˆé¸å–®äº”æ¢æ²¿ç”¨ç¾è¡Œå¯«æ­»çš„ç¹ä¸­ï¼Œå…¶é¤˜ç‚ºæœ¬è¨ˆç•«å®šç¨¿ï¼‰ï¼š

```python
ZH_TW = {
    "Open Dashboard": "é–‹å•Ÿ Dashboard",
    "Restart Bridge": "é‡æ–°å•Ÿå‹• Bridge",
    "Stop Bridge": "åœæ­¢ Bridge",
    "Run at log in": "ç™»å…¥è‡ªå‹•å•Ÿå‹•",
    "Exit": "çµæŸ",
    "Starting": "å•Ÿå‹•ä¸­",
    "Stopping Bridge": "æ­£åœ¨åœæ­¢ Bridge",
    "Starting Bridge": "æ­£åœ¨å•Ÿå‹• Bridge",
    "Bridge stopped": "Bridge å·²åœæ­¢",
    "Bridge running": "Bridge åŸ·è¡Œä¸­",
    "Bridge stopped or not responding": "Bridge å·²åœæ­¢æˆ–æ²’æœ‰å›žæ‡‰",
    "Operation failed; see bridge.log": "æ“ä½œå¤±æ•—ï¼›è«‹æŸ¥çœ‹ bridge.log",
    "created Bridge configuration at {path}": "å·²å»ºç«‹ Bridge è¨­å®šæª”ï¼š{path}",
    "stopped": "å·²åœæ­¢",
    "Stream Micro could not start. Check bridge.log and run uv sync --dev in the project folder, then try again.":
        "Stream Micro ç„¡æ³•å•Ÿå‹•ã€‚è«‹æŸ¥çœ‹ bridge.logï¼Œåœ¨å°ˆæ¡ˆè³‡æ–™å¤¾åŸ·è¡Œ uv sync --dev å¾Œå†è©¦ä¸€æ¬¡ã€‚",
}
```

`resolve_language` çš„ LANGID å°ç…§ï¼š`0x0404/0x0C04/0x1404` â†’ `"zh-TW"`ï¼Œå…¶é¤˜ `"en"`ã€‚

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add src/stream_micro/i18n.py tests/test_i18n.py
git commit -m "feat: add backend i18n for tray and CLI strings"
```

---

### Task 3: CLI è¨Šæ¯æœ¬åœ°åŒ–

**Files:**
- Modify: `src/stream_micro/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `t`ã€`resolve_language`ã€`language_from_config`ï¼ˆTask 2ï¼‰ã€‚`cli.py` ä»¥ `from .i18n import ...` åŒ¯å…¥ï¼Œæ¸¬è©¦ä»¥ `monkeypatch.setattr("stream_micro.cli.resolve_language", ...)` å›ºå®šèªžè¨€ï¼ˆä¸ä¾è³´æ¸¬è©¦æ©Ÿçš„ Windows localeï¼‰ã€‚

- [x] **Step 1: Write the failing tests**

æ²¿ç”¨ `tests/test_cli.py` æ—¢æœ‰æ¨¡å¼ï¼ˆmonkeypatch `stream_micro.runtime.BridgeRuntime`ï¼Œè¦‹ `test_serve_creates_separate_bridge_configuration`ï¼‰åŠ  `capsys`ï¼š

```python
def test_created_config_message_follows_language(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "zh-TW")
    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: å·²å»ºç«‹ Bridge è¨­å®šæª”ï¼š" in capsys.readouterr().out

def test_created_config_message_stays_english_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "resolve_language", lambda language, **_kw: "en")
    assert cli.main(["serve", "--bridge-config", str(tmp_path / "bridge.yaml")]) == 0
    assert "stream-micro: created Bridge configuration at" in capsys.readouterr().out
```

ï¼ˆå…©å€‹æ¸¬è©¦çš„ monkeypatch lambda éƒ½æ”¶ `**_kw`ï¼Œç°½åå°ä¸Š Task 2 çš„ keyword-only `ui_language`ã€‚ï¼‰

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAILï¼ˆè¨Šæ¯ä»æ˜¯è‹±æ–‡ï¼‰

- [x] **Step 3: Implement in `src/stream_micro/cli.py`**

- `main()` çš„ serve/tray åˆ†æ”¯å‰ï¼š`lang = resolve_language(language_from_config(args.bridge_config))`ï¼›`--demo` èˆ‡ legacy `run` åˆ†æ”¯ä¸å‹•ã€‚
- å»ºæª”è¨Šæ¯ï¼š`print(f"stream-micro: {t('created Bridge configuration at {path}', lang).format(path=args.bridge_config)}")`ã€‚
- tray å•Ÿå‹•å¤±æ•—çš„ MessageBoxW å…§æ–‡æ”¹ `t("Stream Micro could not start. ...", lang)`ï¼ˆæ¨™é¡Œ `"Stream Micro"` ä¸è­¯ï¼‰ã€‚
- `except KeyboardInterrupt`ï¼š`print(f"stream-micro: {t('stopped', resolve_language(language_from_config(args.bridge_config)))}")`ï¼ˆæ­¤åˆ†æ”¯å¯èƒ½æ—©æ–¼ `lang` å®šç¾©ï¼Œç›´æŽ¥é‡ç®—ï¼‰ã€‚
- `except (ConfigError, ...)` çš„ `stream-micro: {exc}` è¡Œä¸è­¯ï¼ˆexc å…§å®¹æ˜¯é–‹ç™¼è€…å°Žå‘ï¼‰ã€‚

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASSï¼ˆå«æ—¢æœ‰å…©å€‹æ¸¬è©¦ï¼‰

- [x] **Step 5: Commit**

```bash
git add src/stream_micro/cli.py tests/test_cli.py
git commit -m "feat: localize CLI messages"
```

---

### Task 4: ç³»çµ±åŒ£æœ¬åœ°åŒ–èˆ‡è‡ªå‹•è·Ÿéš¨èªžè¨€

**Files:**
- Modify: `src/stream_micro/tray.py`
- Test: `tests/test_tray.py`

**Interfaces:**
- Consumes: `t`ã€`resolve_language`ã€`language_from_config`ï¼ˆTask 2ï¼‰ã€‚
- Produces: `TrayController.__init__(self, runtime, present, open_url, *, probe=probe_bridge, language="en", language_provider=None, on_language=None)`ï¼ˆæ–°åƒæ•¸çš† keyword-onlyã€æœ‰é è¨­ï¼Œæ—¢æœ‰æ¸¬è©¦å»ºæ§‹å¼ä¸ç ´ï¼‰ï¼›å±¬æ€§ `language: str`ï¼ˆå·²è§£æžå€¼ï¼‰ï¼›`set_language(language: str) -> None`ï¼ˆè®Šæ›´æ™‚æ›´æ–°ä¸¦æŠŠ `_last_status` é‡ç½®ç‚º Noneï¼‰ã€‚`main()` å‚³å…¥ä»¥ config æª”ç‚ºä¾†æºçš„ providerã€‚

- [x] **Step 1: Write the failing tests**

åœ¨ `tests/test_tray.py` æ²¿ç”¨æ—¢æœ‰ `Runtime` fake èˆ‡ views-list æ¨¡å¼ï¼š

```python
def test_status_strings_follow_language():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: False, language="zh-TW")
    controller.handle("stop")
    assert views[-1] == ("red", "Bridge å·²åœæ­¢")
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
    controller.check_health()  # ç‹€æ…‹æ²’è®Šï¼Œä½†èªžè¨€è®Šäº† â†’ å¿…é ˆé‡é€
    assert views[seen] == ("green", "Bridge åŸ·è¡Œä¸­")

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

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tray.py -v`
Expected: FAILï¼ˆ`TrayController` ç„¡ `language` åƒæ•¸ï¼‰

- [x] **Step 3: Implement in `src/stream_micro/tray.py`**

- `handle`ï¼`check_health` çš„å­—ä¸²å…¨éƒ¨æ”¹ `t(text, self.language)`ï¼šStartingã€Stopping Bridgeã€Starting Bridgeã€Bridge stoppedã€Bridge runningã€Bridge stopped or not respondingã€Operation failed; see bridge.logï¼ˆéµåŒ Task 2 å­—å…¸ï¼‰ã€‚
- `set_language`ï¼šä¸åŒæ‰å‹•ä½œï¼›æ›´æ–° `self.language`ã€`self._last_status = None`ã€‚
- `run()` æ¯åœˆå¾ªç’°é–‹é ­ï¼š`if self.language_provider is not None: self.set_language(self.language_provider())`ï¼Œ`set_language` å…§è®Šæ›´æ™‚å‘¼å« `on_language(new)`ï¼ˆæœ‰çš„è©±ï¼‰ã€‚
- `main()`ï¼š`controller.language = resolve_language(language_from_config(config_path))`ï¼›`language_provider = lambda: resolve_language(language_from_config(config_path))`ã€`on_language` å‘¼å« `icon.update_menu()`ã€‚
- é¸å–®ï¼šMenuItem çš„ text æ”¹ callableâ€”â€”`pystray.MenuItem(lambda _item: t("Open Dashboard", controller.language), enqueue("open"), default=True)` ç­‰äº”é …ï¼ˆå« `checked` çš„ç™»å…¥è‡ªå‹•å•Ÿå‹•ï¼‰ï¼Œ`icon.update_menu()` æœƒä»¥æ–°èªžè¨€é‡ç¹ªã€‚
- icon åˆå§‹ title çš„ `"Stream Micro: Starting"`ï¼štext éƒ¨åˆ†æ”¹ `t("Starting", controller.language)`ï¼›å‰ç¶´ `"Stream Micro: "` ä¸è­¯ã€‚

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tray.py -v`
Expected: PASSï¼ˆå«æ—¢æœ‰å…¨éƒ¨æ¸¬è©¦â€”â€”dedupã€restart/stop/exitã€probe ä¾‹å¤–ç­‰ï¼‰

- [x] **Step 5: Commit**

```bash
git add src/stream_micro/tray.py tests/test_tray.py
git commit -m "feat: localize tray menu and status with live language follow"
```

---

### Task 5: å‰ç«¯ config é©…å‹•èªžè¨€èˆ‡åˆ‡æ›å³å­˜

**Files:**
- Modify: `frontend/src/main.tsx`ï¼ˆ`type Config`ã€`switchLang`ã€config è¼‰å…¥å¾Œå¥—ç”¨èªžè¨€ï¼‰
- Modify: `frontend/src/i18n.ts`ï¼ˆç„¡éœ€æ”¹å‹•ï¼›æ²¿ç”¨ `setLang`/`getLang`ï¼‰

**Interfaces:**
- Consumes: `GET /api/config` å›žå‚³å« `language`ï¼ˆTask 1ï¼‰ï¼›æ—¢æœ‰ `api<T>(path, options)` helper èˆ‡ `setLang`/`getLang`ã€‚
- Produces: `type Config` å¢ž `language?: "auto" | "en" | "zh-TW"`ã€‚

ï¼ˆæœ¬ repo å‰ç«¯ç„¡å–®å…ƒæ¸¬è©¦æ¡†æž¶ï¼ˆ`npm test` = `npm run build`ï¼‰ï¼Œæœ¬ä»»å‹™ä»¥ tsc å»ºç½®èˆ‡æ‰‹å‹•é©—è­‰ä»£æ›¿ failing-test å¾ªç’°ã€‚ï¼‰

- [x] **Step 1: Implement in `frontend/src/main.tsx`**

- `type Config` åŠ  `language?: "auto" | "en" | "zh-TW"`ã€‚
- `switchLang` æ”¹ asyncï¼š

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

ï¼ˆæ³¨æ„ï¼šPUT ä»¥ `savedConfig` ç‚ºåŸºåº•â€”â€”è‰ç¨¿ä¸å¤–é€ï¼›æˆåŠŸå¾Œè‰ç¨¿çš„ `language` ä¹Ÿæ›´æ–°ï¼Œä¹‹å¾Œ Save ä¸æœƒè“‹å›žèˆŠå€¼ï¼›`dirty` ç‹€æ…‹å®Œå…¨ä¸å‹•ã€‚ï¼‰

- config è¼‰å…¥è™•ï¼ˆ`refresh` ä¸­ `setConfig` çš„ä½ç½®ï¼‰ï¼š`if (nextConfig.language === "en" || nextConfig.language === "zh-TW") { if (getLang() !== nextConfig.language) { setLang(nextConfig.language); setLangState(nextConfig.language); } }`ã€‚`"auto"` ä¸è¦†è“‹ `initialLang` çš„çµæžœï¼ˆç¶­æŒç¾è¡Œ localStorage â†’ ç€è¦½å™¨åµæ¸¬ï¼‰ã€‚
- `switchLang` çš„ onClick æ”¹ `() => void switchLang()`ã€‚

- [x] **Step 2: Build to verify**

Runï¼ˆåœ¨ `frontend/`ï¼‰: `npm run build`
Expected: tsc èˆ‡ vite build é€šéŽï¼Œç„¡åž‹åˆ¥éŒ¯èª¤

- [x] **Step 3: Manual verificationï¼ˆæœ‰çœŸæ©Ÿæ™‚ï¼‰**

1. `uv run stream-micro serve`ï¼Œé–‹ Dashboardï¼Œåˆ‡åˆ°æŸæœå‹™æ”¹ä¸€å€‹å€¼ï¼ˆä¸æŒ‰ Saveï¼‰ã€‚
2. åˆ‡èªžè¨€ â†’ UI å³æ™‚æ›èªžè¨€ã€æœªå­˜æç¤ºä»åœ¨ï¼ˆdirty æœªå‹•ï¼‰ã€é‡æ–°æ•´ç†å¾Œèªžè¨€ç¶­æŒæ–°å€¼ã€`bridge.yaml` çš„ `language` å·²æ›´æ–°ã€‚
3. å›ž Dashboard æŒ‰å­˜ä¸‹çš„ Save â†’ å…¶ä»–ä¿®æ”¹å¥—ç”¨ä¸” `language` æœªè¢«è“‹å›žã€‚
4. ç³»çµ±åŒ£é¸å–®èˆ‡æç¤ºåœ¨ ~2 ç§’å…§è·Ÿè‘—æ›èªžè¨€ã€‚

- [x] **Step 4: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat: persist Dashboard language in Bridge configuration"
```

---

### Task 6: README é›™èªž

**Files:**
- Modify: `README.md`ï¼ˆå‰¯æ¨™è¡Œä¸‹åŠ èªžè¨€åˆ‡æ›è¡Œï¼‰
- Create: `README.zh-TW.md`

**Interfaces:**
- Consumes: ç„¡ï¼ˆç¨ç«‹ä»»å‹™ï¼Œä½† zh-TW æ–‡æ¡ˆèªžå½™é ˆèˆ‡ Task 2 å­—å…¸ä¸€è‡´â€”â€”Bridgeã€Dashboard ç­‰å°ˆæœ‰åè©žä¸è­¯ï¼‰ã€‚

- [x] **Step 1: Add the language switcher to `README.md`**

åœ¨å‰¯æ¨™ï¼ˆ`**Your AI sessions and usage, at a glance on your Stream Deck.**`ï¼‰ä¸‹æ–¹ç©ºä¸€è¡ŒåŠ ï¼š

```markdown
English | [ç¹é«”ä¸­æ–‡](README.zh-TW.md)
```

- [x] **Step 2: Create `README.zh-TW.md`**

- é–‹é ­ï¼š`# stream-micro`ï¼ˆç”¢å“åä¸è­¯ï¼‰ã€ç¹ä¸­å‰¯æ¨™ã€åˆ‡æ›è¡Œ `[English](README.md) | ç¹é«”ä¸­æ–‡`ã€‚
- é€ç¯€å°æ‡‰ç¿»è­¯ `README.md` å…¨éƒ¨å°ç¯€ï¼›åœ–ç‰‡ç›¸å°è·¯å¾‘ã€æŒ‡ä»¤å€å¡Šã€è³‡æ–™å¤¾/æª”åã€é€£çµç›®æ¨™åŽŸæ¨£ä¿ç•™ï¼ˆå¾ž repo root ç›¸å°è·¯å¾‘å…©ä»½ç›¸åŒï¼‰ã€‚
- é€£å¾€ `integrations/*/README.md` çš„é€£çµæ–‡å­—ä¿ç•™è‹±æ–‡åŽŸå­—ã€‚
- æ–‡æ¡ˆæ˜¯å°ç£æ…£ç”¨ç¹é«”ä¸­æ–‡ï¼›è¡Œç‚ºæè¿°ä»¥è‹±æ–‡ç‰ˆç‚ºæº–ï¼Œç¿»è­¯ä¸å¾—æ”¹å‹•ä»»ä½•æ‰¿è«¾ï¼ˆå¦‚ã€ŒWindows onlyã€çš„ç¯„åœï¼‰ã€‚
- README æ—¢æœ‰ã€Œsupport for English and Traditional Chineseã€çš„æè¿°ç…§ç¿»ã€‚

- [x] **Step 3: Verify links and images**

Run: `git diff --stat README.md` èˆ‡ç›®è¦–æª¢æŸ¥ `README.zh-TW.md` ä¸­æ‰€æœ‰ç›¸å°é€£çµèˆ‡åœ–ç‰‡è·¯å¾‘åœ¨ repo ä¸­å­˜åœ¨ã€‚
Expected: å…©ä»½ README çš„åœ–ç‰‡è·¯å¾‘éƒ½æŒ‡å‘ `docs/images/` ä¸‹æ—¢æœ‰æª”æ¡ˆ

- [x] **Step 4: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: add Traditional Chinese README"
```

---

### Task 7: å…¨å¥—é©—è­‰

**Files:**
- ç„¡æ–°è®Šæ›´ï¼ˆåªé©—è­‰ï¼›è‹¥ç™¼ç¾è·¨ä»»å‹™å›žæ­¸ï¼Œä¿®åœ¨å“ªå€‹ä»»å‹™çš„æª”æ¡ˆå°±æ­¸é‚£å€‹ commitï¼‰

**Interfaces:**
- Consumes: å…¨éƒ¨ä»»å‹™ã€‚

- [x] **Step 1: Run the full backend suite**

Run: `uv run pytest -v`
Expected: å…¨ç¶ ï¼ˆæ³¨æ„ `tests/test_server.py` æ—¢æœ‰çš„ config round-trip æ¸¬è©¦â€”â€”GET å›žçš„ config ç¾åœ¨å« `language`ï¼ŒPUT å›žåŽ»å¿…é ˆç…§æ¨£ 200ï¼‰

- [x] **Step 2: Lint**

Run: `uv run ruff check`
Expected: ç„¡ç™¼ç¾

- [x] **Step 3: Frontend build**

Runï¼ˆåœ¨ `frontend/`ï¼‰: `npm run build`
Expected: é€šéŽ

- [x] **Step 4: è‹¥å…¨ç¶ ï¼Œå·¥ä½œå®Œæˆï¼›è‹¥æœ‰ç´…ï¼Œä¿®åˆ°ç¶ å†æ”¶**