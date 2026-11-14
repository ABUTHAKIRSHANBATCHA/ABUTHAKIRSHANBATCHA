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
        params = dict(s["input_schema"])
        params.pop("additionalProperties", None)       # not accepted by every provider
        out.append({"type": "function", "function": {
            "name": s["name"], "description": s["description"], "parameters": params}})
    return out
