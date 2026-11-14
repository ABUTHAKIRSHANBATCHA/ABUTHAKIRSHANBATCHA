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
# Models that support tools, best first.
LARGE = ["gpt-oss:20b", "qwen3:8b", "llama3.1:8b"]      # about 14 GB: needs a 12 GB+ graphics card to be quick
SMALL = ["qwen3:8b", "llama3.1:8b"]                     # about 5 GB: fits a 6-8 GB graphics card
TOOL_TEST = [{"type": "function", "function": {"name": "get_time", "description": "Current time"}}]


def gpu_gb() -> float | None:
    """Memory of the NVIDIA graphics card in GB, or None when it cannot be read."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout
        return max(float(line) for line in out.split()) / 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def candidates_for(ram_gb: float, vram_gb: float | None = None) -> list[str]:
    """A model that fits on the graphics card answers in seconds; one that spills into normal
    memory takes far longer. So the graphics card decides when we can see it."""
    if vram_gb is not None:
        return LARGE if vram_gb >= 12 else SMALL
    return LARGE if ram_gb >= 24 else SMALL


INSTALLER_URL = "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe"


def download_installer(url: str, say) -> Path | None:
    """Download the official Ollama installer to the temp folder, showing progress."""
    import tempfile
    import urllib.request

    target = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done, shown = 0, -1
            while True:
                chunk = response.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                tenth = int(done * 10 / total) if total else -1
                if tenth != shown and total:
                    shown = tenth
                    say(f"  {done / 1e9:.2f} of {total / 1e9:.2f} GB")
        if total and done != total:
            say("The download was cut off before it finished.")
            return None
        return target
    except OSError as e:
        say(f"The download failed: {e}")
        return None


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
    """Add Ollama to Lumi's free brains (or update its model), keeping the others and their order."""
    file = root / "config.json"
    config = json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}
    entry = {"name": "ollama", "base_url": BASE_URL, "model": model, "key_env": ""}
    brains = config.get("brains", [])
    if any(b.get("name") == "ollama" for b in brains):
        brains = [entry if b.get("name") == "ollama" else b for b in brains]
    else:
        brains.append(entry)
    config.update(provider="free", brains=brains)
    file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(root: Path = ROOT, wanted: str = "", say=print, sh=subprocess.run, ram_gb: float | None = None,
        vram_gb: float | None = -1, download=download_installer, find=find_ollama, up=server_up, works=model_works, wait=time.sleep) -> bool:
    ram_gb = ram_gb if ram_gb is not None else psutil.virtual_memory().total / 1e9
    vram_gb = gpu_gb() if vram_gb == -1 else vram_gb
    say(f"\n=== Ollama setup ===\nThis PC has {ram_gb:.0f} GB of memory"
        + (f" and a {vram_gb:.0f} GB graphics card." if vram_gb else "; no NVIDIA graphics card was detected."))
    if ram_gb < 7.5:
        say("That is less than the 8 GB a useful local model needs. It will be very slow or fail; "
            "the free Gemini brain (setup.bat, option 1) is the better choice on this PC.")

    exe = find()
    if not exe:
        if platform.system() != "Windows":
            say("Ollama is not installed. Install it from https://ollama.com and run this again.")
            return False
        say("Installing Ollama (this downloads about 2 GB) ...")
        try:
            sh(["winget", "install", "-e", "--id", "Ollama.Ollama",
                "--accept-package-agreements", "--accept-source-agreements"])
        except OSError:
            pass                                   # winget itself is missing on this PC
        exe = find()
        if not exe:
            # Windows' installer refuses when its recorded checksum is out of date ("Installer hash
            # does not match"). Fetch the same installer from Ollama's official release page instead.
            say("\nWindows' installer could not install it. Downloading Ollama from its official page instead ...")
            installer = download(INSTALLER_URL, say)
            if installer:
                say("Running the Ollama installer ...")
                sh([str(installer), "/SILENT"])
                for _ in range(60):                # the installer finishes in the background
                    exe = find()
                    if exe:
                        break
                    wait(2)
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

    for model in ([wanted] if wanted else candidates_for(ram_gb, vram_gb)):
        say(f"\nDownloading the model {model} (several GB; this can take a while) ...")
        if sh([exe, "pull", model]).returncode != 0:
            say(f"{model} could not be downloaded.")
            continue
        say(f"Testing {model} ...")
        ok, why = works(model)
        if ok:
            save_choice(root, model)
            say(f"\n{model} works and has been added to Lumi's free brains. Start Lumi with start.bat.\n"
                "The first answer after starting is slow while the model loads.")
            return True
        say(f"{model} is not suitable: {why}.")
    say("\nNo model could be set up. Your earlier brain setting was left unchanged. "
        "See https://ollama.com/search?c=tools for models that support tools, then run:\n"
        "  install-ollama.bat <model name>")
    return False


if __name__ == "__main__":
    sys.exit(0 if run(wanted=sys.argv[1] if len(sys.argv) > 1 else "") else 1)
