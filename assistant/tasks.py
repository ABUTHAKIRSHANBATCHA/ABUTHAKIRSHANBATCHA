"""Scheduled tasks: work Lumi does on its own at a set time, once or repeating."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

REPEATS = {"none": None, "hourly": dt.timedelta(hours=1), "daily": dt.timedelta(days=1),
           "weekly": dt.timedelta(weeks=1)}
FORMAT = "%Y-%m-%d %H:%M"


class Tasks:
    def __init__(self, file: Path) -> None:
        self.file = file
        self.items: list[dict] = json.loads(file.read_text(encoding="utf-8")) if file.exists() else []

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, instruction: str, run_at: str, repeat: str = "none") -> str:
        try:
            when = dt.datetime.strptime(run_at, FORMAT)
        except ValueError:
            return "run_at must be written as YYYY-MM-DD HH:MM (24-hour clock)."
        task = {"id": uuid.uuid4().hex[:6], "instruction": instruction, "run_at": when.strftime(FORMAT),
                "repeat": repeat, "last_run": "", "last_result": ""}
        self.items.append(task)
        self._save()
        return f"Scheduled task {task['id']} for {task['run_at']}" + (f", repeating {repeat}." if repeat != "none" else ".")

    def listing(self) -> str:
        return json.dumps({"tasks": sorted(self.items, key=lambda t: t["run_at"])}, ensure_ascii=False)

    def cancel(self, task_id: str) -> str:
        keep = [t for t in self.items if t["id"] != task_id]
        if len(keep) == len(self.items):
            return f"No task with id {task_id}."
        self.items = keep
        self._save()
        return f"Cancelled task {task_id}."

    def take_due(self, now: dt.datetime | None = None) -> list[dict]:
        """Tasks whose time has come. Each is rescheduled (or removed) before it runs, so a
        crash or restart cannot make it run twice."""
        now = now or dt.datetime.now()
        due = [t for t in self.items if dt.datetime.strptime(t["run_at"], FORMAT) <= now]
        for task in due:
            step = REPEATS.get(task["repeat"])
            if step is None:
                self.items.remove(task)
            else:
                nxt = dt.datetime.strptime(task["run_at"], FORMAT)
                while nxt <= now:                        # skip runs missed while the PC was off
                    nxt += step
                task["run_at"], task["last_run"] = nxt.strftime(FORMAT), now.strftime(FORMAT)
        if due:
            self._save()
        return [dict(t) for t in due]

    def record(self, task_id: str, result: str) -> None:
        for task in self.items:
            if task["id"] == task_id:
                task["last_result"] = result[:500]
                self._save()

    def summary(self) -> list[dict]:
        return [{"id": t["id"], "instruction": t["instruction"][:80], "run_at": t["run_at"], "repeat": t["repeat"]}
                for t in sorted(self.items, key=lambda t: t["run_at"])[:6]]
