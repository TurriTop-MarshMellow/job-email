"""邮件、事件、岗位三种数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


EVENT_TYPES = (
    "投递确认",
    "笔试",
    "面试邀请",
    "面试链接",
    "面试反馈",
    "通过/进下一轮",
    "未通过/感谢信",
    "offer",
)

EVENT_TYPE_SET = frozenset(EVENT_TYPES)


@dataclass
class MailMessage:
    message_id: str
    subject: str
    from_name: str
    from_addr: str
    sent_at: datetime | None
    body: str
    source: str


@dataclass
class Event:
    id: str
    company: str
    role: str
    event_type: str
    occurred_at: str
    confidence: float
    evidence: str
    subject: str
    source: str
    message_id: str
    extractor: str
    seq: int = 0
    flags: list[str] = field(default_factory=list)
    effect: str = "未采纳"
    applied_to_state: bool = False
    blocked: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.event_type,
            "occurred_at": self.occurred_at,
            "confidence": round(float(self.confidence), 2),
            "evidence": self.evidence,
            "subject": self.subject,
            "source": self.source,
            "extractor": self.extractor,
            "flags": list(self.flags),
            "effect": self.effect,
            "applied_to_state": self.applied_to_state,
        }


@dataclass
class Application:
    id: str
    company: str
    company_key: str
    role: str
    role_key: str
    category: str
    status: str
    needs_review: bool
    flags: list[str]
    last_event_at: str
    dropped_duplicates: int
    suspected_with: list[dict]
    role_variants: list[str]
    events: list[Event]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "company": self.company,
            "role": self.role,
            "category": self.category,
            "status": self.status,
            "needs_review": self.needs_review,
            "flags": list(self.flags),
            "last_event_at": self.last_event_at,
            "dropped_duplicates": self.dropped_duplicates,
            "suspected_with": self.suspected_with,
            "role_variants": self.role_variants,
            "events": [event.to_dict() for event in self.events],
        }
