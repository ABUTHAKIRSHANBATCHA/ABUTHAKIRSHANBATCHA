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
    "gemini": {"label": "Google Gemini (free tier, online)", "needs_key": True,
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_help": "Create a free key at https://aistudio.google.com/apikey (Google account, no card)."},
    "groq": {"label": "Groq (free tier, online)", "needs_key": True,
             "base_url": "https://api.groq.com/openai/v1",
             "key_help": "Create a free key at https://console.groq.com/keys (no card)."},
    "ollama": {"label": "Ollama (runs on this PC, fully private, needs a strong PC)", "needs_key": False,
               "base_url": "http://localhost:11434/v1",
               "key_help": "Install Ollama from https://ollama.com and download a model that supports tools."},
}


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
