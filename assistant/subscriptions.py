"""Use the AI subscriptions the owner already pays for, at no extra cost:

  - Claude (Pro / Max)  through the Claude Code command-line program (`claude`)
  - ChatGPT (Plus / Pro) through the Codex command-line program (`codex`)

Each must be installed and signed in on this PC with the subscription account.
They answer in text only, so Lumi uses them for thinking and writing work
(helper agents), while a free brain that can use Lumi's tools runs the conversation.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

WEB_TOOLS = "WebSearch,WebFetch"
SETUP_HELP = {
    "claude": "Install Node.js, then run:  npm install -g @anthropic-ai/claude-code   and then:  claude   "
              "and sign in with your Claude subscription account.",
    "chatgpt": "Install Node.js, then run:  npm install -g @openai/codex   and then:  codex login   "
               "and sign in with your ChatGPT account.",
}


def find(name: str, override: str = "") -> str:
    """Path of the command-line program for a subscription, or "" when it is not installed."""
    if override:
        return override if Path(override).exists() else (shutil.which(override) or "")
    return shutil.which("claude" if name == "claude" else "codex") or ""


def command(name: str, program: str, web: bool) -> list[str]:
    if name == "claude":
        # Print mode. Only web search tools at most: it cannot read or change files on this PC.
        argv = [program, "-p", "--output-format", "text", "--tools", WEB_TOOLS if web else ""]
        return argv + (["--allowedTools", WEB_TOOLS] if web else [])
    # Codex, non-interactive, read-only sandbox, prompt from standard input.
    return [program, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-"]


def ask(name: str, program: str, system: str, task: str, workdir: Path, web: bool = False, timeout: int = 900) -> str:
    """One question to a subscription. Returns the answer, or "" if it failed for any reason."""
    workdir.mkdir(parents=True, exist_ok=True)              # an empty folder: nothing of the owner's to read
    prompt = f"{system}\n\n---\n\n{task}"
    try:
        done = subprocess.run(command(name, program, web), input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, cwd=str(workdir))
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def available(cfg) -> list[tuple[str, str]]:
    """[(name, program)] for the subscriptions switched on in the settings and installed, in order."""
    out = []
    for name in cfg.subscriptions:
        program = find(name, (cfg.subscription_commands or {}).get(name, ""))
        if program:
            out.append((name, program))
    return out
