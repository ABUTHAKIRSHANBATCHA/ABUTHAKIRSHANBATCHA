import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from assistant.config import Config
from assistant.server import Devices, create_app
from assistant.tools import build_registry
from assistant.tools import bim


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return create_app(Config(), Devices(tmp_path / "devices.json"))


def drain(ws, until):
    events = []
    while True:
        e = ws.receive_json()
        if e["type"] != "stats":
            events.append(e)
        if e["type"] == until:
            return events


@contextmanager
def paired_socket(client, app):
    token = client.post("/api/pair", json={"pin": app.state.devices.pin, "device_name": "Phone"}).json()["token"]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "hello"
        yield ws


def test_remote_device_must_pair(app):
    with TestClient(app) as client:
        assert client.get("/api/info").json()["local"] is False
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "wrong"})
            assert ws.receive_json()["type"] == "auth_failed"
        assert client.post("/api/pair", json={"pin": "abc"}).status_code == 403


def test_pin_is_single_use_and_rotates_after_failures(tmp_path):
    d = Devices(tmp_path / "d.json")
    pin = d.pin
    token = d.pair(pin, "Phone")
    assert token and d.valid(token) and d.pin != pin or d.pair(pin, "x") is None
    assert Devices(tmp_path / "d.json").valid(token)          # survives restart
    assert token not in (tmp_path / "d.json").read_text()      # only the hash is stored
    before = d.pin
    for _ in range(5):
        assert d.pair("000000" if before != "000000" else "111111", "x") is None
    assert d.pin != before


def test_hello_hides_pin_from_remote_devices(app):
    with TestClient(app) as client:
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": token})
            hello = ws.receive_json()
            assert hello["type"] == "hello" and "pairing" not in hello and hello["online"] is False


def test_offline_brain_answers_status(app):
    with TestClient(app) as client:
        with paired_socket(client, app) as ws:
            ws.send_json({"type": "say", "text": "what time is it"})
            events = drain(ws, "reply_done")
            kinds = [e["type"] for e in events]
            assert kinds[0] == "user" and "delta" in kinds
            assert "CPU" in next(e["text"] for e in events if e["type"] == "delta")


class FakeStreamBrain:
    """Replaces the model call so the tool loop and approvals can be tested."""

    def __init__(self, brain, turns):
        self.turns = iter(turns)
        brain.client = object()
        brain._stream_turn = self.next

    async def next(self):
        return next(self.turns)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def message(*content, stop="tool_use"):
    return SimpleNamespace(content=list(content), stop_reason=stop)


TEXT = SimpleNamespace(type="text", text="Done.")


@pytest.mark.parametrize("approve", [True, False])
def test_write_file_waits_for_approval(app, tmp_path, approve):
    target = tmp_path / "note.txt"
    brain = app.state.brain
    FakeStreamBrain(brain, [
        message(tool_use("t1", "write_file", {"path": str(target), "contents": "hello"})),
        message(TEXT, stop="end_turn"),
    ])
    with TestClient(app) as client:
        with paired_socket(client, app) as ws:
            ws.send_json({"type": "say", "text": "save a note"})
            request = drain(ws, "confirm_request")[-1]
            assert request["name"] == "write_file" and not target.exists()
            ws.send_json({"type": "confirm", "id": request["id"], "approve": approve})
            events = drain(ws, "reply_done")
    statuses = [e["status"] for e in events if e["type"] == "tool"]
    result = brain.messages[2]["content"][0]
    if approve:
        assert target.read_text() == "hello" and statuses == ["running", "done"]
        assert "is_error" not in result
    else:
        assert not target.exists() and statuses == ["denied"] and result["is_error"]
    assert [m["role"] for m in brain.messages] == ["user", "assistant", "user", "assistant"]


def test_invalid_tool_input_is_rejected_not_run(app, tmp_path):
    brain = app.state.brain
    FakeStreamBrain(brain, [
        message(tool_use("t1", "write_file", {"path": str(tmp_path / "x.txt")})),   # contents missing
        message(tool_use("t2", "no_such_tool", {})),
        message(TEXT, stop="end_turn"),
    ])
    with TestClient(app) as client:
        with paired_socket(client, app) as ws:
            ws.send_json({"type": "say", "text": "go"})
            events = drain(ws, "reply_done")
    assert not any(e["type"] == "confirm_request" for e in events)
    assert brain.messages[2]["content"][0]["is_error"] and "INVALID_JSON" in brain.messages[2]["content"][0]["content"]
    assert brain.messages[4]["content"][0]["is_error"]


def test_failed_turn_leaves_history_clean(app):
    brain = app.state.brain

    async def boom():
        raise RuntimeError("network down")

    brain.client, brain._stream_turn = object(), boom
    with pytest.raises(RuntimeError):
        asyncio.run(brain._converse("hello"))
    assert brain.messages == []


def test_tool_schemas_are_valid_and_stable(app):
    schemas = app.state.brain.registry.schemas()
    names = [s["name"] for s in schemas]
    assert names == sorted(names) and {"run_command", "revit_query", "find_bim_files"} <= set(names)
    assert json.dumps(schemas) == json.dumps(app.state.brain.registry.schemas())
    confirm = {n for n, t in app.state.brain.registry.tools.items() if t.confirm}
    assert confirm == {"run_command", "write_file"}


def test_find_bim_files_skips_backups_and_sorts_newest_first(tmp_path):
    import os
    for i, name in enumerate(["Tower_A.rvt", "Tower_A.0003.rvt", "site.ifc", "notes.txt", "clash.nwd"]):
        f = tmp_path / name
        f.write_text("x")
        os.utime(f, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
    out = json.loads(bim.find_bim_files(Config(), folder=str(tmp_path)))
    assert [p["path"].split(os.sep)[-1] for p in out["files"]] == ["clash.nwd", "site.ifc", "Tower_A.rvt"]
    assert json.loads(bim.find_bim_files(Config(), name_contains="tower", folder=str(tmp_path)))["total_found"] == 1


def test_revit_query_explains_when_bridge_is_down():
    out = bim.revit_query(Config(revit_bridge_url="http://127.0.0.1:9/assistant"), "model")
    assert "Could not reach the Revit bridge" in out


def test_system_tools(app, tmp_path):
    reg = app.state.brain.registry
    status = json.loads(asyncio.run(reg.run("get_system_status", {})))
    assert 0 <= status["memory"] <= 100 and status["now"]
    (tmp_path / "a.txt").write_text("வணக்கம்", encoding="utf-8")
    assert asyncio.run(reg.run("read_file", {"path": str(tmp_path / "a.txt")})) == "வணக்கம்"
    assert "a.txt" in asyncio.run(reg.run("list_directory", {"path": str(tmp_path)}))
    assert "exit code 0" in asyncio.run(reg.run("run_command", {"command": "echo hi"}))
