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
from pathlib import Path

YES = re.compile(r"^(yes|y|approve|ok|okay|go ahead|do it|sari|seri|சரி|ஆம்|ஆமா)\b", re.I)
NO = re.compile(r"^(no|n|deny|cancel|stop|vendam|வேண்டாம்|இல்லை)\b", re.I)


def http_api(token: str):
    def call(method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=40) as r:
            return json.loads(r.read())
    return call


class TelegramBridge:
    def __init__(self, api, brain, hub, devices, store: Path, after_turn=None) -> None:
        self.api, self.brain, self.hub, self.devices = api, brain, hub, devices
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
            if "text" not in message:
                await self.send(chat, "I can only read text messages here for now.")
                continue
            asyncio.create_task(self.on_text(chat, message["text"].strip()))

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
