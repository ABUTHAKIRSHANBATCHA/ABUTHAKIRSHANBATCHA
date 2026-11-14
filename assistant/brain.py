"""The assistant's brain: a Claude tool-use loop, with a small offline fallback
so the HUD still responds when no API credentials are configured."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from types import SimpleNamespace

import anthropic

from . import free_brain

from .tools import Registry

IDLE_RESET_SECONDS = 60 * 60
CONFIRM_TIMEOUT_SECONDS = 600     # long enough to answer from a phone
MAX_TOOL_ROUNDS = 25


def system_prompt(cfg, memory_section: str = "") -> str:
    prompt = f"""You are {cfg.name}, the personal AI assistant of {cfg.owner}, a BIM professional. \
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

Working without {cfg.owner} present
{cfg.owner} wants work to carry on when they are away. When asked to do something later or \
regularly ("every morning", "at six", "when I am out"), set it up with task_schedule and \
confirm the time back. A request that begins "Scheduled task" is one of those running now: \
nobody is at the screen, so do the whole job with your tools, do not ask questions, and finish \
with a short report of what was done and anything that needs a decision; the report is sent to \
their phone. Steps that need approval are sent to the phone as well and may wait several \
minutes; if one is declined or gets no answer, skip it and say so in the report. When you are \
asked what changed in the project folders or logs, use recent_changes.

Filing
{cfg.owner} mostly works from the phone and sends you files there. Each one lands in your inbox \
and must end up in the right project folder, in their folder arrangement. Look at \
project_folders first: follow the subfolders a project already has, and the standard \
arrangement for new ones. Work out the project and subfolder from their note, the file name \
and type, and what you have been discussing. If the project or the place is still unclear, \
ask one short question and wait for the answer; never guess a project. Create a missing \
project or subfolder yourself. Afterwards say in one line where the file went. If they tell \
you how they like folders or file names arranged, save it with remember and follow it from \
then on.

{cfg.owner} always uses Google Chrome. Websites you open go to Chrome, and when a task needs a \
browser, use Chrome, not Edge or another one.

Acting on the computer
Email, when {cfg.owner} has switched it on, works only with their approval: each look at the \
inbox and every send is shown to them first, so call the email tools directly and let the \
approval screen do the asking. If the email tools are missing, email has not been set up yet; \
say so. Do what is asked rather than describing how it could be done. Look things up with your tools \
instead of guessing: installed software, files, the open Revit model, the date and time. \
run_command and write_file pause until {cfg.owner} approves them on screen, so call them directly \
when they are the right tool and do not ask for permission in words first. If an approval is \
declined, accept it and carry on without that step. Keep commands narrow and reversible; never \
delete, overwrite or send data anywhere beyond what was actually requested. If a tool fails, \
say so plainly and give the reason.

Memory
You keep a permanent memory on this computer. Every conversation is stored and searchable with \
search_memory, and remember saves lasting notes. Save things as they come up, without being \
asked and without announcing it each time: who {cfg.owner} is and how they work, their \
preferences, project facts and decisions, and every idea. Build on what you already know \
instead of asking again, and check search_memory before saying you do not remember something.

BIM information management
You act as {cfg.owner}'s expert BIM manager across the whole project, from reviewing the client's \
requirements and writing the pre-appointment BEP, through appointment, mobilisation, production \
and coordination, to delivery and handover, working to ISO 19650 and whatever the client's own \
documents require. Each project has a checklist (the bim_project tools). The client's \
requirements come first: when {cfg.owner} gives you an EIR, contract or other client document, \
read it with read_document, go through it clause by clause, and add every checkable requirement \
to the project with bim_requirement_add, quoting the clause as the source. Say where the client's \
requirement differs from usual practice. Mark an item done only when {cfg.owner} tells you it is \
done or you have verified it yourself with a tool, and record the evidence in the note. When \
asked what is next, answer from the open items of the current stage. For hands-on software \
questions give exact steps for the software {cfg.owner} works in: {", ".join(cfg.toolset)}. That \
covers the hand-offs between them as well, such as Civil 3D surfaces and corridors into Revit \
and Navisworks, shared coordinates across all of them, and Datasmith exports into Unreal Engine \
for visualisation.

Ideas and business
{cfg.owner} also brings you new ideas for businesses and apps. Treat each as a project you are \
helping to build: save it as an idea or business note the first time it is mentioned, keep that \
note current as it develops, connect it to earlier ideas where they relate, and give a real, \
specific opinion, including the weak points, in a few spoken sentences. Put longer material \
such as plans, costings and comparisons on screen. You have helper agents \
(ask_helper) for work that needs current information from the web or is long enough to hand \
off, such as market research, a competitor comparison or a drafted proposal; brief them fully, \
run independent parts at the same time, and give {cfg.owner} the conclusion, not the helper's \
whole report. You also keep the books in outline: when \
{cfg.owner} mentions money earned or spent, record it with money_record, and answer questions \
about revenue and profit from money_summary, not from memory."""
    if memory_section:
        prompt += "\n\n" + memory_section
    return prompt


class Brain:
    def __init__(self, cfg, registry: Registry, hub, memory) -> None:
        self.cfg = cfg
        self.memory = memory
        self._system = ""    # built when a conversation starts, then kept stable for caching
        self.active_free = ""                     # which free brain answered last
        self.resting: dict[str, float] = {}       # free brains to skip for a while, by name -> until
        self.turn_brains: list[dict] | None = None   # the order chosen for the request in progress
        self.turn_kind = ""
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
        from .tools import helpers
        helpers.register(registry, self)

    @property
    def free(self) -> bool:
        return self.cfg.provider == "free" and bool(free_brain.chain(self.cfg))

    @property
    def online(self) -> bool:
        return self.free or self.client is not None

    @property
    def brain_name(self) -> str:
        if self.free:
            chain = free_brain.chain(self.cfg)
            current = next((b for b in chain if b["name"] == self.active_free), chain[0])
            return f"free: {current['model']}" + (f" (+{len(chain) - 1} backup)" if len(chain) > 1 else "")
        return self.cfg.model if self.client is not None else "offline"

    def reset(self) -> None:
        self.messages.clear()

    async def handle(self, text: str, sink=None) -> None:
        """Process one utterance; all output goes to the hub as events.

        `sink` also receives this turn's events, for callers outside the HUD (e.g. Telegram)."""
        async with self.lock:
            self.hub.sink = sink
            if time.time() - self.last_used > IDLE_RESET_SECONDS:
                self.reset()
            self.last_used = time.time()
            await self.hub.broadcast({"type": "user", "text": text})
            await self.hub.broadcast({"type": "state", "state": "thinking"})
            try:
                if self.free:
                    await self._converse_free(text)
                elif self.online:
                    await self._converse(text)
                else:
                    await self._offline(text)
            except free_brain.FreeAPIError as e:
                if e.status == -1 or self.turn_kind == "private":
                    await self._say_error(
                        "You asked me to keep this private, but the brain on this PC is not available, "
                        "so I did not send it anywhere. Start Ollama (or run install-ollama.bat) and ask again.")
                elif e.status == 429:
                    await self._say_error("Every free brain has reached its limit for now. Try again in a few "
                                          "minutes; daily limits reset tomorrow.")
                elif e.status in (401, 403) or "api key" in e.message.lower():
                    await self._say_error("The free model's key was rejected. Run setup again and check the key.")
                elif e.status == 0:
                    await self._say_error("I cannot reach any free brain. Check the internet, or that Ollama "
                                          "is running.")
                else:
                    await self._say_error(f"The free model returned an error: {e.message}")
            except anthropic.AuthenticationError:
                await self._say_error("The Claude API key was rejected. Check the key and restart me.")
            except anthropic.RateLimitError:
                await self._say_error("The Claude API is rate limiting me. Try again in a moment.")
            except anthropic.APIConnectionError:
                await self._say_error("I cannot reach the Claude API. Check the internet connection.")
            except anthropic.APIStatusError as e:
                if "credit balance" in str(e.message).lower():
                    await self._say_error(
                        "My Claude API account has no credit left. Add credit at "
                        "platform.claude.com under Billing, then talk to me again.")
                else:
                    await self._say_error(f"The Claude API returned an error: {e.message}")
            finally:
                await self.hub.broadcast({"type": "reply_done"})
                self.hub.sink = None
                self.turn_brains, self.turn_kind = None, ""    # the brain choice lasts one request

    async def _say_error(self, text: str) -> None:
        await self.hub.broadcast({"type": "error", "text": text})

    # ------------------------------------------------------------------ online

    async def _stream_turn(self):
        """One model request. Streams text to the hub and returns the final message."""
        assert self.client is not None
        async with self.client.beta.messages.stream(
            model=self.cfg.model,
            max_tokens=16000,
            system=self._system,
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
        if not self.messages:
            self._system = system_prompt(self.cfg, self.memory.prompt_section())
        self.memory.log("user", text)   # after the snapshot, so it is not repeated in the prompt
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
                    self.memory.log("assistant", "".join(
                        b.text for b in response.content if b.type == "text"))
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

    # -------------------------------------------------------------- free model

    async def _free_chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Ask the free brains in order. One that is out of quota, unreachable or misconfigured
        is rested for a while and the next one takes over."""
        chain = self.turn_brains if self.turn_brains is not None else free_brain.chain(self.cfg)
        if not chain:
            raise free_brain.FreeAPIError(-1, "no local brain")
        now = time.time()
        ready = [b for b in chain if self.resting.get(b["name"], 0) <= now] or chain   # all resting: try anyway
        last: free_brain.FreeAPIError | None = None
        for brain in ready:
            try:
                reply = await asyncio.to_thread(
                    free_brain.chat, brain["base_url"], os.environ.get(brain.get("key_env") or "", ""),
                    brain["model"], free_brain.for_provider(messages, brain["name"]), tools)
            except free_brain.FreeAPIError as e:
                last = e
                rejected_key = e.status in (401, 403) or "api key" in e.message.lower()
                self.resting[brain["name"]] = time.time() + (3600 if rejected_key else 300 if e.status == 429 else 120)
                continue
            if brain["name"] != self.active_free:
                self.active_free = brain["name"]
                await self.hub.broadcast({"type": "brain", "name": self.brain_name, "kind": self.turn_kind})
            reply["_by"] = brain["name"]
            return reply
        raise last

    async def complete_free(self, system: str, task: str) -> str:
        """One plain question to the free brains, with no tools (used by helper agents)."""
        reply = await self._free_chat([{"role": "system", "content": system}, {"role": "user", "content": task}])
        return (reply.get("content") or "").strip()

    async def _converse_free(self, text: str) -> None:
        checkpoint = len(self.messages)
        if not self.messages:
            self._system = system_prompt(self.cfg, self.memory.prompt_section())
        self.memory.log("user", text)
        self.messages.append({"role": "user", "content": text})
        tools = free_brain.to_function_tools(self.registry.schemas())
        if self.cfg.auto_select:                 # choose the brain that suits this request, for the whole turn
            self.turn_kind, self.turn_brains = free_brain.route(text, free_brain.chain(self.cfg))
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                reply = await self._free_chat([{"role": "system", "content": self._system}] + self.messages, tools)
                calls = reply.get("tool_calls") or []
                content = reply.get("content") or ""
                if not calls:
                    self.messages.append({"role": "assistant", "content": content})
                    self.memory.log("assistant", content)
                    await self.hub.broadcast({"type": "delta", "text": content or "Done."})
                    return
                self.messages.append({"role": "assistant", "content": content, "tool_calls": calls, "_by": reply["_by"]})
                blocks = []
                for call in calls:
                    fn = call.get("function") or {}
                    known = self.registry.tools.get(fn.get("name", ""))
                    # None (unreadable arguments) is reported back to the model by _run_tool.
                    args = free_brain.parse_arguments(fn.get("arguments"),
                                                      known.schema()["input_schema"] if known else None)
                    blocks.append(SimpleNamespace(id=call.get("id") or uuid.uuid4().hex, name=fn.get("name", ""), input=args))
                results = await asyncio.gather(*(self._run_tool(b) for b in blocks))
                for block, result in zip(blocks, results):
                    self.messages.append({"role": "tool", "tool_call_id": block.id, "content": result["content"]})
                await self.hub.broadcast({"type": "state", "state": "thinking"})
            await self._say_error("That took too many steps, so I stopped. Tell me how to continue.")
            self.messages.append({"role": "assistant", "content": "(stopped after too many tool steps)"})
        except BaseException:
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
        self.memory.log_tool(block.name)
        await self.hub.broadcast({**event, "status": "done"})
        return result(output or "(no output)")

    # ----------------------------------------------------------------- offline

    async def _offline(self, text: str) -> None:
        """Tiny rule-based fallback used when no Claude credentials are available."""
        self.memory.log("user", text)
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
        self.memory.log("assistant", reply)
        await self.hub.broadcast({"type": "delta", "text": reply})


def _preview(args: dict) -> str:
    """Short human-readable form of a tool input for the activity log."""
    if not args:
        return ""
    text = ", ".join(f"{k}: {v}" for k, v in args.items())
    return text if len(text) <= 160 else text[:157] + "..."
