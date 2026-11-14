"""Lumi's hands on the Project Control Tower. Every tool goes through the File
Management Service, which validates the request and records it in the audit log."""
from __future__ import annotations

import json

from ..tower.service import Tower, TowerError
from . import Registry, Tool

SECTIONS = {"drawings": "drawing_register", "revisions": "revision_history", "submissions": "submission_history",
            "responses": "responses", "actions": "actions", "timesheets": "timesheets", "history": "history"}


def register(reg: Registry, tower: Tower, source: str = "chat") -> None:
    def safe(fn):
        def run(**kw):
            try:
                return json.dumps(fn(**kw), ensure_ascii=False, default=str)
            except TowerError as e:
                return f"Not done: {e}"
        return run

    def detail(project: str, section: str = "drawings") -> dict:
        data = tower.project_detail(project)
        rows = data[SECTIONS[section]]
        for row in rows:
            row.pop("view", None)
            row.pop("hash", None)
        return {"project": data["project"], section: rows[:60], "total": len(rows)}

    text = {"type": "string"}
    reg.add(Tool(
        "tower_projects",
        "The projects in the Project Control Tower with their drawing counts by status, open and overdue "
        "actions and recent hours, plus uploads waiting for the owner's confirmation. Start here.",
        {}, [], safe(lambda: {"projects": tower.overview(), "uploads_waiting": tower.pending_uploads()})))
    reg.add(Tool(
        "tower_project_detail",
        "One register of one project: drawings (the drawing register), revisions, submissions, responses, "
        "actions (coordination log), timesheets, or history (recent events). Use it to find a drawing "
        "number or action id before updating it.",
        {"project": text, "section": {"type": "string", "enum": list(SECTIONS)}}, ["project"], safe(detail)))
    reg.add(Tool(
        "tower_create_project",
        "Create a new project: a permanent project code, the standard folder structure and empty "
        "registers. If a similar project exists the tool returns a question instead; ask the owner and "
        "call again with confirm_similar only after they say it is a different project.",
        {"name": text, "code": {"type": "string", "description": "Optional short code; made from the name if omitted"},
         "confirm_similar": {"type": "boolean"}}, ["name"], safe(tower.create_project)))
    reg.add(Tool(
        "tower_confirm_upload",
        "Give the owner's answers for an upload that is waiting for confirmation, so it can be filed. "
        "Pass only what the owner actually told you; if something is still missing the tool asks again. "
        "doc_type is design or shop for drawings, or one of: transmittal, crs, consultant_response, "
        "clash_report, rfi, site_issue, meeting_minutes, technical_submittal, datasheet, timesheet, "
        "as_built, correspondence.",
        {"job_id": {"type": "integer"}, "project": text, "area": text, "discipline": text, "revision": text,
         "doc_type": text, "number": {"type": "string", "description": "Drawing or document number, if the owner gave one"}},
        ["job_id"], safe(tower.confirm_upload)))
    reg.add(Tool(
        "tower_record_submission",
        "Record that a drawing was submitted (a new, separate history entry). `document` is the drawing "
        "number or words that identify exactly one drawing in the project.",
        {"project": text, "document": text, "submission_date": {"type": "string", "description": "YYYY-MM-DD or today"},
         "transmittal": text, "reviewer": text, "revision": text, "comments": text},
        ["project", "document"], safe(lambda **kw: tower.record_submission(source=source, **kw))))
    reg.add(Tool(
        "tower_record_response",
        "Record a consultant's response to a drawing: the code (A, B, C, D ...) and their comments, as a "
        "new history entry. Updates the drawing's latest status.",
        {"project": text, "document": text, "code": text, "comments": text,
         "response_date": {"type": "string", "description": "YYYY-MM-DD or today"}, "revision": text},
        ["project", "document", "code"], safe(lambda **kw: tower.record_response(source=source, **kw))))
    reg.add(Tool(
        "tower_action_add",
        "Add an item to a project's coordination action log: something pending, an issue, or a task "
        "with an owner and a target date.",
        {"project": text, "description": text, "discipline": text, "area": text, "assigned_to": text,
         "target_date": {"type": "string", "description": "YYYY-MM-DD"},
         "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "related_drawing": text},
        ["project", "description"], safe(lambda **kw: tower.add_action(source=source, **kw))))
    reg.add(Tool(
        "tower_action_update",
        "Update a coordination action by its id: add the latest update, change the target date, or set "
        "the status. Closing an action requires closure_evidence.",
        {"action_id": {"type": "integer"}, "update": text,
         "status": {"type": "string", "enum": ["open", "in_progress", "closed"]}, "closure_evidence": text,
         "target_date": text},
        ["action_id"], safe(lambda **kw: tower.update_action(source=source, **kw))))
    reg.add(Tool(
        "tower_timesheet_record",
        "Record hours worked. Use only the hours, date, project and person the owner stated; never fill "
        "in or estimate hours. The person defaults to the owner. If the tool reports a duplicate, ask "
        "the owner before calling again with allow_duplicate.",
        {"project": text, "hours": {"type": "number"}, "activity": text,
         "date": {"type": "string", "description": "YYYY-MM-DD or today"}, "person": text, "discipline": text,
         "reference": {"type": "string", "description": "Drawing or task reference, if given"},
         "description": text, "allow_duplicate": {"type": "boolean"}},
        ["project", "hours", "activity"], safe(lambda **kw: tower.record_timesheet(source=source, **kw))))
    reg.add(Tool(
        "tower_timesheet_amend",
        "Correct or cancel a timesheet entry by its id. The earlier values are kept in the history.",
        {"entry_id": {"type": "integer"}, "hours": {"type": "number"}, "activity": text, "date": text,
         "void": {"type": "boolean", "description": "true cancels the entry"}, "reason": text},
        ["entry_id"], safe(lambda **kw: tower.amend_timesheet(source=source, **kw))))
    reg.add(Tool(
        "tower_add_person",
        "Add a person so hours can be recorded for them. Only after the owner confirms the person is new.",
        {"name": text}, ["name"], safe(tower.add_person)))
    reg.add(Tool(
        "tower_export_timesheet",
        "Write a timesheet workbook for a day, week or month (optionally one project) and return its "
        "path inside the tower folder.",
        {"period": {"type": "string", "enum": ["day", "week", "month"]}, "date": text, "project": text},
        [], safe(lambda **kw: {"file": tower.export_timesheet(**kw)})))
