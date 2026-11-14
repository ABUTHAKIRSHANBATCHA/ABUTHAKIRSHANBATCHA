"""Telegram bridge: talk to Lumi from the phone anywhere, by text.

Setup: create a bot with @BotFather, put its token in secrets.env as
LUMI_TELEGRAM_TOKEN, start Lumi, then send the bot  /pair <PIN>  using the
pairing PIN shown on the PC. Only paired chats are answered.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

YES = re.compile(r"^(yes|y|approve|ok|okay|go ahead|do it|sari|seri|சரி|ஆம்|ஆமா)\b", re.I)
NO = re.compile(r"^(no|n|deny|cancel|stop|vendam|வேண்டாம்|இல்லை)\b", re.I)


class http_api:
    """The real Telegram Bot API over HTTPS."""

    def __init__(self, token: str) -> None:
        self.token = token

    def __call__(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(f"https://api.telegram.org/bot{self.token}/{method}", data=data, timeout=40) as r:
            return json.loads(r.read())

    def download(self, file_path: str) -> bytes:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{file_path}", timeout=120) as r:
            return r.read()

    def send_document(self, chat: int, path: Path) -> dict:
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendDocument", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=300) as r:
            return json.loads(r.read())


MAX_DOWNLOAD = 20 * 1024 * 1024       # Telegram lets bots fetch files up to 20 MB
MAX_UPLOAD = 50 * 1024 * 1024         # and send files up to 50 MB


class TelegramBridge:
    def __init__(self, api, brain, hub, devices, store: Path, after_turn=None, inbox: Path | None = None) -> None:
        self.api, self.brain, self.hub, self.devices = api, brain, hub, devices
        self.inbox = inbox or store.parent / "inbox"
        self.upload = None        # set when the Project Control Tower handles incoming files
        self.store, self.after_turn = store, after_turn
        self.chats: list[int] = json.loads(store.read_text()) if store.exists() else []
        self.offset = 0

    async def notify(self, text: str) -> int:
        """Message every paired chat; returns how many were reached."""
        for chat in self.chats:
            await self.send(chat, text)
        return len(self.chats)

    async def send(self, chat: int, text: str) -> None:
        for start in range(0, len(text), 4000):          # Telegram's message size limit
            await asyncio.to_thread(self.api, "sendMessage", {"chat_id": chat, "text": text[start:start + 4000]})

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)                   # network trouble: wait and retry

    async def poll_once(self) -> None:
        result = await asyncio.to_thread(self.api, "getUpdates", {"offset": self.offset, "timeout": 25})
        for update in result.get("result", []):
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            chat = (message.get("chat") or {}).get("id")
            if chat is None:
                continue
            if "text" in message:
                asyncio.create_task(self.on_text(chat, message["text"].strip()))
            elif "document" in message or "photo" in message:
                asyncio.create_task(self.on_file(chat, message))
            else:
                await self.send(chat, "I can read text, documents and photos here. Voice notes are not supported yet.")

    async def on_file(self, chat: int, message: dict) -> None:
        """Save a document or photo sent from the phone into the inbox, then let the brain file it."""
        if chat not in self.chats:
            await self.send(chat, "This chat is not paired. Send  /pair 123456  with the PIN shown on the PC.")
            return
        if "document" in message:
            item = message["document"]
            name = item.get("file_name") or f"file_{item['file_unique_id']}"
        else:
            item = message["photo"][-1]                               # the largest size
            name = f"photo_{item['file_unique_id']}.jpg"
        if item.get("file_size", 0) > MAX_DOWNLOAD:
            await self.send(chat, f"{name} is larger than 20 MB, which is the most Telegram lets me fetch. "
                                  "Put it in a shared or synced folder on the PC and tell me where it is.")
            return
        try:
            info = await asyncio.to_thread(self.api, "getFile", {"file_id": item["file_id"]})
            data = await asyncio.to_thread(self.api.download, info["result"]["file_path"])
        except Exception as e:
            await self.send(chat, f"I could not download {name}: {e}")
            return
        if self.upload:                                              # the tower files it or asks what is missing
            result = await self.upload(name, data, (message.get("caption") or "").strip())
            await self.send(chat, result["said"])
            return
        self.inbox.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip(" .") or "file"
        target, n = self.inbox / safe, 1
        while target.exists():
            n += 1
            target = self.inbox / f"{Path(safe).stem}_{n}{Path(safe).suffix}"
        target.write_bytes(data)
        caption = (message.get("caption") or "").strip()
        await self.on_text(chat, f"I have sent you a file from my phone. It is saved in your inbox as "
                                 f"\"{target.name}\" ({len(data) // 1024 or 1} KB)."
                                 + (f" My note with it: {caption}" if caption else " I added no note.")
                                 + " File it in the right project folder.")

    async def send_file(self, path: Path) -> str:
        if not self.chats:
            return "No phone is paired on Telegram yet."
        if path.stat().st_size > MAX_UPLOAD:
            return f"{path.name} is larger than 50 MB, the most Telegram lets me send."
        for chat in self.chats:
            await asyncio.to_thread(self.api.send_document, chat, path)
        return f"Sent {path.name} to the phone."

    async def on_text(self, chat: int, text: str) -> None:
        if text.lower().startswith("/pair"):
            pin = text[5:].strip()
            if chat in self.chats:
                await self.send(chat, "This chat is already paired.")
            elif self.devices.check_pin(pin):
                self.chats.append(chat)
                self.store.parent.mkdir(parents=True, exist_ok=True)
                self.store.write_text(json.dumps(self.chats))
                await self.send(chat, f"Paired. You can talk to {self.brain.cfg.name} here now.")
            else:
                await self.send(chat, "That PIN is not right. Check the PIN on the PC (MENU) and try again.")
            return
        if chat not in self.chats:
            await self.send(chat, "This chat is not paired. Send  /pair 123456  with the PIN shown on the PC.")
            return
        if self.hub.pending and (YES.match(text) or NO.match(text)):
            for request_id in list(self.hub.pending):
                self.hub.resolve(request_id, bool(YES.match(text)))
            return

        parts: list[str] = []

        async def sink(event: dict) -> None:
            if event["type"] == "delta":
                parts.append(event["text"])
            elif event["type"] == "error":
                parts.append(event["text"])
            elif event["type"] == "card":
                await self.send(chat, "\n".join(x for x in (event["title"], event["text"], event["url"]) if x))
            elif event["type"] == "confirm_request":
                detail = event["input"].get("command") or json.dumps(event["input"], ensure_ascii=False)[:1500]
                await self.send(chat, f"Approval needed: {event['name'].replace('_', ' ')}\n\n{detail}\n\nReply YES or NO.")

        await self.brain.handle(text, sink=sink)
        await self.send(chat, "".join(parts).strip() or "Done.")
        if self.after_turn:
            await self.after_turn()
