"""从 .eml / .mbox 读取邮件，并按关键词、发件域名做粗筛。"""

from __future__ import annotations

import mailbox
import re
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path

from job_email.models import MailMessage


def load_messages(mailbox_dir: Path) -> list[MailMessage]:
    if not mailbox_dir.exists():
        return []
    messages: list[MailMessage] = []
    paths = sorted(mailbox_dir.rglob("*.eml")) + sorted(mailbox_dir.rglob("*.mbox"))
    for path in paths:
        if path.suffix.lower() == ".mbox":
            messages.extend(_load_mbox(path))
        else:
            try:
                messages.append(_load_eml(path))
            except Exception as exc:  # 一封坏邮件不应让整批停掉
                print(f"跳过无法解析的邮件 {path.name}: {exc}")
    return messages


def coarse_keep(message: MailMessage, keywords: list[str], domain_hints: list[str]) -> bool:
    blob = f"{message.subject}\n{message.body}".lower()
    if any(word.lower() in blob for word in keywords if word):
        return True
    domain = _domain(message.from_addr)
    return any(hint.lower() in domain for hint in domain_hints if hint)


def _load_eml(path: Path) -> MailMessage:
    raw = path.read_bytes()
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    return _to_message(parsed, path.as_posix())


def _load_mbox(path: Path) -> list[MailMessage]:
    found: list[MailMessage] = []
    box = mailbox.mbox(path)
    try:
        for index, item in enumerate(box):
            try:
                raw = item.as_bytes()
                parsed = BytesParser(policy=policy.default).parsebytes(raw)
                source = f"{path.as_posix()}#{index}"
                found.append(_to_message(parsed, source))
            except Exception as exc:
                print(f"跳过 mbox 中的一封邮件 {path.name}#{index}: {exc}")
    finally:
        box.close()
    return found


def _to_message(parsed: EmailMessage, source: str) -> MailMessage:
    name, addr = parseaddr(parsed.get("From", ""))
    sent_at = _parse_date(parsed.get("Date", ""))
    message_id = (parsed.get("Message-ID") or source).strip()
    subject = str(parsed.get("Subject") or "").strip()
    return MailMessage(
        message_id=message_id,
        subject=subject,
        from_name=_clean(name),
        from_addr=_clean(addr),
        sent_at=sent_at,
        body=_body_text(parsed),
        source=source,
    )


def _body_text(parsed: EmailMessage) -> str:
    if parsed.is_multipart():
        plain: list[str] = []
        html: list[str] = []
        for part in parsed.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                content = part.get_content()
            except Exception:
                continue
            if not isinstance(content, str):
                continue
            if content_type == "text/plain":
                plain.append(content)
            else:
                html.append(content)
        if plain:
            return "\n".join(plain).strip()
        if html:
            return _strip_html("\n".join(html))
        return ""
    try:
        content = parsed.get_content()
    except Exception:
        return ""
    if not isinstance(content, str):
        return ""
    if parsed.get_content_type() == "text/html":
        return _strip_html(content)
    return content.strip()


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def _domain(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].lower()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
