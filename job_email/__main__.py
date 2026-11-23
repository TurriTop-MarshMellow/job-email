"""python -m job_email：重算进度并在本机打开页面。"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from job_email.config import load_config
from job_email.pipeline import run_pipeline
from job_email.redact import batch_redact
from job_email.serve import serve


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="job_email", description="从求职邮件整理每个岗位的进度")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="只跑抽取和状态推算，写出 JSON")
    run.add_argument("--mailbox", help="邮件目录，默认用 config.json")

    redact = sub.add_parser("redact", help="批量生成脱敏邮件副本，原件不变")
    redact.add_argument("--input", default="data/mailbox")
    redact.add_argument("--output", default="data/redacted_mailbox")
    redact.add_argument("--name", action="append", default=[], help="要全文替换的姓名；可重复指定")
    redact.add_argument("--preview", action="store_true", help="只列出待处理邮件，不写文件")

    web = sub.add_parser("serve", help="打开本地页面")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--rebuild", action="store_true", help="打开页面前先重算")
    web.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    config = load_config()
    command = args.command or "demo"

    if command == "redact":
        input_dir = config.root / args.input if not _is_abs(args.input) else _path(args.input)
        output_dir = config.root / args.output if not _is_abs(args.output) else _path(args.output)
        if args.preview:
            files = sorted(input_dir.rglob("*.eml")) if input_dir.exists() else []
            print(f"找到 {len(files)} 封 EML；预览模式未写入文件。")
            for path in files:
                print(path.relative_to(input_dir))
        else:
            results = batch_redact(input_dir, output_dir, args.name)
            print(f"已生成 {len(results)} 封脱敏副本：{output_dir}")
            print(f"抽查报告：{output_dir / 'redaction-report.json'}")
            print("自动处理邮箱、手机号、常见短链和部分称呼姓名；住址、薪资及图片内容需人工检查。")
        return

    if command == "run":
        if args.mailbox:
            config.mailbox_dir = config.root / args.mailbox if not _is_abs(args.mailbox) else _path(args.mailbox)
        run_pipeline(config)
        return

    if command == "demo":
        run_pipeline(config)
        _open_and_serve(config, 8765, open_browser=True)
        return

    if args.rebuild or not config.output_path.exists():
        run_pipeline(config)
    _open_and_serve(config, args.port, open_browser=not args.no_browser)


def _open_and_serve(config, port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        webbrowser.open(url)
    serve("127.0.0.1", port, config.root / "web", config.output_path)


def _is_abs(value: str) -> bool:
    from pathlib import Path

    return Path(value).is_absolute()


def _path(value: str):
    from pathlib import Path

    return Path(value)


if __name__ == "__main__":
    main()
