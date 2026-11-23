"""把读信、抽取、归并串成一次运行，写出 applications.json。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from job_email.config import AppConfig
from job_email.extract import extract_event
from job_email.ingest import coarse_keep, load_messages
from job_email.merge import merge_events


def run_pipeline(config: AppConfig) -> dict:
    messages = load_messages(config.mailbox_dir)
    events = []
    kept = 0
    for message in messages:
        if not coarse_keep(message, config.keywords, config.sender_domain_hints):
            continue
        kept += 1
        event = extract_event(message, config, seq=kept)
        if event is not None:
            events.append(event)
    applications, dropped = merge_events(events, config.confidence_threshold)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "extractor": "llm" if config.llm_api_key else "heuristic",
        "confidence_threshold": config.confidence_threshold,
        "stats": {
            "seen": len(messages),
            "kept": kept,
            "skipped": len(messages) - kept,
            "events": len(events),
            "duplicates_removed": dropped,
            "applications": len(applications),
        },
        "applications": [app.to_dict() for app in applications],
    }
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mode = "模型" if config.llm_api_key else "规则（未配置 OPENAI_API_KEY）"
    print(
        f"读取 {payload['stats']['seen']} 封，粗筛留下 {kept} 封，"
        f"抽出 {len(events)} 条事件，去掉同一天重复 {dropped} 条，"
        f"归并成 {len(applications)} 个岗位。"
    )
    print(f"抽取方式：{mode}")
    print(f"已写入 {config.output_path}")
    return payload
