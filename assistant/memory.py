"""Long-term memory kept on this PC: every conversation turn (transcript) and
the notes the assistant chooses to keep (profile, preferences, ideas, ...)."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

CATEGORIES = ["profile", "preference", "project", "idea", "business", "other"]
ALWAYS_IN_PROMPT = {"profile", "preference"}


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


class Memory:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.notes_file = folder / "notes.json"
        self.transcript_file = folder / "transcript.jsonl"
        self.notes: list[dict] = []
        if self.notes_file.exists():
            self.notes = json.loads(self.notes_file.read_text(encoding="utf-8"))

    # ------------------------------------------------------------- transcript

    def log(self, role: str, text: str) -> None:
        if not text.strip():
            return
        with self.transcript_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _now(), "role": role, "text": text}, ensure_ascii=False) + "\n")

    def transcript(self) -> list[dict]:
        if not self.transcript_file.exists():
            return []
        out = []
        for line in self.transcript_file.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # --------------------------------------------------------------- tracking

    def log_tool(self, name: str) -> None:
        with (self.folder / "activity.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _now(), "tool": name}) + "\n")

    def tracking(self, days: int = 14, today: dt.date | None = None) -> dict:
        """Usage figures for the dashboard: requests per day, most used actions, saved ideas."""
        today = today or dt.date.today()
        window = [(today - dt.timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]
        per_day = dict.fromkeys(window, 0)
        for t in self.transcript():
            if t["role"] == "user" and t["at"][:10] in per_day:
                per_day[t["at"][:10]] += 1
        tools: dict[str, int] = {}
        path = self.folder / "activity.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry["at"][:10] in per_day:
                    tools[entry["tool"]] = tools.get(entry["tool"], 0) + 1
        ideas = [n for n in self.notes if n["category"] in ("idea", "business")]
        ideas.sort(key=lambda n: n["updated"], reverse=True)
        return {
            "requests": [{"day": d, "count": c} for d, c in per_day.items()],
            "tools": [{"name": k, "count": v} for k, v in sorted(tools.items(), key=lambda kv: -kv[1])[:5]],
            "ideas": [{"title": n["title"], "updated": n["updated"][:10]} for n in ideas[:5]],
            "idea_count": len(ideas),
            "note_count": len(self.notes),
        }

    # ------------------------------------------------------------------ notes

    def _save(self) -> None:
        self.notes_file.write_text(json.dumps(self.notes, ensure_ascii=False, indent=2), encoding="utf-8")

    def remember(self, category: str, title: str, content: str) -> str:
        for note in self.notes:
            if note["title"].lower() == title.lower():
                note.update(category=category, content=content, updated=_now())
                self._save()
                return f"Updated the note '{note['title']}'."
        self.notes.append({"category": category, "title": title, "content": content, "updated": _now()})
        self._save()
        return f"Saved a new {category} note '{title}'."

    def forget(self, title: str) -> str:
        keep = [n for n in self.notes if n["title"].lower() != title.lower()]
        if len(keep) == len(self.notes):
            return f"No note is titled '{title}'. Titles: {[n['title'] for n in self.notes]}"
        self.notes = keep
        self._save()
        return f"Deleted the note '{title}'."

    def search(self, query: str, limit: int = 8) -> str:
        """Keyword search over notes and the full conversation history."""
        wanted = _words(query)
        if not wanted:
            return "Give me at least one word to search for."
        notes = []
        for n in self.notes:
            score = len(wanted & _words(n["title"] + " " + n["content"] + " " + n["category"]))
            if score:
                notes.append((score, n))
        notes.sort(key=lambda x: -x[0])
        turns = self.transcript()
        hits = []
        for i, t in enumerate(turns):
            score = len(wanted & _words(t["text"]))
            if score:
                hits.append((score, i))
        hits.sort(key=lambda x: (-x[0], -x[1]))
        passages, used = [], set()
        for _, i in hits[:limit]:
            if i in used:
                continue
            window = range(max(0, i - 1), min(len(turns), i + 2))   # the turn with its neighbours
            used.update(window)
            passages.append([{**turns[j], "text": turns[j]["text"][:700]} for j in window])
        return json.dumps({"notes": [n for _, n in notes[:limit]], "conversation_passages": passages},
                          ensure_ascii=False)

    # ----------------------------------------------------------------- prompt

    def prompt_section(self, recent_turns: int = 30) -> str:
        """Snapshot placed in the system prompt when a conversation starts."""
        parts = []
        full = [n for n in self.notes if n["category"] in ALWAYS_IN_PROMPT]
        index = [n for n in self.notes if n["category"] not in ALWAYS_IN_PROMPT]
        if full:
            parts.append("What you know about the owner:\n" + "\n".join(
                f"- {n['title']}: {n['content']}" for n in full))
        if index:
            parts.append("Other notes you have saved (use search_memory for the full text):\n" + "\n".join(
                f"- [{n['category']}] {n['title']} (updated {n['updated']}): {n['content'][:140]}" for n in index))
        recent = self.transcript()[-recent_turns:]
        if recent:
            parts.append("The most recent conversation before this one:\n" + "\n".join(
                f"[{t['at']}] {'Owner' if t['role'] == 'user' else 'You'}: {t['text'][:400]}" for t in recent))
        return "\n\n".join(parts)
