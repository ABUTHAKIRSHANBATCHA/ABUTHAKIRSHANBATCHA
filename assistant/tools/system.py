"""General PC control: status, apps, files, shell, and pushing cards to devices."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import psutil

from . import Registry, Tool

MAX_OUTPUT = 12_000
IS_WINDOWS = platform.system() == "Windows"


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more characters not shown]"


def _path(p: str) -> Path:
    return Path(os.path.expandvars(p)).expanduser().resolve()


def system_stats() -> dict:
    """Small snapshot used by both the HUD gauges and the status tool."""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home().anchor or "/"))
    battery = None
    try:
        b = psutil.sensors_battery()
        if b is not None:
            battery = {"percent": round(b.percent), "charging": bool(b.power_plugged)}
    except Exception:
        pass
    return {
        "cpu": round(psutil.cpu_percent(interval=None)),
        "memory": round(mem.percent),
        "disk": round(disk.percent),
        "battery": battery,
    }


def get_system_status() -> str:
    stats = system_stats()
    mem = psutil.virtual_memory()
    top = []
    for p in sorted(psutil.process_iter(["name", "memory_info"]),
                    key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0,
                    reverse=True)[:8]:
        if p.info["memory_info"]:
            top.append({"name": p.info["name"], "memory_mb": round(p.info["memory_info"].rss / 1e6)})
    return json.dumps({
        "now": dt.datetime.now().astimezone().strftime("%A %d %B %Y, %I:%M %p %Z"),
        "computer": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        **stats,
        "memory_total_gb": round(mem.total / 1e9, 1),
        "top_processes_by_memory": top,
    })


def open_target(target: str) -> str:
    """Open a URL, file, folder or application by name."""
    if target.lower().startswith(("http://", "https://")):
        webbrowser.open(target)
        return f"Opened {target} in the browser."
    path = _path(target)
    if path.exists():
        if IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opened {path}."
    # Treat it as an application name.
    if IS_WINDOWS:
        # `start` resolves App Paths (chrome, excel, winword, ...) as well as PATH.
        proc = subprocess.run(["cmd", "/c", "start", "", target], capture_output=True, text=True)
        if proc.returncode != 0:
            return f"Could not start '{target}': {proc.stderr.strip() or 'not found'}"
        return f"Started {target}."
    if sys.platform == "darwin":
        proc = subprocess.run(["open", "-a", target], capture_output=True, text=True)
        return f"Started {target}." if proc.returncode == 0 else f"Could not start '{target}': {proc.stderr.strip()}"
    exe = shutil.which(target)
    if not exe:
        return f"Could not find an application or path called '{target}'."
    subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return f"Started {target}."


def list_directory(path: str) -> str:
    p = _path(path)
    if not p.is_dir():
        return f"Not a folder: {p}"
    rows = []
    for entry in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))[:300]:
        try:
            st = entry.stat()
            rows.append({
                "name": entry.name + ("/" if entry.is_dir() else ""),
                "size_kb": None if entry.is_dir() else round(st.st_size / 1024, 1),
                "modified": dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except OSError:
            rows.append({"name": entry.name, "error": "unreadable"})
    return json.dumps({"folder": str(p), "entries": rows})


def read_file(path: str) -> str:
    p = _path(path)
    if not p.is_file():
        return f"Not a file: {p}"
    data = p.read_bytes()
    if b"\x00" in data[:4096]:
        return f"{p} is a binary file ({len(data)} bytes); it cannot be read as text."
    return _clip(data.decode("utf-8", errors="replace"), 30_000)


def read_document(path: str, start_char: int = 0) -> str:
    """Text of a PDF or Word document, e.g. a client EIR or a BEP."""
    p = _path(path)
    if not p.is_file():
        return f"Not a file: {p}"
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        text = "\n\n".join(f"[page {n}]\n{page.extract_text() or ''}"
                           for n, page in enumerate(PdfReader(str(p)).pages, 1))
    elif suffix == ".docx":
        import docx
        doc = docx.Document(str(p))
        parts = [para.text for para in doc.paragraphs if para.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = "\n".join(parts)
    else:
        return "read_document handles .pdf and .docx files. Use read_file for plain text."
    if not text.strip():
        return "No text could be extracted; the document may be scanned images."
    chunk = text[start_char:start_char + 60_000]
    if start_char + 60_000 < len(text):
        chunk += (f"\n... [document continues: {len(text)} characters in total; "
                  f"call again with start_char={start_char + 60_000}]")
    return chunk


def write_file(path: str, contents: str) -> str:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contents, encoding="utf-8")
    return f"Wrote {len(contents)} characters to {p}."


def run_command(command: str, working_directory: str = "") -> str:
    cwd = str(_path(working_directory)) if working_directory else str(Path.home())
    argv = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
            if IS_WINDOWS else ["bash", "-lc", command])
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120,
                              errors="replace")
    except subprocess.TimeoutExpired:
        return "The command was stopped after 120 seconds without finishing."
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    return _clip(f"exit code {proc.returncode}\n{out.strip()}")


def register(reg: Registry, cfg, hub) -> None:
    async def show_on_devices(title: str, text: str = "", url: str = "") -> str:
        await hub.broadcast({"type": "card", "title": title, "text": text, "url": url})
        return f"Shown on {hub.device_count()} connected device(s)."

    async def set_voice_language(language: str) -> str:
        await hub.broadcast({"type": "language", "language": language})
        return f"Voice language switched to {language}."

    async def hologram(action: str) -> str:
        await hub.broadcast({"type": "holo", "action": action})
        return f"Hologram: {action}."

    reg.add(Tool(
        "hologram",
        "Control hologram mode on the owner's screens: the camera view with a 3D building model "
        "that the owner moves with hand gestures. 'open' and 'close' switch the mode; 'explode' "
        "lifts the model's levels apart, 'assemble' closes it again, 'reset' restores the view.",
        {"action": {"type": "string", "enum": ["open", "close", "explode", "assemble", "reset"]}},
        ["action"], hologram))
    reg.add(Tool(
        "get_system_status",
        "Current date and time, plus this computer's CPU, memory, disk, battery and the "
        "heaviest running programs. Call this whenever the date, time or PC health matters.",
        {}, [], get_system_status))
    reg.add(Tool(
        "open_target",
        "Open a website (full https:// URL), a file or folder (path), or start an "
        "application by name (e.g. 'excel', 'chrome', 'notepad'). For Autodesk and other "
        "BIM programs use launch_bim_app instead.",
        {"target": {"type": "string", "description": "URL, path or application name"}},
        ["target"], open_target))
    reg.add(Tool(
        "list_directory", "List the files and folders inside a folder on this computer.",
        {"path": {"type": "string", "description": "Folder path; ~ and %VARS% are expanded"}},
        ["path"], list_directory))
    reg.add(Tool(
        "read_file", "Read a text file from this computer (first 30,000 characters).",
        {"path": {"type": "string"}}, ["path"], read_file))
    reg.add(Tool(
        "read_document",
        "Read the text of a PDF or Word (.docx) document on this computer, such as a client's EIR, "
        "a BEP, a contract or a specification. Long documents come in parts of 60,000 characters.",
        {"path": {"type": "string"},
         "start_char": {"type": "integer", "description": "Where to continue a long document"}},
        ["path"], read_document))
    reg.add(Tool(
        "write_file",
        "Create or overwrite a text file on this computer. The owner is asked to approve first.",
        {"path": {"type": "string"}, "contents": {"type": "string"}},
        ["path", "contents"], write_file, confirm=True))
    reg.add(Tool(
        "run_command",
        ("Run a PowerShell command" if IS_WINDOWS else "Run a bash command") +
        " on this computer and return its output. Use it for anything the other tools do "
        "not cover. The owner is asked to approve every command before it runs.",
        {"command": {"type": "string"},
         "working_directory": {"type": "string", "description": "Optional; defaults to the home folder"}},
        ["command"], run_command, confirm=True))
    reg.add(Tool(
        "show_on_devices",
        "Push a card (title, optional text, optional link) to every connected screen, "
        "including the owner's phone. Use it for 'send this to my phone' or for anything "
        "better read than heard, such as links, lists, codes and numbers.",
        {"title": {"type": "string"}, "text": {"type": "string"}, "url": {"type": "string"}},
        ["title"], show_on_devices))
    reg.add(Tool(
        "set_voice_language",
        "Switch the microphone's listening language when the owner asks to change between "
        "Tamil and English.",
        {"language": {"type": "string", "enum": ["en-IN", "ta-IN"]}},
        ["language"], set_voice_language))
