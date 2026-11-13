"""Web server: serves the HUD, pairs devices, and relays events between the
brain and every connected screen (PC and phone share one conversation)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
from contextlib import asynccontextmanager

import psutil
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .brain import Brain
from .config import DATA_DIR, WEB_DIR, Config
from .tools import build_registry
from .tools.system import system_stats

LOOPBACK = {"127.0.0.1", "::1"}
MAX_PIN_FAILURES = 5


def lan_addresses() -> list[str]:
    out = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                out.append(a.address)
    return sorted(set(out))


class Devices:
    """Paired devices. Only token hashes are stored on disk."""

    def __init__(self, path=None) -> None:
        self.path = path or DATA_DIR / "devices.json"
        self.pin = self._new_pin()
        self.failures = 0
        self.known: dict[str, str] = {}
        if self.path.exists():
            self.known = json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def _new_pin() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def pair(self, pin: str, device_name: str) -> str | None:
        if not secrets.compare_digest(pin, self.pin):
            self.failures += 1
            if self.failures >= MAX_PIN_FAILURES:
                self.pin, self.failures = self._new_pin(), 0
                print(f"\n  Too many wrong PINs. New pairing PIN: {self.pin}\n")
            return None
        token = secrets.token_urlsafe(32)
        self.known[self._hash(token)] = device_name[:60] or "device"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.known, indent=2), encoding="utf-8")
        self.pin, self.failures = self._new_pin(), 0  # each PIN pairs one device
        return token

    def valid(self, token: str) -> bool:
        return isinstance(token, str) and self._hash(token) in self.known


class Hub:
    """Fan-out of events to all authorised sockets, plus approval requests."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}

    def device_count(self) -> int:
        return len(self.sockets)

    async def broadcast(self, event: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(event)
            except Exception:
                self.sockets.discard(ws)

    async def request_confirmation(self, request: dict, timeout: float) -> bool:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request["id"]] = (future, request)
        await self.broadcast({"type": "confirm_request", **request})
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(request["id"], None)
            await self.broadcast({"type": "confirm_closed", "id": request["id"]})

    def resolve(self, request_id: str, approved: bool) -> None:
        entry = self.pending.get(request_id)
        if entry and not entry[0].done():
            entry[0].set_result(bool(approved))


class PairRequest(BaseModel):
    pin: str
    device_name: str = "phone"


def create_app(cfg: Config, devices: Devices | None = None) -> FastAPI:
    hub = Hub()
    devices = devices or Devices()
    brain = Brain(cfg, build_registry(cfg, hub), hub)

    async def stats_loop() -> None:
        while True:
            if hub.sockets:
                await hub.broadcast({"type": "stats", **system_stats()})
            await asyncio.sleep(3)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(stats_loop())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub, app.state.devices, app.state.brain = hub, devices, brain

    def is_local(scope) -> bool:
        """True only for connections from this PC arriving on the loopback listener."""
        client, server = scope.get("client"), scope.get("server")
        return bool(client and server and client[0] in LOOPBACK and server[1] == cfg.http_port)

    def hello(local: bool) -> dict:
        info = {"type": "hello", "name": cfg.name, "owner": cfg.owner, "online": brain.online,
                "language": cfg.default_language, "local": local}
        if local:
            info["pairing"] = {"pin": devices.pin,
                               "urls": [f"https://{ip}:{cfg.https_port}" for ip in lan_addresses()]}
        return info

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/info")
    async def info(request: Request):
        return {"name": cfg.name, "local": is_local(request.scope)}

    @app.post("/api/pair")
    async def pair(body: PairRequest):
        token = devices.pair(body.pin.strip(), body.device_name)
        if token is None:
            await asyncio.sleep(1)
            raise HTTPException(403, "Wrong PIN")
        await hub.broadcast({"type": "card", "title": "Device paired",
                             "text": f"{body.device_name} is now connected.", "url": ""})
        return {"token": token}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        local = is_local(ws.scope)
        try:
            if not local:
                first = await asyncio.wait_for(ws.receive_json(), timeout=10)
                if first.get("type") != "auth" or not devices.valid(first.get("token", "")):
                    await ws.send_json({"type": "auth_failed"})
                    await ws.close(code=4401)
                    return
            await ws.send_json(hello(local))
            hub.sockets.add(ws)
            for _, request in hub.pending.values():  # show approvals already waiting
                await ws.send_json({"type": "confirm_request", **request})
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "say":
                    text = str(msg.get("text", "")).strip()[:4000]
                    if text:
                        asyncio.create_task(brain.handle(text))
                elif kind == "confirm":
                    hub.resolve(str(msg.get("id", "")), bool(msg.get("approve")))
                elif kind == "reset":
                    brain.reset()
                    await hub.broadcast({"type": "cleared"})
                elif kind == "refresh_pairing" and local:
                    await ws.send_json(hello(True))
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
            pass
        finally:
            hub.sockets.discard(ws)

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app
