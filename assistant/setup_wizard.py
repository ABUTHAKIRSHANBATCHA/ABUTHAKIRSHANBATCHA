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

from . import free_brain
from .config import ROOT, Config
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
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                out[key.strip()] = value.strip()
    return out


def run(root: Path = ROOT, ask=input, say=print, install_voice=None, list_models=None) -> None:
    config_file, secrets_file = root / "config.json", root / "secrets.env"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    secrets = read_env(secrets_file)
    defaults = Config()

    def question(text: str, current: str = "") -> str:
        answer = ask(f"{text}{f' [{current}]' if current else ''}: ").strip()
        return answer or current

    def secret(text: str, key: str) -> None:
        answer = ask(f"{text}{' [saved - Enter to keep]' if secrets.get(key) else ' (Enter to skip)'}: ").strip()
        if answer:
            secrets[key] = answer

    def yes(text: str, current: bool) -> bool:
        answer = ask(f"{text} (y/n) [{'y' if current else 'n'}]: ").strip().lower()
        return current if not answer else answer.startswith("y")

    say("\n=== Assistant setup ===\n")
    config["name"] = question("Assistant name (also the wake word)", config.get("name", defaults.name))
    config["owner"] = question("What should it call you", config.get("owner", defaults.owner))
    config["currency"] = question("Currency for money tracking", config.get("currency", defaults.currency))

    say("\n-- Brain (the AI model that thinks for the assistant) --")
    options = list(free_brain.PROVIDERS) + ["claude"]
    for n, key in enumerate(options, 1):
        label = free_brain.PROVIDERS[key]["label"] if key != "claude" else "Claude (paid: you buy API credit)"
        say(f"  {n}. {label}")
    current = config.get("brain_choice", "gemini")
    picked = question("Choose a number", str(options.index(current) + 1 if current in options else 1))
    choice = options[int(picked) - 1] if picked.isdigit() and 1 <= int(picked) <= len(options) else current
    config["brain_choice"] = choice
    if choice == "claude":
        config["provider"] = "claude"
        say("Get a key at https://platform.claude.com (API keys). It needs paid credit under Billing.")
        secret("Claude API key", "ANTHROPIC_API_KEY")
    else:
        spec = free_brain.PROVIDERS[choice]
        config["provider"], config["free_base_url"] = "free", spec["base_url"]
        say(spec["key_help"])
        if spec["needs_key"]:
            secret("Free API key", "LUMI_FREE_API_KEY")
        else:
            secrets.pop("LUMI_FREE_API_KEY", None)
        models = (list_models or _models)(spec["base_url"], secrets.get("LUMI_FREE_API_KEY", "") if spec["needs_key"] else "")
        if models:
            say("Models available to you:\n  " + "\n  ".join(models[:40]))
            say("Pick one that supports tools (function calling). 'flash' models are a good start on Gemini.")
        else:
            say("I could not list the models (check the key, or that Ollama is running). Type the model name.")
        config["free_model"] = question("Model name", config.get("free_model", "") if config.get("free_model", "") in models or not models else "")

    say("\n-- Phone messaging (Telegram) --\nIn Telegram, message @BotFather, send /newbot, and copy the token it gives you.")
    secret("Telegram bot token", "LUMI_TELEGRAM_TOKEN")

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

    say("\n-- Voice lock --\nRecognises your voice on this PC so only you can give spoken commands (downloads about 60 MB).")
    model = root / "data" / "voice" / MODEL_NAME
    if yes("Install voice lock", model.exists()):
        (install_voice or _install_voice)(model, say)

    config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    secrets_file.write_text("# Keys and passwords. Keep this file private.\n"
                            + "".join(f"{k}={v}\n" for k, v in secrets.items()), encoding="utf-8")
    say(f"\nSaved {config_file.name} and {secrets_file.name}.")
    ready = bool(config.get("free_model")) if config["provider"] == "free" else bool(secrets.get("ANTHROPIC_API_KEY"))
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
