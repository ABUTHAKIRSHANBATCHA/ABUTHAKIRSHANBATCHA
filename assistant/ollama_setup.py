"""Install Ollama, download a model that suits this PC, test it, and make it
Lumi's brain:   python -m assistant.ollama_setup [model]    (install-ollama.bat runs this)

Ollama runs the AI model on this PC: free, private, and it works offline.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from . import free_brain
from .config import ROOT

BASE_URL = free_brain.PROVIDERS["ollama"]["base_url"]
# Models that support tools, best first, by how much memory the PC has.
CANDIDATES = [
    (24, ["gpt-oss:20b", "qwen3:8b", "llama3.1:8b"]),
    (0, ["qwen3:8b", "llama3.1:8b"]),
]
TOOL_TEST = [{"type": "function", "function": {"name": "get_time", "description": "Current time"}}]


def candidates_for(ram_gb: float) -> list[str]:
    return next(models for minimum, models in CANDIDATES if ram_gb >= minimum)


def find_ollama() -> str:
    exe = shutil.which("ollama")
    if exe:
        return exe
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", "")):
        for sub in ("Programs/Ollama/ollama.exe", "Ollama/ollama.exe"):
            path = Path(base) / sub
            if base and path.exists():
                return str(path)
    return ""


def server_up() -> bool:
    try:
        free_brain.list_models(BASE_URL, "")
        return True
    except free_brain.FreeAPIError:
        return False


def model_works(model: str) -> tuple[bool, str]:
    """Ask a question, then check the model can call a tool (Lumi needs both)."""
    try:
        free_brain.chat(BASE_URL, "", model, [{"role": "user", "content": "Reply with the single word: ready"}])
        reply = free_brain.chat(BASE_URL, "", model,
                                [{"role": "user", "content": "What time is it? Use the get_time tool."}], TOOL_TEST)
    except free_brain.FreeAPIError as e:
        return False, e.message[:200]
    return (True, "") if reply.get("tool_calls") else (False, "it answered but did not use the tool")


def save_choice(root: Path, model: str) -> None:
    file = root / "config.json"
    config = json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}
    config.update(provider="free", brain_choice="ollama", free_base_url=BASE_URL, free_model=model)
    file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(root: Path = ROOT, wanted: str = "", say=print, sh=subprocess.run, ram_gb: float | None = None,
        find=find_ollama, up=server_up, works=model_works, wait=time.sleep) -> bool:
    ram_gb = ram_gb if ram_gb is not None else psutil.virtual_memory().total / 1e9
    say(f"\n=== Ollama setup ===\nThis PC has {ram_gb:.0f} GB of memory.")
    if ram_gb < 7.5:
        say("That is less than the 8 GB a useful local model needs. It will be very slow or fail; "
            "the free Gemini brain (setup.bat, option 1) is the better choice on this PC.")

    exe = find()
    if not exe:
        if platform.system() != "Windows":
            say("Ollama is not installed. Install it from https://ollama.com and run this again.")
            return False
        say("Installing Ollama (this downloads about 1 GB) ...")
        sh(["winget", "install", "-e", "--id", "Ollama.Ollama",
            "--accept-package-agreements", "--accept-source-agreements"])
        exe = find()
        if not exe:
            say("Ollama could not be installed automatically. Download it from https://ollama.com/download, "
                "install it, then run this again.")
            return False

    if not up():
        say("Starting Ollama ...")
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            wait(1)
            if up():
                break
        else:
            say("Ollama did not start. Open the Ollama app from the Start menu, then run this again.")
            return False

    for model in ([wanted] if wanted else candidates_for(ram_gb)):
        say(f"\nDownloading the model {model} (several GB; this can take a while) ...")
        if sh([exe, "pull", model]).returncode != 0:
            say(f"{model} could not be downloaded.")
            continue
        say(f"Testing {model} ...")
        ok, why = works(model)
        if ok:
            save_choice(root, model)
            say(f"\n{model} works and is now Lumi's brain. Start Lumi with start.bat.\n"
                "The first answer after starting is slow while the model loads.")
            return True
        say(f"{model} is not suitable: {why}.")
    say("\nNo model could be set up. Your earlier brain setting was left unchanged. "
        "See https://ollama.com/search?c=tools for models that support tools, then run:\n"
        "  install-ollama.bat <model name>")
    return False


if __name__ == "__main__":
    sys.exit(0 if run(wanted=sys.argv[1] if len(sys.argv) > 1 else "") else 1)
