"""Watches the folders and log files listed in config.json and records what
changed, so the dashboards (Lumi's and your own) stay current without asking.

config.json:  "watch": [{"name": "Marina models", "path": "D:/Projects/Marina", "kind": "folder"},
                         {"name": "Clash log", "path": "D:/Projects/Marina/clash.log", "kind": "log"}]
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

MAX_FILES = 20_000
MAX_EVENTS_PER_SCAN = 40


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


class Watcher:
    def __init__(self, sources: list[dict], folder: Path) -> None:
        self.sources = [s for s in sources if s.get("name") and s.get("path")]
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.state_file, self.feed_file = folder / "state.json", folder / "feed.jsonl"
        self.state: dict = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}

    # -------------------------------------------------------------- scanning

    def _folder(self, source: dict) -> list[dict]:
        root = Path(os.path.expandvars(source["path"])).expanduser()
        if not root.is_dir():
            # Not there yet: start from empty so its first files count as new. If it was seen
            # before, it is probably an unplugged drive, so keep what we knew and report nothing.
            self.state.setdefault(source["name"], {"files": {}, "checked": _now()})
            return []
        seen: dict[str, list] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$", "~"))]
            for fn in filenames:
                if fn.startswith(("~$", ".")) or fn.endswith((".tmp", ".bak")) or len(seen) >= MAX_FILES:
                    continue
                full = Path(dirpath) / fn
                try:
                    st = full.stat()
                except OSError:
                    continue
                seen[str(full.relative_to(root)).replace("\\", "/")] = [int(st.st_mtime), st.st_size]
        old = self.state.get(source["name"])
        self.state[source["name"]] = {"files": seen, "checked": _now()}
        if old is None:                                   # first look: remember, report nothing
            return []
        before = old["files"]
        events = [{"kind": "added", "detail": f} for f in seen if f not in before]
        events += [{"kind": "changed", "detail": f} for f in seen if f in before and before[f] != seen[f]]
        events += [{"kind": "removed", "detail": f} for f in before if f not in seen]
        if len(events) > MAX_EVENTS_PER_SCAN:
            counts = {k: sum(e["kind"] == k for e in events) for k in ("added", "changed", "removed")}
            events = [{"kind": "changed", "detail": ", ".join(f"{n} files {k}" for k, n in counts.items() if n)}]
        return events

    def _log(self, source: dict) -> list[dict]:
        path = Path(os.path.expandvars(source["path"])).expanduser()
        if not path.is_file():
            return []
        size = path.stat().st_size
        old = self.state.get(source["name"])
        offset = size if old is None else old.get("offset", 0)   # first look: start from the end
        if size < offset:                                        # the log was cleared or rotated
            offset = 0
        with path.open("rb") as f:
            f.seek(offset)
            new = f.read(200_000)
        complete = new[:new.rfind(b"\n") + 1]                    # leave a half-written last line for next time
        self.state[source["name"]] = {"offset": offset + len(complete), "checked": _now()}
        lines = [l.strip() for l in complete.decode("utf-8", errors="replace").splitlines() if l.strip()]
        events = [{"kind": "log", "detail": line[:300]} for line in lines[-MAX_EVENTS_PER_SCAN:]]
        if len(lines) > MAX_EVENTS_PER_SCAN:
            events.insert(0, {"kind": "log", "detail": f"{len(lines) - MAX_EVENTS_PER_SCAN} earlier new lines not shown"})
        return events

    def scan(self) -> list[dict]:
        """Look at every source once; returns and records the new events."""
        events = []
        for source in self.sources:
            found = self._log(source) if source.get("kind") == "log" else self._folder(source)
            events += [{"at": _now(), "source": source["name"], **e} for e in found]
        self.state_file.write_text(json.dumps(self.state), encoding="utf-8")
        if events:
            with self.feed_file.open("a", encoding="utf-8") as f:
                f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
        return events

    # --------------------------------------------------------------- reading

    def recent(self, limit: int = 20, source: str = "") -> list[dict]:
        if not self.feed_file.exists():
            return []
        events = []
        for line in self.feed_file.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not source or source.lower() in e["source"].lower():
                events.append(e)
        return events[-max(1, min(limit, 200)):][::-1]           # newest first

    def summary(self) -> dict:
        sources = []
        for s in self.sources:
            st = self.state.get(s["name"], {})
            sources.append({"name": s["name"], "kind": s.get("kind", "folder"),
                            "files": len(st["files"]) if "files" in st else None,
                            "checked": st.get("checked", "")})
        return {"sources": sources, "events": self.recent(8)}
