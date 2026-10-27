"""Build and validate a portable Windows distribution of Stream Micro."""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
REQUIRED = [
    "StreamMicro.exe",
    "hidapi.dll",
    "_internal/frontend/dist/index.html",
    "_internal/integrations/claude-code-stream-micro/scripts/report-status.mjs",
    "_internal/integrations/claude-code-stream-micro/scripts/report-quota.mjs",
]


def make_icon(target: Path) -> Path:
    """Render the tray artwork as a multi-resolution Windows icon."""
    from stream_micro.tray import status_icon

    target.parent.mkdir(parents=True, exist_ok=True)
    status_icon("green").save(target, format="ICO", sizes=[(s, s) for s in (16, 32, 48, 64)])
    return target


def app_version() -> str:
    return version("stream-micro")


def zip_name(version: str | None = None) -> str:
    return f"stream-micro-{version if version is not None else app_version()}-win64.zip"


def dist_problems(dist: Path) -> list[str]:
    """Report missing payloads and any accidentally bundled mutable user data."""
    problems = [f"Missing required file: {rel}" for rel in REQUIRED if not (dist / rel).is_file()]
    for path in sorted(dist.rglob("*")):
        if path.name in {"bridge.yaml", ".state"} or path.name.startswith("bridge.log"):
            problems.append(f"Forbidden user data: {path.relative_to(dist).as_posix()}")
    return problems


def main() -> int:
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if npm is None:
        raise SystemExit("npm is required to build the frontend")
    subprocess.run([npm, "ci"], cwd=ROOT / "frontend", check=True)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    make_icon(ROOT / "build/app.ico")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed", "--onedir",
            "--name", "StreamMicro", "--icon", str(ROOT / "build/app.ico"), "--paths", "src",
            "--collect-all", "StreamDeck",
            "--add-data", f"{ROOT / 'frontend/dist'};frontend/dist",
            "--add-data",
            f"{ROOT / 'integrations/claude-code-stream-micro'}"
            ";integrations/claude-code-stream-micro",
            "--distpath", "build/dist", "--workpath", "build/PyInstaller",
            "--specpath", "build", "build_entry.py",
        ],
        cwd=ROOT,
        check=True,
    )
    dist = ROOT / "build/dist/StreamMicro"
    shutil.copy2(ROOT / "hidapi.dll", dist / "hidapi.dll")
    problems = dist_problems(dist)
    if problems:
        raise SystemExit("Distribution validation failed:\n" + "\n".join(problems))
    archive = ROOT / "build" / zip_name()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(dist.parent))
    print(f"Created {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
