from pathlib import Path

from stream_micro import tray


class Runtime:
    dashboard_url = "http://127.0.0.1:9123"
    is_running = False
    stops = 0
    blocked = False

    def start(self):
        self.is_running = True

    def stop(self):
        self.stops += 1
        if self.blocked:
            return False
        self.is_running = False
        return True


def test_controller_restart_stop_exit_and_browser():
    runtime = Runtime()
    views, urls = [], []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), urls.append,
                                     probe=lambda _url: True)
    controller.handle("restart")
    controller.check_health()
    assert views[-1][0] == "green"
    controller.handle("open")
    assert urls == ["http://127.0.0.1:9123"]
    controller.handle("stop")
    assert views[-1][0] == "red"
    controller.handle("restart")
    assert runtime.is_running
    controller.handle("exit")
    assert not runtime.is_running
    assert controller.exited


def test_timeout_blocks_restart_and_exit():
    runtime = Runtime()
    runtime.is_running = True
    runtime.blocked = True
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None)
    controller.handle("restart")
    assert views[-1][0] == "red"
    controller.handle("exit")
    assert not controller.exited


def test_unhealthy_does_not_auto_restart():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: False)
    controller.check_health()
    assert views[-1][0] == "red"
    assert not runtime.is_running


def test_mutex_prevents_duplicate_and_releases(tmp_path: Path):
    from stream_micro.windows_instance import SingleInstance

    with SingleInstance(tmp_path / "bridge.yaml") as first:
        assert first.acquired
        with SingleInstance(tmp_path / "bridge.yaml") as second:
            assert not second.acquired
    with SingleInstance(tmp_path / "bridge.yaml") as third:
        assert third.acquired


def test_controller_survives_probe_exception_and_processes_exit():
    runtime = Runtime()
    controller = tray.TrayController(runtime, lambda *_: None, lambda _: None,
                                     probe=lambda _: (_ for _ in ()).throw(ValueError("bad")))
    controller.commands.put("exit")
    ended = []
    controller.run(lambda: ended.append(True))
    assert ended == [True]
    assert controller.exited


def test_health_probe_exception_is_red_and_recoverable():
    runtime = Runtime()
    runtime.start()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _: (_ for _ in ()).throw(ValueError("failure")))
    controller.check_health()
    assert views[-1][0] == "red"
    controller.handle("exit")
    assert controller.exited


def test_unchanged_health_does_not_rebuild_icon():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None)
    controller.check_health()
    controller.check_health()
    assert len(views) == 1


def test_queued_exit_takes_priority_over_restarts():
    runtime = Runtime()
    controller = tray.TrayController(runtime, lambda *_: None, lambda _: None)
    controller.commands.put("restart")
    controller.commands.put("restart")
    controller.commands.put("exit")
    controller.run(lambda: None)
    assert runtime.stops == 1


def test_status_strings_follow_language():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: False, language="zh-TW")
    controller.handle("stop")
    assert views[-1] == ("red", "Bridge 已停止")
    controller.set_language("en")
    controller.check_health()
    assert views[-1] == ("red", "Bridge stopped or not responding")


def test_language_change_represents_unchanged_status():
    runtime = Runtime()
    views = []
    controller = tray.TrayController(runtime, lambda *s: views.append(s), lambda _: None,
                                     probe=lambda _url: True, language="en")
    controller.handle("restart")  # ends green "Bridge running"
    seen = len(views)
    controller.set_language("zh-TW")
    controller.check_health()  # state unchanged, language changed: must re-present
    assert views[seen] == ("green", "Bridge 執行中")


def test_run_loop_follows_language_provider():
    runtime = Runtime()
    languages = []
    current = {"lang": "en"}
    controller = tray.TrayController(runtime, lambda *_: None, lambda _: None,
                                     probe=lambda _url: True,
                                     language="en",
                                     language_provider=lambda: current["lang"],
                                     on_language=languages.append)
    controller.commands.put("exit")
    current["lang"] = "zh-TW"
    controller.run(lambda: None)
    assert controller.language == "zh-TW"
    assert languages == ["zh-TW"]
