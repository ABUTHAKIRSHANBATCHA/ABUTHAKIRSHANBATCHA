"""Project filing: keep every project in the owner's folder arrangement and
put incoming files (for example ones sent from the phone) in the right place.

Everything here stays inside `projects_root`; files are moved, never
overwritten or deleted."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from . import Registry, Tool


def _clean(name: str) -> str:
    """A safe single folder or file name."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "untitled"


class Filing:
    def __init__(self, root: str, template: list[str], inbox: Path) -> None:
        self.root = Path(os.path.expandvars(root)).expanduser() if root else None
        self.template, self.inbox = template, inbox
        inbox.mkdir(parents=True, exist_ok=True)

    def _inside(self, path: Path, base: Path) -> bool:
        try:
            path.resolve().relative_to(base.resolve())
            return True
        except ValueError:
            return False

    def _project(self, name: str) -> Path | None:
        """Existing project folder matching a spoken name (exact, then partial)."""
        folders = [p for p in self.root.iterdir() if p.is_dir()] if self.root.is_dir() else []
        wanted = name.lower().strip()
        exact = [p for p in folders if p.name.lower() == wanted]
        partial = [p for p in folders if all(w in p.name.lower() for w in wanted.split())]
        found = exact or partial
        return found[0] if len(found) == 1 else None

    def _target(self, project: Path, subfolder: str) -> Path | None:
        parts = [part.strip() for part in re.split(r"[\\/]+", subfolder) if part.strip()]
        if any(part in (".", "..") for part in parts):
            return None
        target = project.joinpath(*[_clean(part) for part in parts])
        return target if self._inside(target, project) else None

    # ------------------------------------------------------------------ tools

    def folders(self, project: str = "") -> str:
        if self.root is None:
            return "No projects folder is set. Ask the owner where their projects are kept and to run setup."
        if not project:
            names = sorted(p.name for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else []
            waiting = sorted(p.name for p in self.inbox.iterdir() if p.is_file())
            return json.dumps({"projects_root": str(self.root), "projects": names,
                               "standard_subfolders": self.template, "files_waiting_in_inbox": waiting})
        folder = self._project(project)
        if folder is None:
            return json.dumps({"error": f"No single project folder matches '{project}'.",
                               "projects": sorted(p.name for p in self.root.iterdir() if p.is_dir())
                               if self.root.is_dir() else []})
        tree = {}
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            items = sorted(sub.iterdir(), key=lambda p: p.name.lower())
            tree[sub.name] = {"subfolders": [p.name for p in items if p.is_dir()],
                              "files": len([p for p in items if p.is_file()]),
                              "latest_files": [p.name for p in sorted((p for p in items if p.is_file()),
                                               key=lambda p: p.stat().st_mtime, reverse=True)[:5]]}
        return json.dumps({"project": folder.name, "path": str(folder), "folders": tree,
                           "loose_files": [p.name for p in folder.iterdir() if p.is_file()]}, ensure_ascii=False)

    def create(self, project: str, subfolder: str = "") -> str:
        if self.root is None:
            return "No projects folder is set, so I cannot create project folders yet."
        folder = self._project(project)
        made = []
        if folder is None:
            folder = self.root / _clean(project)
            if folder.exists():
                return f"More than one project matches '{project}'; use the full name."
            for name in self.template:
                (folder / _clean(name)).mkdir(parents=True, exist_ok=True)
            folder.mkdir(parents=True, exist_ok=True)
            made.append(f"project folder '{folder.name}' with {len(self.template)} standard subfolders")
        if subfolder:
            target = self._target(folder, subfolder)
            if target is None:
                return "That subfolder would be outside the project folder."
            if not target.exists():
                target.mkdir(parents=True)
                made.append(f"subfolder '{target.relative_to(folder)}'")
        return ("Created " + " and ".join(made) + f" in {folder}.") if made else f"Already exists: {folder / subfolder}"

    def file(self, source: str, project: str, subfolder: str, new_name: str = "") -> str:
        if self.root is None:
            return "No projects folder is set, so I cannot file anything yet."
        src = Path(source) if Path(source).is_absolute() else self.inbox / source
        if not src.is_file():
            waiting = sorted(p.name for p in self.inbox.iterdir() if p.is_file())
            return f"No such file: {source}. Files waiting in the inbox: {waiting}"
        if not (self._inside(src, self.inbox) or self._inside(src, self.root)):
            return "I only file things from the inbox or from inside the projects folder."
        folder = self._project(project)
        if folder is None:
            return (f"There is no project folder for '{project}' yet. Create it with project_folder_create "
                    f"first, or check the name. Existing: {sorted(p.name for p in self.root.iterdir() if p.is_dir())}")
        target_dir = self._target(folder, subfolder)
        if target_dir is None:
            return "That subfolder would be outside the project folder."
        target_dir.mkdir(parents=True, exist_ok=True)
        name = _clean(new_name) if new_name else src.name
        if new_name and not Path(name).suffix:
            name += src.suffix
        target, n = target_dir / name, 1
        while target.exists():                              # never overwrite: tower.rvt -> tower_2.rvt
            n += 1
            target = target_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        shutil.move(str(src), str(target))
        return f"Filed as {target.relative_to(self.root)} (full path {target})."


def register(reg: Registry, filing: Filing) -> None:
    reg.add(Tool(
        "project_folders",
        "The owner's project folders. With no project: the list of projects, the standard "
        "subfolder arrangement, and files waiting in the inbox (received from the phone, not yet "
        "filed). With a project: its subfolders, how many files each holds and the newest files.",
        {"project": {"type": "string"}}, [], filing.folders))
    reg.add(Tool(
        "project_folder_create",
        "Create a new project folder with the owner's standard subfolders, or add a subfolder to "
        "an existing project (nested paths such as '03_Models/Structure' are fine).",
        {"project": {"type": "string"}, "subfolder": {"type": "string"}},
        ["project"], filing.create))
    reg.add(Tool(
        "file_to_project",
        "Move a file into a project's subfolder, creating the subfolder if needed. Nothing is ever "
        "overwritten; a number is added to the name instead. `source` is a file name in the inbox "
        "or a full path inside the projects folder. Use new_name to rename to the project's naming "
        "convention when the owner has one.",
        {"source": {"type": "string"}, "project": {"type": "string"},
         "subfolder": {"type": "string", "description": "e.g. '03_Models' or '05_Clash_Reports/2026-11'"},
         "new_name": {"type": "string"}},
        ["source", "project", "subfolder"], filing.file))
