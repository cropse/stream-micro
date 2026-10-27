# Task 1 Report: `paths` Module

## What I implemented

- Added `src/stream_micro/paths.py` with the requested frozen/source path APIs and directory composition.
- Added the four tests in `tests/test_paths.py`, transcribed from the brief.

## Testing

- Focused: `uv run pytest .\tests\test_paths.py -v` — 4 passed.
- Full: `uv run pytest` — 516 passed; one existing Starlette deprecation warning from `fastapi.testclient` about using `httpx`.
- Lint: `uv run ruff check src tests` — All checks passed.
- `git diff --check` — passed.

## TDD Evidence

- **RED:** `uv run pytest .\tests\test_paths.py -v` before implementation — collection failed importing `paths` from `stream_micro`, since `src/stream_micro/paths.py` did not exist. This is the expected missing-module failure (reported as `ImportError: cannot import name 'paths'` rather than `ModuleNotFoundError`, because the package itself exists).
- **GREEN:** After implementation, the same command — 4 tests passed.

## Files changed

- `src/stream_micro/paths.py`
- `tests/test_paths.py`

## Self-review findings

- All six requested public signatures are present; module docstring is present.
- The test source was kept verbatim as required.
- **Requirement conflict:** The brief's source-root test computes `Path(__file__).resolve().parents[2]`, which in this checkout resolves to the worktree's parent (`.../95403...`), not the Git repository root (`.../portable-exe`). The brief's prose implementation says to compute `parents[2]` from the module file, which resolves to the actual repo root. After confirming the Git root and asking the controller, I followed the instruction to keep the test verbatim and used `parents[3]` from `paths.py`, which makes the specified tests pass but differs from the prose implementation. Please resolve this inconsistency before relying on source-mode roots in downstream tasks.

## Issues or concerns

- The source-root conflict described above is the only concern.

## Follow-up fix report

Controller ruling confirmed the test's expected-root expression was wrong. Updated it to use `parents[1]` from `tests/test_paths.py`, and restored source-mode root resolution in `paths.py` to `parents[2]`, which resolves to the Git repository root.

Verification:

- `uv run python -c "from stream_micro import paths; p = paths.app_root(frozen=False); print(p, (p / 'frontend').exists())"` — printed the repository path and `True`.
- `uv run pytest tests/test_paths.py -v` — 4 passed.
- `uv run pytest` — 516 passed, 1 existing Starlette/httpx deprecation warning.
- `uv run ruff check src tests` — All checks passed.

Follow-up commit: `fix: resolve source roots at the repo root`.
