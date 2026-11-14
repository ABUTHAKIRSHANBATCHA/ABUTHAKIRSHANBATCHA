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

    def __call__(self, method, params):
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {"ok": True}
        batch, self.updates = self.updates, []
        return {"result": batch}

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
    assert any("only read text" in t for _, t in api.sent) and any("not paired" in t for _, t in api.sent)


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
    assert asyncio.run(tool.handler(helper="researcher", task="x")) == "Helper agents need the Claude API key."
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
    answers = iter(["Lumi", "Abu", "AED", "sk-test-key", "", "y", "abu@gmail.com", "", "", "mail-pass", "y"])
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
