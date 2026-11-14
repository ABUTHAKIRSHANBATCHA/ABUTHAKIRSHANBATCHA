"""File Management Service for the Project Control Tower.

The only code that writes inside the tower folder. The AI never gets raw file
access: it asks for the operations below, each of which validates its target,
never overwrites or deletes a revision, and is written to the audit log.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import shutil
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import and_, func, select

from . import db

MASTER_FOLDERS = [
    "00_DASHBOARD/PROJECTVERSE_3D", "00_DASHBOARD/REGISTERS", "00_DASHBOARD/EXPORTS", "00_DASHBOARD/ASSETS",
    "01_UPDATE_INBOX/01_TEXT", "01_UPDATE_INBOX/02_VOICE", "01_UPDATE_INBOX/03_MOBILE_UPLOADS",
    "01_UPDATE_INBOX/04_NEEDS_CONFIRMATION", "01_UPDATE_INBOX/05_PROCESSED", "01_UPDATE_INBOX/06_FAILED",
    "02_PROJECTS",
    "03_SHARED_TEMPLATES/DRAWING_LOG", "03_SHARED_TEMPLATES/SUBMISSION_LOG", "03_SHARED_TEMPLATES/COORDINATION_LOG",
    "03_SHARED_TEMPLATES/TIMESHEET", "03_SHARED_TEMPLATES/DATASHEET", "03_SHARED_TEMPLATES/MEETING_MINUTES",
    "04_LUMI_SERVICES/WORKFLOWS", "04_LUMI_SERVICES/AUDIT_LOGS", "04_LUMI_SERVICES/CONFIG",
    "99_SYSTEM_BACKUPS",
]
PROJECT_FOLDERS = [
    "00_PROJECT_INFORMATION", "01_DRAWINGS",
    "02_SUBMISSIONS/TRANSMITTALS", "02_SUBMISSIONS/CONSULTANT_RESPONSES", "02_SUBMISSIONS/CRS",
    "03_COORDINATION/CLASH_REPORTS", "03_COORDINATION/SITE_ISSUES", "03_COORDINATION/RFI",
    "03_COORDINATION/MEETING_MINUTES",
    "04_DATASHEETS/EQUIPMENT", "04_DATASHEETS/MATERIALS", "04_DATASHEETS/TECHNICAL_SUBMITTALS",
    "05_PLANNING/BASELINE", "05_PLANNING/LOOKAHEAD", "05_PLANNING/PROGRESS", "05_PLANNING/TIMESHEETS",
    "06_BIM_MODELS/REVIT", "06_BIM_MODELS/NAVISWORKS", "06_BIM_MODELS/IFC", "06_BIM_MODELS/COORDINATION_EXPORTS",
    "07_AS_BUILT", "08_CORRESPONDENCE", "09_LOGS_AND_REGISTERS", "10_DASHBOARD_DATA", "99_ARCHIVE",
]
# What a non-drawing document is, from words in the instruction or its file type -> where it lives.
CATEGORIES = [
    ("transmittal", ["transmittal"], [], "02_SUBMISSIONS/TRANSMITTALS"),
    ("crs", ["crs", "comment resolution"], [], "02_SUBMISSIONS/CRS"),
    ("consultant_response", ["consultant response", "consultant reply", "response sheet"], [],
     "02_SUBMISSIONS/CONSULTANT_RESPONSES"),
    ("clash_report", ["clash"], [], "03_COORDINATION/CLASH_REPORTS"),
    ("rfi", ["rfi", "request for information"], [], "03_COORDINATION/RFI"),
    ("site_issue", ["site issue", "site observation", "snag"], [], "03_COORDINATION/SITE_ISSUES"),
    ("meeting_minutes", ["minutes", "mom"], [], "03_COORDINATION/MEETING_MINUTES"),
    ("technical_submittal", ["technical submittal", "material submittal"], [], "04_DATASHEETS/TECHNICAL_SUBMITTALS"),
    ("datasheet", ["datasheet", "data sheet"], [], "04_DATASHEETS/EQUIPMENT"),
    ("timesheet", ["timesheet"], [], "05_PLANNING/TIMESHEETS"),
    ("as_built", ["as built", "as-built"], [], "07_AS_BUILT"),
    ("correspondence", ["letter", "correspondence", "email"], [], "08_CORRESPONDENCE"),
    ("revit_model", [], [".rvt", ".rfa"], "06_BIM_MODELS/REVIT"),
    ("navisworks_model", [], [".nwd", ".nwf", ".nwc"], "06_BIM_MODELS/NAVISWORKS"),
    ("ifc_model", [], [".ifc"], "06_BIM_MODELS/IFC"),
]
DRAWING_TYPES = {".pdf", ".dwg", ".dxf"}
ALLOWED_TYPES = DRAWING_TYPES | {".xlsx", ".xls", ".csv", ".docx", ".doc", ".txt", ".png", ".jpg", ".jpeg", ".zip",
                                 ".rvt", ".rfa", ".nwd", ".nwf", ".nwc", ".ifc", ".m4a", ".mp3", ".wav", ".ogg"}
SIGNATURES = {".pdf": b"%PDF", ".zip": b"PK", ".xlsx": b"PK", ".docx": b"PK", ".png": b"\x89PNG", ".jpg": b"\xff\xd8",
              ".jpeg": b"\xff\xd8"}
REVISION_IN_NAME = re.compile(r"[-_ .](?:REV(?:ISION)?[-_ .]?|R)([0-9]{1,3}|[A-Z])$", re.I)
REVISION_IN_TEXT = re.compile(r"\brev(?:ision)?\.?\s*([0-9]{1,3}|[A-Z])\b", re.I)
RESPONSE_CODE = re.compile(r"\bcode\s+([A-E])\b", re.I)


class TowerError(Exception):
    """A request the service refuses, with a message fit to show the owner."""


def clean_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "untitled"


def folder_name(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_") or "GENERAL"


def revision_key(rev: str):
    return (0, int(rev)) if rev.isdigit() else (1, rev.upper())


class Tower:
    def __init__(self, root: Path, db_url: str = "", owner: str = "", max_upload_mb: int = 300) -> None:
        self.root = Path(root)
        self.owner = owner
        self.max_bytes = max_upload_mb * 1024 * 1024
        for folder in MASTER_FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.engine = db.connect(db_url or f"sqlite:///{self.root / '04_LUMI_SERVICES' / 'CONFIG' / 'tower.db'}")
        key_file = self.root / "04_LUMI_SERVICES" / "CONFIG" / "link.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
        self._key = key_file.read_text().strip().encode()
        with self.engine.begin() as c:
            if not c.execute(select(func.count()).select_from(db.disciplines)).scalar():
                c.execute(db.disciplines.insert(), [{"code": code, "name": name, "aliases": json.dumps(aliases)}
                                                    for code, name, aliases in db.STANDARD_DISCIPLINES])
            if owner and not c.execute(select(db.personnel).where(db.personnel.c.name == owner)).first():
                c.execute(db.personnel.insert().values(name=owner, is_owner=True))

    # ---------------------------------------------------------------- helpers

    def _audit(self, c, operation: str, target: str, detail: dict | None = None, ok: bool = True, actor: str = "") -> None:
        c.execute(db.audit.insert().values(at=db.now(), actor=actor or self.owner or "lumi", operation=operation,
                                           target=target, detail=json.dumps(detail or {}, default=str), ok=ok))

    def _event(self, c, project_id, entity: str, entity_id, event: str, detail: dict, source: str) -> None:
        c.execute(db.activity.insert().values(at=db.now(), project_id=project_id, entity=entity, entity_id=entity_id,
                                              event=event, detail=json.dumps(detail, default=str), source=source))

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        resolved.relative_to(self.root.resolve())          # raises ValueError if it escapes the tower
        return resolved

    def _rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root.resolve())).replace("\\", "/")

    @staticmethod
    def _date(value) -> dt.date:
        if isinstance(value, dt.date):
            return value
        if not value or str(value).lower() == "today":
            return dt.date.today()
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            raise TowerError(f"The date '{value}' must be written as YYYY-MM-DD.") from None

    # ---------------------------------------------------------------- lookups

    def _projects(self, c) -> list:
        return c.execute(select(db.projects).order_by(db.projects.c.id)).all()

    def _find_project(self, c, text: str):
        """Project named in `text` by code, alias or name. None if none, or if two fit equally well.
        The longest matching name wins, so "Alpha 2" is not mistaken for "Alpha"."""
        if not text:
            return None
        norm = lambda v: re.sub(r"[^A-Z0-9]+", " ", v.upper()).strip()
        upper = f" {norm(text)} "
        best: dict[int, int] = {}
        rows = {p.id: p for p in self._projects(c)}
        for p in rows.values():
            for n in [p.code, p.name] + json.loads(p.aliases):
                if n and f" {norm(n)} " in upper:
                    best[p.id] = max(best.get(p.id, 0), len(norm(n)))
        if not best:
            return None
        top = max(best.values())
        winners = [pid for pid, size in best.items() if size == top]
        return rows[winners[0]] if len(winners) == 1 else None

    def _find_area(self, c, project_id: int, text: str):
        upper = " " + re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()) + " "
        hits = [a for a in c.execute(select(db.areas).where(db.areas.c.project_id == project_id)).all()
                if f" {a.code} " in upper or f" {re.sub(r'[^A-Z0-9]+', ' ', a.name.upper()).strip()} " in upper]
        return hits[0] if len(hits) == 1 else None

    def _find_discipline(self, c, text: str):
        upper = " " + re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()) + " "
        hits = []
        for d in c.execute(select(db.disciplines)).all():
            names = [d.code, d.name] + json.loads(d.aliases)
            if any(f" {n.upper()} " in upper for n in names):
                hits.append(d)
        return hits[0] if len(hits) == 1 else None

    def _area(self, c, project_id: int, name: str, create: bool):
        found = self._find_area(c, project_id, name)
        if found or not create or not name.strip():
            return found
        code = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)).upper() if " " in name.strip() else folder_name(name)
        taken = {a.code for a in c.execute(select(db.areas).where(db.areas.c.project_id == project_id)).all()}
        if code in taken:
            code = folder_name(name)
        new_id = c.execute(db.areas.insert().values(project_id=project_id, code=code, name=name.strip())).inserted_primary_key[0]
        self._audit(c, "create_area", f"{project_id}/{code}", {"name": name})
        return c.execute(select(db.areas).where(db.areas.c.id == new_id)).first()

    # --------------------------------------------------------------- projects

    def create_project(self, name: str, code: str = "", aliases: list[str] | None = None,
                       areas: list[tuple[str, str]] | None = None, confirm_similar: bool = False) -> dict:
        name = name.strip()
        if not name:
            raise TowerError("A project needs a name.")
        code = folder_name(code or name)
        with self.engine.begin() as c:
            existing = self._projects(c)
            for p in existing:
                same = p.code == code or p.name.lower() == name.lower()
                similar = SequenceMatcher(None, p.name.lower(), name.lower()).ratio() >= 0.8 or \
                    name.upper() in [a.upper() for a in json.loads(p.aliases)]
                if same:
                    raise TowerError(f"The project '{p.name}' ({p.code}) already exists.")
                if similar and not confirm_similar:
                    return {"created": False, "needs_confirmation": True, "similar_to": {"code": p.code, "name": p.name},
                            "question": f"A project called '{p.name}' already exists. Is '{name}' a different project?"}
            pid = c.execute(db.projects.insert().values(code=code, name=name, aliases=json.dumps(aliases or []),
                                                        created_at=db.now())).inserted_primary_key[0]
            for area_code, area_name in areas or []:
                c.execute(db.areas.insert().values(project_id=pid, code=area_code.upper(), name=area_name))
            base = self.root / "02_PROJECTS" / code
            for folder in PROJECT_FOLDERS:
                (base / folder).mkdir(parents=True, exist_ok=True)
            self._audit(c, "create_project", code, {"name": name, "folders": len(PROJECT_FOLDERS)})
            self._event(c, pid, "project", pid, "created", {"name": name}, "lumi")
        self.export_registers(code)
        return {"created": True, "project_id": pid, "code": code, "name": name, "folder": f"02_PROJECTS/{code}"}

    def seed_projects(self, projects: list[dict]) -> None:
        """Create the starting projects once, on an empty tower."""
        with self.engine.begin() as c:
            if self._projects(c):
                return
        for p in projects:
            self.create_project(p["name"], p.get("code", ""), p.get("aliases", []),
                                [tuple(a) for a in p.get("areas", [])], confirm_similar=True)

    # ---------------------------------------------------------------- uploads

    def receive_upload(self, filename: str, data: bytes, instruction: str = "", source: str = "mobile") -> dict:
        """Take in a file: quarantine it, check it, work out where it belongs, and file it or ask."""
        original = clean_name(Path(filename).name)
        suffix = Path(original).suffix.lower()
        sha = hashlib.sha256(data).hexdigest()
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        inbox = self.root / "01_UPDATE_INBOX" / "03_MOBILE_UPLOADS" / f"{stamp}_{sha[:8]}_{original}"
        inbox.write_bytes(data)                                    # the isolated incoming area
        problem = None
        if not data:
            problem = "The file is empty."
        elif len(data) > self.max_bytes:
            problem = f"The file is larger than the {self.max_bytes // 1024 // 1024} MB limit."
        elif suffix not in ALLOWED_TYPES:
            problem = f"Files of type '{suffix or 'unknown'}' are not accepted."
        elif data[:2] == b"MZ":
            problem = "The file is a Windows program, not a document."
        elif suffix in SIGNATURES and not data.startswith(SIGNATURES[suffix]):
            problem = f"The file does not look like a real {suffix} file; it may be damaged or renamed."
        with self.engine.begin() as c:
            job_id = c.execute(db.upload_jobs.insert().values(
                received_at=db.now(), original_name=original, inbox_path=self._rel(inbox), sha256=sha, size=len(data),
                instruction=instruction, source=source, status="received")).inserted_primary_key[0]
            self._audit(c, "receive_upload", original, {"job": job_id, "sha256": sha, "size": len(data)})
            if problem:
                failed = self.root / "01_UPDATE_INBOX" / "06_FAILED" / inbox.name
                shutil.move(str(inbox), str(failed))
                c.execute(db.upload_jobs.update().where(db.upload_jobs.c.id == job_id).values(
                    status="failed", inbox_path=self._rel(failed), question=problem))
                self._audit(c, "reject_upload", original, {"job": job_id, "reason": problem}, ok=False)
                return {"job": job_id, "status": "failed", "reason": problem}
        return self._process(job_id, {})

    def classify(self, c, filename: str, instruction: str) -> dict:
        """Read what can be known from the file name and the owner's words. Nothing is guessed."""
        stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
        proposal: dict = {"problems": []}
        match = REVISION_IN_NAME.search(stem)
        number = stem[:match.start()] if match else stem
        revision = match.group(1) if match else ""
        said = REVISION_IN_TEXT.search(instruction or "")
        if said:
            if revision and revision_key(revision) != revision_key(said.group(1)):
                proposal["problems"].append(f"the file name says revision {revision} but your note says {said.group(1)}")
            revision = revision or said.group(1)
        if revision.isdigit():
            revision = f"{int(revision):02d}"
        code = RESPONSE_CODE.search(instruction or "")

        by_name, by_note = self._find_project(c, stem), self._find_project(c, instruction)
        if by_name and by_note and by_name.id != by_note.id:
            proposal["problems"].append(f"the file name points to {by_name.name} but your note says {by_note.name}")
        project = by_name or by_note
        area = discipline = None
        if project:
            area = self._find_area(c, project.id, stem) or self._find_area(c, project.id, instruction)
        discipline = self._find_discipline(c, stem) or self._find_discipline(c, instruction)

        category, folder = "", ""
        lowered = " " + (instruction or "").lower() + " " + stem.lower().replace("_", " ").replace("-", " ") + " "
        for name, words, types, target in CATEGORIES:
            if suffix in types or any(f" {w} " in lowered or f" {w}s " in lowered for w in words):
                category, folder = name, target
                break
        if not category and suffix in DRAWING_TYPES:
            category = "shop" if re.search(r"\bshop\b", lowered) else "design"
        proposal.update(project=project.code if project else "", area=area.name if area else "",
                        discipline=discipline.code if discipline else "", number=number, revision=revision,
                        doc_type=category, folder=folder, response_code=code.group(1).upper() if code else "")
        return proposal

    def _questions(self, p: dict) -> list[str]:
        ask = list(p.get("problems", []))
        if not p.get("project"):
            ask.append("which project it belongs to")
        if not p.get("doc_type"):
            ask.append("what kind of document it is (drawing, transmittal, datasheet, clash report, minutes ...)")
        if p.get("doc_type") in ("design", "shop"):
            if not p.get("area"):
                ask.append("which area of the project")
            if not p.get("discipline"):
                ask.append("which discipline")
            if not p.get("revision"):
                ask.append("the revision number")
        return ask

    def _process(self, job_id: int, overrides: dict) -> dict:
        with self.engine.begin() as c:
            job = c.execute(select(db.upload_jobs).where(db.upload_jobs.c.id == job_id)).first()
            if job is None:
                raise TowerError(f"There is no upload number {job_id}.")
            if job.status in ("filed", "duplicate", "failed"):
                return {"job": job_id, "status": job.status, **json.loads(job.result or "{}")}
            source = self.root / job.inbox_path
            proposal = self.classify(c, job.original_name, job.instruction)
            if overrides:
                proposal["problems"] = []                           # the owner has now answered
            proposal.update({k: v for k, v in overrides.items() if v not in (None, "")})
            if overrides.get("project"):
                named = self._find_project(c, overrides["project"])
                proposal["project"] = named.code if named else ""
                if not named:
                    proposal["problems"].append(f"there is no project called '{overrides['project']}'")
            if overrides.get("discipline"):
                named = self._find_discipline(c, overrides["discipline"])
                proposal["discipline"] = named.code if named else ""
            if proposal.get("doc_type") and not proposal.get("folder"):
                proposal["folder"] = next((t for n, _, _, t in CATEGORIES if n == proposal["doc_type"]), "")
            questions = self._questions(proposal)
            if questions:
                waiting = self.root / "01_UPDATE_INBOX" / "04_NEEDS_CONFIRMATION" / source.name
                if source != waiting and source.exists():
                    shutil.move(str(source), str(waiting))
                question = f"Upload {job_id} ({job.original_name}) is waiting. I need to know " + "; ".join(questions) + "."
                c.execute(db.upload_jobs.update().where(db.upload_jobs.c.id == job_id).values(
                    status="needs_confirmation", inbox_path=self._rel(waiting), proposal=json.dumps(proposal),
                    question=question))
                self._audit(c, "hold_upload", job.original_name, {"job": job_id, "questions": questions})
                return {"job": job_id, "status": "needs_confirmation", "question": question, "understood": proposal}
            result = self._file(c, job, source, proposal)
            c.execute(db.upload_jobs.update().where(db.upload_jobs.c.id == job_id).values(
                status=result["status"], proposal=json.dumps(proposal), result=json.dumps(result), question="",
                project_id=result["project_id"], inbox_path=result.pop("inbox_path")))
        self.export_registers(proposal["project"])
        return {"job": job_id, **result}

    def confirm_upload(self, job_id: int, project: str = "", area: str = "", discipline: str = "", revision: str = "",
                       doc_type: str = "", number: str = "") -> dict:
        """The owner's answers for an upload that was waiting; files it if everything is now known."""
        if revision.isdigit():
            revision = f"{int(revision):02d}"
        return self._process(job_id, {"project": project, "area": area, "discipline": discipline, "revision": revision,
                                      "doc_type": doc_type.lower().replace(" ", "_"), "number": number,
                                      "_new_area": area})

    def _file(self, c, job, source: Path, p: dict) -> dict:
        project = c.execute(select(db.projects).where(db.projects.c.code == p["project"])).first()
        base = self.root / "02_PROJECTS" / project.code
        is_drawing = p["doc_type"] in ("design", "shop")
        area = discipline = None
        if is_drawing:
            area = self._area(c, project.id, p.get("_new_area") or p["area"], create=bool(p.get("_new_area")))
            if area is None:
                raise TowerError(f"'{p['area']}' is not a known area of {project.name}.")
            discipline = c.execute(select(db.disciplines).where(db.disciplines.c.code == p["discipline"])).first()
            stage = "SHOP_DRAWINGS" if p["doc_type"] == "shop" else "DESIGN"
            folder = base / "01_DRAWINGS" / folder_name(area.name) / folder_name(discipline.name) / stage
            current_dir, old_dir = folder / "CURRENT", folder / "SUPERSEDED_REVISIONS"
        else:
            current_dir = old_dir = base / p["folder"]
        revision = p.get("revision") or "-"

        doc = c.execute(select(db.documents).where(and_(db.documents.c.project_id == project.id,
                                                        db.documents.c.number == p["number"]))).first()
        if doc:
            same = c.execute(select(db.revisions.c.revision).join(db.file_objects).where(and_(
                db.revisions.c.document_id == doc.id, db.file_objects.c.sha256 == job.sha256))).first()
            if same:                                               # identical content already registered
                done = self.root / "01_UPDATE_INBOX" / "05_PROCESSED" / source.name
                shutil.move(str(source), str(done))
                self._audit(c, "duplicate_upload", p["number"], {"job": job.id, "revision": same.revision})
                return {"status": "duplicate", "project_id": project.id, "inbox_path": self._rel(done),
                        "message": f"This exact file is already registered as {p['number']} revision {same.revision}. "
                                   "Nothing was changed."}
        else:
            doc_id = c.execute(db.documents.insert().values(
                project_id=project.id, area_id=area.id if area else None,
                discipline_id=discipline.id if discipline else None, number=p["number"], doc_type=p["doc_type"],
                created_at=db.now())).inserted_primary_key[0]
            doc = c.execute(select(db.documents).where(db.documents.c.id == doc_id)).first()

        newest = not doc.latest_revision or revision_key(revision) >= revision_key(doc.latest_revision) or not is_drawing
        target_dir = current_dir if newest else old_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        if is_drawing:
            old_dir.mkdir(parents=True, exist_ok=True)
        superseded = []
        if newest and is_drawing:                                  # move the previous current revision aside
            rows = c.execute(select(
                db.revisions.c.id.label("rev_id"), db.revisions.c.revision, db.file_objects.c.id.label("file_id"),
                db.file_objects.c.storage_path, db.file_objects.c.sha256).join(db.file_objects).where(
                and_(db.revisions.c.document_id == doc.id, db.revisions.c.superseded.is_(False)))).all()
            for row in rows:
                old_path = self.root / row.storage_path
                moved = self._free(old_dir / old_path.name, row.sha256)
                if old_path.exists():
                    shutil.move(str(old_path), str(moved))
                c.execute(db.file_objects.update().where(db.file_objects.c.id == row.file_id).values(
                    storage_path=self._rel(moved), normalized_name=moved.name))
                c.execute(db.revisions.update().where(db.revisions.c.id == row.rev_id).values(superseded=True))
                self._audit(c, "supersede_revision", p["number"], {"revision": row.revision, "moved_to": self._rel(moved)})
                superseded.append(row.revision)

        target = self._inside(self._free(target_dir / job.original_name, job.sha256))
        shutil.copy2(str(source), str(target))                     # copy first; the upload stays until all is recorded
        file_id = c.execute(db.file_objects.insert().values(
            sha256=job.sha256, size=job.size, original_name=job.original_name, normalized_name=target.name,
            storage_path=self._rel(target), uploaded_at=db.now(), uploaded_by=self.owner, source=job.source)).inserted_primary_key[0]
        rev_id = c.execute(db.revisions.insert().values(
            document_id=doc.id, revision=revision, file_object_id=file_id, uploaded_at=db.now(), source=job.source,
            superseded=not newest)).inserted_primary_key[0]
        changes = {}
        if newest:
            changes.update(latest_revision=revision, current_file_id=file_id)
        if p.get("response_code"):
            c.execute(db.responses.insert().values(document_id=doc.id, revision=revision, response_date=dt.date.today(),
                                                   code=p["response_code"], comments=job.instruction, created_at=db.now()))
            changes.update(latest_status=f"Code {p['response_code']}", latest_response_date=dt.date.today())
        elif newest and not doc.latest_status:
            changes["latest_status"] = "Received"
        if changes:
            c.execute(db.documents.update().where(db.documents.c.id == doc.id).values(**changes))
        self._event(c, project.id, "document", doc.id, "revision_registered",
                    {"number": p["number"], "revision": revision, "file": target.name, "superseded": superseded,
                     "note": job.instruction}, job.source)
        self._audit(c, "file_document", self._rel(target), {"job": job.id, "document": doc.id, "revision": rev_id})
        done = self.root / "01_UPDATE_INBOX" / "05_PROCESSED" / source.name
        shutil.move(str(source), str(done))                        # the original upload is preserved here
        message = f"Filed {job.original_name} as {p['number']} revision {revision} in {self._rel(target.parent)}."
        if superseded:
            message += f" Revision {', '.join(superseded)} was moved to superseded revisions."
        if not newest:
            message += f" It is older than the current revision {doc.latest_revision}, so it went straight to superseded revisions."
        return {"status": "filed", "project_id": project.id, "project": project.code, "document": p["number"],
                "revision": revision, "stored_at": self._rel(target), "superseded": superseded,
                "inbox_path": self._rel(done), "message": message}

    @staticmethod
    def _free(path: Path, sha: str) -> Path:
        """A path that does not overwrite anything: the same name can hold different content."""
        if not path.exists():
            return path
        candidate = path.with_name(f"{path.stem}__{sha[:8]}{path.suffix}")
        n = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}__{sha[:8]}_{n}{path.suffix}")
            n += 1
        return candidate

    def pending_uploads(self) -> list[dict]:
        with self.engine.begin() as c:
            rows = c.execute(select(db.upload_jobs).where(db.upload_jobs.c.status == "needs_confirmation")
                             .order_by(db.upload_jobs.c.id)).all()
        return [{"job": r.id, "file": r.original_name, "received": str(r.received_at), "question": r.question,
                 "understood": json.loads(r.proposal)} for r in rows]

    def upload_history(self, limit: int = 20) -> list[dict]:
        with self.engine.begin() as c:
            rows = c.execute(select(db.upload_jobs).order_by(db.upload_jobs.c.id.desc()).limit(limit)).all()
        return [{"job": r.id, "file": r.original_name, "received": str(r.received_at), "status": r.status,
                 "detail": json.loads(r.result or "{}").get("message") or r.question} for r in rows]

    # ------------------------------------------------------- document records

    def _document(self, c, project: str, reference: str):
        proj = self._find_project(c, project)
        if proj is None:
            raise TowerError(f"I could not tell which project '{project}' means. Projects: "
                             f"{[p.code for p in self._projects(c)]}")
        docs = c.execute(select(db.documents).where(db.documents.c.project_id == proj.id)).all()
        exact = [d for d in docs if d.number.lower() == reference.lower().strip()]
        partial = [d for d in docs if all(w in (d.number + " " + d.title).lower() for w in reference.lower().split())]
        found = exact or partial
        if len(found) != 1:
            raise TowerError(("No document" if not found else "More than one document") +
                             f" in {proj.name} matches '{reference}'. "
                             f"Known: {[d.number for d in (found or docs)][:15]}")
        return proj, found[0]

    def record_submission(self, project: str, document: str, submission_date="today", transmittal: str = "",
                          reviewer: str = "", revision: str = "", comments: str = "", source: str = "chat") -> dict:
        with self.engine.begin() as c:
            proj, doc = self._document(c, project, document)
            when = self._date(submission_date)
            sid = c.execute(db.submissions.insert().values(
                document_id=doc.id, revision=revision or doc.latest_revision, transmittal=transmittal,
                submission_date=when, reviewer=reviewer, comments=comments, created_at=db.now())).inserted_primary_key[0]
            c.execute(db.documents.update().where(db.documents.c.id == doc.id).values(
                latest_status="Submitted", latest_submission_date=when))
            self._event(c, proj.id, "document", doc.id, "submitted", {"number": doc.number, "transmittal": transmittal}, source)
            self._audit(c, "record_submission", doc.number, {"submission": sid})
        self.export_registers(proj.code)
        return {"submission_id": sid, "document": doc.number, "project": proj.code, "status": "Submitted"}

    def record_response(self, project: str, document: str, code: str, comments: str = "", response_date="today",
                        revision: str = "", source: str = "chat") -> dict:
        with self.engine.begin() as c:
            proj, doc = self._document(c, project, document)
            when = self._date(response_date)
            last = c.execute(select(db.submissions.c.id).where(db.submissions.c.document_id == doc.id)
                             .order_by(db.submissions.c.id.desc())).first()
            rid = c.execute(db.responses.insert().values(
                document_id=doc.id, submission_id=last.id if last else None, revision=revision or doc.latest_revision,
                response_date=when, code=code.upper(), comments=comments, created_at=db.now())).inserted_primary_key[0]
            c.execute(db.documents.update().where(db.documents.c.id == doc.id).values(
                latest_status=f"Code {code.upper()}", latest_response_date=when))
            self._event(c, proj.id, "document", doc.id, "response", {"number": doc.number, "code": code.upper(),
                                                                     "comments": comments}, source)
            self._audit(c, "record_response", doc.number, {"response": rid, "code": code.upper()})
        self.export_registers(proj.code)
        return {"response_id": rid, "document": doc.number, "project": proj.code, "status": f"Code {code.upper()}"}

    # ---------------------------------------------------------------- actions

    def add_action(self, project: str, description: str, discipline: str = "", area: str = "", assigned_to: str = "",
                   target_date: str = "", priority: str = "normal", related_drawing: str = "", source: str = "chat") -> dict:
        with self.engine.begin() as c:
            proj = self._find_project(c, project)
            if proj is None:
                raise TowerError(f"I could not tell which project '{project}' means.")
            disc = self._find_discipline(c, discipline)
            ar = self._find_area(c, proj.id, area)
            doc_id = None
            if related_drawing:
                doc_id = self._document(c, proj.code, related_drawing)[1].id
            aid = c.execute(db.actions.insert().values(
                project_id=proj.id, area_id=ar.id if ar else None, discipline_id=disc.id if disc else None,
                document_id=doc_id, description=description, assigned_to=assigned_to, date_raised=dt.date.today(),
                target_date=self._date(target_date) if target_date else None, priority=priority,
                updated_at=db.now())).inserted_primary_key[0]
            self._event(c, proj.id, "action", aid, "raised", {"description": description}, source)
            self._audit(c, "add_action", f"{proj.code}/{aid}", {"description": description})
        self.export_registers(proj.code)
        return {"action_id": aid, "project": proj.code, "status": "open"}

    def update_action(self, action_id: int, update: str = "", status: str = "", closure_evidence: str = "",
                      target_date: str = "", source: str = "chat") -> dict:
        with self.engine.begin() as c:
            row = c.execute(select(db.actions).where(db.actions.c.id == action_id)).first()
            if row is None:
                raise TowerError(f"There is no action number {action_id}.")
            if status == "closed" and not (closure_evidence or row.closure_evidence):
                raise TowerError("To close an action I need the closure evidence (what resolved it).")
            values = {"updated_at": db.now()}
            if update:
                values["latest_update"] = update
            if status:
                values["status"] = status
            if closure_evidence:
                values["closure_evidence"] = closure_evidence
            if target_date:
                values["target_date"] = self._date(target_date)
            c.execute(db.actions.update().where(db.actions.c.id == action_id).values(**values))
            self._event(c, row.project_id, "action", action_id, status or "updated", {"update": update}, source)
            self._audit(c, "update_action", str(action_id), values)
            code = c.execute(select(db.projects.c.code).where(db.projects.c.id == row.project_id)).scalar()
        self.export_registers(code)
        return {"action_id": action_id, "status": status or row.status}

    # ------------------------------------------------------------- timesheets

    def record_timesheet(self, project: str, hours: float, activity: str, date="today", person: str = "",
                         discipline: str = "", reference: str = "", description: str = "", source: str = "chat",
                         allow_duplicate: bool = False) -> dict:
        if not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 0 < hours <= 24:
            raise TowerError("Hours must be a number greater than 0 and no more than 24.")
        with self.engine.begin() as c:
            proj = self._find_project(c, project)
            if proj is None:
                raise TowerError(f"I could not tell which project '{project}' means. Projects: "
                                 f"{[p.code for p in self._projects(c)]}")
            who = c.execute(select(db.personnel).where(func.lower(db.personnel.c.name) == (person or self.owner).lower())).first()
            if who is None:
                known = [r.name for r in c.execute(select(db.personnel)).all()]
                raise TowerError(f"I do not know a person called '{person or self.owner}'. Known people: {known}. "
                                 "Tell me to add this person first if they are new.")
            when = self._date(date)
            disc = self._find_discipline(c, discipline or activity)
            twin = c.execute(select(db.timesheets.c.id).where(and_(
                db.timesheets.c.date == when, db.timesheets.c.project_id == proj.id, db.timesheets.c.person_id == who.id,
                func.lower(db.timesheets.c.activity) == activity.lower(), db.timesheets.c.hours == float(hours),
                db.timesheets.c.review_status != "void"))).first()
            if twin and not allow_duplicate:
                return {"recorded": False, "duplicate_of": twin.id,
                        "question": f"Entry {twin.id} already records {hours:g} hours for this on {when}. "
                                    "Is this a second, separate entry?"}
            tid = c.execute(db.timesheets.insert().values(
                date=when, project_id=proj.id, person_id=who.id, discipline_id=disc.id if disc else None,
                activity=activity, reference=reference, hours=float(hours), description=description, source=source,
                created_at=db.now())).inserted_primary_key[0]
            self._event(c, proj.id, "timesheet", tid, "recorded", {"hours": hours, "activity": activity,
                                                                 "date": str(when), "person": who.name}, source)
            self._audit(c, "record_timesheet", f"{proj.code}/{tid}", {"hours": hours, "date": str(when)})
            total = c.execute(select(func.sum(db.timesheets.c.hours)).where(and_(
                db.timesheets.c.date == when, db.timesheets.c.person_id == who.id,
                db.timesheets.c.review_status != "void"))).scalar()
        self.export_registers(proj.code)
        return {"recorded": True, "entry_id": tid, "project": proj.code, "date": str(when), "hours": float(hours),
                "person": who.name, "day_total_hours": total}

    def amend_timesheet(self, entry_id: int, hours: float | None = None, activity: str = "", date: str = "",
                        void: bool = False, reason: str = "", source: str = "chat") -> dict:
        with self.engine.begin() as c:
            row = c.execute(select(db.timesheets).where(db.timesheets.c.id == entry_id)).first()
            if row is None:
                raise TowerError(f"There is no timesheet entry {entry_id}.")
            values: dict = {}
            if hours is not None:
                if not 0 < hours <= 24:
                    raise TowerError("Hours must be greater than 0 and no more than 24.")
                values["hours"] = float(hours)
            if activity:
                values["activity"] = activity
            if date:
                values["date"] = self._date(date)
            if void:
                values["review_status"] = "void"
            if not values:
                raise TowerError("Tell me what to change: the hours, the activity, the date, or to cancel the entry.")
            before = {k: getattr(row, k) for k in values}
            c.execute(db.timesheets.update().where(db.timesheets.c.id == entry_id).values(**values))
            self._event(c, row.project_id, "timesheet", entry_id, "amended",
                        {"before": before, "after": values, "reason": reason}, source)
            self._audit(c, "amend_timesheet", str(entry_id), {"before": before, "after": values, "reason": reason})
            code = c.execute(select(db.projects.c.code).where(db.projects.c.id == row.project_id)).scalar()
        self.export_registers(code)
        return {"entry_id": entry_id, "changed": {k: str(v) for k, v in values.items()}}

    def add_person(self, name: str) -> dict:
        with self.engine.begin() as c:
            if c.execute(select(db.personnel).where(func.lower(db.personnel.c.name) == name.lower().strip())).first():
                return {"added": False, "message": f"{name} is already known."}
            c.execute(db.personnel.insert().values(name=name.strip(), is_owner=False))
            self._audit(c, "add_person", name)
        return {"added": True, "name": name.strip()}

    # ---------------------------------------------------------------- reading

    def overview(self) -> list[dict]:
        today = dt.date.today()
        out = []
        with self.engine.begin() as c:
            for p in self._projects(c):
                docs = c.execute(select(db.documents.c.latest_status, db.documents.c.doc_type)
                                 .where(db.documents.c.project_id == p.id)).all()
                acts = c.execute(select(db.actions.c.status, db.actions.c.target_date)
                                 .where(db.actions.c.project_id == p.id)).all()
                hours = c.execute(select(func.sum(db.timesheets.c.hours)).where(and_(
                    db.timesheets.c.project_id == p.id, db.timesheets.c.review_status != "void",
                    db.timesheets.c.date >= today - dt.timedelta(days=30)))).scalar() or 0
                status: dict[str, int] = {}
                for d in docs:
                    if d.doc_type in ("design", "shop"):
                        status[d.latest_status or "Received"] = status.get(d.latest_status or "Received", 0) + 1
                open_actions = [a for a in acts if a.status != "closed"]
                out.append({"code": p.code, "name": p.name,
                            "drawings": sum(d.doc_type in ("design", "shop") for d in docs), "documents": len(docs),
                            "drawing_status": status, "open_actions": len(open_actions),
                            "overdue_actions": sum(1 for a in open_actions if a.target_date and a.target_date < today),
                            "hours_30_days": round(hours, 1)})
        return out

    def project_detail(self, project: str) -> dict:
        with self.engine.begin() as c:
            proj = self._find_project(c, project)
            if proj is None:
                raise TowerError(f"I could not tell which project '{project}' means.")
            area = {a.id: a.name for a in c.execute(select(db.areas).where(db.areas.c.project_id == proj.id)).all()}
            disc = {d.id: d.name for d in c.execute(select(db.disciplines)).all()}
            people = {r.id: r.name for r in c.execute(select(db.personnel)).all()}
            docs = c.execute(select(db.documents).where(db.documents.c.project_id == proj.id).order_by(db.documents.c.number)).all()
            ids = [d.id for d in docs]
            files = {f.id: f for f in c.execute(select(db.file_objects)).all()}
            revs = c.execute(select(db.revisions).where(db.revisions.c.document_id.in_(ids)).order_by(db.revisions.c.id)).all() if ids else []
            subs = c.execute(select(db.submissions).where(db.submissions.c.document_id.in_(ids)).order_by(db.submissions.c.id)).all() if ids else []
            resp = c.execute(select(db.responses).where(db.responses.c.document_id.in_(ids)).order_by(db.responses.c.id)).all() if ids else []
            acts = c.execute(select(db.actions).where(db.actions.c.project_id == proj.id).order_by(db.actions.c.id)).all()
            times = c.execute(select(db.timesheets).where(and_(db.timesheets.c.project_id == proj.id,
                                                               db.timesheets.c.review_status != "void"))
                              .order_by(db.timesheets.c.date.desc(), db.timesheets.c.id.desc())).all()
            history = c.execute(select(db.activity).where(db.activity.c.project_id == proj.id)
                                .order_by(db.activity.c.id.desc()).limit(40)).all()
        number = {d.id: d.number for d in docs}
        return {
            "project": {"id": proj.id, "code": proj.code, "name": proj.name, "areas": sorted(area.values())},
            "drawing_register": [{
                "document_id": d.id, "area": area.get(d.area_id, ""), "discipline": disc.get(d.discipline_id, ""),
                "number": d.number, "title": d.title, "type": d.doc_type, "revision": d.latest_revision,
                "status": d.latest_status, "submission_date": str(d.latest_submission_date or ""),
                "response_date": str(d.latest_response_date or ""), "responsible": d.responsible,
                "current_file": files[d.current_file_id].storage_path if d.current_file_id else "",
                "view": self.link(d.current_file_id) if d.current_file_id else ""} for d in docs],
            "revision_history": [{
                "document": number[r.document_id], "revision_id": r.id, "revision": r.revision,
                "file": files[r.file_object_id].original_name, "uploaded": str(r.uploaded_at), "source": r.source,
                "hash": files[r.file_object_id].sha256, "superseded": r.superseded,
                "path": files[r.file_object_id].storage_path, "view": self.link(r.file_object_id)} for r in revs],
            "submission_history": [{
                "submission_id": s.id, "document": number[s.document_id], "revision": s.revision,
                "transmittal": s.transmittal, "date": str(s.submission_date), "reviewer": s.reviewer,
                "comments": s.comments} for s in subs],
            "responses": [{"response_id": r.id, "document": number[r.document_id], "revision": r.revision,
                           "date": str(r.response_date), "code": r.code, "comments": r.comments} for r in resp],
            "actions": [{
                "action_id": a.id, "area": area.get(a.area_id, ""), "discipline": disc.get(a.discipline_id, ""),
                "description": a.description, "assigned_to": a.assigned_to, "raised": str(a.date_raised),
                "target": str(a.target_date or ""), "status": a.status, "priority": a.priority,
                "latest_update": a.latest_update, "related_drawing": number.get(a.document_id, ""),
                "closure_evidence": a.closure_evidence} for a in acts],
            "timesheets": [{
                "entry_id": t.id, "date": str(t.date), "person": people.get(t.person_id, ""),
                "discipline": disc.get(t.discipline_id, ""), "activity": t.activity, "reference": t.reference,
                "hours": t.hours, "description": t.description, "source": t.source, "review_status": t.review_status}
                for t in times],
            "history": [{"at": str(h.at), "what": h.entity, "event": h.event, "detail": json.loads(h.detail),
                         "source": h.source} for h in history],
        }

    def audit_trail(self, limit: int = 50) -> list[dict]:
        with self.engine.begin() as c:
            rows = c.execute(select(db.audit).order_by(db.audit.c.id.desc()).limit(limit)).all()
        return [{"at": str(r.at), "actor": r.actor, "operation": r.operation, "target": r.target, "ok": r.ok,
                 "detail": json.loads(r.detail)} for r in rows]

    # -------------------------------------------------------- secure file links

    def link(self, file_id: int, minutes: int = 60) -> str:
        """A time-limited token for viewing one file; no Windows path is ever given to the browser."""
        expires = int((dt.datetime.now() + dt.timedelta(minutes=minutes)).timestamp())
        sig = hmac.new(self._key, f"{file_id}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]
        return f"{file_id}.{expires}.{sig}"

    def open_link(self, token: str) -> tuple[Path, str]:
        try:
            file_id, expires, sig = token.split(".")
            good = hmac.new(self._key, f"{file_id}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(sig, good) or int(expires) < dt.datetime.now().timestamp():
                raise ValueError
        except ValueError:
            raise TowerError("This file link is not valid or has expired.") from None
        with self.engine.begin() as c:
            row = c.execute(select(db.file_objects).where(db.file_objects.c.id == int(file_id))).first()
        if row is None:
            raise TowerError("That file is not registered.")
        path = self._inside(self.root / row.storage_path)
        if not path.is_file():
            raise TowerError("The file is registered but missing from the folder.")
        return path, row.original_name

    # ---------------------------------------------------------------- exports

    def export_registers(self, project: str) -> str:
        """Write the project's registers to one Excel workbook, generated from the database."""
        from openpyxl import Workbook
        from openpyxl.styles import Font

        data = self.project_detail(project)
        code = data["project"]["code"]
        sheets = [("Drawing Register", data["drawing_register"], ["view"]), ("Revision History", data["revision_history"], ["view"]),
                  ("Submission History", data["submission_history"], []), ("Consultant Responses", data["responses"], []),
                  ("Coordination Actions", data["actions"], []), ("Timesheets", data["timesheets"], [])]
        book = Workbook()
        book.remove(book.active)
        for title, rows, hide in sheets:
            sheet = book.create_sheet(title)
            columns = [k for k in (rows[0] if rows else {}) if k not in hide]
            sheet.append([k.replace("_", " ").title() for k in columns] or ["No records yet"])
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for row in rows:
                sheet.append([row[k] for k in columns])
        target = self.root / "02_PROJECTS" / code / "09_LOGS_AND_REGISTERS" / f"{code}_REGISTERS.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp.xlsx")
        try:
            book.save(temp)
            temp.replace(target)                                   # never leaves a half-written workbook
        except OSError:
            temp.unlink(missing_ok=True)                           # the workbook is open in Excel: skip this refresh
        return self._rel(target)

    def export_timesheet(self, period: str = "week", date="today", project: str = "") -> str:
        from openpyxl import Workbook

        day = self._date(date)
        if period == "day":
            start, end = day, day
        elif period == "month":
            start = day.replace(day=1)
            end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        else:
            start = day - dt.timedelta(days=day.weekday())
            end = start + dt.timedelta(days=6)
        with self.engine.begin() as c:
            query = select(db.timesheets, db.projects.c.code, db.personnel.c.name).join(db.projects).join(db.personnel).where(and_(
                db.timesheets.c.date >= start, db.timesheets.c.date <= end, db.timesheets.c.review_status != "void"))
            if project:
                proj = self._find_project(c, project)
                if proj is None:
                    raise TowerError(f"I could not tell which project '{project}' means.")
                query = query.where(db.timesheets.c.project_id == proj.id)
            rows = c.execute(query.order_by(db.timesheets.c.date, db.timesheets.c.id)).all()
        book = Workbook()
        sheet = book.active
        sheet.title = "Timesheet"
        sheet.append(["Date", "Project", "Person", "Activity", "Reference", "Hours", "Description", "Source", "Entry"])
        for r in rows:
            sheet.append([str(r.date), r.code, r.name, r.activity, r.reference, r.hours, r.description, r.source, r.id])
        sheet.append([])
        sheet.append(["", "", "", "", "Total", sum(r.hours for r in rows)])
        target = self.root / "00_DASHBOARD" / "EXPORTS" / f"TIMESHEET_{period.upper()}_{start}_{end}.xlsx"
        book.save(target)
        return self._rel(target)
