"""Money tracking for the owner's businesses: income and expenses, kept on this PC."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import Registry, Tool


class Ledger:
    def __init__(self, folder: Path, currency: str) -> None:
        self.file = folder / "ledger.jsonl"
        folder.mkdir(parents=True, exist_ok=True)
        self.currency = currency

    def entries(self) -> list[dict]:
        if not self.file.exists():
            return []
        out = []
        for line in self.file.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def record(self, kind: str, amount: float, description: str, business: str = "", date: str = "") -> str:
        if amount <= 0:
            return "The amount must be greater than zero."
        try:
            day = dt.date.fromisoformat(date) if date else dt.date.today()
        except ValueError:
            return "The date must be written as YYYY-MM-DD."
        entry = {"date": day.isoformat(), "kind": kind, "amount": round(float(amount), 2),
                 "description": description, "business": business or "General"}
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return f"Recorded {kind} of {entry['amount']:,.2f} {self.currency} for {entry['business']} on {entry['date']}."

    def months(self, count: int = 6, business: str = "", today: dt.date | None = None) -> list[dict]:
        today = today or dt.date.today()
        keys = []
        year, month = today.year, today.month
        for _ in range(count):
            keys.append(f"{year:04d}-{month:02d}")
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        totals = {k: {"month": k, "income": 0.0, "expense": 0.0} for k in reversed(keys)}
        for e in self.entries():
            if business and business.lower() not in e["business"].lower():
                continue
            if e["date"][:7] in totals:
                totals[e["date"][:7]][e["kind"]] += e["amount"]
        return [{**m, "income": round(m["income"], 2), "expense": round(m["expense"], 2),
                 "net": round(m["income"] - m["expense"], 2)} for m in totals.values()]

    def summary(self, months: int = 6, business: str = "") -> str:
        months = max(1, min(months, 36))
        rows = self.months(months, business)
        first = rows[0]["month"]
        per_business: dict[str, dict] = {}
        recent = []
        for e in self.entries():
            if e["date"][:7] < first or (business and business.lower() not in e["business"].lower()):
                continue
            b = per_business.setdefault(e["business"], {"income": 0.0, "expense": 0.0})
            b[e["kind"]] += e["amount"]
            recent.append(e)
        return json.dumps({
            "currency": self.currency, "months": rows,
            "by_business": [{"business": k, **{x: round(v[x], 2) for x in v},
                             "net": round(v["income"] - v["expense"], 2)} for k, v in per_business.items()],
            "latest_entries": sorted(recent, key=lambda e: e["date"])[-10:],
        }, ensure_ascii=False)

    def dashboard(self) -> dict:
        return {"currency": self.currency, "months": self.months(6), "has_entries": bool(self.entries())}


def register(reg: Registry, ledger: Ledger) -> None:
    reg.add(Tool(
        "money_record",
        "Record money earned (income) or spent (expense) for one of the owner's businesses or "
        f"projects, in {ledger.currency}. Use it when the owner reports a payment, invoice paid, sale "
        "or cost. Confirm the amount back in your reply.",
        {"kind": {"type": "string", "enum": ["income", "expense"]},
         "amount": {"type": "number"},
         "description": {"type": "string"},
         "business": {"type": "string", "description": "Which business or project it belongs to"},
         "date": {"type": "string", "description": "YYYY-MM-DD; today if omitted"}},
        ["kind", "amount", "description"], ledger.record))
    reg.add(Tool(
        "money_summary",
        "Income, expenses and net per month, totals per business, and the latest entries. Use it "
        "for questions like 'how much did I make this month' or 'is the business profitable'.",
        {"months": {"type": "integer", "description": "How many months back; default 6"},
         "business": {"type": "string", "description": "Optional: only this business"}},
        [], ledger.summary))
