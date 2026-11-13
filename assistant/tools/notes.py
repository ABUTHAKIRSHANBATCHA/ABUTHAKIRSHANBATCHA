"""Memory tools: keep notes for good, and search everything said before."""
from __future__ import annotations

from ..memory import CATEGORIES, Memory
from . import Registry, Tool


def register(reg: Registry, memory: Memory) -> None:
    reg.add(Tool(
        "remember",
        "Save a lasting note, or replace the note with the same title. Use it without being asked "
        "whenever the owner shares something worth keeping: facts about them and their work "
        "(profile), how they like things done (preference), project facts and decisions (project), "
        "and every new idea or business plan (idea, business). For ideas, keep the whole idea in one "
        "note and rewrite it in full as it develops, so the note is always the current version.",
        {"category": {"type": "string", "enum": CATEGORIES},
         "title": {"type": "string", "description": "Short and specific; reused to update the note"},
         "content": {"type": "string"}},
        ["category", "title", "content"], memory.remember))
    reg.add(Tool(
        "search_memory",
        "Search your saved notes and the complete history of past conversations by keywords. Use it "
        "before saying you do not know or remember something, and to fetch the full text of a note.",
        {"query": {"type": "string", "description": "Keywords, in the language they were spoken"}},
        ["query"], memory.search))
    reg.add(Tool(
        "forget", "Delete a saved note by its title, when the owner asks you to forget it.",
        {"title": {"type": "string"}}, ["title"], memory.forget))
