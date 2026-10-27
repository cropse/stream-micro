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
