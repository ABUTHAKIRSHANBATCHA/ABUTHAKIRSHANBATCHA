"""BIM project lifecycle: one checklist per project, from requirements review
and the pre-appointment BEP through to handover, following the ISO 19650-2
information management stages. Client requirements are added on top of the
template and tracked the same way."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from . import Registry, Tool

STAGES = {
    1: "Client requirements review",
    2: "Tender response and pre-appointment BEP",
    3: "Appointment and BEP confirmation",
    4: "Mobilisation",
    5: "Collaborative production",
    6: "Information model delivery",
    7: "Handover and close-out",
}

TEMPLATE = {
    1: [
        "Obtain and read the client's information requirements: EIR, OIR, AIR, PIR and the project information standard",
        "List every information deliverable the client requires, with format, level of information need and due milestone",
        "Confirm the client's naming convention, classification system and file formats (native, IFC, PDF, COBie)",
        "Confirm the client's CDE platform, folder structure, status codes and approval workflow",
        "Review the client's information delivery milestones against the project programme",
        "Review reference information and shared resources provided by the client (templates, libraries, surveys, existing models)",
        "Record unclear or conflicting requirements and raise them as tender queries",
    ],
    2: [
        "Write the pre-appointment BEP responding to each client requirement",
        "Propose the information delivery strategy and federation strategy (model breakdown, volumes, disciplines)",
        "Prepare the high-level responsibility matrix",
        "Assess the capability and capacity of each task team (people, software, hardware, training)",
        "Prepare the mobilisation plan",
        "Prepare the information delivery risk register",
        "Propose software, versions and exchange formats, and confirm interoperability",
        "Check the BEP against the client's tender evaluation criteria before submission",
    ],
    3: [
        "Confirm and issue the post-appointment BEP agreed with the client",
        "Produce the detailed responsibility matrix (who produces what, to what level of information need)",
        "Collect a TIDP from every task team and compile the MIDP",
        "Agree the project information standard and the production methods and procedures",
        "Set shared coordinates, project base point, survey point, levels and grids, and issue them to all teams",
        "Set the model breakdown structure, worksets and model file list with agreed file names",
        "Set clash detection rules, tolerances, clash matrix and the coordination meeting cycle",
        "Include information requirements and the BIM protocol in every appointment and subcontract",
    ],
    4: [
        "Set up the CDE: folders, permissions, status codes, revision codes and workflows",
        "Create project templates, shared parameters, title blocks and view templates",
        "Test information exchange between all teams and software (native, IFC, coordinates) and record the result",
        "Check that every team has the agreed software versions, licences and hardware",
        "Train and brief the teams on the BEP, CDE and project standards",
        "Run the BIM kick-off meeting and issue the minutes",
        "Confirm the mobilisation plan is complete before production starts",
    ],
    5: [
        "Each team checks its model against the project standards before sharing (naming, worksets, warnings, purged, coordinates)",
        "Share models to the CDE on the agreed cycle with the correct status and revision",
        "Federate the models and run clash detection to the clash matrix",
        "Hold coordination meetings, assign clashes and issues, and track them to closure",
        "Check model element data against the level of information need for the current milestone",
        "Check drawings and schedules are produced from the models and match them",
        "Track deliverables against the MIDP and report progress and delays",
        "Keep the BEP, MIDP and risk register up to date as the project changes",
    ],
    6: [
        "Task team quality assurance check completed and recorded for each deliverable",
        "Lead appointed party review against the EIR, the BEP and the level of information need",
        "Validate IFC exports and data drops (COBie or client asset data template)",
        "Authorise the information model and submit it to the client on the CDE",
        "Record the client's review comments and acceptance or rejection for each deliverable",
        "Resolve rejected information and resubmit",
        "Publish accepted information with the correct status and revision",
    ],
    7: [
        "Update models to as-built using site records, surveys and red-line mark-ups",
        "Complete the asset data (COBie or client template) and validate it against the AIR",
        "Link or attach O&M manuals, warranties, certificates and test records to the assets",
        "Deliver final native models, IFC, PDFs and the federated model in the required formats",
        "Check the handover package item by item against the client's handover requirements",
        "Obtain the client's written acceptance of the final information model",
        "Archive the project information in the CDE and confirm retention requirements",
        "Hold the lessons-learned session and record the outcomes",
    ],
}

STATUSES = ["open", "in_progress", "done", "not_applicable"]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


class Projects:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def _all(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.folder.glob("*.json"))]

    def _find(self, name: str) -> dict:
        projects = self._all()
        wanted = _slug(name)
        matches = [p for p in projects if _slug(p["name"]) == wanted] or \
                  [p for p in projects if wanted in _slug(p["name"])]
        if len(matches) != 1:
            raise LookupError(f"{'No' if not matches else 'More than one'} project matches '{name}'. "
                              f"Projects: {[p['name'] for p in projects]}")
        return matches[0]

    def _save(self, project: dict) -> None:
        path = self.folder / f"{_slug(project['name'])}.json"
        path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _progress(items: list[dict]) -> dict:
        counted = [i for i in items if i["status"] != "not_applicable"]
        done = sum(i["status"] == "done" for i in counted)
        return {"done": done, "total": len(counted),
                "percent": round(100 * done / len(counted)) if counted else 100}

    def summary(self) -> list[dict]:
        """Progress of every project and stage, for the dashboard."""
        return [{"name": p["name"], "client": p["client"], **self._progress(p["items"]),
                 "stages": [{"stage": n, "title": title,
                             **self._progress([i for i in p["items"] if i["stage"] == n])}
                            for n, title in STAGES.items()]}
                for p in self._all()]

    # ------------------------------------------------------------------ tools

    def create(self, name: str, client: str = "", description: str = "") -> str:
        if (self.folder / f"{_slug(name)}.json").exists():
            return f"A project named '{name}' already exists."
        items = [{"id": f"S{stage}-{n:02d}", "stage": stage, "text": text, "source": "ISO 19650 template",
                  "status": "open", "note": "", "updated": ""}
                 for stage, texts in TEMPLATE.items() for n, text in enumerate(texts, 1)]
        self._save({"name": name, "client": client, "description": description,
                    "created": _now(), "items": items})
        return (f"Created project '{name}' with {len(items)} checklist items across {len(STAGES)} stages. "
                "Add the client's own requirements with bim_requirement_add.")

    def status(self, project: str = "", stage: int = 0, only_open: bool = False) -> str:
        if not project:
            return json.dumps({"projects": [
                {"name": p["name"], "client": p["client"], **self._progress(p["items"])} for p in self._all()]})
        try:
            p = self._find(project)
        except LookupError as e:
            return str(e)
        stages = []
        for number, title in STAGES.items():
            if stage and stage != number:
                continue
            items = [i for i in p["items"] if i["stage"] == number]
            shown = [i for i in items if not only_open or i["status"] in ("open", "in_progress")]
            entry = {"stage": number, "title": title, **self._progress(items)}
            if stage or only_open:
                entry["items"] = [{k: i[k] for k in ("id", "text", "source", "status", "note") if i[k]}
                                  for i in shown]
            stages.append(entry)
        return json.dumps({"name": p["name"], "client": p["client"], "description": p["description"],
                           "overall": self._progress(p["items"]), "stages": stages}, ensure_ascii=False)

    def add_requirement(self, project: str, stage: int, requirement: str, source: str = "") -> str:
        if stage not in STAGES:
            return f"Stage must be 1 to {len(STAGES)}: {STAGES}"
        try:
            p = self._find(project)
        except LookupError as e:
            return str(e)
        n = 1 + sum(i["id"].startswith(f"C{stage}-") for i in p["items"])
        item = {"id": f"C{stage}-{n:02d}", "stage": stage, "text": requirement,
                "source": f"Client requirement{': ' + source if source else ''}",
                "status": "open", "note": "", "updated": _now()}
        p["items"].append(item)
        self._save(p)
        return f"Added {item['id']} to stage {stage} ({STAGES[stage]})."

    def update_item(self, project: str, item_id: str, status: str, note: str = "") -> str:
        try:
            p = self._find(project)
        except LookupError as e:
            return str(e)
        for item in p["items"]:
            if item["id"].lower() == item_id.lower():
                item.update(status=status, updated=_now())
                if note:
                    item["note"] = note
                self._save(p)
                return f"{item['id']} is now {status}. Project progress: {self._progress(p['items'])['percent']} percent."
        return f"No item '{item_id}' in {p['name']}."

    def export(self, project: str) -> str:
        try:
            p = self._find(project)
        except LookupError as e:
            return str(e)
        marks = {"open": "[ ]", "in_progress": "[~]", "done": "[x]", "not_applicable": "[-]"}
        overall = self._progress(p["items"])
        lines = [f"# {p['name']} - BIM checklist", "",
                 f"Client: {p['client'] or '-'}  ", f"Exported: {_now()}  ",
                 f"Progress: {overall['done']} of {overall['total']} ({overall['percent']}%)", "",
                 "`[ ]` open, `[~]` in progress, `[x]` done, `[-]` not applicable", ""]
        for number, title in STAGES.items():
            items = [i for i in p["items"] if i["stage"] == number]
            prog = self._progress(items)
            lines += [f"## {number}. {title} ({prog['done']}/{prog['total']})", ""]
            for i in items:
                tag = " **(client)**" if i["id"].startswith("C") else ""
                lines.append(f"- {marks[i['status']]} `{i['id']}`{tag} {i['text']}"
                             + (f"  \n  Note: {i['note']}" if i["note"] else ""))
            lines.append("")
        out = self.folder / "exports" / f"{_slug(p['name'])}-checklist.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        return f"Checklist written to {out}"


def register(reg: Registry, projects: Projects) -> None:
    stage_help = "Stage number: " + ", ".join(f"{k} {v}" for k, v in STAGES.items())
    reg.add(Tool(
        "bim_project_create",
        "Start tracking a BIM project. Creates its checklist from the ISO 19650 template, covering "
        "client requirements review, pre-appointment BEP, appointment, mobilisation, production, "
        "delivery and handover.",
        {"name": {"type": "string"}, "client": {"type": "string"}, "description": {"type": "string"}},
        ["name"], projects.create))
    reg.add(Tool(
        "bim_project_status",
        "Progress of BIM projects. With no project: list all projects. With a project: progress per "
        "stage. Give a stage number or only_open to get the individual checklist items with their ids.",
        {"project": {"type": "string"}, "stage": {"type": "integer", "description": stage_help},
         "only_open": {"type": "boolean"}},
        [], projects.status))
    reg.add(Tool(
        "bim_requirement_add",
        "Add one client-specific requirement to a project's checklist, taken from the client's EIR, "
        "contract or instructions. Add each requirement as its own item, worded so it can be checked "
        "off, and give the document and clause as the source.",
        {"project": {"type": "string"}, "stage": {"type": "integer", "description": stage_help},
         "requirement": {"type": "string"}, "source": {"type": "string", "description": "e.g. 'EIR v2 clause 4.3'"}},
        ["project", "stage", "requirement"], projects.add_requirement))
    reg.add(Tool(
        "bim_item_update",
        "Set the status of one checklist item and optionally record a note or the evidence.",
        {"project": {"type": "string"}, "item_id": {"type": "string", "description": "e.g. S3-04 or C1-02"},
         "status": {"type": "string", "enum": STATUSES}, "note": {"type": "string"}},
        ["project", "item_id", "status"], projects.update_item))
    reg.add(Tool(
        "bim_project_export",
        "Write a project's full checklist to a Markdown file and return its path, for printing or "
        "sharing. Open it with open_target if the owner wants to see it.",
        {"project": {"type": "string"}}, ["project"], projects.export))
