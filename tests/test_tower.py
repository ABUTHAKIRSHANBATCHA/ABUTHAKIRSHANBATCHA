"""Project Control Tower: the specification's test scenarios, on SQLite and (when available) PostgreSQL."""
import datetime as dt
import json
import os

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from assistant.tower import db
from assistant.tower.service import PROJECT_FOLDERS, Tower, TowerError

PDF = b"%PDF-1.7 synthetic drawing "
SEED = [{"name": "Qiddiya", "code": "QIDDIYA", "aliases": ["QID"], "areas": [["EV", "East Village"]]},
        {"name": "KSU", "code": "KSU", "aliases": ["King Saud University"]},
        {"name": "Mishraq", "code": "MISHRAQ", "aliases": ["MSQ"]},
        {"name": "Solution Valley", "code": "SOLUTION_VALLEY", "aliases": ["SV"]}]
URLS = ["sqlite"] + ([os.environ["LUMI_TEST_PG"]] if os.environ.get("LUMI_TEST_PG") else [])


@pytest.fixture(params=URLS)
def tower(request, tmp_path):
    url = ""
    if request.param != "sqlite":
        url = request.param
        engine = db.connect(url)
        db.metadata.drop_all(engine)                      # a clean database for each test
        engine.dispose()
    t = Tower(tmp_path / "PROJECT_CONTROL_TOWER", url, owner="Abu")
    t.seed_projects(SEED)
    yield t
    t.engine.dispose()


def files_under(tower, rel):
    base = tower.root / rel
    return sorted(str(p.relative_to(base)).replace("\\", "/") for p in base.rglob("*") if p.is_file())


def test_a_mobile_upload_is_filed_registered_and_revised(tower):
    r4 = tower.receive_upload("TEST-QID-EV-ELE-001-REV04.pdf", PDF + b"rev4",
                              "Lumi, save this under Qiddiya East Village electrical drawings and update the register.")
    assert r4["status"] == "filed" and r4["project"] == "QIDDIYA" and r4["revision"] == "04"
    assert r4["stored_at"] == "02_PROJECTS/QIDDIYA/01_DRAWINGS/EAST_VILLAGE/ELECTRICAL/DESIGN/CURRENT/TEST-QID-EV-ELE-001-REV04.pdf"
    assert (tower.root / r4["stored_at"]).read_bytes() == PDF + b"rev4"
    assert files_under(tower, "01_UPDATE_INBOX/03_MOBILE_UPLOADS") == []            # quarantine emptied
    assert len(files_under(tower, "01_UPDATE_INBOX/05_PROCESSED")) == 1              # the original upload is kept

    # A newer revision: the old one moves to superseded, nothing is deleted or overwritten.
    r5 = tower.receive_upload("TEST-QID-EV-ELE-001-REV05.pdf", PDF + b"rev5", "Qiddiya East Village electrical, Code C")
    assert r5["status"] == "filed" and r5["superseded"] == ["04"]
    drawings = "02_PROJECTS/QIDDIYA/01_DRAWINGS/EAST_VILLAGE/ELECTRICAL/DESIGN"
    assert files_under(tower, drawings) == ["CURRENT/TEST-QID-EV-ELE-001-REV05.pdf",
                                            "SUPERSEDED_REVISIONS/TEST-QID-EV-ELE-001-REV04.pdf"]
    # An older revision arriving late goes straight to superseded and does not replace the current one.
    r3 = tower.receive_upload("TEST-QID-EV-ELE-001-REV03.pdf", PDF + b"rev3", "Qiddiya")
    assert "older than the current revision 05" in r3["message"]
    # The same file again: recognised by its content, nothing changes.
    again = tower.receive_upload("renamed-copy-REV05.pdf", PDF + b"rev5", "Qiddiya East Village electrical TEST-QID-EV-ELE-001")
    assert again["status"] in ("duplicate", "needs_confirmation", "filed")
    dup = tower.receive_upload("TEST-QID-EV-ELE-001-REV05.pdf", PDF + b"rev5", "Qiddiya")
    assert dup["status"] == "duplicate" and "already registered" in dup["message"]
    # Same file name, different content: both are kept.
    clash = tower.receive_upload("TEST-QID-EV-ELE-001-REV05.pdf", PDF + b"rev5 reissued", "Qiddiya")
    assert clash["status"] == "filed" and clash["superseded"] == ["05"]
    kept = tower.root / "02_PROJECTS/QIDDIYA/01_DRAWINGS/EAST_VILLAGE/ELECTRICAL/DESIGN/SUPERSEDED_REVISIONS"
    assert (kept / "TEST-QID-EV-ELE-001-REV05.pdf").read_bytes() == PDF + b"rev5"   # the earlier issue is intact

    detail = tower.project_detail("Qiddiya")
    row = next(d for d in detail["drawing_register"] if d["number"] == "TEST-QID-EV-ELE-001")
    assert (row["area"], row["discipline"], row["revision"], row["status"], row["type"]) == \
           ("East Village", "Electrical", "05", "Code C", "design")
    history = [(r["revision"], r["superseded"]) for r in detail["revision_history"] if r["document"] == "TEST-QID-EV-ELE-001"]
    assert history == [("04", True), ("05", True), ("03", True), ("05", False)]
    assert detail["responses"][0]["code"] == "C"
    assert all(len(r["hash"]) == 64 for r in detail["revision_history"])
    for r in detail["revision_history"]:                                            # every registered file exists
        assert (tower.root / r["path"]).is_file()
    assert tower.overview()[0]["drawings"] >= 1 and tower.project_detail("KSU")["drawing_register"] == []

    book = load_workbook(tower.root / "02_PROJECTS/QIDDIYA/09_LOGS_AND_REGISTERS/QIDDIYA_REGISTERS.xlsx")
    assert book.sheetnames[:2] == ["Drawing Register", "Revision History"]
    assert book["Drawing Register"]["D2"].value == "TEST-QID-EV-ELE-001"

    path, name = tower.open_link(row["view"])                                       # secure, time-limited link
    assert path.read_bytes() == PDF + b"rev5 reissued" and name.endswith(".pdf")
    with pytest.raises(TowerError):
        tower.open_link(row["view"][:-1] + ("0" if row["view"][-1] != "0" else "1"))
    with pytest.raises(TowerError):
        tower.open_link(tower.link(1, minutes=-5))


def test_b_unknown_project_waits_for_confirmation(tower):
    before = files_under(tower, "02_PROJECTS")
    r = tower.receive_upload("scan0042.pdf", PDF + b"mystery", "please file this")
    assert r["status"] == "needs_confirmation" and "which project" in r["question"]
    assert len(files_under(tower, "01_UPDATE_INBOX/04_NEEDS_CONFIRMATION")) == 1
    assert files_under(tower, "02_PROJECTS") == before                              # no project folder touched
    assert tower.pending_uploads()[0]["job"] == r["job"]

    still = tower.confirm_upload(r["job"], project="KSU")                           # one answer is not enough
    assert still["status"] == "needs_confirmation" and "which area" in still["question"]
    assert "which project it belongs to" not in still["question"]
    wrong = tower.confirm_upload(r["job"], project="Atlantis")
    assert "no project called 'Atlantis'" in wrong["question"]
    done = tower.confirm_upload(r["job"], project="KSU", area="Main Campus", discipline="electrical", revision="1")
    assert done["status"] == "filed"
    assert done["stored_at"] == "02_PROJECTS/KSU/01_DRAWINGS/MAIN_CAMPUS/ELECTRICAL/DESIGN/CURRENT/scan0042.pdf"
    assert tower.pending_uploads() == [] and "Main Campus" in tower.project_detail("KSU")["project"]["areas"]
    assert tower.confirm_upload(r["job"], project="QIDDIYA")["status"] == "filed"   # already done: not filed twice
    assert tower.project_detail("Qiddiya")["drawing_register"] == []

    # File name and note disagree about the project: never guessed.
    conflict = tower.receive_upload("TEST-QID-EV-ELE-009-REV01.pdf", PDF + b"c", "this is for KSU")
    assert conflict["status"] == "needs_confirmation" and "points to Qiddiya but your note says KSU" in conflict["question"]

    # Unsafe or broken files are rejected before any classification.
    assert tower.receive_upload("setup.exe", b"MZ\x90", "Qiddiya")["status"] == "failed"
    assert "does not look like a real .pdf" in tower.receive_upload("TEST-QID-EV-ELE-002-REV01.pdf", b"not a pdf", "")["reason"]
    assert tower.receive_upload("empty.pdf", b"", "")["reason"] == "The file is empty."
    assert len(files_under(tower, "01_UPDATE_INBOX/06_FAILED")) == 3

    # Non-drawing documents go to their own folders.
    clash = tower.receive_upload("Clash_Report_Week46.xlsx", b"PK\x03\x04 data", "Qiddiya clash report")
    assert clash["stored_at"] == "02_PROJECTS/QIDDIYA/03_COORDINATION/CLASH_REPORTS/Clash_Report_Week46.xlsx"
    model = tower.receive_upload("KSU_Network.nwd", b"navis", "KSU")
    assert model["stored_at"] == "02_PROJECTS/KSU/06_BIM_MODELS/NAVISWORKS/KSU_Network.nwd"


def test_c_new_project(tower):
    made = tower.create_project("TEST PROJECT ALPHA")
    assert made["created"] and made["code"] == "TEST_PROJECT_ALPHA"
    base = tower.root / "02_PROJECTS" / "TEST_PROJECT_ALPHA"
    assert all((base / f).is_dir() for f in PROJECT_FOLDERS)
    assert not any((base / "01_DRAWINGS").iterdir())                                # area folders only when needed
    assert (base / "09_LOGS_AND_REGISTERS" / "TEST_PROJECT_ALPHA_REGISTERS.xlsx").is_file()
    assert "TEST_PROJECT_ALPHA" in [p["code"] for p in tower.overview()]
    assert any(a["operation"] == "create_project" and a["target"] == "TEST_PROJECT_ALPHA" for a in tower.audit_trail())

    with pytest.raises(TowerError, match="already exists"):
        tower.create_project("test project alpha")
    similar = tower.create_project("TEST PROJECT ALPHA 2")
    assert similar["created"] is False and similar["similar_to"]["code"] == "TEST_PROJECT_ALPHA"
    assert tower.create_project("TEST PROJECT ALPHA 2", confirm_similar=True)["created"]
    ids = [p["code"] for p in tower.overview()]
    assert len(ids) == len(set(ids)) == 6


def test_d_timesheet(tower):
    today = dt.date.today()
    r = tower.record_timesheet("KSU", 2, "network model coordination", "today")
    assert r == {"recorded": True, "entry_id": r["entry_id"], "project": "KSU", "date": str(today), "hours": 2.0,
                 "person": "Abu", "day_total_hours": 2.0}
    entry = tower.project_detail("KSU")["timesheets"][0]
    assert (entry["discipline"], entry["source"], entry["hours"]) == ("Low Current", "chat", 2.0)
    assert tower.project_detail("Qiddiya")["timesheets"] == []                      # other projects untouched

    twin = tower.record_timesheet("KSU", 2, "Network model coordination", str(today))
    assert twin["recorded"] is False and twin["duplicate_of"] == r["entry_id"]       # accidental repeat caught
    assert tower.record_timesheet("KSU", 2, "network model coordination", allow_duplicate=True)["day_total_hours"] == 4.0
    with pytest.raises(TowerError, match="do not know a person"):
        tower.record_timesheet("KSU", 1, "site visit", person="Ravi")
    tower.add_person("Ravi")
    assert tower.record_timesheet("KSU", 1, "site visit", person="ravi")["person"] == "Ravi"
    for bad in (0, -1, 25, True, "three"):
        with pytest.raises(TowerError):
            tower.record_timesheet("KSU", bad, "x")                                 # hours are never invented
    with pytest.raises(TowerError, match="which project"):
        tower.record_timesheet("Atlantis", 1, "x")
    with pytest.raises(TowerError, match="YYYY-MM-DD"):
        tower.record_timesheet("KSU", 1, "x", "14 November")

    tower.amend_timesheet(r["entry_id"], hours=3, reason="was three hours")
    assert next(t for t in tower.project_detail("KSU")["timesheets"] if t["entry_id"] == r["entry_id"])["hours"] == 3.0
    change = next(h for h in tower.project_detail("KSU")["history"] if h["event"] == "amended")
    assert change["detail"]["before"] == {"hours": 2.0} and change["detail"]["reason"] == "was three hours"
    tower.amend_timesheet(r["entry_id"], void=True)
    assert r["entry_id"] not in [t["entry_id"] for t in tower.project_detail("KSU")["timesheets"]]

    book = load_workbook(tower.root / tower.export_timesheet("month"))
    rows = list(book.active.iter_rows(values_only=True))
    assert rows[0][0] == "Date" and rows[-1][5] == 3.0                              # 2 + 1, the voided entry excluded
    assert tower.export_timesheet("week").startswith("00_DASHBOARD/EXPORTS/TIMESHEET_WEEK_")


def test_natural_language_updates_keep_history_per_project(tower):
    tower.receive_upload("QID-EV-LC-CCTV-010-REV02.pdf", PDF + b"cctv", "Qiddiya East Village CCTV shop drawing")
    doc = tower.project_detail("Qiddiya")["drawing_register"][0]
    assert (doc["type"], doc["discipline"]) == ("shop", "Low Current")
    assert "SHOP_DRAWINGS/CURRENT" in doc["current_file"]

    tower.record_submission("Qiddiya", "CCTV", "2026-11-01", transmittal="TR-0042", reviewer="Consultant A")
    tower.record_response("Qiddiya", "QID-EV-LC-CCTV-010", "c", "Installation details and cable sizing pending", "2026-11-10")
    tower.record_submission("Qiddiya", "cctv 010", "2026-11-12", transmittal="TR-0051")
    detail = tower.project_detail("Qiddiya")
    assert [s["transmittal"] for s in detail["submission_history"]] == ["TR-0042", "TR-0051"]     # each kept separately
    assert detail["responses"][0]["code"] == "C" and detail["drawing_register"][0]["status"] == "Submitted"

    a = tower.add_action("Qiddiya", "Subcontractor to issue cable sizing", discipline="CCTV", area="East Village",
                         assigned_to="Subcontractor", target_date="2020-01-01", related_drawing="CCTV")
    k = tower.add_action("KSU", "Earthing calculation pending: soil resistivity report not received", discipline="earthing")
    assert tower.project_detail("KSU")["actions"][0]["discipline"] == "Electrical"
    assert len(tower.project_detail("Qiddiya")["actions"]) == 1                     # KSU's update did not touch Qiddiya
    overview = {p["code"]: p for p in tower.overview()}
    assert overview["QIDDIYA"]["overdue_actions"] == 1 and overview["KSU"]["open_actions"] == 1
    with pytest.raises(TowerError, match="closure evidence"):
        tower.update_action(a["action_id"], status="closed")
    tower.update_action(a["action_id"], status="closed", closure_evidence="Cable schedule rev 2 received")
    assert tower.project_detail("Qiddiya")["actions"][0]["status"] == "closed"
    with pytest.raises(TowerError, match="No document"):
        tower.record_response("KSU", "CCTV", "B")
    with pytest.raises(TowerError):
        tower.update_action(9999, update="x")
    assert k["project"] == "KSU"


def test_everything_is_audited_and_ids_are_stable(tower):
    tower.receive_upload("TEST-QID-EV-ELE-001-REV04.pdf", PDF, "Qiddiya East Village electrical")
    ops = [a["operation"] for a in tower.audit_trail()]
    assert {"create_project", "receive_upload", "file_document"} <= set(ops)
    again = Tower(tower.root, str(tower.engine.url.render_as_string(hide_password=False)) if tower.engine.name != "sqlite" else "", owner="Abu")
    again.seed_projects([{"name": "Should Not Appear"}])                            # seeding only happens once
    assert [p["code"] for p in again.overview()] == ["QIDDIYA", "KSU", "MISHRAQ", "SOLUTION_VALLEY"]
    assert again.project_detail("QID")["drawing_register"][0]["document_id"] == 1
    again.engine.dispose()


# ------------------------------------------------------------------ through Lumi

import asyncio                                        # noqa: E402
from types import SimpleNamespace as NS               # noqa: E402

from fastapi.testclient import TestClient             # noqa: E402

from assistant.config import Config                   # noqa: E402
from assistant.server import Devices, create_app      # noqa: E402
from test_assistant import FakeStreamBrain, message, tool_use      # noqa: E402
from test_integrations import FakeTelegram            # noqa: E402


def lumi(tmp_path, monkeypatch, **kw):
    for key in ("ANTHROPIC_API_KEY", "LUMI_TELEGRAM_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    cfg = Config(data_dir=str(tmp_path / "data"), tower_root=str(tmp_path / "TOWER"), owner="Abu")
    return create_app(cfg, Devices(tmp_path / "devices.json"), **kw)


def test_upload_from_phone_then_answer_in_chat(tmp_path, monkeypatch):
    app = lumi(tmp_path, monkeypatch)
    brain, tower = app.state.brain, app.state.tower
    names = set(brain.registry.tools)
    assert {"tower_projects", "tower_confirm_upload", "tower_timesheet_record"} <= names
    assert "file_to_project" not in names                       # one filing system, not two
    with TestClient(app) as client:
        assert client.post("/api/tower/upload?name=a.pdf", content=PDF).status_code == 401      # unpaired phone
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        h = {"X-Lumi-Token": token}
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json()["type"] == "hello" and "tower" in ws.receive_json()

            ok = client.post("/api/tower/upload", params={"name": "TEST-QID-EV-ELE-001-REV04.pdf",
                             "note": "Qiddiya East Village electrical, update the register"}, content=PDF, headers=h).json()
            assert ok["status"] == "filed" and ok["said"].startswith("Filed TEST-QID-EV-ELE-001-REV04.pdf")

            held = client.post("/api/tower/upload", params={"name": "scan0042.pdf"}, content=PDF + b"x", headers=h).json()
            assert held["status"] == "needs_confirmation"
            events = []
            while not (events and events[-1]["type"] == "dashboard" and events[-1]["tower"]["waiting"]):
                e = ws.receive_json()
                if e["type"] != "stats":
                    events.append(e)
            assert any(e["type"] == "card" and e["title"] == "Upload needs confirmation" for e in events)
            assert events[-1]["tower"]["waiting"][0]["job"] == held["job"]

            # The owner answers in chat; Lumi already knows about the upload and passes the answer on.
            assert brain.messages[-2]["content"] == 'I uploaded the file "scan0042.pdf" with no note.'
            assert "which project" in brain.messages[-1]["content"]
            FakeStreamBrain(brain, [
                message(tool_use("t1", "tower_confirm_upload", {"job_id": held["job"], "project": "KSU",
                                 "area": "Main Campus", "discipline": "electrical", "revision": "1"})),
                message(NS(type="text", text="Filed under KSU, Main Campus, electrical."), stop="end_turn")])
            ws.send_json({"type": "say", "text": "KSU main campus electrical rev 1"})
            while ws.receive_json()["type"] != "reply_done":
                pass
        assert json.loads(brain.messages[-2]["content"][0]["content"])["status"] == "filed"

        overview = client.get("/api/tower/overview", headers=h).json()
        assert overview["waiting"] == [] and [u["status"] for u in overview["uploads"]] == ["filed", "filed"]
        register = client.get("/api/tower/project/QIDDIYA", headers=h).json()["drawing_register"]
        assert "D:" not in json.dumps(register) and not register[0]["current_file"].startswith("/")
        got = client.get(f"/api/tower/file/{register[0]['view']}")                  # the signed link is the permission
        assert got.content == PDF and "inline" in got.headers["content-disposition"]
        assert client.get("/api/tower/file/1.9999999999.forged").status_code == 403
        assert client.get("/api/tower/project/ATLANTIS", headers=h).status_code == 404
        assert client.get("/api/tower/overview").status_code == 401


def test_voice_updates_and_telegram_uploads_reach_the_tower(tmp_path, monkeypatch):
    api = FakeTelegram()
    api.files["f9"] = PDF + b"tg"
    app = lumi(tmp_path, monkeypatch, telegram_api=api)
    brain, tower, bridge = app.state.brain, app.state.tower, app.state.telegram
    bridge.chats.append(7)
    doc = {"chat": {"id": 7}, "caption": "Qiddiya East Village CCTV shop drawing rev 2",
           "document": {"file_id": "f9", "file_unique_id": "u", "file_name": "QID-EV-LC-CCTV-010.pdf", "file_size": 30}}
    asyncio.run(bridge.on_file(7, doc))
    assert api.sent[-1][1].startswith("Filed QID-EV-LC-CCTV-010.pdf as QID-EV-LC-CCTV-010 revision 02")
    assert "SHOP_DRAWINGS/CURRENT" in tower.project_detail("Qiddiya")["drawing_register"][0]["current_file"]

    FakeStreamBrain(brain, [
        message(tool_use("a", "tower_record_response", {"project": "Qiddiya", "document": "CCTV", "code": "C",
                                                        "comments": "Installation details and cable sizing pending"}),
                tool_use("b", "tower_action_add", {"project": "Qiddiya", "description": "Cable sizing from subcontractor",
                                                   "discipline": "CCTV", "area": "East Village", "related_drawing": "CCTV"}),
                tool_use("c", "tower_timesheet_record", {"project": "KSU", "hours": 2, "activity": "network model coordination"}),
                tool_use("d", "tower_timesheet_record", {"project": "Atlantis", "hours": 1, "activity": "x"}),
                tool_use("e", "tower_create_project", {"name": "TEST PROJECT ALPHA"})),
        message(NS(type="text", text="Recorded."), stop="end_turn")])
    asyncio.run(brain.handle("the Qiddiya East Village CCTV shop drawing received Code C ..."))
    results = [r["content"] for r in brain.messages[-2]["content"]]
    assert json.loads(results[0])["status"] == "Code C" and json.loads(results[2])["person"] == "Abu"
    assert results[3].startswith("Not done: I could not tell which project 'Atlantis' means")    # refused, not guessed
    assert json.loads(results[4])["code"] == "TEST_PROJECT_ALPHA"
    q = tower.project_detail("Qiddiya")
    assert q["drawing_register"][0]["status"] == "Code C" and q["actions"][0]["related_drawing"] == "QID-EV-LC-CCTV-010"
    assert tower.project_detail("KSU")["timesheets"][0]["hours"] == 2.0 and q["timesheets"] == []
