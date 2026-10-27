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
