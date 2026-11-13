"""Settings: defaults, overridden by config.json in the project root."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = Path(__file__).resolve().parent / "web"


@dataclass
class Config:
    name: str = "Assistant"          # shown on the HUD and used as the wake word
    owner: str = "Boss"              # how the assistant addresses you
    model: str = "claude-opus-5-5"
    effort: str = "medium"           # low | medium | high | xhigh | max
    http_port: int = 8765            # this PC only (127.0.0.1)
    https_port: int = 8443           # phone / other devices on your Wi-Fi
    default_language: str = "en-IN"  # en-IN or ta-IN
    bim_search_roots: list[str] = field(default_factory=list)
    revit_bridge_url: str = "http://127.0.0.1:48884/assistant"


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.json"
    cfg = Config()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        for key, value in raw.items():
            if key in known:
                setattr(cfg, key, value)
    return cfg
