"""One-time setup:  python -m assistant.setup_wizard   (setup.bat runs this)

Asks a few questions, then writes config.json (settings) and secrets.env
(keys and passwords, kept only on this PC). Run it again any time to change
an answer; press Enter to keep the value shown in [brackets].
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import free_brain, subscriptions
from .config import ROOT, Config
from .telegram import looks_like_token
from .voice_id import MODEL_NAME, MODEL_URL

MAIL_HOSTS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
    "live.com": ("outlook.office365.com", "smtp.office365.com"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
}


def read_env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                out[key.strip()] = value.strip()
    return out


def run(root: Path = ROOT, ask=input, say=print, install_voice=None, list_models=None) -> None:
    config_file, secrets_file = root / "config.json", root / "secrets.env"
    config = json.loads(config_file.read_text(encoding="utf-8-sig")) if config_file.exists() else {}
    secrets = read_env(secrets_file)
    defaults = Config()

    def question(text: str, current: str = "") -> str:
        answer = ask(f"{text}{f' [{current}]' if current else ''}: ").strip()
        return answer or current

    def secret(text: str, key: str, valid=None, wrong: str = "") -> None:
        answer = ask(f"{text}{' [saved - Enter to keep]' if secrets.get(key) else ' (Enter to skip)'}: ").strip()
        if answer and valid and not valid(answer):
            say(wrong)
            answer = ask(f"{text} (Enter to skip): ").strip()          # one more try
            if answer and not valid(answer):
                say("That is still not right, so nothing was saved for it. Run setup again when you have it.")
                answer = ""
        if answer:
            secrets[key] = answer
        elif valid and secrets.get(key) and not valid(secrets[key]):
            del secrets[key]                                           # a wrong value saved earlier

    def yes(text: str, current: bool) -> bool:
        answer = ask(f"{text} (y/n) [{'y' if current else 'n'}]: ").strip().lower()
        return current if not answer else answer.startswith("y")

    say("\n=== Assistant setup ===\n")
    config["name"] = question("Assistant name (also the wake word)", config.get("name", defaults.name))
    config["owner"] = question("What should it call you", config.get("owner", defaults.owner))
    config["currency"] = question("Currency for money tracking", config.get("currency", defaults.currency))

    say("\n-- Brain (the AI model that thinks for the assistant) --")
    say("Set up as many free brains as you like. The assistant uses the first one and switches to the\n"
        "next automatically when one reaches its free limit or cannot be reached.")
    lister = list_models or _models
    old = {b["name"]: b for b in config.get("brains", [])}
    if secrets.get("LUMI_FREE_API_KEY") and config.get("brain_choice") in ("gemini", "groq"):   # earlier setup
        key_env = free_brain.PROVIDERS[config["brain_choice"]]["key_env"]
        secrets.setdefault(key_env, secrets.pop("LUMI_FREE_API_KEY"))
        old.setdefault(config["brain_choice"], {"model": config.get("free_model", "")})
    brains = []
    for name, spec in free_brain.PROVIDERS.items():
        say(f"\n{spec['label']}\n{spec['key_help']}")
        if spec["needs_key"]:
            secret(f"{name.capitalize()} API key", spec["key_env"])
            if not secrets.get(spec["key_env"]):
                continue
        models = lister(spec["base_url"], secrets.get(spec["key_env"], ""))
        current = old.get(name, {}).get("model", "")
        if not spec["needs_key"] and not models:
            say("Ollama is not running or has no models yet. To add it later, run install-ollama.bat.")
            if current:
                brains.append(old[name])               # keep what was set up before
            continue
        if models:
            say("Models available to you:\n  " + "\n  ".join(models[:40]))
            say("Pick one that supports tools (function calling)."
                + (" A 'flash' model is a good start." if name == "gemini" else ""))
        else:
            say("I could not list the models (check the key). You can still type a model name.")
        model = question(f"{name.capitalize()} model (Enter to skip)" if not current else f"{name.capitalize()} model", current)
        if model:
            brains.append({"name": name, "base_url": spec["base_url"], "model": model, "key_env": spec["key_env"]})
    if len(brains) > 1:
        names = [b["name"] for b in brains]
        first = question(f"Which one first ({', '.join(names)})", old and next((n for n in old if n in names), names[0]) or names[0])
        brains.sort(key=lambda b: b["name"] != first)
    config["brains"] = brains
    for stale in ("free_base_url", "free_model", "brain_choice"):
        config.pop(stale, None)
    secrets.pop("LUMI_FREE_API_KEY", None)

    say("\n-- Your existing subscriptions --\nIf you have Claude Pro/Max or ChatGPT Plus/Pro, the assistant can use them for thinking and\n"
        "writing work at no extra cost. Each needs its command-line program installed and signed in.")
    wanted = []
    for name, label in (("claude", "Claude"), ("chatgpt", "ChatGPT")):
        found = subscriptions.find(name)
        say(f"{label}: " + ("found on this PC." if found else "not found. " + subscriptions.SETUP_HELP[name]))
        if yes(f"Use your {label} subscription", name in config.get("subscriptions", ["claude", "chatgpt"])):
            wanted.append(name)
    config["subscriptions"] = wanted

    paid = yes("\nUse the paid Claude API (charged per use) instead of the free brains", config.get("provider") == "claude" and bool(secrets.get("ANTHROPIC_API_KEY")))
    config["provider"] = "claude" if paid else "free"
    if paid:
        say("Get a key at https://platform.claude.com (API keys). It needs paid credit under Billing.")
        secret("Claude API key", "ANTHROPIC_API_KEY")

    say("\n-- Phone messaging (Telegram) --\nIn Telegram, message @BotFather, send /newbot, and copy the token it gives you.")
    say("The token is the long code on its own line, like  1234567890:AAHk3...  Copy only that code.")
    secret("Telegram bot token", "LUMI_TELEGRAM_TOKEN", looks_like_token,
           "That is not a bot token. BotFather's message says \"Use this token to access the HTTP API:\" and the\n"
           "token is the code on the NEXT line (digits, a colon, then letters). Paste only that.")

    say("\n-- Email --\nOptional. It reads and sends only after you approve each time.")
    mail = {**defaults.email, **config.get("email", {})}
    mail["enabled"] = yes("Turn email on", bool(mail["enabled"]))
    if mail["enabled"]:
        mail["address"] = question("Email address", mail["address"])
        domain = mail["address"].rpartition("@")[2].lower()
        imap, smtp = MAIL_HOSTS.get(domain, (mail["imap_host"], mail["smtp_host"]))
        mail["imap_host"] = question("IMAP server", imap)
        mail["smtp_host"] = question("SMTP server", smtp)
        say("Use an app password, not your normal password (Gmail: Google Account > Security > App passwords).")
        secret("Email app password", "LUMI_EMAIL_PASSWORD")
    config["email"] = mail

    say("\n-- Project folders --\nThe folder that holds one folder per project. Files you send from the phone are filed here.")
    config["projects_root"] = question("Projects folder (full path)", config.get("projects_root", "")).strip('"')

    say("\n-- Folders and logs to watch --\nChanges show on the dashboard and can be sent to your own dashboard.")
    watch = config.get("watch", [])
    for kind, text in (("folder", "A project folder to watch"), ("log", "A log file to watch")):
        path = ask(f"{text} (full path, Enter to skip): ").strip().strip('"')
        if path and not any(w["path"] == path for w in watch):
            watch.append({"name": Path(path).name or path, "path": path, "kind": kind})
    config["watch"] = watch

    say("\n-- Project Control Tower --\nThe master folder for all projects, registers and uploads, for example D:\\PROJECT_CONTROL_TOWER.\n"
        "Lumi creates the folder structure and keeps the drawing, submission, action and timesheet records.")
    config["tower_root"] = question("Project Control Tower folder (Enter to skip)", config.get("tower_root", "")).strip('"')

    say("\n-- Voice lock --\nRecognises your voice on this PC so only you can give spoken commands (downloads about 60 MB).")
    model = root / "data" / "voice" / MODEL_NAME
    if yes("Install voice lock", model.exists()):
        (install_voice or _install_voice)(model, say)

    config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    secrets_file.write_text("# Keys and passwords. Keep this file private.\n"
                            + "".join(f"{k}={v}\n" for k, v in secrets.items()), encoding="utf-8")
    say(f"\nSaved {config_file.name} and {secrets_file.name}.")
    ready = bool(config["brains"]) if config["provider"] == "free" else bool(secrets.get("ANTHROPIC_API_KEY"))
    if not ready:
        say("No model or key was entered, so the assistant will start with its brain offline.")
    say("Start the assistant with start.bat (or: python -m assistant).\n")


def _models(base_url: str, key: str) -> list[str]:
    try:
        return free_brain.list_models(base_url, key)
    except free_brain.FreeAPIError:
        return []


def _install_voice(model: Path, say) -> None:
    try:
        say("Installing the voice recognition package ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "sherpa-onnx", "numpy"], check=True)
        if not model.exists():
            say("Downloading the voice model ...")
            model.parent.mkdir(parents=True, exist_ok=True)
            partial = model.with_suffix(".part")
            urllib.request.urlretrieve(MODEL_URL, partial)
            partial.replace(model)
        say("Voice lock installed. Add your voice from MENU > VOICE LOCK after starting.")
    except Exception as e:
        say(f"Voice lock could not be installed ({e}). Everything else still works; run setup again to retry.")


if __name__ == "__main__":
    try:
        run()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled; nothing was saved.")
