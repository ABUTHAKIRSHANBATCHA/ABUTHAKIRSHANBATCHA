"""Project Control Tower database: the permanent record of projects, documents,
revisions, submissions, actions, timesheets and every operation performed.

Runs on PostgreSQL in production and on SQLite for a zero-install start; the
schema is the same. Record ids are never reused or changed.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine)

metadata = MetaData()


def _id() -> Column:
    return Column("id", Integer, primary_key=True, autoincrement=True)


def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


projects = Table(
    "projects", metadata, _id(),
    Column("code", String(40), nullable=False, unique=True),       # permanent identifier, also the folder name
    Column("name", String(200), nullable=False),
    Column("aliases", Text, nullable=False, default="[]"),         # JSON list of short codes / other names
    Column("created_at", DateTime, nullable=False, default=now))

areas = Table(
    "areas", metadata, _id(),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("code", String(40), nullable=False),
    Column("name", String(200), nullable=False),
    UniqueConstraint("project_id", "code"))

disciplines = Table(
    "disciplines", metadata, _id(),
    Column("code", String(20), nullable=False, unique=True),
    Column("name", String(100), nullable=False),
    Column("aliases", Text, nullable=False, default="[]"))

personnel = Table(
    "personnel", metadata, _id(),
    Column("name", String(120), nullable=False, unique=True),
    Column("is_owner", Boolean, nullable=False, default=False))

file_objects = Table(
    "file_objects", metadata, _id(),
    Column("sha256", String(64), nullable=False, index=True),
    Column("size", Integer, nullable=False),
    Column("original_name", String(300), nullable=False),          # exactly as submitted
    Column("normalized_name", String(300), nullable=False),        # the name used on disk
    Column("storage_path", Text, nullable=False),                  # relative to the tower root
    Column("uploaded_at", DateTime, nullable=False, default=now),
    Column("uploaded_by", String(120), nullable=False, default=""),
    Column("source", String(60), nullable=False, default=""))

documents = Table(
    "documents", metadata, _id(),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("area_id", ForeignKey("areas.id")),
    Column("discipline_id", ForeignKey("disciplines.id")),
    Column("number", String(200), nullable=False),
    Column("title", String(300), nullable=False, default=""),
    Column("doc_type", String(40), nullable=False),                # design, shop, transmittal, datasheet, ...
    Column("latest_revision", String(20), nullable=False, default=""),
    Column("latest_status", String(60), nullable=False, default=""),
    Column("latest_submission_date", Date),
    Column("latest_response_date", Date),
    Column("responsible", String(120), nullable=False, default=""),
    Column("current_file_id", ForeignKey("file_objects.id")),
    Column("created_at", DateTime, nullable=False, default=now),
    UniqueConstraint("project_id", "number"))

revisions = Table(
    "revisions", metadata, _id(),
    Column("document_id", ForeignKey("documents.id"), nullable=False),
    Column("revision", String(20), nullable=False),
    Column("file_object_id", ForeignKey("file_objects.id"), nullable=False),
    Column("uploaded_at", DateTime, nullable=False, default=now),
    Column("source", String(60), nullable=False, default=""),
    Column("superseded", Boolean, nullable=False, default=False))

submissions = Table(
    "submissions", metadata, _id(),
    Column("document_id", ForeignKey("documents.id"), nullable=False),
    Column("revision", String(20), nullable=False, default=""),
    Column("transmittal", String(120), nullable=False, default=""),
    Column("submission_date", Date, nullable=False),
    Column("reviewer", String(120), nullable=False, default=""),
    Column("comments", Text, nullable=False, default=""),
    Column("reference", String(300), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=now))

responses = Table(
    "consultant_responses", metadata, _id(),
    Column("document_id", ForeignKey("documents.id"), nullable=False),
    Column("submission_id", ForeignKey("submissions.id")),
    Column("revision", String(20), nullable=False, default=""),
    Column("response_date", Date, nullable=False),
    Column("code", String(20), nullable=False),
    Column("comments", Text, nullable=False, default=""),
    Column("reference", String(300), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=now))

actions = Table(
    "coordination_actions", metadata, _id(),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("area_id", ForeignKey("areas.id")),
    Column("discipline_id", ForeignKey("disciplines.id")),
    Column("document_id", ForeignKey("documents.id")),
    Column("description", Text, nullable=False),
    Column("assigned_to", String(120), nullable=False, default=""),
    Column("date_raised", Date, nullable=False),
    Column("target_date", Date),
    Column("status", String(20), nullable=False, default="open"),
    Column("priority", String(20), nullable=False, default="normal"),
    Column("latest_update", Text, nullable=False, default=""),
    Column("closure_evidence", Text, nullable=False, default=""),
    Column("updated_at", DateTime, nullable=False, default=now))

timesheets = Table(
    "timesheets", metadata, _id(),
    Column("date", Date, nullable=False),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("person_id", ForeignKey("personnel.id"), nullable=False),
    Column("discipline_id", ForeignKey("disciplines.id")),
    Column("activity", String(300), nullable=False),
    Column("reference", String(200), nullable=False, default=""),
    Column("hours", Float, nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("source", String(60), nullable=False, default=""),
    Column("review_status", String(20), nullable=False, default="recorded"),
    Column("created_at", DateTime, nullable=False, default=now))

datasheets = Table(
    "datasheets", metadata, _id(),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("tag", String(120), nullable=False),
    Column("name", String(200), nullable=False, default=""),
    Column("manufacturer", String(200), nullable=False, default=""),
    Column("model", String(200), nullable=False, default=""),
    Column("submission_ref", String(200), nullable=False, default=""),
    Column("revision", String(20), nullable=False, default=""),
    Column("review_status", String(60), nullable=False, default=""),
    Column("date", Date),
    Column("file_object_id", ForeignKey("file_objects.id")))

upload_jobs = Table(
    "upload_jobs", metadata, _id(),
    Column("received_at", DateTime, nullable=False, default=now),
    Column("original_name", String(300), nullable=False),
    Column("inbox_path", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("instruction", Text, nullable=False, default=""),
    Column("source", String(60), nullable=False, default=""),
    Column("status", String(30), nullable=False),      # received, needs_confirmation, filed, duplicate, failed
    Column("proposal", Text, nullable=False, default="{}"),
    Column("question", Text, nullable=False, default=""),
    Column("result", Text, nullable=False, default="{}"),
    Column("project_id", ForeignKey("projects.id")))

activity = Table(
    "activity_history", metadata, _id(),
    Column("at", DateTime, nullable=False, default=now),
    Column("project_id", ForeignKey("projects.id")),
    Column("entity", String(40), nullable=False),
    Column("entity_id", Integer),
    Column("event", String(60), nullable=False),
    Column("detail", Text, nullable=False, default="{}"),
    Column("source", String(60), nullable=False, default=""))

workflow_executions = Table(
    "ai_workflow_executions", metadata, _id(),
    Column("at", DateTime, nullable=False, default=now),
    Column("workflow", String(80), nullable=False),
    Column("status", String(30), nullable=False),
    Column("detail", Text, nullable=False, default="{}"))

audit = Table(
    "audit_logs", metadata, _id(),
    Column("at", DateTime, nullable=False, default=now),
    Column("actor", String(120), nullable=False, default=""),
    Column("operation", String(80), nullable=False),
    Column("target", Text, nullable=False, default=""),
    Column("detail", Text, nullable=False, default="{}"),
    Column("ok", Boolean, nullable=False, default=True))

STANDARD_DISCIPLINES = [
    ("MEC", "Mechanical", ["mech", "mechanical", "hvac", "plumbing"]),
    ("ELE", "Electrical", ["elec", "electrical", "power", "lighting", "earthing"]),
    ("CIV", "Civil", ["civil", "roads", "infrastructure"]),
    ("STR", "Structural", ["structural", "structure"]),
    ("ARC", "Architectural", ["arch", "architectural", "architecture"]),
    ("FLS", "FLS", ["fls", "fire", "life safety", "fire fighting", "fire alarm"]),
    ("LC", "Low Current", ["low current", "elv", "cctv", "access control", "network", "data"]),
    ("LPG", "LPG", ["lpg", "gas"]),
    ("INS", "Instrumentation", ["instrumentation", "instrument", "controls"]),
    ("LAN", "Landscape", ["landscape", "irrigation"]),
    ("GIS", "GIS", ["gis"]),
]


def connect(url: str):
    engine = create_engine(url, future=True)
    metadata.create_all(engine)
    return engine
