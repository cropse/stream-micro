"""Owned, cooperatively stoppable Bridge runtime for the desktop tray."""
from __future__ import annotations

import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn

from .bridge import BridgeService
from .control_config import load_control_config
from .deck_bridge import Mk2BridgeRunner
from .server import create_app


class BridgeRuntime:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path.resolve()
        self._bound_config = load_control_config(self.config_path).bridge
        self._threads: list[Thread] = []
        self._service: BridgeService | None = None
        self._runner: Mk2BridgeRunner | None = None
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self.error: str | None = None

    @property
    def dashboard_url(self) -> str:
        config = self._bound_config
        host = config.host
        if host == "::1":
            host = "[::1]"
        return f"http://{host}:{config.port}"

    @property
    def is_running(self) -> bool:
        return bool(self._server and self._server.started and self._threads
                    and all(thread.is_alive() for thread in self._threads))

    def start(self) -> None:
        if self._service is not None or any(t.is_alive() for t in self._threads):
            raise RuntimeError("Previous Bridge has not stopped")
        config = self._bound_config = load_control_config(self.config_path).bridge
        self.error = None
        sock = socket.socket(socket.AF_INET6 if ":" in config.host else socket.AF_INET)
        try:
            # Reserve the port before acquiring hardware or constructing providers.
            sock.bind((config.host, config.port))
            sock.listen(128)
            self._socket = sock
            service = self._service = BridgeService(self.config_path)
            self._runner = Mk2BridgeRunner(service)
            server = self._server = uvicorn.Server(uvicorn.Config(
                create_app(self.config_path, service=service, close_service=False),
                host=config.host, port=config.port, log_config=None,
                timeout_graceful_shutdown=2, access_log=False,
            ))
            self._threads = [
                Thread(target=self._run_deck, name="stream-micro-deck", daemon=True),
                Thread(target=lambda: server.run(sockets=[sock]),
                       name="stream-micro-http", daemon=True),
            ]
            for thread in self._threads:
                thread.start()
            deadline = time.monotonic() + 5
            while not server.started:
                if not all(t.is_alive() for t in self._threads):
                    raise RuntimeError(self.error or "Bridge exited during startup")
                if time.monotonic() >= deadline:
                    raise RuntimeError("Bridge startup timed out")
                time.sleep(0.02)
        except BaseException:
            self.stop()
            if self._socket is None:
                sock.close()
            raise

    def _run_deck(self) -> None:
        try:
            assert self._runner is not None
            self._runner.run()
        except Exception:
            self.error = "Deck worker failed; see bridge.log"
            import logging
            logging.getLogger(__name__).exception("Deck worker failed")

    def stop(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        if self._runner:
            self._runner.stop()
        if self._server:
            self._server.should_exit = True
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self._threads):
            self.error = "Bridge shutdown timed out; restart is blocked"
            return False
        if self._service:
            self._service.close()
        if self._socket:
            self._socket.close()
        self._service = self._runner = self._server = self._socket = None
        self._threads = []
        return True
