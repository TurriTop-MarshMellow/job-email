"""Create local, redacted copies of EML files without modifying originals."""

from __future__ import annotations

import argparse
import json
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, getaddresses
from pathlib import Path


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.I)
SHORT_URL_RE = re.compile(r"(?<![\w@])(?:[\w-]+\.)+(?:cn|com|net|org|io|me|cc|tv|top|xyz|link|vip)/[^\s<>\]\[\"'，。；）》】]+", re.I)
CODE_VALUE_RE = re.compile(r"((?:邀约码|邀約碼|邀请码|邀請碼|面试码|面試碼|验证码|驗證碼|校验码|校驗碼|口令|访问码|訪問碼|激活码|激活碼|测评码|測評碼)\s*(?:为|是)?\s*[:：]?\s*)([A-Z0-9][A-Z0-9_-]{3,})", re.I)
NAME_CONTEXT_RE = re.compile(r"(?:尊敬的|亲爱的|您好[,，]?\s*|你好[,，]?\s*)([\u4e00-\u9fff]{2,4})(?=同学|先生|女士|[,，：:\s]|$)|([\u4e00-\u9fff]{2,4})(?=您好|你好|同学您好)")


def redact_text(value: str) -> tuple[str, dict[str, int]]:
    counts = {"email": 0, "phone": 0, "url": 0, "code": 0, "name": 0}
    for label, pattern in (("email", EMAIL_RE), ("phone", PHONE_RE), ("url", URL_RE), ("url", SHORT_URL_RE)):
        value, counts[label] = pattern.subn(f"[{label.upper()}_REDACTED]", value)
    value, counts["code"] = CODE_VALUE_RE.subn(r"\1[CODE_REDACTED]", value)
    return value, counts


def redact_message(source: Path, target: Path, names_to_redact: list[str] | None = None) -> dict:
    original = BytesParser(policy=policy.default).parsebytes(source.read_bytes())
    redacted = EmailMessage(policy=policy.default)
    counts = {"email": 0, "phone": 0, "url": 0, "code": 0, "name": 0}

    recipient_names = []
    for display_name, _ in getaddresses(original.get_all("To", []) + original.get_all("Cc", [])):
        candidate = re.sub(r"[^\u4e00-\u9fff]", "", display_name)
        if 2 <= len(candidate) <= 4:
            recipient_names.append(candidate)

    subject, found = redact_text(str(original.get("Subject", "")))
    subject, found_names = _redact_names(subject, names_to_redact or [])
    found["name"] += found_names
    _add(redacted, "Subject", subject)
    counts = _sum(counts, found)
    _add(redacted, "Date", str(original.get("Date", "")))
    sender_name = str(original.get("From", "招聘团队")).split("<", 1)[0].strip().strip('"') or "招聘团队"
    sender_name, found = redact_text(sender_name)
    counts = _sum(counts, found)
    _add(redacted, "From", formataddr((sender_name, "recruiter@example.invalid")))
    _add(redacted, "To", "recipient@example.invalid")

    body_text: list[str] = []
    body_html: list[str] = []
    for part in original.walk():
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
        names = set(recipient_names)
        names.update(next(group for group in match.groups() if group) for match in NAME_CONTEXT_RE.finditer(content))
        cleaned, found = redact_text(content)
        cleaned, replaced = _redact_names(cleaned, list(names) + (names_to_redact or []))
        found["name"] += replaced
        counts = _sum(counts, found)
        (body_html if content_type == "text/html" else body_text).append(cleaned)

    if body_text:
        redacted.set_content("\n\n".join(body_text))
    elif body_html:
        redacted.set_content("HTML 邮件正文已保留，请在 HTML 版本中抽查脱敏效果。")
        redacted.add_alternative("\n\n".join(body_html), subtype="html")
    else:
        redacted.set_content("[未提取到文本正文]")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(redacted.as_bytes())
    return {"file": source.name, **counts, "attachments_removed": True}


def _add(message: EmailMessage, key: str, value: str) -> None:
    if value:
        message[key] = value


def _sum(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: left[key] + right[key] for key in left}


def _redact_names(value: str, names: list[str]) -> tuple[str, int]:
    replacements = 0
    unique_names = sorted({name.strip() for name in names if name and name.strip()}, key=len, reverse=True)
    for name in unique_names:
        value, count = re.subn(re.escape(name), "[NAME_REDACTED]", value, flags=re.IGNORECASE)
        replacements += count
    return value, replacements


def batch_redact(input_dir: Path, output_dir: Path, names_to_redact: list[str] | None = None) -> list[dict]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if input_dir == output_dir or input_dir in output_dir.parents:
        raise ValueError("输出目录不能位于输入目录内，避免重复处理生成的副本")
    results = []
    for source in sorted(input_dir.rglob("*.eml")):
        relative = source.relative_to(input_dir)
        target = output_dir / relative
        results.append(redact_message(source, target, names_to_redact))
    report = output_dir / "redaction-report.json"
    report.write_text(json.dumps({"files": results, "note": "会从收件人显示名和常见称呼提取姓名并替换全文；请仍检查未在邮件头/称呼中出现的姓名、住址、薪资及嵌入图像。"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="批量生成邮件脱敏副本；原文件保持不变")
    parser.add_argument("--input", type=Path, default=Path("data/mailbox"))
    parser.add_argument("--output", type=Path, default=Path("data/redacted_mailbox"))
    parser.add_argument("--name", action="append", default=[], help="需要全文替换的姓名；可重复指定")
    parser.add_argument("--preview", action="store_true", help="只显示将处理的邮件，不写入文件")
    args = parser.parse_args(argv)
    if args.preview:
        files = sorted(args.input.rglob("*.eml")) if args.input.exists() else []
        print(f"找到 {len(files)} 封 EML；预览模式未写入文件。")
        for path in files:
            print(path.relative_to(args.input))
        return
    results = batch_redact(args.input, args.output, args.name)
    print(f"已生成 {len(results)} 封脱敏副本：{args.output}")
    print(f"请打开报告抽查：{args.output / 'redaction-report.json'}")
    print("自动处理邮箱、手机号和链接；中文姓名、住址、薪资及图片内容需人工检查。")


if __name__ == "__main__":
    main()
