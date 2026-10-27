# 可攜 exe 打包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Stream Micro 打包成可攜 exe（下載 → 解壓 → 雙擊進系統匣），設定跟著 exe 走，新 zip 蓋上即更新。

**Architecture:** 新增 `stream_micro.paths` 作為凍結（PyInstaller）／原始碼路徑解析的唯一入口，各模體改經它取路徑，行為以 `sys.frozen` 分流且參數可注入。exe 進入點沿用 `cli.main`，凍結環境預設命令為 `tray`、`bridge.yaml` 預設在 exe 同資料夾。根目錄 `build_exe.py` 負責：前端建置 → PyInstaller onedir → 出貨清單檢查 → zip。

**Tech Stack:** Python ≥3.10、PyInstaller 6（`packaging` dependency group）、Pillow（.ico 生成）、pytest/ruff/pyright。

**Spec:** `docs/superpowers/specs/2026-10-27-portable-exe-design.md`

## Global Constraints

- 所有凍結行為經 `stream_micro.paths` 以 `sys.frozen` 分流；原始碼模式行為必須完全不變（既有測試除明列者外不改就過）。
- 各模組以 `from . import paths` 匯入、呼叫 `paths.xxx()`，讓測試可用 `monkeypatch.setattr("stream_micro.paths.xxx", ...)` 攔截。
- zip 永遠不含 `bridge.yaml`、`.state/`、`bridge.log*`；缺 `StreamMicro.exe`、`hidapi.dll`、frontend `index.html`、兩個 `.mjs` companion 腳本任一項就不得出包。
- ruff：line-length 100、select `E,F,I,UP,B,SIM`；pyright 與 `uv run pytest` 全綠是每個 task 的隱含驗收。
- 自動啟動沿用 .vbs ＋ 所有權檢查機制；tray 僅 Windows。

## Review Focus

以下輸入類別是 spec 隱含但需明確測試釘住的（對應測試已加進各 task）：

1. exe 在含空白／中文字的資料夾 → 自動啟動 .vbs 內容仍完整引用 → Task 6 凍結測試用「工具 空 白」exe 路徑。
2. 打包漏掉必要檔案 → build 拒絕出 zip 而非默默出包 → Task 7 `dist_problems` 測試。
3. 使用者設定混進 zip → `dist_problems` 禁止清單測試（Task 7）。
4. 凍結環境下使用者顯式傳 `--bridge-config` → 不被 exe 同資料夾預設覆寫 → Task 5 測試。
5. 凍結模式 Startup 檔被他人佔用 → 拒改不覆寫（ownership）→ Task 6 foreign 測試。

---

### Task 1: `paths` 模組（凍結／原始碼路徑解析）

**Files:**
- Create: `src/stream_micro/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces（後續 task 全靠這些簽名）：
  - `is_frozen() -> bool`
  - `app_root(*, frozen: bool | None = None, executable: Path | None = None) -> Path`（凍結＝exe 資料夾；原始碼＝repo 根）
  - `bundled_root(*, frozen: bool | None = None, meipass: Path | None = None) -> Path`（凍結＝`sys._MEIPASS`；原始碼＝repo 根）
  - `frontend_dist_dir(*, frozen=None, meipass=None) -> Path`＝`bundled_root()/frontend/dist`
  - `integration_dir(*, frozen=None, meipass=None) -> Path`＝`bundled_root()/integrations/claude-code-stream-micro`
  - `default_bridge_config(*, frozen=None, app_root_dir=None) -> Path`（凍結＝`app_root()/bridge.yaml`；原始碼＝`Path("bridge.yaml")`）

- [ ] **Step 1: 寫失敗測試**（`tests/test_paths.py`）

```python
from pathlib import Path

from stream_micro import paths


def test_source_roots_point_at_repo_layout() -> None:
    repo = Path(__file__).resolve().parents[2]
    assert paths.app_root(frozen=False) == repo
    assert paths.frontend_dist_dir(frozen=False) == repo / "frontend" / "dist"
    assert paths.integration_dir(frozen=False) == repo / "integrations" / "claude-code-stream-micro"
    assert paths.default_bridge_config(frozen=False) == Path("bridge.yaml")


def test_frozen_app_root_is_exe_folder() -> None:
    exe = Path("C:/工具 空 白/StreamMicro.exe")
    assert paths.app_root(frozen=True, executable=exe) == exe.parent


def test_frozen_bundled_root_is_meipass() -> None:
    internal = Path("C:/app/_internal")
    assert paths.bundled_root(frozen=True, meipass=internal) == internal
    assert paths.frontend_dist_dir(frozen=True, meipass=internal) == internal / "frontend" / "dist"
    assert paths.integration_dir(frozen=True, meipass=internal) == (
        internal / "integrations" / "claude-code-stream-micro"
    )


def test_frozen_default_bridge_config_sits_next_to_exe() -> None:
    root = Path("C:/app")
    assert paths.default_bridge_config(frozen=True, app_root_dir=root) == root / "bridge.yaml"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL（`ModuleNotFoundError: stream_micro.paths`）

- [ ] **Step 3: 實作 `src/stream_micro/paths.py`**

`is_frozen()` 讀 `getattr(sys, "frozen", False)`。私有 helper 解析 `frozen=None` → `is_frozen()`。`app_root`：凍結取 `(executable or sys.executable).resolve().parent`，原始碼取 `Path(__file__).resolve().parents[2]`。`bundled_root`：凍結取 `meipass or Path(getattr(sys, "_MEIPASS", sys.executable))`，原始碼同 repo 根。三個資料夾函式與 `default_bridge_config` 依 Interfaces 組合。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS（4 項）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/paths.py tests/test_paths.py
git commit -m "feat: add frozen/source path resolution module"
```

### Task 2: `server.py` Dashboard 目錄走 `paths`

**Files:**
- Modify: `src/stream_micro/server.py`（`create_app` 內 `dashboard_dir` 一行，約 228 行；頂部加 `from . import paths`）
- Test: `tests/test_server.py`（新增）

**Interfaces:**
- Consumes: `paths.frontend_dist_dir()`（Task 1）
- Produces: `create_app` 行為不變；Dashboard 靜態檔根目錄由 `paths` 解析。

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_server.py`，沿用檔內既有的 `write_control_config` ＋ `TestClient` 模式）

```python
def test_dashboard_serves_bundled_dist_from_resolver(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>deck</html>", encoding="utf-8")
    monkeypatch.setattr("stream_micro.paths.frontend_dist_dir", lambda: dist)
    config_path = tmp_path / "control.yaml"
    write_control_config(config_path, default_control_config())
    with TestClient(create_app(config_path)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/").text == "<html>deck</html>"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_server.py::test_dashboard_serves_bundled_dist_from_resolver -v`
Expected: FAIL（mount 到 repo 的 dist，內容不符）

- [ ] **Step 3: 改 `server.py`**

`dashboard_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"` 改成 `dashboard_dir = paths.frontend_dist_dir()`；`if (dashboard_dir / "index.html").is_file():` 與其後全部不動。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_server.py tests/test_frontend_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/server.py tests/test_server.py
git commit -m "refactor: serve dashboard via path resolver"
```

### Task 3: `device.py` hidapi.dll 搜尋路徑走 `paths`

**Files:**
- Modify: `src/stream_micro/device.py`（`_prepare_hidapi_search_path`，22–26 行；頂部加 `from . import paths`）
- Test: `tests/test_device.py`（新增）

**Interfaces:**
- Consumes: `paths.app_root()`（Task 1）
- Produces: 凍結環境把 exe 同資料夾加入 DLL 搜尋路徑；原始碼行為不變。

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_device.py`）

```python
def test_hidapi_search_path_uses_app_root(monkeypatch, tmp_path) -> None:
    import os

    from stream_micro import device

    added: list[str] = []
    monkeypatch.setattr(device.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda value: added.append(value) or object())
    (tmp_path / "hidapi.dll").touch()
    device._HIDAPI_DIRECTORY = None
    try:
        device._prepare_hidapi_search_path()
    finally:
        device._HIDAPI_DIRECTORY = None
    assert added == [str(tmp_path)]


def test_hidapi_search_path_skipped_without_dll(monkeypatch, tmp_path) -> None:
    import os

    from stream_micro import device

    added: list[str] = []
    monkeypatch.setattr(device.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda value: added.append(value) or object())
    device._HIDAPI_DIRECTORY = None
    try:
        device._prepare_hidapi_search_path()
    finally:
        device._HIDAPI_DIRECTORY = None
    assert added == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_device.py -k hidapi_search_path -v`
Expected: FAIL（`device.paths` 不存在）

- [ ] **Step 3: 改 `device.py`**

`bundled_hidapi = Path(__file__).resolve().parents[2] / "hidapi.dll"` 改成 `bundled_hidapi = paths.app_root() / "hidapi.dll"`；其餘邏輯不動。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_device.py -v`
Expected: PASS（含既有測試）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/device.py tests/test_device.py
git commit -m "refactor: load hidapi.dll via path resolver"
```

### Task 4: `claude_setup.py` 整合腳本目錄走 `paths` ＋ 搬家偵測

**Files:**
- Modify: `src/stream_micro/claude_setup.py`（`INTEGRATION_DIR` 一行，32 行；`inspect()` 的 stale 判斷，約 160–176 行；頂部加 `from . import paths`）
- Test: `tests/test_claude_setup.py`（新增一個測試；其餘不改）

**Interfaces:**
- Consumes: `paths.integration_dir()`（Task 1）
- Produces: `INTEGRATION_DIR` 常數值改為凍結感知；`_script`、`check_scripts` 等使用處不改。`inspect()` 把「內嵌腳本路徑 ≠ 目前 INTEGRATION_DIR」回報為既有的 `wrong_port` 狀態（Dashboard 已有對應的一鍵修復 UI，前端零改動）。

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_claude_setup.py`，假整合資料夾 fixture 沿用檔內既有測試的模式）

```python
def test_moved_installation_is_reported_as_stale(tmp_path: Path, monkeypatch) -> None:
    integration = tmp_path / "integrations" / "claude-code-stream-micro"
    scripts = integration / "scripts"
    scripts.mkdir(parents=True)
    for name in ("report-status.mjs", "report-quota.mjs"):
        (scripts / name).write_text("export {}", encoding="utf-8")
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", integration)
    settings = tmp_path / "settings.json"
    claude_setup.install(settings, 8765, {"hooks", "statusline"})
    assert claude_setup.inspect(settings, 8765)["hooks"]["state"] == "ours"
    monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", tmp_path / "moved")
    result = claude_setup.inspect(settings, 8765)
    assert result["hooks"]["state"] == "wrong_port"
    assert result["statusline"]["state"] == "wrong_port"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_claude_setup.py -k moved_installation -v`
Expected: FAIL（搬家後仍回報 `ours`）

- [ ] **Step 3: 實作**

`INTEGRATION_DIR = paths.integration_dir()`（import 時求值，凍結狀態執行中不變；既有測試的 `monkeypatch.setattr(claude_setup, "INTEGRATION_DIR", ...)` 繼續有效）。`inspect()`：hooks 的 `stale` 追加 `or _script(HOOK_SCRIPT) not in command`；statusline 的 `fresh` 追加 `and _script(QUOTA_SCRIPT) in command`。原始碼模式安裝時內嵌的就是 `_script(...)` 產生的字串，同行為不變。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_claude_setup.py -v`
Expected: PASS（含既有測試不改而過）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/claude_setup.py tests/test_claude_setup.py
git commit -m "feat: detect moved Claude companion installs"
```

### Task 5: `cli.py` 凍結預設（config 路徑、預設命令、錯誤訊息）

**Files:**
- Modify: `src/stream_micro/cli.py`（parser `--bridge-config` 預設、`main()` 開頭、tray 失敗 MessageBox 訊息約 94–101 行；頂部加 `from . import paths`）
- Test: `tests/test_cli.py`（新增）

**Interfaces:**
- Consumes: `paths.default_bridge_config()`、`paths.is_frozen()`、`paths.app_root()`（Task 1）
- Produces: `--bridge-config` 預設值由 `Path("bridge.yaml")` 改為 `None`，`main()` 中 None → `paths.default_bridge_config()`；凍結且無命令 → `args.command = "tray"`。

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_cli.py`，沿用檔內既有假 Runtime 與 monkeypatch 模式）

```python
def test_frozen_serve_defaults_bridge_config_next_to_exe(tmp_path, monkeypatch) -> None:
    from stream_micro import cli

    seen: list[Path] = []

    class Runtime:
        def __init__(self, config_path):
            seen.append(Path(config_path))

        def start(self):
            pass

        is_running = False
        error = None

        def stop(self):
            pass

    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", Runtime)
    assert cli.main(["serve"]) == 0
    assert seen == [tmp_path / "bridge.yaml"]


def test_explicit_bridge_config_survives_frozen(tmp_path, monkeypatch) -> None:
    from stream_micro import cli

    seen: list[Path] = []

    class Runtime:
        def __init__(self, config_path):
            seen.append(Path(config_path))

        def start(self):
            pass

        is_running = False
        error = None

        def stop(self):
            pass

    explicit = tmp_path / "custom.yaml"
    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.runtime.BridgeRuntime", Runtime)
    assert cli.main(["serve", "--bridge-config", str(explicit)]) == 0
    assert seen == [explicit]


def test_frozen_without_command_enters_tray(monkeypatch, tmp_path) -> None:
    from stream_micro import cli

    called: list[Path] = []
    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    monkeypatch.setattr("stream_micro.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr(
        "stream_micro.tray.main", lambda config_path: called.append(Path(config_path)) or 0
    )
    assert cli.main([]) == 0
    assert called == [tmp_path / "bridge.yaml"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL（凍結分支未實作）

- [ ] **Step 3: 實作**

`build_parser()` 的 `--bridge-config` 預設改 `None`（help 文字不動）。`main()` parse 後：
`if args.bridge_config is None: args.bridge_config = paths.default_bridge_config()`；再 `if args.command is None and paths.is_frozen(): args.command = "tray"`。tray 失敗 MessageBox 文案：凍結時用 `"Stream Micro could not start. Check bridge.log next to StreamMicro.exe, then try again."`，原始碼保留現行 uv sync 文案。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS（含既有測試；原始碼模式 `--bridge-config` 未給時仍解析為 `Path("bridge.yaml")`）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/cli.py tests/test_cli.py
git commit -m "feat: freeze-aware CLI defaults"
```

### Task 6: 凍結環境的登入自動啟動

**Files:**
- Modify: `src/stream_micro/windows_startup.py`（`StartupRegistration.__init__` 與 `set_enabled`；加 `import sys`、`from . import paths`）
- Modify: `src/stream_micro/tray.py`（139–141 行的 registration 建構，改用新 factory）
- Test: `tests/test_windows_startup.py`、`tests/test_tray.py`（新增）

**Interfaces:**
- Consumes: `paths.is_frozen()`（Task 1）
- Produces: `StartupRegistration(config_path: Path, pythonw: Path, startup_dir: Path | None = None, *, frozen: bool | None = None, executable: Path | None = None)`；`tray.make_startup_registration(config_path: Path, executable: Path) -> StartupRegistration`（module-level、lazy import `StartupRegistration`）。

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_windows_startup.py`）

```python
def test_frozen_registration_runs_only_the_executable(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    exe = tmp_path / "工具 空 白" / "StreamMicro.exe"
    registration = StartupRegistration(
        tmp_path / "bridge.yaml", tmp_path / "missing-pythonw.exe", tmp_path,
        frozen=True, executable=exe,
    )
    registration.set_enabled(True)  # pythonw 不存在也不該被擋
    assert registration.is_enabled()
    content = registration.path.read_text(encoding="utf-16")
    assert "StreamMicro.exe" in content
    assert "pythonw" not in content and "-m" not in content
    registration.set_enabled(False)
    assert not registration.path.exists()


def test_frozen_registration_refuses_foreign_entry(tmp_path: Path):
    from stream_micro.windows_startup import StartupRegistration

    registration = StartupRegistration(
        tmp_path / "bridge.yaml", tmp_path / "pythonw.exe", tmp_path,
        frozen=True, executable=tmp_path / "StreamMicro.exe",
    )
    registration.path.write_text("foreign", encoding="utf-16")
    with pytest.raises(RuntimeError):
        registration.set_enabled(True)
    assert registration.path.read_text(encoding="utf-16") == "foreign"
```

（`tests/test_tray.py` 新增）

```python
def test_startup_registration_factory_points_source_at_pythonw(tmp_path):
    from stream_micro.tray import make_startup_registration

    exe = tmp_path / "Scripts" / "python.exe"
    registration = make_startup_registration(tmp_path / "bridge.yaml", exe)
    assert not registration.frozen
    assert registration.pythonw == (tmp_path / "Scripts" / "pythonw.exe")


def test_startup_registration_factory_points_frozen_at_the_exe(tmp_path, monkeypatch):
    from stream_micro.tray import make_startup_registration

    monkeypatch.setattr("stream_micro.paths.is_frozen", lambda: True)
    exe = tmp_path / "StreamMicro.exe"
    registration = make_startup_registration(tmp_path / "bridge.yaml", exe)
    assert registration.frozen
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_windows_startup.py tests/test_tray.py -v`
Expected: FAIL（`frozen` 參數不存在）

- [ ] **Step 3: 實作**

`StartupRegistration`：`self.frozen = paths.is_frozen() if frozen is None else frozen`；凍結時 `command = subprocess.list2cmdline([str(executable.resolve() if executable is not None else Path(sys.executable))])`，原始碼時 command 照現行 pythonw + `-m stream_micro.cli tray --bridge-config`。`set_enabled(True)` 的 `pythonw.is_file()` 檢查加 `and not self.frozen`。`tray.py` 新增 module-level `make_startup_registration`（內部 lazy `from .windows_startup import StartupRegistration`），凍結 → `StartupRegistration(config_path, executable, frozen=True, executable=executable)`，原始碼 → `StartupRegistration(config_path, executable.with_name("pythonw.exe"))`；`main()` 139–141 行改 `controller.startup = make_startup_registration(config_path, Path(sys.executable))`。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_windows_startup.py tests/test_tray.py -v`
Expected: PASS（含既有 source 模式測試不改而過）

- [ ] **Step 5: Commit**

```bash
git add src/stream_micro/windows_startup.py src/stream_micro/tray.py tests/test_windows_startup.py tests/test_tray.py
git commit -m "feat: freeze-aware login autostart"
```

### Task 7: `build_exe.py` 建置腳本（icon、PyInstaller、清單檢查、zip）

**Files:**
- Create: `build_exe.py`（repo 根目錄）
- Create: `build_entry.py`（repo 根目錄，PyInstaller 進入點，兩行：`from stream_micro.cli import main` + `raise SystemExit(main())`）
- Create: `tests/test_build_exe.py`
- Modify: `pyproject.toml`（`[dependency-groups]` 加 `packaging = ["pyinstaller>=6,<7"]`；`[tool.pytest.ini_options]` 加 `pythonpath = ["."]`）

**Interfaces:**
- Consumes: `tray.status_icon("green")`（重畫 exe 圖示）
- Produces: `make_icon(target: Path) -> Path`；`app_version() -> str`；`zip_name(version: str | None = None) -> str`；`dist_problems(dist: Path) -> list[str]`（空＝可出貨）；`main() -> int`。

- [ ] **Step 1: 寫失敗測試**（`tests/test_build_exe.py`）

```python
from pathlib import Path

from PIL import Image

import build_exe


def test_make_icon_writes_an_ico(tmp_path: Path) -> None:
    target = build_exe.make_icon(tmp_path / "app.ico")
    with Image.open(target) as icon:
        assert icon.format == "ICO"
        assert icon.size == (64, 64)


def test_zip_name_uses_the_package_version() -> None:
    assert build_exe.zip_name("1.2.3") == "stream-micro-1.2.3-win64.zip"


def test_dist_problems_reports_missing_and_forbidden(tmp_path: Path) -> None:
    assert build_exe.dist_problems(tmp_path)
    (tmp_path / "StreamMicro.exe").touch()
    (tmp_path / "hidapi.dll").touch()
    internal = tmp_path / "_internal"
    for rel in (
        "frontend/dist/index.html",
        "integrations/claude-code-stream-micro/scripts/report-status.mjs",
        "integrations/claude-code-stream-micro/scripts/report-quota.mjs",
    ):
        path = internal / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert build_exe.dist_problems(tmp_path) == []
    (tmp_path / "bridge.yaml").touch()
    (tmp_path / ".state").mkdir()
    assert build_exe.dist_problems(tmp_path)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_build_exe.py -v`
Expected: FAIL（`build_exe` 不存在）

- [ ] **Step 3: 實作 `build_exe.py` 與 `build_entry.py`**

常數：`ROOT = Path(__file__).resolve().parent`；`REQUIRED = ["StreamMicro.exe", "hidapi.dll", "_internal/frontend/dist/index.html", "_internal/integrations/claude-code-stream-micro/scripts/report-status.mjs", "_internal/integrations/claude-code-stream-micro/scripts/report-quota.mjs"]`；禁止出現：`bridge.yaml`、`bridge.log*`、`.state/`。`make_icon` 用 `stream_micro.tray.status_icon("green")` 存 `.ico`（sizes 含 16/32/48/64）。`dist_problems` 列出缺檔與禁止檔。`main()` 依序：`frontend` 跑 `npm ci` 與 `npm run build`（subprocess，cwd=frontend）→ `make_icon` 到 `build/app.ico` → PyInstaller（subprocess `[sys.executable, "-m", "PyInstaller", ...]`，參數：`--noconfirm --clean --windowed --onedir --name StreamMicro --icon build/app.ico --paths src --collect-all StreamDeck --add-data "<frontend/dist>;frontend/dist" --add-data "<integrations/claude-code-stream-micro>;integrations/claude-code-stream-micro" --distpath build/dist --workpath build/PyInstaller --specpath build build_entry.py`）→ 複製 repo 根的 `hidapi.dll` 到 `build/dist/StreamMicro/` → `dist_problems` 非空即 `sys.exit` 列出問題 → 以 `zipfile` 把 `build/dist/StreamMicro` 打成 `build/<zip_name>`。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv sync --dev --group packaging && uv run pytest tests/test_build_exe.py -v`
Expected: PASS

- [ ] **Step 5: 真機建置煙霧測試（手動）**

Run: `uv run --group packaging python build_exe.py`，解壓 `build/stream-micro-*-win64.zip` 到含中文與空白的資料夾，雙擊 exe：系統匣綠圖示、Dashboard 開啟、Deck 五個 Session 鍵渲染、右鍵勾「登入自動啟動」寫出 .vbs；在 Dashboard 啟用 Claude Code 整合並一鍵安裝 companion，確認狀態列與 hook 回報。Expected: 全部正常。

- [ ] **Step 6: Commit**

```bash
git add build_exe.py build_entry.py tests/test_build_exe.py pyproject.toml uv.lock
git commit -m "feat: add portable exe build script"
```

### Task 8: README 文件與全量驗證

**Files:**
- Modify: `README.md`（「Windows system tray」之後新增一節；repo 已有未提交的 README 變更，只加不動）

**Interfaces:**
- Consumes: 無新介面；收斂 Task 1–7。

- [ ] **Step 1: README 新增「可攜 exe（下載即用）」一節**

內容依 spec：解壓任意資料夾雙擊 `StreamMicro.exe`；設定（`bridge.yaml`、`.state/`、`bridge.log`）生成在 exe 同資料夾；更新＝新 zip 解壓蓋上原資料夾（zip 永不含設定）；搬資料夾後自動啟動與 Claude companion 需重新勾選／重裝；開發者建置：`uv sync --dev --group packaging`＋`uv run --group packaging python build_exe.py`；原始碼開發流程（serve、start.vbs）不變。

- [ ] **Step 2: 全量驗證**

Run: `uv run pytest`、`uv run ruff check src tests build_exe.py`、`uv run pyright src tests`、`cd frontend; npm run build`
Expected: 全綠；重複 Task 7 Step 5 的煙霧測試一次確認收斂後仍正常。

- [ ] **Step 3: Commit**

注意：`README.md` 目前已有使用者未提交的變更——先 `git diff README.md` 確認內容，詢問使用者是否一併提交；未經同意不得把無關變更掃進這個 commit。

```bash
git add README.md
git commit -m "docs: document portable exe usage and build"
```