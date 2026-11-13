"""The assistant's brain: a Claude tool-use loop, with a small offline fallback
so the HUD still responds when no API credentials are configured."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

import anthropic

from .tools import Registry

IDLE_RESET_SECONDS = 60 * 60
CONFIRM_TIMEOUT_SECONDS = 120
MAX_TOOL_ROUNDS = 25


def system_prompt(cfg) -> str:
    return f"""You are {cfg.name}, the personal AI assistant of {cfg.owner}, a BIM professional. \
You run on {cfg.owner}'s own computer and act on it through your tools. {cfg.owner} talks to you \
by voice from a holographic display on the PC and from a phone; both show the same conversation.

How your replies are used
Everything you write is read aloud by a speech engine, so write the way a capable aide speaks: \
short, direct sentences, normally one to three. No markdown, bullet points, emoji, code or file \
paths spelled out in full, because they sound terrible when spoken. When something is better \
read than heard (a link, a list, a number, a path, a snippet), push it to the screens with \
show_on_devices and just say that it is on screen.

Language
{cfg.owner} speaks English and Tamil and switches between them freely. Answer in the language of \
the latest message. When the message is in Tamil, including Tamil typed in English letters, \
answer in Tamil script, because the Tamil voice can only pronounce Tamil script; keep technical \
and product names (Revit, Navisworks, IFC, clash) in English. Speech recognition is imperfect: \
if a message looks garbled, work out the most likely meaning from context, and ask a short \
question only when a wrong guess would matter.

Acting on the computer
Do what is asked rather than describing how it could be done. Look things up with your tools \
instead of guessing: installed software, files, the open Revit model, the date and time. \
run_command and write_file pause until {cfg.owner} approves them on screen, so call them directly \
when they are the right tool and do not ask for permission in words first. If an approval is \
declined, accept it and carry on without that step. Keep commands narrow and reversible; never \
delete, overwrite or send data anywhere beyond what was actually requested. If a tool fails, \
say so plainly and give the reason.

Beyond the computer
{cfg.owner} also uses you as a thinking partner for BIM work (Revit, Dynamo, Navisworks, ACC, IFC, \
standards and coordination) and for building businesses and apps. For those, give a real, \
specific opinion in a few spoken sentences and put any longer material on screen."""


class Brain:
    def __init__(self, cfg, registry: Registry, hub) -> None:
        self.cfg = cfg
        self.registry = registry
        self.hub = hub
        self.messages: list[dict] = []
        self.lock = asyncio.Lock()
        self.last_used = 0.0
        self.client: anthropic.AsyncAnthropic | None = None
        try:
            client = anthropic.AsyncAnthropic()
            # The SDK only complains at request time, so check for credentials now:
            # an API key, an auth token, or a profile saved by `ant auth login`.
            if client.api_key or client.auth_token or client.credentials:
                self.client = client
        except Exception:
            pass

    @property
    def online(self) -> bool:
        return self.client is not None

    def reset(self) -> None:
        self.messages.clear()

    async def handle(self, text: str) -> None:
        """Process one utterance; all output goes to the hub as events."""
        async with self.lock:
            if time.time() - self.last_used > IDLE_RESET_SECONDS:
                self.reset()
            self.last_used = time.time()
            await self.hub.broadcast({"type": "user", "text": text})
            await self.hub.broadcast({"type": "state", "state": "thinking"})
            try:
                if self.online:
                    await self._converse(text)
                else:
                    await self._offline(text)
            except anthropic.AuthenticationError:
                await self._say_error("The Claude API key was rejected. Check the key and restart me.")
            except anthropic.RateLimitError:
                await self._say_error("The Claude API is rate limiting me. Try again in a moment.")
            except anthropic.APIConnectionError:
                await self._say_error("I cannot reach the Claude API. Check the internet connection.")
            except anthropic.APIStatusError as e:
                await self._say_error(f"The Claude API returned an error: {e.message}")
            finally:
                await self.hub.broadcast({"type": "reply_done"})

    async def _say_error(self, text: str) -> None:
        await self.hub.broadcast({"type": "error", "text": text})

    # ------------------------------------------------------------------ online

    async def _stream_turn(self):
        """One model request. Streams text to the hub and returns the final message."""
        assert self.client is not None
        async with self.client.beta.messages.stream(
            model=self.cfg.model,
            max_tokens=16000,
            system=system_prompt(self.cfg),
            tools=self.registry.schemas(),
            messages=self.messages,
            output_config={"effort": self.cfg.effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    await self.hub.broadcast({"type": "delta", "text": event.text})
            return await stream.get_final_message()

    async def _converse(self, text: str) -> None:
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        try:
            json_retries = 0
            for _ in range(MAX_TOOL_ROUNDS):
                try:
                    response = await self._stream_turn()
                except ValueError:
                    # The SDK could not parse a streamed tool input; re-issue the turn.
                    json_retries += 1
                    if json_retries > 2:
                        raise
                    continue
                json_retries = 0

                if response.stop_reason == "pause_turn":
                    self.messages.append({"role": "assistant", "content": response.content})
                    continue
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason == "refusal":
                    self.messages.append({"role": "assistant", "content": [
                        {"type": "text", "text": "(declined)"}]})
                    await self._say_error("I can't help with that request.")
                    return
                if not tool_uses:
                    self.messages.append({"role": "assistant", "content": response.content})
                    return
                if response.stop_reason == "max_tokens":
                    raise RuntimeError("the reply was cut off in the middle of a tool call")

                results = await asyncio.gather(*(self._run_tool(b) for b in tool_uses))
                self.messages.append({"role": "assistant", "content": response.content})
                self.messages.append({"role": "user", "content": list(results)})
                await self.hub.broadcast({"type": "state", "state": "thinking"})
            await self._say_error("That took too many steps, so I stopped. Tell me how to continue.")
            self.messages.append({"role": "assistant", "content": [
                {"type": "text", "text": "(stopped after too many tool steps)"}]})
        except BaseException:
            # Leave history as it was before this utterance so the next one starts clean.
            del self.messages[checkpoint:]
            raise

    async def _run_tool(self, block) -> dict:
        def result(content: str, is_error: bool = False) -> dict:
            out = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            if is_error:
                out["is_error"] = True
            return out

        tool = self.registry.tools.get(block.name)
        if tool is None:
            return result(f"Unknown tool '{block.name}'.", True)
        problem = tool.validate(block.input)
        if problem:
            return result(json.dumps({"INVALID_JSON": json.dumps(block.input), "problem": problem}), True)

        event = {"type": "tool", "id": block.id, "name": block.name, "input": _preview(block.input)}
        if tool.confirm:
            approved = await self.hub.request_confirmation(
                {"id": block.id, "name": block.name, "input": block.input}, CONFIRM_TIMEOUT_SECONDS)
            if not approved:
                await self.hub.broadcast({**event, "status": "denied"})
                return result(f"{self.cfg.owner} declined this action, so it did not run.", True)
        await self.hub.broadcast({**event, "status": "running"})
        try:
            output = await self.registry.run(block.name, block.input)
        except Exception as e:
            await self.hub.broadcast({**event, "status": "error"})
            return result(f"{type(e).__name__}: {e}", True)
        await self.hub.broadcast({**event, "status": "done"})
        return result(output or "(no output)")

    # ----------------------------------------------------------------- offline

    async def _offline(self, text: str) -> None:
        """Tiny rule-based fallback used when no Claude credentials are available."""
        lowered = text.lower().strip()
        reply = None
        m = re.match(r"(?:please\s+)?(?:open|start|launch)\s+(.+)", lowered)
        if m:
            target = m.group(1).strip(" .")
            event = {"type": "tool", "id": uuid.uuid4().hex, "name": "open_target", "input": target}
            await self.hub.broadcast({**event, "status": "running"})
            reply = await self.registry.run("open_target", {"target": target})
            await self.hub.broadcast({**event, "status": "done"})
        elif re.search(r"\b(time|date|status|battery|cpu|memory)\b", lowered):
            s = json.loads(await self.registry.run("get_system_status", {}))
            reply = (f"It is {s['now']}. CPU is at {s['cpu']} percent and memory at "
                     f"{s['memory']} percent.")
        if reply is None:
            reply = ("My brain is offline because no Claude API key is configured, so I can only "
                     "open things and report system status. Add ANTHROPIC_API_KEY and restart me.")
        await self.hub.broadcast({"type": "delta", "text": reply})


def _preview(args: dict) -> str:
    """Short human-readable form of a tool input for the activity log."""
    if not args:
        return ""
    text = ", ".join(f"{k}: {v}" for k, v in args.items())
    return text if len(text) <= 160 else text[:157] + "..."
