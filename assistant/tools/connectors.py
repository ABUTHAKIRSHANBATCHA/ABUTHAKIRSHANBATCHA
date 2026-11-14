"""Connectors: other applications Lumi can send data to over HTTP, defined by
the owner in config.json. Used, for example, to update an external dashboard."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import Registry, Tool


def call(connector: dict, data: dict) -> str:
    method = connector.get("method", "POST").upper()
    body = None if method == "GET" else json.dumps(data, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(connector["url"], data=body, method=method, headers={
        "Content-Type": "application/json", **connector.get("headers", {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return f"{connector['name']} answered {resp.status}: {resp.read().decode('utf-8', 'replace')[:3000]}"
    except urllib.error.HTTPError as e:
        return f"{connector['name']} refused the request ({e.code}): {e.read().decode('utf-8', 'replace')[:500]}"
    except (urllib.error.URLError, OSError) as e:
        return f"Could not reach {connector['name']} at {connector['url']}: {e}"


def register(reg: Registry, cfg) -> None:
    connectors = {c["name"]: c for c in cfg.connectors if c.get("name") and c.get("url")}
    if not connectors:
        return

    def use_connector(name: str, data: dict | None = None) -> str:
        if name not in connectors:
            return f"No connector named '{name}'. Available: {list(connectors)}"
        return call(connectors[name], data or {})

    listing = "; ".join(f"{c['name']}: {c.get('description', 'no description')}" for c in connectors.values())
    reg.add(Tool(
        "use_connector",
        "Send data to one of the owner's other applications. The owner approves each call. "
        f"Connectors: {listing}",
        {"name": {"type": "string", "enum": list(connectors)},
         "data": {"type": "object", "description": "JSON to send, shaped as the connector's description says"}},
        ["name"], use_connector, confirm=True))
