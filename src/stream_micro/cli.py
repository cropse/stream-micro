from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread

from .app import run
from .bridge import BridgeService
from .config import ConfigError, load_config
from .control_config import (
    ControlConfigError,
    default_control_config,
    load_control_config,
    write_control_config,
)
from .deck_bridge import Mk2BridgeRunner
from .device import DeviceError
from .models import QuotaWindow, WindowKind
from .renderer import render_quota_key

_RELOAD_CONFIG_ENV = "STREAM_MICRO_BRIDGE_CONFIG"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Display Ollama quota usage on Stream Deck")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="YAML config path")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Write sample PNGs without network/hardware",
    )
    parser.add_argument("--once", action="store_true", help="Fetch/update once, then exit")
    parser.add_argument("--output", type=Path, default=Path("output"), help="Demo PNG directory")
    parser.add_argument(
        "command",
        choices=("serve", "tray"),
        nargs="?",
        help="Run the local Agent Control Surface API and Dashboard",
    )
    parser.add_argument(
        "--bridge-config",
        type=Path,
        default=Path("bridge.yaml"),
        help="Bridge control configuration path (used with serve)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload the local Bridge when Python source files change (development only)",
    )
    return parser


def write_demo(config_path: Path, output_dir: Path) -> tuple[Path, Path]:
    config = load_config(config_path, demo=True)
    now = datetime.now(timezone.utc)
    session = QuotaWindow(WindowKind.SESSION, 42, now + timedelta(hours=2, minutes=18))
    weekly = QuotaWindow(WindowKind.WEEKLY, 78, now + timedelta(days=3, hours=6))
    output_dir.mkdir(parents=True, exist_ok=True)
    session_path = output_dir / "session.png"
    weekly_path = output_dir / "weekly.png"
    render_quota_key(session, "5H", config.display).save(session_path, "PNG")
    render_quota_key(weekly, "WEEK", config.display).save(weekly_path, "PNG")
    return session_path, weekly_path


def create_reload_app():
    """Uvicorn factory run in its reload worker, including the Deck loop."""

    config_path = Path(os.environ[_RELOAD_CONFIG_ENV])
    service = BridgeService(config_path)
    runner = Mk2BridgeRunner(service)
    Thread(target=runner.run, name="stream-micro-mk2", daemon=True).start()
    from .server import create_app

    return create_app(config_path, service=service)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "tray":
            from .tray import main as tray_main

            try:
                return tray_main(args.bridge_config)
            except Exception:
                logging.getLogger(__name__).exception("Tray startup failed")
                if os.name == "nt":
                    import ctypes

                    ctypes.windll.user32.MessageBoxW(
                        None,
                        "Stream Micro could not start. Check bridge.log and run "
                        "uv sync --dev in the project folder, then try again.",
                        "Stream Micro", 16,
                    )
                return 2
        if args.command == "serve":
            if not args.bridge_config.exists():
                write_control_config(args.bridge_config, default_control_config())
                print(f"stream-micro: created Bridge configuration at {args.bridge_config}")
            logging.basicConfig(
                level=os.environ.get("STREAM_MICRO_LOG", "INFO").upper(),
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
            from uvicorn import run as run_server

            bridge_config = load_control_config(args.bridge_config)
            if args.reload:
                os.environ[_RELOAD_CONFIG_ENV] = str(args.bridge_config.resolve())
                run_server(
                    "stream_micro.cli:create_reload_app",
                    factory=True,
                    reload=True,
                    reload_dirs=[str(Path("src").resolve())],
                    host=bridge_config.bridge.host,
                    port=bridge_config.bridge.port,
                )
                return 0
            import time

            from .runtime import BridgeRuntime

            runtime = BridgeRuntime(args.bridge_config)
            try:
                runtime.start()
                while runtime.is_running:
                    time.sleep(0.2)
                if getattr(runtime, "error", None):
                    logging.error("%s", runtime.error)
                    return 2
            finally:
                runtime.stop()
            return 0
        if args.demo:
            paths = write_demo(args.config, args.output)
            print(f"Demo images written: {paths[0]} and {paths[1]}")
            return 0
        config = load_config(args.config)
        logging.basicConfig(
            level=config.logging.level.upper(),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        run(config, once=args.once)
        return 0
    except (ConfigError, ControlConfigError, DeviceError, OSError, RuntimeError) as exc:
        print(f"stream-micro: {exc}")
        return 2
    except KeyboardInterrupt:
        print("stream-micro: stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
