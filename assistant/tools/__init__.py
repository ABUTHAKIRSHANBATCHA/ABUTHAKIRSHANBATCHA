"""Tool registry: everything the assistant can do on this machine.

Each tool has a JSON schema (sent to the model), a handler, and a `confirm`
flag. Tools with confirm=True never run until the owner approves them on a
paired device.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class Tool:
    name: str
    description: str
    properties: dict[str, dict]
    required: list[str]
    handler: Callable[..., Any]
    confirm: bool = False

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "eager_input_streaming": True,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
                "additionalProperties": False,
            },
        }

    def validate(self, args: Any) -> str | None:
        """Return an error string if args do not match the schema."""
        if not isinstance(args, dict):
            return "input must be an object"
        for key in self.required:
            if key not in args:
                return f"missing required field '{key}'"
        for key, value in args.items():
            spec = self.properties.get(key)
            if spec is None:
                return f"unknown field '{key}'"
            expected = _JSON_TYPES.get(spec.get("type", ""))
            if expected and (not isinstance(value, expected) or
                             (spec["type"] in ("integer", "number") and isinstance(value, bool))):
                return f"field '{key}' must be of type {spec['type']}"
            if "enum" in spec and value not in spec["enum"]:
                return f"field '{key}' must be one of {spec['enum']}"
        return None


class Registry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in sorted(self.tools.values(), key=lambda t: t.name)]

    async def run(self, name: str, args: dict) -> str:
        tool = self.tools[name]
        if inspect.iscoroutinefunction(tool.handler):
            result: Awaitable | Any = await tool.handler(**args)
        else:
            result = await asyncio.to_thread(tool.handler, **args)
        return result if isinstance(result, str) else str(result)


def build_registry(cfg, hub, memory) -> Registry:
    from . import bim, lifecycle, notes, system

    reg = Registry()
    system.register(reg, cfg, hub)
    bim.register(reg, cfg)
    lifecycle.register(reg, lifecycle.Projects(cfg.data_path / "projects"))
    notes.register(reg, memory)
    return reg
