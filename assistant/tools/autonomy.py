"""Tools for working without the owner present: scheduled tasks, messages to
the owner's phone, and the feed of folder and log changes."""
from __future__ import annotations

import json

from ..tasks import REPEATS
from . import Registry, Tool


def register(reg: Registry, tasks, watcher, notify) -> None:
    async def notify_owner(message: str) -> str:
        return await notify(message)

    reg.add(Tool(
        "task_schedule",
        "Schedule work for yourself to do later without the owner present, once or repeating. "
        "At the set time you receive the instruction as a new request and the result is sent to "
        "the owner's phone. Write the instruction so it is complete on its own. Get the current "
        "date and time from get_system_status before choosing run_at.",
        {"instruction": {"type": "string"},
         "run_at": {"type": "string", "description": "YYYY-MM-DD HH:MM, 24-hour, this computer's local time"},
         "repeat": {"type": "string", "enum": list(REPEATS)}},
        ["instruction", "run_at"], tasks.add))
    reg.add(Tool("task_list", "List the scheduled tasks with their ids, next run time and last result.",
                 {}, [], tasks.listing))
    reg.add(Tool("task_cancel", "Cancel a scheduled task by its id.",
                 {"task_id": {"type": "string"}}, ["task_id"], tasks.cancel))
    reg.add(Tool(
        "notify_owner",
        "Send a message to the owner's phone (Telegram) and screens right now. Use it to report "
        "the result of unattended work or something that needs their attention.",
        {"message": {"type": "string"}}, ["message"], notify_owner))
    if watcher.sources:
        names = ", ".join(s["name"] for s in watcher.sources)
        reg.add(Tool(
            "recent_changes",
            f"What changed recently in the watched folders and log files ({names}): files added, "
            "changed or removed, and new log lines, newest first.",
            {"source": {"type": "string", "description": "Optional: only this watched source"},
             "limit": {"type": "integer"}},
            [], lambda source="", limit=20: json.dumps({"events": watcher.recent(limit, source)}, ensure_ascii=False)))
