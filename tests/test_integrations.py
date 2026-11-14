"""Telegram, email, connectors, helper agents, voice lock and the setup wizard."""
import asyncio
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from assistant import setup_wizard
from assistant.config import Config, load_secrets
from assistant.server import Devices, create_app
from assistant.telegram import TelegramBridge
from assistant.tools import connectors, email_tools
from assistant.tools.helpers import HELPERS
from assistant.voice_id import MODEL_NAME, VoiceID

from test_assistant import FakeStreamBrain, TEXT, message, tool_use


def make_app(tmp_path, monkeypatch, **cfg):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LUMI_TELEGRAM_TOKEN", raising=False)
    return create_app(Config(data_dir=str(tmp_path / "data"), **cfg), Devices(tmp_path / "devices.json"))


# ------------------------------------------------------------------ telegram

class FakeTelegram:
    def __init__(self):
        self.sent, self.updates = [], []

    files, documents = {}, []

    def __call__(self, method, params):
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {"ok": True}
        if method == "getFile":
            return {"result": {"file_path": "remote/" + params["file_id"]}}
        batch, self.updates = self.updates, []
        return {"result": batch}

    def download(self, file_path):
        return self.files[file_path.split("/", 1)[1]]

    def send_document(self, chat, path):
        self.documents.append((chat, path.name, path.read_bytes()))

    def incoming(self, chat, text, n=[0]):
        n[0] += 1
        self.updates.append({"update_id": n[0], "message": {"chat": {"id": chat}, "text": text}})


def test_telegram_pairing_conversation_and_approval(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    brain, hub, devices = app.state.brain, app.state.hub, app.state.devices
    target = tmp_path / "note.txt"
    FakeStreamBrain(brain, [
        message(tool_use("t1", "write_file", {"path": str(target), "contents": "hi"})),
        message(TEXT, stop="end_turn"),
    ])
    api = FakeTelegram()
    bridge = TelegramBridge(api, brain, hub, devices, tmp_path / "tg.json")

    async def scenario():
        await bridge.on_text(7, "hello")                        # stranger
        await bridge.on_text(7, "/pair 000000" if devices.pin != "000000" else "/pair 111111")
        pin = devices.pin
        await bridge.on_text(7, f"/pair {pin}")
        assert devices.pin != pin                               # the PIN is used up
        turn = asyncio.create_task(bridge.on_text(7, "save a note"))
        for _ in range(200):                                    # wait for the approval request
            if hub.pending:
                break
            await asyncio.sleep(0.01)
        assert not target.exists()
        await bridge.on_text(7, "yes")
        await turn
        await bridge.on_text(99, "save a note")                 # a different, unpaired chat

    asyncio.run(scenario())
    texts = [t for _, t in api.sent]
    assert "not paired" in texts[0] and "PIN is not right" in texts[1] and texts[2].startswith("Paired")
    assert texts[3].startswith("Approval needed: write file") and "Reply YES or NO" in texts[3]
    assert texts[4] == "Done." and target.read_text() == "hi"
    assert api.sent[5][0] == 99 and "not paired" in texts[5]
    assert json.loads((tmp_path / "tg.json").read_text()) == [7]


def test_telegram_polling_dispatches_messages(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    api = FakeTelegram()
    bridge = TelegramBridge(api, app.state.brain, app.state.hub, app.state.devices, tmp_path / "tg.json")
    api.incoming(5, "hi")
    api.updates.append({"update_id": 500, "message": {"chat": {"id": 5}, "photo": []}})

    async def scenario():
        await bridge.poll_once()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert bridge.offset == 501
    assert sum("not paired" in t for _, t in api.sent) == 2          # the text and the photo, both refused


# --------------------------------------------------------------------- email

RAW = (b"From: =?utf-8?b?QUJDIERldmVsb3BlcnM=?= <pm@abc.example>\r\nTo: me@example.com\r\n"
       b"Subject: EIR revision 3\r\nDate: Fri, 13 Nov 2026 10:00:00 +0400\r\nMessage-ID: <m1@abc>\r\n"
       b"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
       b"<html><style>p{color:red}</style><p>Please confirm <b>COBie</b> by Monday.</p></html>")


class FakeIMAP:
    log = []

    def __init__(self, host):
        FakeIMAP.log.append(("connect", host))

    def login(self, user, password):
        FakeIMAP.log.append(("login", user, password))

    def select(self, box, readonly=False):
        FakeIMAP.log.append(("select", box, readonly))

    def uid(self, command, *args):
        FakeIMAP.log.append((command, args))
        if command == "search":
            return "OK", [b"11 12 13"]
        return "OK", [(b"13 (BODY[] {300}", RAW), b")"]

    def logout(self):
        pass


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.where = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self):
        FakeSMTP.sent.append("tls")

    def login(self, user, password):
        FakeSMTP.sent.append(("login", user, password))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_email_is_off_by_default_and_gated_when_on(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert not any(n.startswith("email_") for n in app.state.brain.registry.tools)

    monkeypatch.setenv("LUMI_EMAIL_PASSWORD", "app-pass")
    monkeypatch.setattr(email_tools.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(email_tools.smtplib, "SMTP", FakeSMTP)
    mail = {**Config().email, "enabled": True, "address": "me@example.com"}
    tools = make_app(tmp_path, monkeypatch, email=mail).state.brain.registry.tools
    assert tools["email_inbox"].confirm and tools["email_read"].confirm and tools["email_send"].confirm
    relaxed = make_app(tmp_path, monkeypatch, email={**mail, "ask_before_reading": False}).state.brain.registry.tools
    assert not relaxed["email_inbox"].confirm and relaxed["email_send"].confirm      # sending always asks

    inbox = json.loads(tools["email_inbox"].handler(limit=2))
    assert inbox["count"] == 2 and inbox["messages"][0]["from"] == "ABC Developers <pm@abc.example>"
    assert inbox["messages"][0]["preview"] == "Please confirm COBie by Monday."
    assert ("select", "INBOX", True) in FakeIMAP.log                                # read-only
    full = json.loads(tools["email_read"].handler(uid="13"))
    assert full["message_id"] == "<m1@abc>" and "COBie" in full["body"]

    assert tools["email_send"].handler(to="pm@abc.example", subject="Re: EIR revision 3",
                                       body="Confirmed.", in_reply_to="<m1@abc>") == "Email sent to pm@abc.example."
    sent = FakeSMTP.sent[-1]
    assert sent["From"] == "me@example.com" and sent["In-Reply-To"] == "<m1@abc>"
    assert sent.get_content().strip() == "Confirmed." and FakeSMTP.sent[0] == "tls"


# ---------------------------------------------------------------- connectors

def test_connector_posts_json_and_needs_approval(tmp_path, monkeypatch):
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received["key"] = self.headers.get("X-Key")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"updated": true}')

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = [{"name": "world_dashboard", "description": "Send {project, percent}",
                "url": f"http://127.0.0.1:{server.server_port}/update", "headers": {"X-Key": "k"}}]
        tools = make_app(tmp_path, monkeypatch, connectors=cfg).state.brain.registry.tools
        tool = tools["use_connector"]
        assert tool.confirm and "world_dashboard: Send {project, percent}" in tool.description
        assert tool.validate({"name": "world_dashboard", "data": {"a": 1}}) is None
        out = tool.handler(name="world_dashboard", data={"project": "Marina Tower", "percent": 26})
        assert 'answered 200: {"updated": true}' in out
        assert received == {"body": {"project": "Marina Tower", "percent": 26}, "key": "k"}
    finally:
        server.shutdown()
    assert "Could not reach" in connectors.call({"name": "x", "url": "http://127.0.0.1:9/"}, {})
    assert "use_connector" not in make_app(tmp_path, monkeypatch).state.brain.registry.tools


def test_status_endpoint_requires_a_paired_device(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        data = client.get("/api/status", headers={"X-Lumi-Token": token}).json()
        assert {"projects", "money", "requests", "ideas"} <= set(data)


# ------------------------------------------------------------------- helpers

def test_helper_agent_runs_its_own_conversation(tmp_path, monkeypatch):
    brain = make_app(tmp_path, monkeypatch).state.brain
    calls = []

    class FakeStream:
        def __init__(self, kwargs):
            calls.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get_final_message(self):
            if len(calls) == 1:
                return NS(stop_reason="pause_turn", content=[NS(type="server_tool_use")])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="Three competitors found.")])

    tool = brain.registry.tools["ask_helper"]
    assert asyncio.run(tool.handler(helper="researcher", task="x")) .startswith("Helper agents need a brain.")
    brain.client = NS(beta=NS(messages=NS(stream=lambda **kw: FakeStream(kw))))
    out = asyncio.run(tool.handler(helper="researcher", task="Find BIM audit competitors in Dubai"))
    assert out == "Three competitors found." and len(calls) == 2                    # resumed after the pause
    assert calls[0]["tools"][0]["type"] == "web_search_20260209" and len(calls[1]["messages"]) == 2
    assert HELPERS["researcher"]["prompt"] in calls[0]["system"]
    asyncio.run(tool.handler(helper="writer", task="Draft an email"))
    assert "tools" not in calls[-1]
    assert tool.validate({"helper": "pilot", "task": "x"}) is not None


# --------------------------------------------------------------------- voice
# Needs the speaker model and two speech recordings of different people:
#   LUMI_TEST_VOICE_MODEL=/path/model.onnx  LUMI_TEST_VOICES=/path/a.wav,/path/b.wav

needs_voice = pytest.mark.skipif(not os.environ.get("LUMI_TEST_VOICE_MODEL"), reason="voice model not provided")


def halves(path):
    w = wave.open(path)
    pcm = w.readframes(w.getnframes())
    mid = len(pcm) // 4 * 2
    return pcm[:mid], pcm[mid:]


@needs_voice
def test_voice_lock_end_to_end(tmp_path, monkeypatch):
    a, b = os.environ["LUMI_TEST_VOICES"].split(",")
    (owner1, owner2), (other1, other2) = halves(a), halves(b)
    voice_dir = tmp_path / "data" / "voice"
    voice_dir.mkdir(parents=True)
    (voice_dir / MODEL_NAME).write_bytes(Path(os.environ["LUMI_TEST_VOICE_MODEL"]).read_bytes())
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        h = {"X-Lumi-Token": token}
        assert client.post("/api/voice/enroll?name=Abu", content=owner1).status_code == 401      # unpaired
        assert client.get("/api/voice", headers=h).json() == {"available": True, "locked": False, "voices": []}
        assert client.post("/api/voice/enroll?name=Abu", content=b"\0" * 64000, headers=h).status_code == 400  # silence
        assert client.post("/api/voice/enroll?name=Abu", content=owner1, headers=h).json() == {"name": "Abu", "samples": 1}
        assert client.get("/api/voice", headers=h).json()["locked"] is True

        good = client.post("/api/voice/verify", content=owner2, headers=h).json()
        bad = client.post("/api/voice/verify", content=other1, headers=h).json()
        assert good["ok"] and good["name"] == "Abu" and good["score"] > 0.6
        assert bad["ok"] is False and bad["score"] < 0.6 and "token" not in bad

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json()["voice"] == {"available": True, "locked": True}
            assert ws.receive_json()["type"] == "dashboard"
            ws.send_json({"type": "say", "text": "what time is it", "via": "voice", "voice_pass": "guess"})
            assert ws.receive_json()["type"] == "voice_rejected"
            ws.send_json({"type": "say", "text": "what time is it", "via": "voice", "voice_pass": good["token"]})
            assert ws.receive_json() == {"type": "user", "text": "what time is it"}

            # Another person's voice needs approval on a paired screen: denied, then approved.
            results = {}

            def enroll(key):
                results[key] = client.post("/api/voice/enroll?name=Guest", content=other1, headers=h).status_code

            for key, approve in (("denied", False), ("approved", True)):
                t = threading.Thread(target=enroll, args=(key,))
                t.start()
                while True:
                    e = ws.receive_json()
                    if e["type"] == "confirm_request":
                        break
                assert e["name"] == "add_voice" and e["input"]["voice"] == "Guest"
                ws.send_json({"type": "confirm", "id": e["id"], "approve": approve})
                t.join(10)
            assert results == {"denied": 403, "approved": 200}
        assert client.post("/api/voice/verify", content=other2, headers=h).json()["name"] == "Guest"
        assert client.post("/api/voice/enroll?name=Abu", content=owner2, headers=h).json()["samples"] == 2  # own voice: no prompt
    assert "Abu" in VoiceID(voice_dir).voices and b"RIFF" not in (voice_dir / "voices.json").read_bytes()


def test_voice_lock_is_inactive_without_the_model(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        token = client.post("/api/pair", json={"pin": app.state.devices.pin}).json()["token"]
        h = {"X-Lumi-Token": token}
        assert client.get("/api/voice", headers=h).json()["available"] is False
        assert client.post("/api/voice/enroll?name=Abu", content=b"x", headers=h).status_code == 400


# --------------------------------------------------------------------- setup

def test_setup_wizard_writes_config_and_secrets(tmp_path, monkeypatch):
    answers = iter(["Lumi", "Abu", "AED", "4", "sk-test-key", "", "y", "abu@gmail.com", "", "", "mail-pass",
                    "D:/Projects", "D:/Projects/Marina", "", "y"])
    said, installed = [], []
    setup_wizard.run(tmp_path, ask=lambda _: next(answers), say=said.append,
                     install_voice=lambda model, say: installed.append(model))
    config = json.loads((tmp_path / "config.json").read_text())
    assert config["name"] == "Lumi" and config["owner"] == "Abu" and config["currency"] == "AED"
    assert config["email"]["enabled"] and config["email"]["imap_host"] == "imap.gmail.com"
    secrets = (tmp_path / "secrets.env").read_text()
    assert "ANTHROPIC_API_KEY=sk-test-key" in secrets and "LUMI_EMAIL_PASSWORD=mail-pass" in secrets
    assert "LUMI_TELEGRAM_TOKEN" not in secrets and "mail-pass" not in (tmp_path / "config.json").read_text()
    assert installed == [tmp_path / "data" / "voice" / MODEL_NAME]
    assert config["projects_root"] == "D:/Projects"
    assert config["watch"] == [{"name": "Marina", "path": "D:/Projects/Marina", "kind": "folder"}]

    # Running it again and pressing Enter keeps every answer.
    keep = iter([""] * 20)
    setup_wizard.run(tmp_path, ask=lambda _: next(keep), say=said.append, install_voice=lambda *a: None)
    assert json.loads((tmp_path / "config.json").read_text()) == config
    assert "ANTHROPIC_API_KEY=sk-test-key" in (tmp_path / "secrets.env").read_text()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LUMI_EMAIL_PASSWORD", "from-environment")
    load_secrets(tmp_path / "secrets.env")
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-key" and os.environ["LUMI_EMAIL_PASSWORD"] == "from-environment"
    monkeypatch.delenv("ANTHROPIC_API_KEY")


# ------------------------------------------------------- watcher and tasks

from assistant.tasks import Tasks                     # noqa: E402
from assistant.watcher import Watcher                 # noqa: E402


def test_watcher_reports_folder_and_log_changes(tmp_path):
    import datetime as dt
    proj, log = tmp_path / "proj", tmp_path / "clash.log"
    (proj / "models").mkdir(parents=True)
    (proj / "models" / "tower.rvt").write_text("v1")
    (proj / "~$tower.rvt").write_text("lock file")
    log.write_text("old line\n")
    sources = [{"name": "Marina", "path": str(proj), "kind": "folder"},
               {"name": "Clash log", "path": str(log), "kind": "log"},
               {"name": "Missing", "path": str(tmp_path / "nope"), "kind": "folder"}]
    w = Watcher(sources, tmp_path / "watch")
    assert w.scan() == []                                         # first look only remembers
    assert w.scan() == []

    (proj / "models" / "tower.rvt").write_text("version 2 is longer")
    (proj / "site.ifc").write_text("x")
    with log.open("a") as f:
        f.write("12 new clashes in L03\nrun complete\nhalf written")
    events = Watcher(sources, tmp_path / "watch").scan()          # state survives a restart
    got = {(e["source"], e["kind"], e["detail"]) for e in events}
    assert got == {("Marina", "added", "site.ifc"), ("Marina", "changed", "models/tower.rvt"),
                   ("Clash log", "log", "12 new clashes in L03"), ("Clash log", "log", "run complete")}

    (proj / "site.ifc").unlink()
    with log.open("a") as f:
        f.write(" line now finished\n")
    events = w.__class__(sources, tmp_path / "watch").scan()
    assert {(e["kind"], e["detail"]) for e in events} == {("removed", "site.ifc"), ("log", "half written line now finished")}

    log.write_text("fresh after rotation\n")                      # log cleared: start again from the top
    w2 = Watcher(sources, tmp_path / "watch")
    assert [e["detail"] for e in w2.scan()] == ["fresh after rotation"]
    assert w2.recent(2)[0]["detail"] == "fresh after rotation" and len(w2.recent(50, "marina")) == 3
    summary = w2.summary()
    assert summary["sources"][0]["files"] == 1 and summary["sources"][2]["files"] == 0      # a folder that does not exist yet

    for i in range(60):
        (proj / f"sheet{i}.pdf").write_text("x")
    assert [e["detail"] for e in w2.scan()] == ["60 files added"]  # bulk changes are summarised


def test_tasks_run_once_or_repeat_and_survive_restart(tmp_path):
    import datetime as dt
    tasks = Tasks(tmp_path / "tasks.json")
    assert "YYYY-MM-DD HH:MM" in tasks.add("x", "tomorrow 9am")
    assert "Scheduled task" in tasks.add("send the weekly report", "2026-11-16 09:00", "weekly")
    tasks.add("back up the model", "2026-11-14 18:00")
    now = dt.datetime(2026, 11, 14, 18, 0, 30)
    assert [t["instruction"] for t in tasks.take_due(now)] == ["back up the model"]
    assert tasks.take_due(now) == []                               # a one-off never runs twice
    later = dt.datetime(2026, 12, 1, 12, 0)                        # PC was off for two weeks
    due = Tasks(tmp_path / "tasks.json").take_due(later)
    assert [t["instruction"] for t in due] == ["send the weekly report"]
    reloaded = Tasks(tmp_path / "tasks.json")
    assert reloaded.items[0]["run_at"] == "2026-12-07 09:00"       # one catch-up run, then the next Monday
    assert "Cancelled" in reloaded.cancel(reloaded.items[0]["id"]) and "No task" in reloaded.cancel("zzz")


def test_scheduled_task_runs_unattended_and_reports_to_phone(tmp_path, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    api = FakeTelegram()
    app = create_app(Config(
        data_dir=str(tmp_path / "data"),
        watch=[{"name": "Marina", "path": str(proj), "kind": "folder"}],
        connectors=[{"name": "world_dashboard", "url": f"http://127.0.0.1:{server.server_port}/", "auto": True},
                    {"name": "manual_only", "url": "http://127.0.0.1:9/"}],
    ), Devices(tmp_path / "devices.json"), telegram_api=api)
    brain, state = app.state.brain, app.state
    state.telegram.chats.append(7)
    target = tmp_path / "report.txt"
    FakeStreamBrain(brain, [
        message(tool_use("t1", "recent_changes", {}), tool_use("t2", "write_file", {"path": str(target), "contents": "r"})),
        message(NS(type="text", text="Report written. One new model arrived."), stop="end_turn"),
    ])

    async def scenario():
        assert await state.watch_once() == []
        (proj / "tower.rvt").write_text("x")
        events = await state.watch_once()
        assert [e["detail"] for e in events] == ["tower.rvt"]
        run = asyncio.create_task(state.run_task({"id": "abc123", "instruction": "write the daily report"}))
        for _ in range(300):
            if state.hub.pending:
                break
            await asyncio.sleep(0.01)
        await state.telegram.on_text(7, "YES")                     # approved from the phone
        await run

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
    assert len(received) == 1 and received[0]["events"][0]["detail"] == "tower.rvt"     # auto connector only
    assert received[0]["status"]["watch"]["events"][0]["source"] == "Marina"
    texts = [t for _, t in api.sent]
    assert texts[0].startswith("Approval needed for a scheduled task: write file")
    assert texts[1] == "Task finished: write the daily report\n\nReport written. One new model arrived."
    assert target.read_text() == "r"
    assert brain.messages[0]["content"].startswith("Scheduled task, running now while I am away: write the daily")
    changes = json.loads(brain.messages[2]["content"][0]["content"])
    assert changes["events"][0]["detail"] == "tower.rvt"
    tools = brain.registry.tools
    assert {"task_schedule", "task_list", "task_cancel", "notify_owner", "recent_changes"} <= set(tools)
    assert asyncio.run(tools["notify_owner"].handler(message="hello")) == "Sent to the phone and the screens."


# -------------------------------------------------------------------- filing

from assistant.tools.filing import Filing            # noqa: E402


def test_filing_keeps_the_folder_arrangement_and_never_overwrites(tmp_path):
    root, inbox = tmp_path / "Projects", tmp_path / "inbox"
    f = Filing(str(root), ["01_Requirements", "03_Models", "99_Incoming"], inbox)
    assert json.loads(f.folders())["projects"] == []
    assert "3 standard subfolders" in f.create("Marina Tower")
    assert sorted(p.name for p in (root / "Marina Tower").iterdir()) == ["01_Requirements", "03_Models", "99_Incoming"]
    assert "subfolder" in f.create("marina", "03_Models/Structure") and (root / "Marina Tower/03_Models/Structure").is_dir()
    assert "Already exists" in f.create("marina", "03_Models")

    (inbox / "tower.rvt").write_text("v1")
    assert "no project folder" in f.file("tower.rvt", "Metro Depot", "03_Models")           # never guesses a project
    assert f.file("tower.rvt", "marina", "03_Models/Structure").startswith("Filed as Marina Tower")
    assert (root / "Marina Tower/03_Models/Structure/tower.rvt").read_text() == "v1" and not (inbox / "tower.rvt").exists()
    (inbox / "tower.rvt").write_text("v2")
    assert "tower_2.rvt" in f.file("tower.rvt", "marina", "03_Models/Structure")            # kept both
    (inbox / "scan.pdf").write_text("x")
    assert "EIR_rev3.pdf" in f.file("scan.pdf", "marina", "01_Requirements", "EIR_rev3")    # renamed, extension kept

    (inbox / "x.txt").write_text("x")
    assert "outside the project folder" in f.file("x.txt", "marina", "../../elsewhere")
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    assert "only file things from the inbox" in f.file(str(outside), "marina", "03_Models") and outside.exists()
    assert "No such file" in f.file("missing.pdf", "marina", "03_Models")

    f.create("Marina Mall")
    assert "No single project" in f.folders("marina")                                       # ambiguous name
    tree = json.loads(f.folders("marina tower"))
    assert tree["folders"]["03_Models"]["subfolders"] == ["Structure"] and tree["folders"]["01_Requirements"]["files"] == 1
    assert json.loads(f.folders())["files_waiting_in_inbox"] == ["x.txt"]
    assert "No projects folder is set" in Filing("", [], tmp_path / "i2").folders()


def test_file_sent_from_phone_is_filed_and_dashboard_follows(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = tmp_path / "Projects"
    api = FakeTelegram()
    api.files["f1"] = b"%PDF clash report"
    app = create_app(Config(data_dir=str(tmp_path / "data"), projects_root=str(root)),
                     Devices(tmp_path / "devices.json"), telegram_api=api)
    brain, state = app.state.brain, app.state
    bridge = state.telegram
    bridge.chats.append(7)
    FakeStreamBrain(brain, [
        message(NS(type="text", text="Which project is this clash report for?"), stop="end_turn"),
        message(tool_use("t1", "project_folder_create", {"project": "Marina Tower"})),
        message(tool_use("t2", "file_to_project", {"source": "Clash report L03.pdf", "project": "Marina Tower",
                                                   "subfolder": "05_Clash_and_Coordination"})),
        message(NS(type="text", text="Filed under Marina Tower, clash and coordination."), stop="end_turn"),
        message(tool_use("t3", "send_file_to_phone",
                         {"path": str(root / "Marina Tower/05_Clash_and_Coordination/Clash report L03.pdf")})),
        message(NS(type="text", text="Sent."), stop="end_turn"),
    ])
    doc = {"chat": {"id": 7}, "document": {"file_id": "f1", "file_unique_id": "u1",
                                           "file_name": "Clash report L03.pdf", "file_size": 17}}

    async def scenario():
        await state.watch_once()                                   # baseline before anything is filed
        await bridge.on_file(7, doc)                               # no note: Lumi asks which project
        await bridge.on_text(7, "Marina Tower")                    # the answer; no approval is needed to file
        events = await state.watch_once()
        await bridge.on_text(7, "send it back to me")
        await bridge.on_file(7, {"chat": {"id": 7}, "document": {"file_id": "big", "file_unique_id": "b",
                                                                 "file_name": "model.rvt", "file_size": 300_000_000}})
        await bridge.on_file(99, doc)                              # stranger
        return events

    events = asyncio.run(scenario())
    filed = root / "Marina Tower" / "05_Clash_and_Coordination" / "Clash report L03.pdf"
    assert filed.read_bytes() == b"%PDF clash report"
    assert sorted(p.name for p in (root / "Marina Tower").iterdir()) == sorted(Config().folder_template)
    assert not list((tmp_path / "data" / "inbox").iterdir())                                # inbox emptied
    assert 'saved in your inbox as "Clash report L03.pdf"' in brain.messages[0]["content"]
    texts = [t for _, t in api.sent]
    assert texts[0] == "Which project is this clash report for?"
    assert texts[1] == "Filed under Marina Tower, clash and coordination."
    assert any(e["source"] == "Projects" and e["detail"].endswith("Clash report L03.pdf") for e in events)
    assert api.documents == [(7, "Clash report L03.pdf", b"%PDF clash report")]
    assert "larger than 20 MB" in texts[3] and "not paired" in texts[4]
    assert not any(e.get("confirm") for e in [])                                            # (filing tools are approval-free)
    tools = brain.registry.tools
    assert not tools["file_to_project"].confirm and not tools["project_folder_create"].confirm


# ---------------------------------------------------------------- free brain

from assistant import free_brain                      # noqa: E402


class FakeProvider(BaseHTTPRequestHandler):
    """A stand-in for a free OpenAI-compatible provider."""
    requests, replies, status = [], [], 200

    def do_GET(self):
        self._send(200, {"data": [{"id": "models/flash-free"}, {"id": "models/pro-paid"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.requests.append({"auth": self.headers.get("Authorization"), **body})
        if FakeProvider.status != 200:
            return self._send(FakeProvider.status, {"error": {"message": "quota exceeded"}})
        self._send(200, {"choices": [{"message": FakeProvider.replies.pop(0)}]})

    def _send(self, code, payload):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def provider():
    FakeProvider.requests, FakeProvider.replies, FakeProvider.status = [], [], 200
    server = HTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def call(id, name, args):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_free_brain_talks_uses_tools_and_asks_approval(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("LUMI_FREE_API_KEY", "free-key")
    app = make_app(tmp_path, monkeypatch, provider="free", free_base_url=provider, free_model="flash-free")
    brain, hub = app.state.brain, app.state.hub
    assert brain.online and brain.free and brain.brain_name == "free: flash-free"
    target = tmp_path / "note.txt"
    FakeProvider.replies = [
        {"role": "assistant", "content": None, "tool_calls": [
            call("c1", "get_system_status", {}), call("c2", "write_file", {"path": str(target), "contents": "hi"})]},
        {"role": "assistant", "content": "Saved your note."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c3", "function": {"name": "write_file", "arguments": "{broken"}}]},
        {"role": "assistant", "content": "I could not do that."},
    ]
    seen = []

    async def sink(event):
        seen.append(event)
        if event["type"] == "confirm_request":
            hub.resolve(event["id"], True)

    asyncio.run(brain.handle("save a note", sink=sink))
    assert target.read_text() == "hi" and [e["text"] for e in seen if e["type"] == "delta"] == ["Saved your note."]
    first, second = FakeProvider.requests[0], FakeProvider.requests[1]
    assert first["auth"] == "Bearer free-key" and first["model"] == "flash-free"
    assert first["messages"][0]["role"] == "system" and "Lumi" in first["messages"][0]["content"]
    names = [t["function"]["name"] for t in first["tools"]]
    assert "file_to_project" in names and "additionalProperties" not in first["tools"][0]["function"]["parameters"]
    tool_msgs = [m for m in second["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"] and "Wrote 2 characters" in tool_msgs[1]["content"]
    assert [t["text"] for t in brain.memory.transcript()] == ["save a note", "Saved your note."]

    asyncio.run(brain.handle("again", sink=sink))                 # broken tool arguments are sent back, not run
    assert "INVALID_JSON" in FakeProvider.requests[3]["messages"][-1]["content"]

    FakeProvider.status = 429
    seen.clear()
    before = len(brain.messages)
    asyncio.run(brain.handle("one more", sink=sink))
    assert "limit is used up" in next(e["text"] for e in seen if e["type"] == "error") and len(brain.messages) == before

    FakeProvider.status, FakeProvider.replies = 200, [{"role": "assistant", "content": "Market looks small."}]
    out = asyncio.run(brain.registry.tools["ask_helper"].handler(helper="business_analyst", task="BIM audits in Dubai"))
    assert out == "Market looks small." and "tools" not in FakeProvider.requests[-1]
    assert "cannot search the web" in FakeProvider.requests[-1]["messages"][0]["content"]


def test_setup_offers_free_brains_first(tmp_path, provider, monkeypatch):
    monkeypatch.setitem(free_brain.PROVIDERS["gemini"], "base_url", provider)
    answers = iter(["", "Abu", "", "", "free-key", "flash-free", "", "n", "", "", "", "n"])
    said = []
    setup_wizard.run(tmp_path, ask=lambda _: next(answers), say=said.append)   # Enter = option 1, the free one
    config = json.loads((tmp_path / "config.json").read_text())
    assert config["provider"] == "free" and config["free_model"] == "flash-free" and config["free_base_url"] == provider
    assert "LUMI_FREE_API_KEY=free-key" in (tmp_path / "secrets.env").read_text()
    assert any("flash-free" in line and "pro-paid" in line for line in said)             # listed from the provider
    assert not any("brain offline" in line for line in said)
    assert free_brain.list_models(provider, "k") == ["flash-free", "pro-paid"]


def test_installation_check_reports_each_part_without_secrets(tmp_path, provider, monkeypatch):
    from assistant import check
    for key in ("ANTHROPIC_API_KEY", "LUMI_TELEGRAM_TOKEN", "LUMI_EMAIL_PASSWORD", "LUMI_FREE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    said = []
    rows = {item: (status, detail) for status, item, detail in check.run(tmp_path, say=said.append)}
    assert rows["Brain"][0] == check.FAIL and "option 1" in rows["Brain"][1]            # nothing set up yet
    assert rows["Telegram"][0] == check.OFF and rows["Email"][0] == check.OFF
    assert rows["Projects folder"][0] == check.WARN and rows["Settings file"][0] == check.WARN

    projects = tmp_path / "Projects"
    (projects / "Marina Tower").mkdir(parents=True)
    (tmp_path / "config.json").write_text(json.dumps({
        "provider": "free", "free_base_url": provider, "free_model": "flash-free", "data_dir": str(tmp_path / "data"),
        "projects_root": str(projects), "watch": [{"name": "Gone", "path": str(tmp_path / "nope"), "kind": "folder"}]}))
    (tmp_path / "secrets.env").write_text("LUMI_FREE_API_KEY=super-secret-key\n")
    FakeProvider.replies = [{"role": "assistant", "content": "ready"},
                            {"role": "assistant", "content": None, "tool_calls": [call("c1", "get_time", {})]}]
    rows = {item: (status, detail) for status, item, detail in check.run(tmp_path, say=said.append)}
    monkeypatch.delenv("LUMI_FREE_API_KEY", raising=False)
    assert rows["Brain"] == (check.OK, "free model flash-free answered: 'ready'")
    assert rows["Brain can use tools"] == (check.OK, "yes")
    assert rows["Projects folder"][0] == check.OK and "1 project folders" in rows["Projects folder"][1]
    assert rows["Watching: Gone"][0] == check.FAIL
    report = (tmp_path / "lumi-check.txt").read_text()
    assert "super-secret-key" not in report and "[OK  ] Brain:" in report and "1 problem(s)" in report
