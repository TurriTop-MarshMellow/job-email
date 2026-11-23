"""从一封邮件抽出一条事件。模型只做抽取；没配密钥时用规则兜底。"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime

from job_email.config import AppConfig
from job_email.models import EVENT_TYPE_SET, Event, MailMessage


SYSTEM_PROMPT = """你是求职邮件的事件抽取器。只输出一个 JSON 对象，不要 Markdown。
你只判断「这封邮件发生了什么事件」，不要判断这个岗位现在处于什么进度。

event_type 必须是以下之一：
投递确认、笔试、面试邀请、面试链接、面试反馈、通过/进下一轮、未通过/感谢信、offer

规则：
- 感谢信不一定是拒信。只有明确表达未录用、无法推进、名额已满、选择了其他候选人时，才标「未通过/感谢信」。拿不准时 confidence 必须低于 0.5。
- 发出面试邀请但没有会议链接、会议号，是「面试邀请」。邮件主体是会议链接、会议号、入会地址，是「面试链接」。
- evidence 必须是邮件原文片段，不超过 80 个字，不要改写。
- occurred_at 用 YYYY-MM-DD。正文没有写明事件日期时，用邮件发送日期。
- company、role 只从邮件里摘，不要编造。摘不到就留空字符串，并降低 confidence。
- 与求职流程无关时 relevant 设为 false。

输出：
{"relevant": true, "company": "", "role": "", "event_type": "", "occurred_at": "YYYY-MM-DD", "confidence": 0.0, "evidence": ""}
"""


# 越靠前越优先。感谢信不在这里直接当成拒信。
_RULES: list[tuple[str, float, list[str]]] = [
    ("offer", 0.9, [r"录用通知", r"向你发出\s*offer", r"向您发出\s*offer", r"\boffer\b"]),
    (
        "未通过/感谢信",
        0.86,
        [
            r"很遗憾",
            r"遗憾地通知",
            r"未通过",
            r"未能通过",
            r"不予录用",
            r"暂不录用",
            r"不符合.*要求",
            r"因为与?岗位匹配程度的问题",
            r"暂不.*推进",
            r"暂时无法为您?安排后续环节",
            r"无法为您?安排后续环节",
            r"未进入.*(面试|下一轮|后续环节)",
            r"无法.*(推进|录用|进入下一轮)",
            r"无法继续",
            r"其他候选人",
            r"不再推进",
            r"未能进入",
            r"无法进入下一轮",
        ],
    ),
    (
        "通过/进下一轮",
        0.84,
        [r"进入下一轮", r"通过初试", r"通过复试", r"恭喜你通过", r"恭喜您通过", r"笔试通过", r"面试通过"],
    ),
    ("笔试", 0.83, [r"在线测评", r"笔试链接", r"参加笔试", r"笔试通知", r"笔试"]),
    ("面试链接", 0.82, [r"会议号", r"腾讯会议", r"zoom\.us/\S+", r"面试链接\s*[:：]\s*\S+"]),
    ("面试邀请", 0.84, [r"面试邀请", r"邀请你参加面试", r"邀请您参加面试", r"安排面试"]),
    ("面试反馈", 0.74, [r"面试评价", r"面试反馈", r"面试官的评价"]),
    (
        "投递确认",
        0.86,
        [r"已收到您的简历", r"已收到你的简历", r"简历已收到", r"网申已成功", r"网申成功", r"投递成功", r"申请已收到"],
    ),
    # 感谢信本身不是拒信。只有这种含糊收尾才记成低置信度的未通过，交给人确认。
    ("未通过/感谢信", 0.4, [r"人才库", r"后续如有合适", r"暂时没有更合适的岗位"]),
]


def extract_event(message: MailMessage, config: AppConfig, seq: int) -> Event | None:
    fallback_day = _day(message.sent_at)
    if config.llm_api_key:
        try:
            payload = _ask_llm(message, config, fallback_day)
            extractor = "llm"
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            print(f"模型抽取失败，改用规则：{message.subject}（{exc}）")
            payload = classify_text(
                message.subject,
                message.body,
                message.from_name,
                message.from_addr,
                fallback_day,
                config.company_by_domain,
            )
            extractor = "heuristic"
    else:
        payload = classify_text(
            message.subject,
            message.body,
            message.from_name,
            message.from_addr,
            fallback_day,
            config.company_by_domain,
        )
        extractor = "heuristic"
    if not payload or not payload.get("relevant", True):
        return None
    event_type = str(payload.get("event_type") or "")
    if event_type not in EVENT_TYPE_SET:
        return None
    occurred_at = str(payload.get("occurred_at") or fallback_day)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", occurred_at):
        occurred_at = fallback_day or "1970-01-01"
    company = _clip(str(payload.get("company") or ""), 40)
    role = _clip(str(payload.get("role") or ""), 40)
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0.0, min(1.0, confidence))
    if not company or not role:
        confidence = min(confidence, 0.45)
    evidence = _clip(re.sub(r"\s+", " ", str(payload.get("evidence") or "")).strip(), 80)
    digest = hashlib.sha1(f"{message.message_id}|{event_type}|{occurred_at}".encode("utf-8")).hexdigest()[:12]
    return Event(
        id=digest,
        company=company or "未知公司",
        role=role or "未知岗位",
        event_type=event_type,
        occurred_at=occurred_at,
        confidence=confidence,
        evidence=evidence,
        subject=message.subject,
        source=message.source,
        message_id=message.message_id,
        extractor=extractor,
        seq=seq,
    )


def classify_text(
    subject: str,
    body: str,
    from_name: str,
    from_addr: str,
    occurred_at: str,
    domain_map: dict[str, str],
) -> dict | None:
    text = f"{subject}\n{body}"
    matched: tuple[str, float, str] | None = None
    for event_type, confidence, patterns in _RULES:
        for pattern in patterns:
            found = re.search(pattern, text, flags=re.IGNORECASE)
            if found:
                matched = (event_type, confidence, _quote(text, pattern))
                break
        if matched:
            break
    if matched is None:
        if "感谢" in text and "面试" in text:
            matched = ("面试反馈", 0.42, _quote(text, r"感谢.{0,40}"))
        else:
            return None
    event_type, confidence, evidence = matched
    company = guess_company(subject, body, from_name, from_addr, domain_map)
    role = guess_role(body, subject)
    return {
        "relevant": True,
        "company": company,
        "role": role,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "confidence": confidence,
        "evidence": evidence,
    }


def guess_company(subject: str, body: str, from_name: str, from_addr: str, domain_map: dict[str, str]) -> str:
    bracketed = re.search(r"[【\[]([^】\]]{2,30})[】\]]", subject)
    if bracketed:
        candidate = bracketed.group(1).strip()
        candidate = re.sub(r"(校园招聘|校招|招聘|人才招募|人才招聘)$", "", candidate).strip()
        if candidate:
            return candidate
    body_company = _company_from_text(body[:2500])
    if body_company:
        return body_company
    sender_domain = from_addr.rsplit("@", 1)[-1].lower() if "@" in from_addr else ""
    domain_aliases = {"yuexiuproperty.com": "越秀地产", "ciderglobal.com": "Cider", "mail.feishu.cn": "Cider", "tencent.com": "腾讯", "baidu.com": "百度", "huawei.com": "华为", "lenovo.com": "联想"}
    for domain, canonical in domain_aliases.items():
        if sender_domain == domain or sender_domain.endswith("." + domain):
            return canonical
    labeled = re.search(r"公司[:：]\s*([^\n，。,.]{2,30})", body)
    if labeled:
        return labeled.group(1).strip()
    domain = from_addr.rsplit("@", 1)[-1].lower() if "@" in from_addr else ""
    if domain and domain in {key.lower(): value for key, value in domain_map.items()}:
        mapped = {key.lower(): value for key, value in domain_map.items()}
        return mapped[domain]
    name = re.sub(r"(招聘组|招聘邮箱|招聘|人力资源部|人力资源|校招组|人才招募)$", "", from_name or "", flags=re.IGNORECASE)
    name = name.strip(" -_|｜")
    if name and not re.search(r"(通知|系统|面试|邮箱|无回复|noreply)", name, flags=re.I):
        return name.strip()
    brand = re.search(r"(?:来自|诚邀您参加|邀请您参加|邀请你参加|感谢您投递|感谢你投递|感谢您应聘|感谢你应聘|对)\s*([\u4e00-\u9fffA-Za-z0-9（）()·]{2,24}?)(?:的关注|的认可|参加|应聘|投递|校园招聘|校招|在线|面试|$)", body[:1800])
    if brand:
        candidate = brand.group(1).strip()
        if candidate not in {"我们", "我司", "贵司", "公司"}:
            return candidate
    signature = re.search(r"(?:—{2,}|_{2,}|\*{2,})\s*([\u4e00-\u9fffA-Za-z0-9（）()·]{2,24}?)(?:人资|人力资源|招聘|校园招聘|hr)", body[-800:], flags=re.I)
    return signature.group(1).strip() if signature else ""


def _company_from_text(text: str) -> str:
    aliases = (
        (r"越秀(?:地产)?", "越秀地产"),
        (r"百度(?:在线网络技术（北京）有限公司)?", "百度"),
        (r"腾讯(?:云)?", "腾讯"),
        (r"联想(?:中国)?", "联想"),
        (r"华为(?:公司)?", "华为"),
        (r"字节跳动|ByteDance", "字节跳动 ByteDance"),
        (r"京东", "京东"),
        (r"蔚来|NIO", "NIO蔚来"),
        (r"高德地图", "高德地图"),
        (r"SHEIN", "SHEIN"),
        (r"Cider", "Cider"),
    )
    for pattern, canonical in aliases:
        if re.search(pattern, text, flags=re.I):
            return canonical
    formal = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}(?:股份有限公司|有限责任公司|有限公司))", text)
    if formal:
        return formal.group(1).strip()
    return ""


def guess_role(body: str, subject: str = "") -> str:
    title_before_position = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()·\- ]{2,45}?)(?:职位|岗位)(?:的|面试|$)", body[:2500])
    if title_before_position:
        candidate = _clean_role(title_before_position.group(1))
        candidate = re.sub(r"^(?:你好[,，]?|您好[,，]?|.*?诚邀你参加|.*?邀请您参加)\s*", "", candidate)
        if candidate and not re.search(r"(感谢|公司|招聘|诚邀|你好)", candidate):
            return candidate
    contextual = re.search(r"(?:百度|腾讯|联想|华为)[-—：:]\s*([^，。\n]{2,45}?)\s*岗位", body[:2500])
    if contextual:
        return _clean_role(contextual.group(1))
    labeled = re.search(r"(?:应聘|申请|投递|招聘)?(?:岗位|职位)[:：]\s*([^\n，。,.；;]{2,50})", body)
    if labeled:
        return _clean_role(labeled.group(1))
    subject_role = re.search(r"(?:——|—|\s-\s)(.{2,70})$", subject)
    if subject_role:
        candidate = _clean_role(subject_role.group(1))
        if re.search(r"(实习|工程师|经理|运营|产品|算法|设计|开发|分析师|专员|助理|intern|engineer|manager|analyst)", candidate, re.I):
            return candidate
    if re.search(r"(腾讯|联想|华为|百度).*(面试邀请|面试邀约|面试提醒)", subject):
        return "实习生"
    body_role = re.search(r"(?:应聘|申请|投递)(?:了)?\s*(?:我们|我司|贵司)?\s*([^，。\n]{2,40}?)(?:岗位|职位|，|。|$)", body[:1600])
    if body_role:
        return _clean_role(body_role.group(1))
    return ""


def _clean_role(value: str) -> str:
    return re.sub(r"[\s：:，。；;]+$", "", re.sub(r"^(?:的|招聘)\s*", "", value.strip()))[:50]


def _ask_llm(message: MailMessage, config: AppConfig, fallback_day: str) -> dict:
    user = (
        f"发送日期：{fallback_day or '未知'}\n"
        f"发件人：{message.from_name} <{message.from_addr}>\n"
        f"主题：{message.subject}\n\n"
        f"{message.body[:6000]}"
    )
    body = json.dumps(
        {
            "model": config.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{config.llm_base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.llm_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回 JSON")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型返回的不是对象")
    return data


def _quote(text: str, pattern: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or re.match(r"^(公司|岗位|职位)[:：]", stripped):
            continue
        if re.search(pattern, stripped, flags=re.IGNORECASE):
            return _clip(stripped, 80)
    found = re.search(pattern, text, flags=re.IGNORECASE)
    if not found:
        return ""
    start = max(0, found.start() - 12)
    snippet = text[start : found.end() + 24]
    return _clip(re.sub(r"\s+", " ", snippet).strip(), 80)


def _day(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.date().isoformat()


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
