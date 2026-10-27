"""Windows tray UI; callbacks queue commands to one lifecycle worker."""
from __future__ import annotations

import logging
import sys
import webbrowser
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any

import httpx
from PIL import Image, ImageDraw

from .control_config import default_control_config, write_control_config
from .runtime import BridgeRuntime


def make_startup_registration(config_path: Path, executable: Path) -> Any:
    from . import paths
    from .windows_startup import StartupRegistration

    if paths.is_frozen():
        return StartupRegistration(config_path, executable, frozen=True, executable=executable)
    return StartupRegistration(config_path, executable.with_name("pythonw.exe"))


def probe_bridge(url: str) -> bool:
    try:
        return httpx.get(url + "/api/status?include_plugins=false", timeout=1,
                         trust_env=False).is_success
    except httpx.HTTPError:
        return False


class TrayController:
    def __init__(self, runtime: Any, present: Callable[[str, str], None],
                 open_url: Callable[[str], object], *,
                 probe: Callable[[str], bool] = probe_bridge) -> None:
        self.runtime = runtime
        self._present = present
        self._last_status: tuple[str, str] | None = None
        self.open_url = open_url
        self.probe = probe
        self.exited = False
        self.commands: Queue[str] = Queue()
        self.startup: Any = None

    def present(self, color: str, text: str) -> None:
        if self._last_status != (color, text):
            self._present(color, text)
            self._last_status = (color, text)

    def handle(self, command: str) -> None:
        try:
            if command == "open":
                self.open_url(self.runtime.dashboard_url)
            elif command == "startup":
                self.startup.set_enabled(not self.startup.is_enabled())
            elif command in ("restart", "stop", "exit"):
                self.present("yellow", "Stopping Bridge")
                if not self.runtime.stop():
                    raise RuntimeError("Shutdown timed out; restart blocked")
                if command == "restart":
                    self.present("yellow", "Starting Bridge")
                    self.runtime.start()
                    self.check_health()
                else:
                    self.present("red", "Bridge stopped")
                    self.exited = command == "exit"
        except Exception:
            logging.getLogger(__name__).exception("Tray command failed: %s", command)
            self.present("red", "Operation failed; see bridge.log")

    def check_health(self) -> None:
        try:
            ok = self.runtime.is_running and self.probe(self.runtime.dashboard_url)
        except Exception:
            logging.getLogger(__name__).exception("Bridge health check failed")
            ok = False
        self.present("green" if ok else "red",
                     "Bridge running" if ok else "Bridge stopped or not responding")

    def run(self, on_exit: Callable[[], None]) -> None:
        if self.commands.empty():
            self.handle("restart")
        while not self.exited:
            try:
                pending = [self.commands.get(timeout=2)]
                while True:
                    try:
                        pending.append(self.commands.get_nowait())
                    except Empty:
                        break
                if "exit" in pending:
                    self.handle("exit")
                else:
                    for command in dict.fromkeys(pending):
                        self.handle(command)
            except Empty:
                self.check_health()
        on_exit()


def status_icon(color: str) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = {"green": "#22c55e", "yellow": "#fbbf24", "red": "#ef4444"}[color]
    draw.rounded_rectangle((1, 10, 62, 53), radius=6, fill="#20242c")
    for row in range(3):
        for col in range(5):
            x, y = 7 + col * 10, 17 + row * 10
            draw.rounded_rectangle((x, y, x + 7, y + 7), radius=1, fill=fill)
    return image


def main(config_path: Path) -> int:
    if sys.platform != "win32":
        raise RuntimeError("System tray mode requires Windows")
    from .windows_instance import SingleInstance

    config_path = config_path.resolve()
    if not config_path.exists():
        write_control_config(config_path, default_control_config())
    with SingleInstance(config_path) as instance:
        runtime = BridgeRuntime(config_path)
        if not instance.acquired:
            webbrowser.open(runtime.dashboard_url)
            return 0
        handler = RotatingFileHandler(config_path.parent / "bridge.log",
                                      maxBytes=5 * 1024 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        worker: Thread | None = None
        try:
            import pystray

            icon = pystray.Icon("stream-micro", status_icon("yellow"), "Stream Micro: Starting")

            def present(color: str, text: str) -> None:
                icon.icon = status_icon(color)
                icon.title = "Stream Micro: " + text

            controller = TrayController(runtime, present, webbrowser.open)
            controller.startup = make_startup_registration(config_path, Path(sys.executable))

            def enqueue(command: str) -> Callable[..., None]:
                def callback(*_args: object) -> None:
                    if command == "startup":
                        # Native callback rebuilds the menu on its own UI thread.
                        controller.handle(command)
                    else:
                        controller.commands.put(command)
                return callback

            icon.menu = pystray.Menu(
                pystray.MenuItem("開啟 Dashboard", enqueue("open"), default=True),
                pystray.MenuItem("重新啟動 Bridge", enqueue("restart")),
                pystray.MenuItem("停止 Bridge", enqueue("stop")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("登入自動啟動", enqueue("startup"),
                                 checked=lambda _item: controller.startup.is_enabled()),
                pystray.MenuItem("結束", enqueue("exit")),
            )

            def tracked_setup(native_icon: Any) -> None:
                nonlocal worker
                native_icon.visible = True
                worker = Thread(target=controller.run, args=(icon.stop,),
                                name="stream-micro-controller", daemon=True)
                worker.start()

            icon.run(setup=tracked_setup)
            if worker and worker.is_alive():
                controller.commands.put("exit")
                worker.join(15)
                if worker.is_alive():
                    raise RuntimeError("Tray controller could not stop")
            return 0
        except Exception:
            logging.getLogger(__name__).exception("Tray startup or UI failed")
            raise
        finally:
            # Normal exit has already stopped through the serialized controller.
            # Do not race an active lifecycle worker on an abnormal UI return.
            if worker is None or not worker.is_alive():
                runtime.stop()
            logging.getLogger().removeHandler(handler)
            handler.close()
