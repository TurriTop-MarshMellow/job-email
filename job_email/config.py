"""读取 config.json，并用 .env 里的模型配置覆盖。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class AppConfig:
    root: Path
    mailbox_dir: Path
    output_path: Path
    confidence_threshold: float = 0.6
    keywords: list[str] = field(default_factory=list)
    sender_domain_hints: list[str] = field(default_factory=list)
    company_by_domain: dict[str, str] = field(default_factory=dict)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""


def load_config(root: Path | None = None) -> AppConfig:
    root = root or project_root()
    load_dotenv(root / ".env")
    data = json.loads((root / "config.json").read_text(encoding="utf-8"))
    llm = data.get("llm") or {}
    mailbox = Path(data.get("mailbox_dir", "data/mailbox"))
    output = Path(data.get("output_path", "data/applications.json"))
    if not mailbox.is_absolute():
        mailbox = root / mailbox
    if not output.is_absolute():
        output = root / output
    return AppConfig(
        root=root,
        mailbox_dir=mailbox,
        output_path=output,
        confidence_threshold=float(data.get("confidence_threshold", 0.6)),
        keywords=list(data.get("keywords") or []),
        sender_domain_hints=list(data.get("sender_domain_hints") or []),
        company_by_domain=dict(data.get("company_by_domain") or {}),
        llm_base_url=os.environ.get("OPENAI_BASE_URL", llm.get("base_url", "https://api.openai.com/v1")).rstrip("/"),
        llm_model=os.environ.get("OPENAI_MODEL", llm.get("model", "gpt-4o-mini")),
        llm_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
    )
