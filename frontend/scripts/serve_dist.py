"""把 `frontend/dist` 当成 SPA 静态站点跑起来，并把 /api、/static 转发给后端

    python scripts/serve_dist.py                 # http://127.0.0.1:5173
    python scripts/serve_dist.py --port 4173 --backend http://127.0.0.1:8000

为什么需要它：

    `npm run dev` / `npm run preview` 都依赖 vite 自己的进程，某些受限环境
    （例如带文件沙箱的 CI）里 vite 起子进程会失败。这个脚本只用标准库：

        * 静态资源命中就直接返回（带正确的 Content-Type）；
        * `/api/...`、`/static/...` 反向代理到后端，生产构建因此可以「同源部署」
          （.env.production 里 VITE_API_BASE_URL 留空）；
        * 其余路径（/manager/lab/3 这类前端路由）一律回 index.html，
          由 vue-router 自己解析，刷新页面不会 404。

它只是本地预览 / 演示用的轻量服务，正式部署请用 nginx 或 Caddy：
静态目录指到 dist，把 /api 与 /static 反代到后端即可。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

DIST = Path(__file__).resolve().parent.parent / "dist"

# 需要转发给后端的路径前缀
PROXY_PREFIXES = ("/api/", "/static/")
# 上游超时：助手接口要调模型，给足 120 秒
UPSTREAM_TIMEOUT = 120

# 少量补充映射：Windows 注册表里偶尔缺这些类型
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


class SpaHandler(BaseHTTPRequestHandler):
    """静态文件 + SPA 回退 + 后端反向代理"""

    server_version = "SmartCampusPreview/1.0"
    backend = "http://127.0.0.1:8000"

    # ---------------------------------------------------------------- HTTP 方法
    def do_GET(self) -> None:                      # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:                     # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:                     # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:                      # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:                   # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:                  # noqa: N802
        # 同源部署不需要预检；这里给 curl 调试留个空响应
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        if self.path.startswith(PROXY_PREFIXES):
            self._proxy(method)
            return
        if method not in ("GET", "HEAD"):
            self._plain(405, "Method Not Allowed")
            return
        self._serve_static(send_body=method == "GET")

    # ---------------------------------------------------------------- 静态文件
    def _serve_static(self, *, send_body: bool) -> None:
        parsed = urlparse(self.path)
        relative = posixpath.normpath(unquote(parsed.path)).lstrip("/")
        target = (DIST / relative).resolve()

        # 目录穿越保护：解析后的路径必须仍在 dist 里
        if not str(target).startswith(str(DIST.resolve())):
            self._plain(403, "Forbidden")
            return

        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # 前端路由（/manager/...）：交给 SPA
            target = DIST / "index.html"
        if not target.is_file():
            self._plain(404, "dist 还没有构建：先执行 npm run build")
            return

        content = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        header_type = content_type or "application/octet-stream"
        if header_type.startswith(("text/", "application/json")):
            header_type = f"{header_type}; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", header_type)
        self.send_header("Content-Length", str(len(content)))
        # 构建产物带 hash，可以长缓存；index.html 不缓存，避免发版后看到旧页面
        self.send_header(
            "Cache-Control",
            "no-cache" if target.name == "index.html" else "public, max-age=31536000, immutable",
        )
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    # ---------------------------------------------------------------- 反向代理
    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        request = urllib.request.Request(
            self.backend + self.path, data=body, method=method
        )
        # 只转发必要的头：Host 换成上游，Authorization 必须带上（JWT）
        for header in ("Content-Type", "Accept", "Authorization"):
            value = self.headers.get(header)
            if value:
                request.add_header(header, value)

        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 也是正常业务响应（统一信封），原样透传
            payload = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json")
        except Exception as exc:                   # noqa: BLE001
            payload = json.dumps(
                {"code": 502, "message": f"后端不可用：{exc}", "data": None},
                ensure_ascii=False,
            ).encode("utf-8")
            status = 502
            content_type = "application/json; charset=utf-8"

        if not content_type.startswith(("text/", "application/json")):
            content_type = f"{content_type}; charset=utf-8"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    # ---------------------------------------------------------------- 工具
    def _plain(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print(f"[preview] {self.address_string()} {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="静态预览 frontend/dist（带 /api 代理）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--backend", default="http://127.0.0.1:8000", help="后端地址")
    args = parser.parse_args()

    if not (DIST / "index.html").exists():
        print(f"找不到 {DIST / 'index.html'}，请先在 frontend 目录执行 npm run build")
        return 1

    SpaHandler.backend = args.backend.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), SpaHandler)
    print(f"前端预览：http://{args.host}:{args.port}（静态 {DIST}，/api → {SpaHandler.backend}）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
