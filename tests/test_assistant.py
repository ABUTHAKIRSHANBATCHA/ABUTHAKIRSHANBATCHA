import asyncio
import json
import pathlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from assistant.brain import system_prompt
from assistant.config import Config
from assistant.memory import Memory
from assistant.server import Devices, create_app
from assistant.tools import build_registry
from assistant.tools import bim
from assistant.tools.lifecycle import STAGES, TEMPLATE, Projects
from assistant.tools.money import Ledger


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return create_app(Config(data_dir=str(tmp_path / "data")), Devices(tmp_path / "devices.json"))


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
        assert ws.receive_json()["type"] == "dashboard"
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


def test_memory_survives_restart_and_reaches_the_prompt(tmp_path):
    m = Memory(tmp_path)
    m.log("user", "My Dubai tower client wants COBie at every stage")
    m.log("assistant", "Noted, COBie at every stage for the Dubai tower.")
    m.remember("profile", "Role", "BIM manager, works mostly in Revit")
    m.remember("idea", "Clash app", "Mobile app that shows clashes on site")
    assert "Updated" in m.remember("idea", "clash app", "Mobile app for site clash review, version 2")

    again = Memory(tmp_path)                      # a fresh start reads everything back
    assert len(again.notes) == 2 and len(again.transcript()) == 2
    prompt = system_prompt(Config(), again.prompt_section())
    assert "BIM manager, works mostly in Revit" in prompt       # profile in full
    assert "Clash app" in prompt and "version 2" in prompt      # idea listed
    assert "COBie at every stage" in prompt                     # recent conversation carried over

    found = json.loads(again.search("cobie dubai"))
    assert found["conversation_passages"] and "COBie" in found["conversation_passages"][0][0]["text"]
    assert json.loads(again.search("clash"))["notes"][0]["title"] == "Clash app"
    assert "Deleted" in again.forget("Clash app") and len(Memory(tmp_path).notes) == 1


def test_conversation_is_logged_and_system_prompt_stays_stable(app):
    brain = app.state.brain
    FakeStreamBrain(brain, [message(TEXT, stop="end_turn")] * 3)
    asyncio.run(brain.handle("remember that I prefer metric units"))
    first = brain._system
    asyncio.run(brain.handle("and again"))
    assert brain._system == first                                # unchanged mid-conversation
    assert [(t["role"], t["text"]) for t in brain.memory.transcript()] == [
        ("user", "remember that I prefer metric units"), ("assistant", "Done."),
        ("user", "and again"), ("assistant", "Done.")]
    brain.reset()
    asyncio.run(brain._converse("new conversation"))
    assert "I prefer metric units" in brain._system              # the next conversation sees the last one
    assert "new conversation" not in brain._system


def test_bim_project_lifecycle(tmp_path):
    projects = Projects(tmp_path)
    assert "already exists" not in projects.create("Marina Tower", client="ABC Developers")
    assert "already exists" in projects.create("Marina Tower")
    total = sum(len(v) for v in TEMPLATE.values())
    overview = json.loads(projects.status("marina"))
    assert overview["overall"] == {"done": 0, "total": total, "percent": 0}
    assert [s["stage"] for s in overview["stages"]] == list(STAGES)

    assert "C1-01" in projects.add_requirement("marina", 1, "Models in Revit 2024 only", "EIR 3.2")
    assert "C7-01" in projects.add_requirement("marina", 7, "COBie 2.4 UK at handover", "EIR 9.1")
    assert "Stage must be" in projects.add_requirement("marina", 9, "x")
    assert "now done" in projects.update_item("marina", "c1-01", "done", "Confirmed by email 12 Nov")
    assert "not_applicable" in projects.update_item("marina", "S1-07", "not_applicable")
    assert "No item" in projects.update_item("marina", "S9-99", "done")

    stage1 = json.loads(projects.status("marina", stage=1))["stages"][0]
    assert stage1["done"] == 1 and stage1["total"] == len(TEMPLATE[1])      # +1 client, -1 n/a
    client_item = next(i for i in stage1["items"] if i["id"] == "C1-01")
    assert client_item["source"] == "Client requirement: EIR 3.2" and client_item["note"]
    open_ids = [i["id"] for i in json.loads(projects.status("marina", stage=1, only_open=True))["stages"][0]["items"]]
    assert "C1-01" not in open_ids and "S1-07" not in open_ids and "S1-01" in open_ids

    text = pathlib.Path(projects.export("marina").split(" to ", 1)[1]).read_text(encoding="utf-8")
    assert "[x] `C1-01` **(client)** Models in Revit 2024 only" in text and "Note: Confirmed by email" in text
    assert json.loads(Projects(tmp_path).status())["projects"][0]["name"] == "Marina Tower"
    assert "No project" in projects.status("unknown")


def test_read_document_docx(app, tmp_path):
    import docx
    doc = docx.Document()
    doc.add_paragraph("4.3 All models shall be delivered in IFC4.")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Stage 4", "LOD 350"
    doc.save(tmp_path / "eir.docx")
    out = asyncio.run(app.state.brain.registry.run("read_document", {"path": str(tmp_path / "eir.docx")}))
    assert "delivered in IFC4" in out and "Stage 4 | LOD 350" in out


def test_toolset_launch_matching(tmp_path):
    exe = tmp_path / "MyTool.exe"
    exe.write_text("")
    installed = [
        {"name": "Autodesk Revit 2024", "executable": "r24.exe"},
        {"name": "Autodesk Revit 2025", "executable": "r25.exe"},
        {"name": "Autodesk Revit 2026", "executable": ""},                 # found but not launchable
        {"name": "Autodesk Civil 3D 2025 - English", "executable": "acad.exe"},
        {"name": "AutoCAD Plant 3D 2025 - English", "executable": "acad.exe"},
        {"name": "Autodesk AutoCAD 2025 - English", "executable": "acad.exe"},
        {"name": "Unreal Engine 5.4", "executable": "UnrealEditor.exe"},
    ]
    assert bim.pick_install(installed, "revit")["name"] == "Autodesk Revit 2025"
    assert bim.pick_install(installed, "Revit 2024")["executable"] == "r24.exe"
    assert bim.pick_install(installed, "tekla") is None
    assert bim.launch_command(bim.pick_install(installed, "civil 3d")) == ["acad.exe", "/product", "C3D"]
    assert bim.launch_command(bim.pick_install(installed, "plant 3d"), "a.dwg") == ["acad.exe", "/product", "PLNT3D", "a.dwg"]
    assert bim.launch_command(bim.pick_install(installed, "autocad 2025")) == ["acad.exe"]
    assert bim.launch_command(bim.pick_install(installed, "unreal"), "g.uproject") == ["UnrealEditor.exe", "g.uproject"]

    cfg = Config(extra_software=[{"name": "My Pipeline Tool", "executable": str(exe)},
                                 {"name": "Missing Tool", "executable": str(tmp_path / "nope.exe")}])
    listed = json.loads(bim.find_bim_software(cfg))
    assert "Unreal Engine" in listed["owner_toolset"]
    assert {r["name"]: bool(r["executable"]) for r in listed["installed"]} == {
        "My Pipeline Tool": True, "Missing Tool": False}
    assert bim.pick_install(bim.installed_software(cfg), "pipeline")["executable"] == str(exe)
    assert "Unreal Engine" in system_prompt(cfg) and "Civil 3D" in system_prompt(cfg)


def test_tracking_figures(tmp_path):
    import datetime as dt
    m = Memory(tmp_path)
    today = dt.date(2026, 11, 14)
    lines = [{"at": "2026-11-14 09:00", "role": "user", "text": "a"},
             {"at": "2026-11-14 09:01", "role": "assistant", "text": "b"},
             {"at": "2026-11-12 10:00", "role": "user", "text": "c"},
             {"at": "2026-10-01 10:00", "role": "user", "text": "too old"}]
    m.transcript_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    (tmp_path / "activity.jsonl").write_text(
        "\n".join(json.dumps({"at": at, "tool": t}) for at, t in [
            ("2026-11-14 09:00", "revit_query"), ("2026-11-13 09:00", "revit_query"),
            ("2026-11-13 09:05", "open_target"), ("2026-09-01 09:00", "run_command")]) + "\n")
    m.remember("idea", "Clash app", "x")
    m.remember("profile", "Role", "BIM manager")
    t = m.tracking(today=today)
    assert len(t["requests"]) == 14 and t["requests"][-1] == {"day": "2026-11-14", "count": 1}
    assert sum(r["count"] for r in t["requests"]) == 2            # assistant turns and old days excluded
    assert t["tools"] == [{"name": "revit_query", "count": 2}, {"name": "open_target", "count": 1}]
    assert t["idea_count"] == 1 and t["ideas"][0]["title"] == "Clash app" and t["note_count"] == 2


def test_dashboard_is_sent_on_connect_and_after_each_reply(app, tmp_path):
    brain = app.state.brain
    FakeStreamBrain(brain, [
        message(tool_use("t1", "bim_project_create", {"name": "Marina Tower"})),
        message(TEXT, stop="end_turn"),
    ])
    with TestClient(app) as client:
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json()["type"] == "hello"
            first = ws.receive_json()
            assert first["type"] == "dashboard" and first["projects"] == []
            ws.send_json({"type": "say", "text": "start Marina Tower"})
            after = drain(ws, "dashboard")[-1]
    assert after["projects"][0]["name"] == "Marina Tower" and len(after["projects"][0]["stages"]) == 7
    assert after["tools"] == [{"name": "bim_project_create", "count": 1}]
    assert after["requests"][-1]["count"] == 1


def test_money_ledger(tmp_path):
    import datetime as dt
    ledger = Ledger(tmp_path, "INR")
    assert "Recorded income of 50,000.00 INR for BIM Audits on 2026-11-03" in ledger.record(
        "income", 50000, "Audit for ABC", "BIM Audits", "2026-11-03")
    ledger.record("expense", 12000.5, "Software licence", "BIM Audits", "2026-11-05")
    ledger.record("income", 8000, "Training", "Training", "2026-10-20")
    ledger.record("income", 999, "old", "Training", "2025-01-01")
    assert "greater than zero" in ledger.record("income", 0, "x")
    assert "YYYY-MM-DD" in ledger.record("income", 5, "x", date="3 Nov")

    months = Ledger(tmp_path, "INR").months(6, today=dt.date(2026, 11, 14))     # reloads from disk
    assert [m["month"] for m in months] == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    assert months[-1] == {"month": "2026-11", "income": 50000.0, "expense": 12000.5, "net": 37999.5}
    assert months[-2]["income"] == 8000.0 and months[0]["income"] == 0.0
    assert ledger.months(2, "training", today=dt.date(2026, 11, 14))[0]["income"] == 8000.0
    assert ledger.months(2, today=dt.date(2026, 1, 10))[0]["month"] == "2025-12"   # year rollover
