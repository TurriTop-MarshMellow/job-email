"""本地打开两个视图。只监听本机。"""

from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def serve(host: str, port: int, web_dir: Path, output_path: Path) -> None:
    handler = partial(AppHandler, web_dir=web_dir, output_path=output_path)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"打开 {url}")
    print("停掉服务用 Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, web_dir: Path, output_path: Path, **kwargs):
        self.web_dir = web_dir
        self.output_path = output_path
        super().__init__(*args, directory=str(web_dir), **kwargs)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/applications":
            self._send_json()
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _send_json(self) -> None:
        if not self.output_path.exists():
            body = json.dumps({"applications": [], "stats": {}}, ensure_ascii=False).encode("utf-8")
        else:
            body = self.output_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return
