"""V2 wire-contract regressions for the real OpenCode adapter."""

import httpx
import pytest

from stream_micro.plugins.base import SessionState
from stream_micro.plugins.opencode import OpenCodePlugin


def v2_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/info":
        return httpx.Response(200, json={"version": "2.0.21", "pid": 123,
                                         "urls": ["http://127.0.0.1:1534"],
                                         "paths": {"tmp": "C:/temp/opencode"}})
    if path == "/api/session":
        if request.url.params.get("cursor") == "page-two":
            return httpx.Response(200, json={"data": [
                {"id": "ses_idle", "title": "Older", "location": {"directory": "C:/work/older"},
                 "time": {"updated": 1}},
                {"id": "ses_child", "parentID": "ses_running", "time": {"updated": 5}},
                {"id": "ses_archived", "time": {"updated": 10, "archived": 9}},
            ], "cursor": {"next": None, "previous": "page-one"}})
        return httpx.Response(200, json={"data": [
            {"id": "ses_running", "title": "Running", "location": {"directory": "C:/work/app"},
             "time": {"updated": 3}},
            {"id": "ses_waiting", "title": "Waiting", "location": {"directory": "C:/work/app"},
             "time": {"updated": 2}},
        ], "cursor": {"next": "page-two", "previous": None}})
    if path == "/api/session/active":
        return httpx.Response(200, json={"data": {
            "ses_running": {"type": "running"}, "ses_waiting": {"type": "running"}}})
    if path.endswith("/permission"):
        return httpx.Response(200, json={"data": []})
    if path.endswith("/form"):
        forms = [{"id": "form_one"}] if "ses_waiting" in path else []
        return httpx.Response(200, json={"data": forms})
    return httpx.Response(404)


@pytest.mark.parametrize("url", ["http://127.0.0.1:57123/api", "http://127.0.0.1:1534"])
def test_v2_proxy_and_direct_server_sessions(url: str) -> None:
    adapter = OpenCodePlugin({"endpoints": [url]},
                             client=httpx.Client(transport=httpx.MockTransport(v2_handler)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert [s.id for s in snapshot.sessions] == ["ses_running", "ses_waiting", "ses_idle"]
    assert [s.state for s in snapshot.sessions] == [SessionState.RUNNING, SessionState.WAITING,
                                                 SessionState.IDLE]
    assert snapshot.sessions[0].project == "app"
    assert snapshot.sessions[0].directory == "C:/work/app"


@pytest.mark.parametrize("metadata", [
    None, {}, {"openchamber": None}, {"openchamber": {"work": None}},
    {"openchamber": {"work": {"state": "open"}}},
])
def test_v2_done_work_is_hidden_until_reopened(metadata: object) -> None:
    work_metadata: object = {"openchamber": {"work": {"state": "done"}}}

    def work_sessions(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"data": [
                {"id": "ses_done", "title": "Finished work", "time": {"updated": 5},
                 "metadata": work_metadata},
                {"id": "ses_open", "title": "Current work", "time": {"updated": 4}},
            ], "cursor": {"next": None}})
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": {}})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(work_sessions)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert [s.id for s in snapshot.sessions] == ["ses_open"]

    work_metadata = metadata
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert [s.id for s in snapshot.sessions] == ["ses_done", "ses_open"]


def test_v2_finished_drain_shows_completed() -> None:
    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(v2_handler)),
                             port_provider=lambda: [])
    assert adapter.snapshot().sessions[0].state is SessionState.RUNNING

    def idle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": {}})
        return v2_handler(request)

    adapter._client = httpx.Client(transport=httpx.MockTransport(idle))
    assert adapter.snapshot().sessions[0].state is SessionState.COMPLETED
    adapter.mark_viewed("ses_running")
    assert adapter.snapshot().sessions[0].state is SessionState.IDLE


@pytest.mark.parametrize("payload", [{}, [], {"version": "not-opencode"},
                                     {"version": "2", "pid": "invalid", "urls": []}])
def test_discovery_rejects_unrelated_info_endpoints(payload: object) -> None:
    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:9000"]},
                             client=httpx.Client(transport=httpx.MockTransport(
                                 lambda r: httpx.Response(200, json=payload))),
                             port_provider=lambda: [])
    assert adapter.snapshot().health.connected is False


def test_v2_permission_blocks_running_session() -> None:
    def permission(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/ses_running/permission":
            return httpx.Response(200, json={"data": [{"id": "perm_one"}]})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(permission)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert snapshot.sessions[0].state is SessionState.WAITING


def test_v2_active_session_beyond_first_five_can_be_waiting() -> None:
    def older_waiting(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            items = [{"id": f"ses_new{i}", "time": {"updated": 10 + i}} for i in range(5)]
            items.append({"id": "ses_waiting", "time": {"updated": 1}})
            return httpx.Response(200, json={"data": items, "cursor": {"next": None}})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(older_waiting)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert snapshot.sessions[-1].state is SessionState.WAITING


@pytest.mark.parametrize("depth", [1, 3])
@pytest.mark.parametrize("blocked", [None, "form", "permission"])
def test_v2_descendant_activity_rolls_up_to_root(depth: int, blocked: str | None) -> None:
    active = True

    def delegated(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session":
            return httpx.Response(200, json={"data": [
                {"id": "ses_root", "title": "Parent", "time": {"updated": 1}},
                {"id": "ses_unrelated", "time": {"updated": 2}},
            ], "cursor": {"next": None}})
        if path == "/api/session/active":
            return httpx.Response(200, json={"data": {
                f"ses_child{depth}": {"type": "running"}} if active else {}})
        for i in range(1, depth + 1):
            if path == f"/api/session/ses_child{i}":
                return httpx.Response(200, json={"data": {
                    "id": f"ses_child{i}",
                    "parentID": "ses_root" if i == 1 else f"ses_child{i - 1}",
                }})
        if blocked and path == f"/api/session/ses_child{depth}/{blocked}":
            return httpx.Response(200, json={"data": [{"id": "request_one"}]})
        if path.endswith(("/form", "/permission")):
            return httpx.Response(200, json={"data": []})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(delegated)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert [s.id for s in snapshot.sessions] == ["ses_unrelated", "ses_root"]
    assert snapshot.sessions[0].state is SessionState.IDLE
    expected = SessionState.WAITING if blocked else SessionState.RUNNING
    assert snapshot.sessions[1].state is expected
    active = False
    assert adapter.snapshot().sessions[1].state is SessionState.COMPLETED


@pytest.mark.parametrize("reverse", [False, True])
def test_v2_waiting_child_takes_priority_over_running_sibling(reverse: bool) -> None:
    def siblings(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session/active":
            ids = ["ses_working_child", "ses_blocked_child"]
            if reverse:
                ids.reverse()
            return httpx.Response(200, json={"data": {sid: {"type": "running"} for sid in ids}})
        if path in ("/api/session/ses_working_child", "/api/session/ses_blocked_child"):
            return httpx.Response(200, json={"data": {
                "id": path.rsplit("/", 1)[1], "parentID": "ses_running"}})
        if path == "/api/session/ses_blocked_child/permission":
            return httpx.Response(200, json={"data": [{"id": "perm_one"}]})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(siblings)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert snapshot.sessions[0].state is SessionState.WAITING


@pytest.mark.parametrize("missing", [True, False])
def test_v2_missing_or_cyclic_child_does_not_hang(missing: bool) -> None:
    def invalid_child(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/active":
            return httpx.Response(200, json={"data": {"ses_bad": {"type": "running"}}})
        if request.url.path == "/api/session/ses_bad":
            if missing:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": {"id": "ses_bad", "parentID": "ses_bad"}})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(invalid_child)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert snapshot.sessions[0].state is SessionState.IDLE


def test_v2_intermediate_ancestor_permission_rolls_up() -> None:
    def ancestor(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session/active":
            return httpx.Response(200, json={"data": {"ses_leaf": {"type": "running"}}})
        if path == "/api/session/ses_leaf":
            return httpx.Response(200, json={"data": {"id": "ses_leaf", "parentID": "ses_middle"}})
        if path == "/api/session/ses_middle":
            return httpx.Response(200, json={"data": {
                "id": "ses_middle", "parentID": "ses_running"}})
        if path == "/api/session/ses_middle/permission":
            return httpx.Response(200, json={"data": [{"id": "perm_one"}]})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(ancestor)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is True
    assert snapshot.sessions[0].state is SessionState.WAITING


def test_v2_repeated_cursor_reports_error_instead_of_looping() -> None:
    def repeated(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"data": [], "cursor": {"next": "same"}})
        return v2_handler(request)

    adapter = OpenCodePlugin({"endpoints": ["http://127.0.0.1:57123/api"]},
                             client=httpx.Client(transport=httpx.MockTransport(repeated)),
                             port_provider=lambda: [])
    snapshot = adapter.snapshot()
    assert snapshot.health.connected is False
    assert "cursor" in snapshot.health.detail.lower()
