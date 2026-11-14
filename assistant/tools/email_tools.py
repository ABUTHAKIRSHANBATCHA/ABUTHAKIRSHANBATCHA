"""Email over IMAP/SMTP (Gmail, Outlook and most providers). Off until the
owner enables it in config.json; the password lives in secrets.env. Sending
always needs the owner's approval, and reading does too unless they change
`ask_before_reading`."""
from __future__ import annotations

import email
import email.utils
import imaplib
import json
import os
import re
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage

from . import Registry, Tool


def _header(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


def body_text(msg) -> str:
    """Plain text of a message; falls back to HTML with the tags removed."""
    plain = html = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html:
            html = text
    if plain.strip():
        return plain.strip()
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def summarise(uid: str, raw: bytes, full: bool = False) -> dict:
    msg = email.message_from_bytes(raw)
    text = body_text(msg)
    out = {"uid": uid, "from": _header(msg["From"]), "subject": _header(msg["Subject"]),
           "date": _header(msg["Date"])}
    if full:
        out.update(to=_header(msg["To"]), body=text[:20000], message_id=msg["Message-ID"] or "",
                   attachments=[_header(p.get_filename()) for p in msg.walk() if p.get_filename()])
    else:
        out["preview"] = text[:200]
    return out


class Mailbox:
    def __init__(self, settings: dict, password: str) -> None:
        self.s, self.password = settings, password

    def _imap(self):
        box = imaplib.IMAP4_SSL(self.s["imap_host"])
        box.login(self.s["address"], self.password)
        box.select("INBOX", readonly=True)        # reading never marks mail as read
        return box

    def inbox(self, limit: int = 10, unread_only: bool = True, search: str = "") -> str:
        limit = max(1, min(limit, 30))
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        if search:
            criteria += ["TEXT", '"' + search.replace('"', "") + '"']
        box = self._imap()
        try:
            _, data = box.uid("search", None, *criteria)
            uids = data[0].split()[-limit:][::-1]
            messages = []
            for uid in uids:
                _, parts = box.uid("fetch", uid, "(BODY.PEEK[])")
                messages.append(summarise(uid.decode(), parts[0][1]))
            return json.dumps({"count": len(messages), "messages": messages}, ensure_ascii=False)
        finally:
            box.logout()

    def read(self, uid: str) -> str:
        box = self._imap()
        try:
            _, parts = box.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            if not parts or parts[0] is None:
                return f"No message with uid {uid}."
            return json.dumps(summarise(uid, parts[0][1], full=True), ensure_ascii=False)
        finally:
            box.logout()

    def send(self, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s["address"], to, subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        if in_reply_to:
            msg["In-Reply-To"] = msg["References"] = in_reply_to
        msg.set_content(body)
        with smtplib.SMTP(self.s["smtp_host"], int(self.s.get("smtp_port", 587)), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.s["address"], self.password)
            smtp.send_message(msg)
        return f"Email sent to {to}."


def register(reg: Registry, cfg) -> bool:
    password = os.environ.get("LUMI_EMAIL_PASSWORD", "")
    if not (cfg.email.get("enabled") and cfg.email.get("address") and password):
        return False
    box = Mailbox(cfg.email, password)
    ask = bool(cfg.email.get("ask_before_reading", True))
    reg.add(Tool(
        "email_inbox",
        f"List recent emails in the owner's inbox ({cfg.email['address']}): sender, subject, date "
        "and a short preview. Does not mark anything as read."
        + (" The owner approves each look at the inbox." if ask else ""),
        {"limit": {"type": "integer"}, "unread_only": {"type": "boolean"},
         "search": {"type": "string", "description": "Optional words to search for"}},
        [], box.inbox, confirm=ask))
    reg.add(Tool(
        "email_read", "Read one email in full by its uid from email_inbox.",
        {"uid": {"type": "string"}}, ["uid"], box.read, confirm=ask))
    reg.add(Tool(
        "email_send",
        "Send an email from the owner's address. The owner sees the full email and approves it "
        "before it goes; never claim an email was sent unless this tool reported success. To "
        "reply, pass the original's message_id as in_reply_to and prefix the subject with Re:.",
        {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
         "in_reply_to": {"type": "string"}},
        ["to", "subject", "body"], box.send, confirm=True))
    return True
