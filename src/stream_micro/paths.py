"""Resolve application and bundled-resource paths in source and frozen builds."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_frozen(frozen: bool | None) -> bool:
    return is_frozen() if frozen is None else frozen


def app_root(*, frozen: bool | None = None, executable: Path | None = None) -> Path:
    if _is_frozen(frozen):
        return (executable or Path(sys.executable)).resolve().parent
    return Path(__file__).resolve().parents[3]


def bundled_root(*, frozen: bool | None = None, meipass: Path | None = None) -> Path:
    if _is_frozen(frozen):
        return meipass or Path(getattr(sys, "_MEIPASS", sys.executable))
    return Path(__file__).resolve().parents[3]


def frontend_dist_dir(*, frozen: bool | None = None, meipass: Path | None = None) -> Path:
    return bundled_root(frozen=frozen, meipass=meipass) / "frontend" / "dist"


def integration_dir(*, frozen: bool | None = None, meipass: Path | None = None) -> Path:
    return (
        bundled_root(frozen=frozen, meipass=meipass)
        / "integrations"
        / "claude-code-stream-micro"
    )


def default_bridge_config(
    *, frozen: bool | None = None, app_root_dir: Path | None = None
) -> Path:
    if _is_frozen(frozen):
        return (app_root_dir or app_root(frozen=True)) / "bridge.yaml"
    return Path("bridge.yaml")
