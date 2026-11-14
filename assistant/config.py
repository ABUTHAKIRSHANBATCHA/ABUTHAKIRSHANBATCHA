"""Settings: defaults, overridden by config.json in the project root."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = Path(__file__).resolve().parent / "web"


@dataclass
class Config:
    name: str = "Lumi"               # shown on the HUD; "Hey <name>" wakes it in wake-word mode
    owner: str = "Boss"              # how the assistant addresses you
    # Which brain to use: "free" (no cost; see free_base_url / free_model) or "claude" (paid API).
    provider: str = "claude"
    # Free brains in the order to try them; the next one takes over when one reaches its limit.
    # [{"name": "gemini", "base_url": "...", "model": "...", "key_env": "LUMI_GEMINI_KEY"}, ...]
    brains: list[dict] = field(default_factory=list)
    auto_select: bool = True         # pick the brain to suit each request; false = always use them in the listed order
    free_base_url: str = ""          # e.g. https://generativelanguage.googleapis.com/v1beta/openai
    free_model: str = ""             # a model name from that provider that supports tools
    model: str = "claude-opus-5-5"
    effort: str = "medium"           # low | medium | high | xhigh | max
    http_port: int = 8765            # this PC only (127.0.0.1)
    https_port: int = 8443           # phone / other devices on your Wi-Fi
    browser: str = "chrome"          # web pages open in Chrome when it is installed; "default" = system browser
    default_language: str = "en-IN"  # en-IN or ta-IN
    bim_search_roots: list[str] = field(default_factory=list)
    revit_bridge_url: str = "http://127.0.0.1:48884/assistant"
    currency: str = "INR"            # used for money tracking
    data_dir: str = ""               # where memory and projects are stored; default: ./data
    # The software you work in. Add to this list as your toolset grows.
    # Where your project folders live, and the subfolders every new project gets.
    projects_root: str = ""
    folder_template: list[str] = field(default_factory=lambda: [
        "01_Client_Requirements", "02_BEP_and_Plans", "03_Models", "04_Drawings",
        "05_Clash_and_Coordination", "06_Correspondence", "07_Reports", "08_Handover", "99_Incoming"])
    toolset: list[str] = field(default_factory=lambda: [
        "Autodesk Civil 3D", "Revit", "Navisworks", "AutoCAD (2D and 3D)", "AutoCAD Plant 3D",
        "Unreal Engine"])
    # Programs the automatic search does not find: [{"name": "...", "executable": "C:/.../app.exe"}]
    extra_software: list[dict] = field(default_factory=list)
    # Other applications Lumi may send data to, each call approved by you:
    # [{"name": "world_dashboard", "description": "what to send", "url": "http://...", "method": "POST"}]
    connectors: list[dict] = field(default_factory=list)
    # Email (off until you turn it on). The password goes in secrets.env, never here.
    email: dict = field(default_factory=lambda: {
        "enabled": False, "address": "", "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "ask_before_reading": True})
    # Folders and log files to watch: [{"name": "...", "path": "...", "kind": "folder" or "log"}]
    watch: list[dict] = field(default_factory=list)
    watch_seconds: int = 30          # how often they are checked
    voice_lock: bool = True          # once a voice is enrolled, spoken commands need a recognised voice
    voice_threshold: float = 0.6     # higher = stricter voice match

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else DATA_DIR


def load_secrets(path: Path | None = None) -> None:
    """Read KEY=value lines from secrets.env into the environment (existing values win)."""
    path = path or ROOT / "secrets.env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.json"
    cfg = Config()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        known = {f.name for f in fields(Config)}
        for key, value in raw.items():
            if key in known:
                setattr(cfg, key, value)
    return cfg
