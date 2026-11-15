"""Self-repair for missing packages.

After an update the program can need packages that an older installation does
not have yet (the error "No module named ..."). Rather than stopping, install
what requirements.txt lists and carry on.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# import name -> shown only to decide whether an install is needed
NEEDED = ["anthropic", "fastapi", "uvicorn", "psutil", "cryptography", "pypdf", "docx", "sqlalchemy", "openpyxl"]


def missing(names: list[str] | None = None) -> list[str]:
    return [n for n in (names or NEEDED) if importlib.util.find_spec(n) is None]


def ensure(root: Path, say=print, run=subprocess.run, names: list[str] | None = None) -> bool:
    """Install missing packages. Returns True when everything needed is present afterwards."""
    absent = missing(names)
    if not absent:
        return True
    say(f"\n  Some packages are missing ({', '.join(absent)}). Installing them now; this takes a minute ...\n")
    done = run([sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")])
    importlib.invalidate_caches()
    still = missing(names)
    if done.returncode != 0 or still:
        say(f"\n  The packages could not be installed ({', '.join(still or absent)}). Check the internet "
            "connection, then run setup.bat.\n")
        return False
    return True
