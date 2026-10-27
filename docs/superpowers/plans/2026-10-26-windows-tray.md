# Windows Tray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Windows 系統匣圖示管理 Stream Micro 的啟動、狀態、停止與退出。

**Architecture:** 系統匣主執行緒與可停止的 Bridge runtime 共用一個進程，透過序列化命令管理生命週期。Windows named mutex 防止同份 config 多開；不殺未知 port owner。登入啟動與 launcher 使用目前使用者權限。

**Tech Stack:** Python >=3.10、Uvicorn、Pillow、Windows-only pystray、ctypes Win32 mutex。

**Spec:** `docs/superpowers/specs/2026-10-26-windows-tray-design.md`

## Steps
- [x] Task 1: Make the Deck runner and Bridge runtime stoppable.
- [x] Task 2: Add single-instance tray controller and status presentation.
- [x] Task 3: Add login startup and windowless launchers.
- [ ] Task 4: Verify integration and document usage.

## Global Constraints
- 不留黑色命令列視窗；重複啟動不多開 Bridge。
- 網址取自 bridge.yaml，不寫死 port。
- 只停止自己管理的 Bridge，不根據 port 終止未知進程。
- 停止 Bridge 保留圖示；結束則整組停止並釋放 Stream Deck。
- 第一版不自動故障重啟，不安裝 Windows Service，不管理或重啟 OpenCode server。
- 保留 serve 作開發入口與現有 reload 行為。
- 登入自啟預設關閉，只有使用者切換選單才安裝。
- 保留使用者未追蹤的 IMG_6896.jpg 與 docs/images/。

## Review Focus
- 無硬體或硬體重連中停止：中斷等待，確定退出後才 close service。
- 相同 config 同時啟動或 port 被他人佔用：不得多開或終止他人。
- 快速連按重啟及退出：操作序列化，不重疊存取硬體。
- 路徑有空白／中文及 cwd 不同：launcher、自啟與 config 路徑仍正確。
- HTTP 卡住或 runtime 未退出：有限 timeout、UI 不凍結、不可顯示假成功。

---

### Task 1: Stoppable runtime

**Files:** modify `src/stream_micro/deck_bridge.py`, `src/stream_micro/cli.py`; create `src/stream_micro/runtime.py`, `tests/test_runtime.py`; extend existing Deck runner tests.

**Interfaces:** `Mk2BridgeRunner.stop() -> None`; new `BridgeRuntime(config_path: Path)`, `start() -> None`, `stop(timeout: float = 10.0) -> bool`, `is_running: bool`, `dashboard_url: str`. Runtime owns service, runner, uvicorn.Server and threads; start failures are observable. Keep reload factory separate.

- [ ] Write failing tests for stop during refresh wait, stop during device-unavailable retry, context manager hardware release, runtime bind failure, stop ordering (runner exits before service.close), repeated stop, and timeout prohibiting restart. Use fakes; no hardware/network credentials.
- [ ] Run `uv run pytest tests/test_runtime.py` and the modified Deck tests; confirm assertions fail due to missing behavior.
- [ ] Implement cooperative stop Event in Deck loops, interruptible waits preserving existing once/sleep test hooks. Implement owned runtime lifecycle, background Uvicorn startup/error reporting, cleanup on partial failure, and bounded stop. Route non-reload serve through shared lifecycle with KeyboardInterrupt cleanup; preserve logging and configuration creation.
- [ ] Run runtime, CLI and Deck tests; confirm PASS and no hardware access in tests.
- [ ] Commit only task-owned changes after passing checks: `feat: add stoppable bridge runtime`.

### Task 2: Tray controller

**Files:** create `src/stream_micro/tray.py`, `src/stream_micro/windows_instance.py`, `tests/test_tray.py`, `tests/test_windows_instance.py`; modify `src/stream_micro/cli.py`, `pyproject.toml`, `uv.lock`.

**Interfaces:** CLI `stream-micro tray --bridge-config <path>`; `tray.main(config_path: Path) -> int`; `TrayController` consumes Task 1 runtime, browser opener and icon adapter injected for tests; serial command queue owns lifecycle. `SingleInstance(config_path: Path)` context manager exposes `acquired: bool`.

- [ ] Write failing tests: start yellow then healthy green; stopped/error red; upstream offline does not imply red; local HTTP timeout red; restart after explicit stop works; rapid restart/exit serializes; exit stops runtime before icon removal; unexpected exit does not auto-restart. Assert configured port used for browser; duplicate launch never constructs runtime; occupied port never invokes kill. Test mutex key normalization and release with fake Win32 calls.
- [ ] Run `uv run pytest tests/test_tray.py tests/test_windows_instance.py`; confirm missing-feature failures.
- [ ] Implement lazy Windows-only pystray import and dependency marker (`pystray>=0.19,<1; sys_platform == 'win32'`), green/yellow/red Pillow icons and tooltip. Define default open-Dashboard action, Restart Bridge, Stop Bridge, checked Start at login, Exit. Poll owned runtime plus bounded loopback HTTP status without fetching plugin snapshots (`/api/status?include_plugins=false`); require own server startup before green. Named mutex name uses hashed resolved config path and current-user context. Duplicate launch opens Dashboard and exits. Never kill by port or adopt other processes. Keep UI callbacks nonblocking and surface errors in tooltip/log. Update lock via `uv lock`.
- [ ] Run tests; confirm PASS. Check importing CLI on non-Windows does not import Windows tray backend.
- [ ] Commit task files: `feat: add Windows system tray bridge controller`.

### Task 3: Login startup and launchers

**Files:** create `src/stream_micro/windows_startup.py`, `tests/test_windows_startup.py`, `tests/test_launchers.py`; modify `src/stream_micro/tray.py`, `start.bat`, `start.vbs`.

**Interfaces:** `StartupRegistration(config_path: Path, pythonw: Path, startup_dir: Path | None = None)` with `is_enabled() -> bool`, `set_enabled(enabled: bool) -> None`. Resolve current-user Startup via Windows known-folder API, with injectable directory for tests. Registration owns one config-hashed .vbs file; no writes outside its own registration.

- [ ] Write failing tests for default off, enable/disable/idempotence, correct quoted absolute paths containing spaces and Chinese, launch from different cwd, registration mismatch/foreign content not overwritten or removed, missing interpreter/dependencies diagnostic, and launchers containing no port-based process termination or hidden build/install steps.
- [ ] Run `uv run pytest tests/test_windows_startup.py tests/test_launchers.py`; confirm failures.
- [ ] Implement owned Startup launcher invoking absolute .venv pythonw with `-m stream_micro.cli tray --bridge-config <absolute path>` and project cwd. Connect checked menu and error feedback. Change start.vbs to validated pythonw launch, not redirected cmd; make start.bat validated console tray launch. Remove kill-by-port and frontend build/install from launch path. Put rotating bridge.log configuration in tray startup (5 MiB, one backup); use visible Windows error reporting for failures before tray creation, without exposing secrets.
- [ ] Run startup, launcher and tray tests; confirm PASS and temporary startup files removed by fixtures.
- [ ] Commit task-owned files: `feat: add windowless tray launch and login startup`.

### Task 4: Integration verification and usage

**Files:** modify `README.md`, add integration cases to `tests/test_runtime.py`, `tests/test_tray.py` as needed.

**Interfaces:** No new public interfaces; verify Tasks 1–3 together.

- [ ] Add an isolated loopback runtime integration test using fake Bridge/Deck resources: own server responds, stop releases resources and port, restart works, occupied socket fails without touching owner. Run it and fix gaps through failing assertions first.
- [ ] Update README with uv sync/frontend build prerequisites, start.vbs/windowless and console tray launch, menu/state meanings, login opt-in, 5 MiB rotating log location, manual serve compatibility, no automatic restart, and Windows ^ visibility preferences.
- [ ] Run `uv run pytest`, `uv run ruff check src tests`, `uv run pyright src tests`, and `npm run build` in frontend. Record actual results; do not claim success for unrun checks.
- [ ] With user permission to touch real hardware/runtime, manually test Windows icon default click action, context menu, tooltip, no console, browser configured URL, duplicate launch, Stop/Restart/Exit releasing Deck, and login registration toggle. If not permitted or unavailable, explicitly record manual checks as unverified. Do not terminate a pre-existing manually launched Bridge to make tests pass.
- [ ] Commit documentation and verified integration tests: `docs: document Windows tray operation`.
