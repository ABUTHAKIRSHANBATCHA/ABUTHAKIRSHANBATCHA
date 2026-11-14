"""Free brain: runs Lumi on a model that costs nothing, through the
"OpenAI-compatible" chat API that free providers share:

  - Google Gemini free tier   https://generativelanguage.googleapis.com/v1beta/openai
  - Groq free tier            https://api.groq.com/openai/v1
  - Ollama on this PC         http://localhost:11434/v1   (no key, no internet)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

PROVIDERS = {
    "gemini": {"label": "Google Gemini (free key from Google AI Studio, online)", "needs_key": True, "key_env": "LUMI_GEMINI_KEY",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_help": "Create a free key at https://aistudio.google.com/apikey (Google account, no card)."},
    "groq": {"label": "Groq (free tier, online)", "needs_key": True, "key_env": "LUMI_GROQ_KEY",
             "base_url": "https://api.groq.com/openai/v1",
             "key_help": "Create a free key at https://console.groq.com/keys (no card)."},
    "ollama": {"label": "Ollama (runs on this PC, private, works offline)", "needs_key": False, "key_env": "",
               "base_url": "http://localhost:11434/v1",
               "key_help": "Install Ollama from https://ollama.com and download a model that supports tools."},
}


def chain(cfg) -> list[dict]:
    """The free brains to use, in order: [{"name", "base_url", "model", "key_env"}]."""
    brains = [b for b in cfg.brains if b.get("base_url") and b.get("model")]
    if not brains and cfg.free_base_url and cfg.free_model:          # settings from before several brains existed
        brains = [{"name": "free", "base_url": cfg.free_base_url, "model": cfg.free_model, "key_env": "LUMI_FREE_API_KEY"}]
    return brains


# ---- choosing a brain for the work in hand
# deep    = thinking work: analysis, documents, plans, writing  -> the most capable brain
# quick   = everyday commands and short questions               -> the fastest brain, saving the best one's quota
# private = the owner asked for it to stay on this PC           -> only a brain that runs locally
GOOD_FOR = {"gemini": ["deep", "quick"], "groq": ["quick", "deep"], "ollama": ["private", "quick", "deep"]}
ORDER = {"deep": ["gemini", "groq", "ollama"], "quick": ["groq", "ollama", "gemini"]}
PRIVATE_WORDS = ("private", "privately", "confidential", "offline", "secret", "do not send", "don't send",
                 "stay on this pc", "locally", "தனிப்பட்ட", "ரகசிய")
DEEP_WORDS = ("analy", "review", "compare", "evaluate", "assess", "plan", "strategy", "draft", "write ", "rewrite",
              "report", "proposal", "summar", "explain", "research", "business", "market", "idea", "bep", "eir",
              "checklist", "requirement", "standard", "iso 19650", "clause", "contract", "document", "pdf", "email",
              "why ", "how should", "what should", "recommend", "pros and cons", "calculate", "estimate",
              "ஆய்வு", "திட்டம்", "அறிக்கை", "விளக்க", "ஒப்பிட")


def kind_of_work(text: str) -> str:
    lowered = " " + text.lower() + " "
    if any(word in lowered for word in PRIVATE_WORDS):
        return "private"
    if len(text) > 240 or lowered.startswith(" scheduled task") or any(word in lowered for word in DEEP_WORDS):
        return "deep"
    return "quick"


def route(text: str, brains: list[dict]) -> tuple[str, list[dict]]:
    """Pick the order to try the brains in for this request. Returns (kind, ordered brains).
    For private work only local brains are returned (possibly none)."""
    lowered = text.lower()
    for brain in brains:                       # "use ollama ...", "ask gemini ...": the owner's choice wins
        if any(f"{verb} {brain['name']}" in lowered for verb in ("use", "ask", "with", "via")):
            return "chosen", [brain] + [b for b in brains if b is not brain]
    kind = kind_of_work(text)

    def good_for(brain: dict) -> list[str]:
        return brain.get("good_for") or GOOD_FOR.get(brain["name"], ["quick", "deep"])

    if kind == "private":
        return kind, [b for b in brains if "private" in good_for(b)]
    preferred = ORDER[kind]
    rank = lambda b: (kind not in good_for(b), preferred.index(b["name"]) if b["name"] in preferred else len(preferred))
    return kind, sorted(brains, key=rank)


def for_provider(messages: list[dict], name: str) -> list[dict]:
    """History as one provider should receive it. Tool calls made by a different provider keep
    only the standard fields, because each provider adds private extras the others reject."""
    out = []
    for m in messages:
        m = dict(m)
        made_by = m.pop("_by", None)
        if m.get("tool_calls") and made_by != name:
            m["tool_calls"] = [{"id": c.get("id"), "type": "function", "function": c.get("function")}
                               for c in m["tool_calls"]]
        out.append(m)
    return out


class FreeAPIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _request(url: str, key: str, body: dict | None = None, timeout: int = 180) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(text)
            detail = detail[0] if isinstance(detail, list) else detail
            text = (detail.get("error") or {}).get("message") or text
        except (ValueError, AttributeError):
            pass
        raise FreeAPIError(e.code, str(text)[:400]) from None
    except (urllib.error.URLError, OSError) as e:
        raise FreeAPIError(0, str(e)) from None


def list_models(base_url: str, key: str) -> list[str]:
    data = _request(base_url.rstrip("/") + "/models", key, timeout=20)
    return sorted(m["id"].removeprefix("models/") for m in data.get("data", []) if m.get("id"))


def chat(base_url: str, key: str, model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One chat request; returns the assistant message (content and/or tool_calls)."""
    body: dict = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    data = _request(base_url.rstrip("/") + "/chat/completions", key, body)
    choices = data.get("choices") or []
    if not choices:
        raise FreeAPIError(0, "The model returned an empty answer.")
    return choices[0].get("message") or {}


def to_function_tools(schemas: list[dict]) -> list[dict]:
    """Lumi's tool definitions in the function-calling format these providers expect."""
    out = []
    for s in schemas:
        function = {"name": s["name"], "description": s["description"]}
        properties = {}
        for key, spec in s["input_schema"].get("properties", {}).items():
            if spec.get("type") == "object" and not spec.get("properties"):
                # A free-form object: ask for it as JSON text (see parse_arguments).
                spec = {"type": "string", "description": (spec.get("description", "") + " Write this as JSON text.").strip()}
            properties[key] = spec
        if properties:                                  # tools without inputs send no parameters at all
            function["parameters"] = {"type": "object", "properties": properties}
            if s["input_schema"].get("required"):
                function["parameters"]["required"] = s["input_schema"]["required"]
        out.append({"type": "function", "function": function})
    return out


def parse_arguments(raw: str | dict | None, schema: dict | None):
    """Tool arguments from the model, with JSON-text objects turned back into objects.
    Returns None when the arguments cannot be read."""
    if isinstance(raw, dict):
        args = raw                                      # some providers already send an object
    else:
        try:
            args = json.loads(raw or "{}")
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    for key, spec in ((schema or {}).get("properties") or {}).items():
        if spec.get("type") == "object" and isinstance(args.get(key), str):
            try:
                args[key] = json.loads(args[key])
            except ValueError:
                return None
    return args
