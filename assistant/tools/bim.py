"""BIM tooling: find installed software, launch it, locate model files, and
query the open Revit model through the pyRevit bridge (revit/Assistant.extension)."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import Registry, Tool

IS_WINDOWS = platform.system() == "Windows"

# product keyword -> executable file name
KNOWN_EXECUTABLES = {
    "revit": "Revit.exe",
    "navisworks manage": "Roamer.exe",
    "navisworks simulate": "Roamer.exe",
    "navisworks freedom": "Roamer.exe",
    "autocad": "acad.exe",
    "civil 3d": "acad.exe",
    "plant 3d": "acad.exe",
    "map 3d": "acad.exe",
    "recap": "ReCap.exe",
    "tekla structures": "TeklaStructures.exe",
    "solibri": "Solibri.exe",
    "archicad": "Archicad.exe",
    "sketchup": "SketchUp.exe",
    "bimcollab": "BIMcollab ZOOM.exe",
    "bluebeam": "Revu.exe",
    "rhino": "Rhino.exe",
    "infraworks": "InfraWorks.exe",
    "advance steel": "acad.exe",
    "robot structural": "robot.exe",
    "etabs": "ETABS.exe",
    "staad": "StaadPro.exe",
    "dialux": "DIALux.exe",
    "enscape": "Enscape.exe",
    "lumion": "Lumion.exe",
    "twinmotion": "Twinmotion.exe",
    "synchro": "Synchro.exe",
    "bentley": "",
    "microstation": "ustation.exe",
    "primavera": "",
    "cadwork": "",
    "vectorworks": "",
}

MODEL_EXTENSIONS = {".rvt", ".rfa", ".rte", ".nwd", ".nwf", ".nwc", ".ifc", ".dwg", ".dxf", ".dyn", ".dgn",
                    ".skp", ".uproject", ".udatasmith", ".fbx", ".rcp", ".rcs", ".landxml"}

# AutoCAD-based products share acad.exe; this switch selects the product to start.
ACAD_PRODUCT_SWITCH = {"civil 3d": "C3D", "plant 3d": "PLNT3D", "map 3d": "MAP"}


def _installed_from_registry() -> list[dict]:
    import winreg  # Windows only

    found: dict[str, dict] = {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, key_path in roots:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
            except OSError:
                continue
            lowered = name.lower()
            keyword = next((k for k in KNOWN_EXECUTABLES if k in lowered), None)
            if not keyword:
                continue
            if any(skip in lowered for skip in ("language pack", "content", "library", "add-in",
                                                "plugin", "plug-in", "update", "hotfix", "runtime")):
                continue

            def value(field: str) -> str:
                try:
                    return str(winreg.QueryValueEx(sub, field)[0])
                except OSError:
                    return ""

            location = value("InstallLocation")
            exe = ""
            exe_name = KNOWN_EXECUTABLES[keyword]
            if location and exe_name:
                candidate = Path(location) / exe_name
                if candidate.exists():
                    exe = str(candidate)
            found[name] = {"name": name, "version": value("DisplayVersion"),
                           "location": location, "executable": exe}
    return sorted(found.values(), key=lambda r: r["name"])


def _unreal_engines() -> list[dict]:
    """Unreal Engine versions installed through the Epic Games Launcher."""
    import winreg  # Windows only

    found = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine")
    except OSError:
        return found
    for i in range(winreg.QueryInfoKey(key)[0]):
        try:
            version = winreg.EnumKey(key, i)
            location = str(winreg.QueryValueEx(winreg.OpenKey(key, version), "InstalledDirectory")[0])
        except OSError:
            continue
        exe = next((str(p) for p in (Path(location) / "Engine/Binaries/Win64" / n
                                     for n in ("UnrealEditor.exe", "UE4Editor.exe")) if p.exists()), "")
        found.append({"name": f"Unreal Engine {version}", "version": version,
                      "location": location, "executable": exe})
    return found


def installed_software(cfg) -> list[dict]:
    found = (_installed_from_registry() + _unreal_engines()) if IS_WINDOWS else []
    for extra in cfg.extra_software:      # programs the owner listed in config.json
        exe = str(Path(os.path.expandvars(extra.get("executable", ""))).expanduser())
        found.append({"name": extra.get("name", Path(exe).stem), "version": extra.get("version", ""),
                      "location": str(Path(exe).parent), "executable": exe if Path(exe).exists() else ""})
    return sorted(found, key=lambda r: r["name"])


def pick_install(installed: list[dict], product: str) -> dict | None:
    """Best launchable match for a spoken product name; the newest version wins."""
    words = product.lower().split()
    matches = [r for r in installed if r["executable"] and all(w in r["name"].lower() for w in words)]
    return sorted(matches, key=lambda r: r["name"])[-1] if matches else None


def launch_command(target: dict, file: str = "") -> list[str]:
    argv = [target["executable"]]
    switch = next((s for key, s in ACAD_PRODUCT_SWITCH.items() if key in target["name"].lower()), None)
    if switch:
        argv += ["/product", switch]
    if file:
        argv.append(file)
    return argv


def find_bim_software(cfg) -> str:
    out: dict = {"installed": installed_software(cfg), "owner_toolset": cfg.toolset}
    if not IS_WINDOWS:
        out["note"] = (f"Automatic discovery reads the Windows registry; this computer runs "
                       f"{platform.system()}, so only programs listed in config.json are shown.")
    return json.dumps(out)


def launch_bim_app(cfg, product: str, file: str = "") -> str:
    installed = installed_software(cfg)
    target = pick_install(installed, product)
    if target is None:
        return json.dumps({"error": f"No launchable install matched '{product}'.",
                           "installed": [r["name"] for r in installed]})
    if file:
        path = Path(os.path.expandvars(file)).expanduser()
        if not path.exists():
            return f"File not found: {path}"
        file = str(path)
    subprocess.Popen(launch_command(target, file), close_fds=True)
    return f"Starting {target['name']}" + (f" with {file}." if file else ".")


def find_bim_files(cfg, name_contains: str = "", folder: str = "", limit: int = 25) -> str:
    roots = [Path(os.path.expandvars(folder)).expanduser()] if folder else \
            [Path(r).expanduser() for r in cfg.bim_search_roots] or \
            [Path.home() / "Documents", Path.home() / "Desktop", Path.home() / "Downloads"]
    needle = name_contains.lower()
    hits: list[tuple[float, Path, int]] = []
    scanned = 0
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and
                           d.lower() not in ("node_modules", "appdata", "__pycache__")]
            scanned += 1
            if scanned > 20_000:
                break
            for fn in filenames:
                if Path(fn).suffix.lower() in MODEL_EXTENSIONS and needle in fn.lower():
                    # Skip Revit backup copies such as model.0012.rvt
                    stem_parts = fn.rsplit(".", 2)
                    if len(stem_parts) == 3 and stem_parts[1].isdigit() and len(stem_parts[1]) == 4:
                        continue
                    full = Path(dirpath) / fn
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    hits.append((st.st_mtime, full, st.st_size))
    hits.sort(reverse=True)
    limit = max(1, min(limit, 100))
    return json.dumps({
        "searched": [str(r) for r in roots],
        "total_found": len(hits),
        "files": [{"path": str(p), "size_mb": round(size / 1e6, 1),
                   "modified": dt.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M")}
                  for m, p, size in hits[:limit]],
    })


def revit_query(cfg, what: str) -> str:
    url = f"{cfg.revit_bridge_url.rstrip('/')}/{what}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")[:20_000]
    except urllib.error.HTTPError as e:
        return f"The Revit bridge answered with an error ({e.code}): {e.read().decode('utf-8', 'replace')[:500]}"
    except (urllib.error.URLError, OSError) as e:
        return ("Could not reach the Revit bridge. Revit must be open with a model, pyRevit installed "
                "with its Routes server enabled, and the Assistant extension loaded. "
                f"(Tried {url}: {e})")


def register(reg: Registry, cfg) -> None:
    reg.add(Tool(
        "find_bim_software",
        "List the design software installed on this computer (Revit, Navisworks, AutoCAD, "
        "Civil 3D, Plant 3D, Unreal Engine and others) with versions, plus the owner's toolset.",
        {}, [], lambda: find_bim_software(cfg)))
    reg.add(Tool(
        "launch_bim_app",
        "Start an installed design application (Revit, Navisworks, AutoCAD, Civil 3D, Plant 3D, "
        "Unreal Engine or one the owner added), optionally opening a model or project file in it. "
        "Picks the newest version unless a year is included in the product name.",
        {"product": {"type": "string", "description": "e.g. 'Revit', 'Revit 2025', 'Navisworks Manage'"},
         "file": {"type": "string", "description": "Optional model file path to open"}},
        ["product"], lambda **kw: launch_bim_app(cfg, **kw)))
    reg.add(Tool(
        "find_bim_files",
        "Find model and project files (.rvt .rfa .nwd .nwf .nwc .ifc .dwg .dxf .dyn .uproject "
        ".fbx .rcp ...) on this computer, "
        "most recently modified first.",
        {"name_contains": {"type": "string", "description": "Optional part of the file name"},
         "folder": {"type": "string", "description": "Optional folder to search instead of the defaults"},
         "limit": {"type": "integer"}},
        [], lambda **kw: find_bim_files(cfg, **kw)))
    reg.add(Tool(
        "revit_query",
        "Read live information from the model currently open in Revit. 'model' gives the "
        "title, path, Revit version and project information; 'categories' gives element "
        "counts per category; 'warnings' gives the warning count and the most common "
        "warnings; 'levels', 'sheets' and 'links' list those elements.",
        {"what": {"type": "string",
                  "enum": ["model", "categories", "warnings", "levels", "sheets", "links"]}},
        ["what"], lambda what: revit_query(cfg, what)))
