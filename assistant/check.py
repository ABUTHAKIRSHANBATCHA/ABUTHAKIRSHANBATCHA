"""Installation check:  python -m assistant.check   (check.bat runs this)

Tests each part of the installation for real and writes the result to
lumi-check.txt. The report contains no keys or passwords.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import sys
import urllib.request
from pathlib import Path

from . import free_brain
from .config import ROOT, load_config, load_secrets

OK, WARN, FAIL, OFF = "OK  ", "WARN", "FAIL", "OFF "


def run(root: Path = ROOT, say=print) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    def add(status: str, item: str, detail: str) -> None:
        rows.append((status, item, detail))

    load_secrets(root / "secrets.env")
    cfg = load_config(root / "config.json")
    env = os.environ.get

    # Basics
    v = sys.version_info
    add(OK if v >= (3, 10) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro} on {platform.system()} {platform.release()}")
    missing = [m for m in ("anthropic", "fastapi", "uvicorn", "psutil", "cryptography", "pypdf", "docx")
               if not _has(m)]
    add(FAIL if missing else OK, "Packages", f"missing: {', '.join(missing)} - run setup.bat" if missing else "all installed")
    add(OK if (root / "config.json").exists() else WARN, "Settings file",
        "config.json found" if (root / "config.json").exists() else "config.json not found - run setup.bat")
    add(OK, "Name", f"{cfg.name} (wake phrase: Hey {cfg.name}); calls you {cfg.owner}")

    # Brain
    if cfg.provider == "free":
        if not (cfg.free_base_url and cfg.free_model):
            add(FAIL, "Brain", "free brain chosen but no model set - run setup.bat")
        else:
            try:
                reply = free_brain.chat(cfg.free_base_url, env("LUMI_FREE_API_KEY", ""), cfg.free_model,
                                        [{"role": "user", "content": "Reply with the single word: ready"}])
                add(OK, "Brain", f"free model {cfg.free_model} answered: {(reply.get('content') or '').strip()[:40]!r}")
                try:
                    tool = [{"type": "function", "function": {"name": "get_time", "description": "Current time"}}]
                    r = free_brain.chat(cfg.free_base_url, env("LUMI_FREE_API_KEY", ""), cfg.free_model,
                                        [{"role": "user", "content": "What time is it? Use the get_time tool."}], tool)
                    add(OK if r.get("tool_calls") else WARN, "Brain can use tools",
                        "yes" if r.get("tool_calls") else "the model answered without using the tool; "
                        "Lumi may talk but not act. Pick another model in setup.bat")
                except free_brain.FreeAPIError as e:
                    add(FAIL, "Brain can use tools", f"this model rejected tools ({e.status}): {e.message[:120]}. Pick another model")
            except free_brain.FreeAPIError as e:
                add(FAIL, "Brain", f"free model {cfg.free_model} failed ({e.status}): {e.message[:160]}")
    elif env("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            anthropic.Anthropic().messages.create(model=cfg.model, max_tokens=16,
                                                  messages=[{"role": "user", "content": "Say: ready"}])
            add(OK, "Brain", f"Claude {cfg.model} answered")
        except Exception as e:
            message = getattr(e, "message", str(e))
            add(FAIL, "Brain", "Claude account has no credit - choose a free brain in setup.bat"
                if "credit balance" in str(message).lower() else f"Claude failed: {str(message)[:160]}")
    else:
        add(FAIL, "Brain", "no brain set up - run setup.bat and choose option 1 (free)")

    # Telegram
    if env("LUMI_TELEGRAM_TOKEN"):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{env('LUMI_TELEGRAM_TOKEN')}/getMe", timeout=20) as r:
                bot = json.loads(r.read())["result"]
            chats_file = cfg.data_path / "telegram.json"
            paired = len(json.loads(chats_file.read_text())) if chats_file.exists() else 0
            add(OK if paired else WARN, "Telegram", f"bot @{bot.get('username')} works; "
                + (f"{paired} phone chat(s) paired" if paired else "no phone paired yet - send /pair <PIN> to the bot"))
        except Exception as e:
            add(FAIL, "Telegram", f"the bot token did not work: {str(e)[:120]}")
    else:
        add(OFF, "Telegram", "not set up (needed to talk to Lumi and send files from the phone)")

    # Email
    if cfg.email.get("enabled"):
        if not env("LUMI_EMAIL_PASSWORD"):
            add(FAIL, "Email", "turned on but no app password saved - run setup.bat")
        else:
            try:
                import imaplib
                box = imaplib.IMAP4_SSL(cfg.email["imap_host"])
                box.login(cfg.email["address"], env("LUMI_EMAIL_PASSWORD"))
                box.logout()
                add(OK, "Email", f"signed in to {cfg.email['address']}; asks before each use")
            except Exception as e:
                add(FAIL, "Email", f"could not sign in to {cfg.email.get('address')}: {str(e)[:120]}")
    else:
        add(OFF, "Email", "turned off (your choice; turn on in setup.bat)")

    # Voice lock
    model = cfg.data_path / "voice" / "speaker-model.onnx"
    if _has("sherpa_onnx") and model.exists():
        voices_file = cfg.data_path / "voice" / "voices.json"
        voices = list(json.loads(voices_file.read_text())) if voices_file.exists() else []
        add(OK if voices else WARN, "Voice lock", f"installed; voices: {', '.join(voices)}" if voices
            else "installed, but no voice added yet - open MENU > VOICE LOCK and add yours")
    else:
        add(OFF, "Voice lock", "not installed (choose it in setup.bat to accept only your voice)")

    from .tools.system import chrome_path
    chrome = chrome_path()
    add(OK if chrome else WARN, "Google Chrome", chrome or "not found - Lumi's voice needs Chrome (or Edge); pages will open in the default browser")

    # Folders
    if cfg.projects_root:
        p = Path(os.path.expandvars(cfg.projects_root)).expanduser()
        add(OK if p.is_dir() else FAIL, "Projects folder",
            f"{p} ({sum(1 for x in p.iterdir() if x.is_dir())} project folders)" if p.is_dir() else f"{p} does not exist")
    else:
        add(WARN, "Projects folder", "not set - files sent from the phone cannot be filed until it is (setup.bat)")
    for w in cfg.watch:
        p = Path(os.path.expandvars(w["path"])).expanduser()
        add(OK if p.exists() else FAIL, f"Watching: {w['name']}", str(p) if p.exists() else f"{p} does not exist")
    for c in cfg.connectors:
        add(OK, f"Connector: {c.get('name')}", f"{c.get('url')}" + (" (automatic)" if c.get("auto") else " (asks first)"))
    if not cfg.connectors:
        add(OFF, "Your 3D dashboard", "no connector set up yet")

    # BIM software
    try:
        from .tools.bim import installed_software, revit_query
        found = installed_software(cfg)
        launchable = [s["name"] for s in found if s["executable"]]
        if platform.system() != "Windows":
            add(WARN, "BIM software", "this check only finds software on Windows")
        else:
            add(OK if launchable else WARN, "BIM software",
                f"{len(launchable)} can be opened: " + "; ".join(launchable[:12]) if launchable else
                f"none found that Lumi can open ({len(found)} listed without a program path)")
        bridge = revit_query(cfg, "model")
        add(OK if bridge.lstrip().startswith("{") else OFF, "Revit link",
            "connected to the open Revit model" if bridge.lstrip().startswith("{")
            else "not connected (needs Revit open with pyRevit and the Assistant extension)")
    except Exception as e:
        add(FAIL, "BIM software", f"check failed: {str(e)[:120]}")

    # Memory
    transcript = cfg.data_path / "memory" / "transcript.jsonl"
    lines = sum(1 for _ in transcript.open(encoding="utf-8")) if transcript.exists() else 0
    add(OK, "Memory", f"{lines} messages remembered so far")

    report = [f"Lumi installation check", ""] + [f"[{s}] {item}: {detail}" for s, item, detail in rows]
    fails = sum(s == FAIL for s, _, _ in rows)
    warns = sum(s == WARN for s, _, _ in rows)
    report += ["", f"{fails} problem(s), {warns} thing(s) to finish. OFF means a feature you have not switched on."]
    text = "\n".join(report)
    (root / "lumi-check.txt").write_text(text + "\n", encoding="utf-8")
    say(text)
    say(f"\nSaved to {root / 'lumi-check.txt'}")
    return rows


def _has(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    run()
