"""岗位名归一、疑似同岗位、同一天同类型去重。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict

from job_email.models import Application, Event
from job_email.state_machine import apply_state

_COMPANY_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "公司",
    "inc.",
    "inc",
    "ltd.",
    "ltd",
    "co.",
    "corp.",
)


def normalize_company(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    for suffix in _COMPANY_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text


def normalize_role(name: str) -> str:
    """去掉届别和标点。括号里的方向留在名字里，避免把不同方向并成一个岗位。"""
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    text = re.sub(r"(校招|社招|春招|秋招|实习生|实习|20\d{2}届)", "", text)
    text = re.sub(r"[\s_\-—/|·,，。()（）【】\[\]]+", "", text)
    return text


def roles_look_alike(left: str, right: str) -> bool:
    """包含关系且相差不大时，只标记疑似，不合并。"""
    if not left or not right or left == right:
        return False
    if left in {"未知岗位"} or right in {"未知岗位"}:
        return False
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) < 4:
        return False
    return short in long and len(long) - len(short) <= 8


def role_category(role: str) -> str:
    text = unicodedata.normalize("NFKC", role or "").lower()
    if "运营" in text:
        return "运营"
    if any(word in text for word in ("助理", "行政", "秘书", "总监", "人力", "招聘", "hr", "财务", "会计", "审计", "法务")):
        return "职能"
    if "产品" in text or "product" in text:
        return "产品"
    if any(word in text for word in ("工程", "开发", "算法", "前端", "后端", "测试", "数据", "研发", "工程师", "engineer", "developer", "客户端")):
        return "技术"
    if any(word in text for word in ("设计", "design", "体验")):
        return "设计"
    if any(word in text for word in ("人力", "招聘", "hr", "人才", "recruit")):
        return "人力资源"
    if any(word in text for word in ("市场", "营销", "品牌", "marketing")):
        return "市场"
    if any(word in text for word in ("财务", "会计", "审计", "法务", "行政", "秘书")):
        return "职能"
    if any(word in text for word in ("销售", "商务拓展", "客户经理", "sales", "business development")):
        return "销售/商务"
    if any(word in text for word in ("客服", "客户成功", "客户支持", "customer success", "support")):
        return "客户服务"
    return "其他"


def merge_events(events: list[Event], threshold: float = 0.6) -> tuple[list[Application], int]:
    buckets: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        company_key = normalize_company(event.company) or "未知公司"
        role_key = normalize_role(event.role) or "岗位待确认"
        buckets[(company_key, role_key)].append(event)

    applications: list[Application] = []
    dropped_total = 0
    for (company_key, role_key), grouped in buckets.items():
        kept, dropped = _dedupe(grouped)
        dropped_total += dropped
        status, reviewed = apply_state(kept, threshold)
        company = _display_name([event.company for event in kept])
        variants = list(dict.fromkeys(event.role for event in kept))
        role = _display_name(variants)
        unknown = company_key == "未知公司" or role_key == "岗位待确认"
        flags: list[str] = []
        if reviewed or unknown:
            flags.append("待确认")
        if any("异常" in event.flags for event in kept):
            flags.append("异常")
        if any("冲突" in event.flags for event in kept):
            flags.append("冲突")
        app_id = hashlib.sha1(f"{company_key}::{role_key}".encode("utf-8")).hexdigest()[:10]
        applications.append(
            Application(
                id=app_id,
                company=company,
                company_key=company_key,
                role=role if role_key != "岗位待确认" else "岗位待确认",
                role_key=role_key,
                category=role_category(role),
                status=status,
                needs_review=reviewed or unknown,
                flags=flags,
                last_event_at=max(event.occurred_at for event in kept),
                dropped_duplicates=dropped,
                suspected_with=[],
                role_variants=variants if len(variants) > 1 else [],
                events=kept,
            )
        )
    _link_suspicions(applications)
    applications.sort(key=lambda app: app.last_event_at, reverse=True)
    return applications, dropped_total


def _dedupe(events: list[Event]) -> tuple[list[Event], int]:
    """同一岗位、同一类型、同一天只留一条。"""
    best: dict[tuple[str, str], Event] = {}
    dropped = 0
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.seq))
    for event in ordered:
        key = (event.event_type, event.occurred_at)
        current = best.get(key)
        if current is None:
            best[key] = event
            continue
        challenger = (event.confidence, len(event.evidence or ""))
        incumbent = (current.confidence, len(current.evidence or ""))
        if challenger > incumbent:
            best[key] = event
        dropped += 1
    kept = sorted(best.values(), key=lambda event: (event.occurred_at, event.seq))
    return kept, dropped


def _display_name(names: list[str]) -> str:
    counter = Counter(name.strip() for name in names if name and name.strip())
    if not counter:
        return "未知"
    # 出现次数优先，其次用更短的写法，避免「产品经理（校招）」盖过「产品经理」。
    return sorted(counter, key=lambda name: (-counter[name], len(name), name))[0]


def _link_suspicions(applications: list[Application]) -> None:
    by_company: dict[str, list[Application]] = defaultdict(list)
    for app in applications:
        by_company[app.company_key].append(app)
    for group in by_company.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if not roles_look_alike(left.role_key, right.role_key):
                    continue
                _remember(left, right)
                _remember(right, left)
                if "疑似同岗位" not in left.flags:
                    left.flags.append("疑似同岗位")
                if "疑似同岗位" not in right.flags:
                    right.flags.append("疑似同岗位")


def _remember(app: Application, other: Application) -> None:
    app.suspected_with.append({"id": other.id, "company": other.company, "role": other.role})
