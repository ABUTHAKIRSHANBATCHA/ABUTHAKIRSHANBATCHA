"""Helper agents: specialists Lumi can hand a self-contained task to. Each
runs as its own Claude conversation and returns a written result; Lumi can
run several at once."""
from __future__ import annotations

from . import Registry, Tool

HELPERS = {
    "researcher": {
        "web": True,
        "prompt": "You are a research specialist. Search the web for current, primary sources, "
                  "compare what they say, and report the facts that answer the task. Give figures "
                  "with their dates, name each source with its link, and say plainly what you "
                  "could not confirm.",
    },
    "bim_specialist": {
        "web": False,
        "prompt": "You are a senior BIM manager and information manager working to ISO 19650. "
                  "Review or draft what the task asks (BEP sections, responsibility matrices, "
                  "model checks, clash rules, handover data) with exact, checkable content. Point "
                  "out gaps and risks against the client's requirements.",
    },
    "business_analyst": {
        "web": True,
        "prompt": "You are a business analyst for a new venture. Work out the market, customers, "
                  "competitors, pricing, costs and risks for the idea in the task, using web "
                  "search for current facts. Show your numbers and assumptions, and give a clear "
                  "recommendation including reasons not to proceed.",
    },
    "writer": {
        "web": False,
        "prompt": "You are a professional writer. Produce the document the task asks for (email, "
                  "proposal, report, procedure) ready to send, in plain direct language, with no "
                  "placeholders unless a fact is truly missing.",
    },
}


def register(reg: Registry, brain) -> None:
    async def ask_helper(helper: str, task: str) -> str:
        if brain.client is None:
            return "Helper agents need the Claude API key."
        spec = HELPERS[helper]
        kwargs = {}
        if spec["web"]:
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
        messages = [{"role": "user", "content": task}]
        for _ in range(6):                       # a web search turn can pause and resume
            async with brain.client.beta.messages.stream(
                model=brain.cfg.model, max_tokens=16000,
                system=spec["prompt"] + " Your reader is another AI assistant who will relay your "
                       "findings, so write the full result with everything needed to act on it.",
                messages=messages, output_config={"effort": brain.cfg.effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs,
            ) as stream:
                response = await stream.get_final_message()
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason == "refusal":
                return "The helper declined this task."
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            return text or "The helper returned nothing."
        return "The helper did not finish in time."

    reg.add(Tool(
        "ask_helper",
        "Hand a task to a helper agent and get its written result. Helpers: researcher (searches "
        "the web for current facts and sources), bim_specialist (drafts and reviews BIM documents "
        "and checks to ISO 19650), business_analyst (market, competitors, pricing and risks for a "
        "business idea, with web search), writer (finished emails, proposals and reports). The "
        "helper knows nothing about this conversation, so put everything it needs in the task. "
        "For a job with several independent parts, call several helpers in the same turn. Use a "
        "helper when the work needs current information from the web or is long enough to be "
        "worth handing off; answer simple questions yourself.",
        {"helper": {"type": "string", "enum": list(HELPERS)},
         "task": {"type": "string", "description": "Complete, self-contained instructions"}},
        ["helper", "task"], ask_helper))
