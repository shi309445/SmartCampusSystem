"""抓取兰州理工大学党委学生工作部「规章制度」栏目下的全部文件，逐篇保存为 Markdown。

用法::

    python getting_data.py --debug        # 只抓列表页，打印结构诊断并把 HTML 存到 _debug/
    python getting_data.py --list         # 只列出抓到的标题和链接，不写文件
    python getting_data.py --limit 3      # 只处理前 3 篇，用于快速验证
    python getting_data.py                # 抓取全部
    python getting_data.py --out get_data # 指定输出目录

关于站点防护（重要）
--------------------
xuegong.lut.edu.cn 整个站点部署了「瑞数信息」动态防护（Riverdance）：

* 普通 HTTP 客户端（requests / httpx / curl）只会拿到 **HTTP 412** 的 JS 挑战页；
* 挑战页下发的 Cookie 重放后依然是 412；
* 伪造 UA（含 Googlebot / Baiduspider）同样被拦；
* 无头浏览器也会被识别并返回 **HTTP 400**。

因此本脚本不直接发 HTTP 请求，而是通过 **CDP 驱动一个真实浏览器**渲染页面，
等挑战脚本执行完成、DOM 变成真正的网页后再取内容。默认使用有窗口模式，
因为无头模式更容易被瑞数识别；确认能跑通后再用 ``--headless`` 提速。

脚本刻意**不覆盖 User-Agent**：真实浏览器的 UA 与 sec-ch-ua 客户端提示必须一致，
只改 UA 反而会造成指纹矛盾而被拦截。

依赖：``beautifulsoup4``、``lxml``、``websockets``（均为项目已装依赖），无需额外安装。

输出的 Markdown 结构对齐 docs/management_rules/普通高等学校学生管理规定.md：

* ``#``  文档标题
* ``##`` 章标题（``第一章  总 则``）
* ``###`` 条标题（只放条号，正文另起一段，与参考文档一致）
* 其余内容按段落原样输出
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:  # websockets 是项目已有依赖
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 websockets，请先执行：pip install websockets")

BASE = "https://xuegong.lut.edu.cn"
LIST_URL = f"{BASE}/gzzd.htm"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "get_data"

# 文章链接形如 info/1197/4603.htm。
# 注意：列表页里是**相对地址且没有前导斜杠**（info/... 而不是 /info/...），
# urljoin 之后才变成带斜杠的绝对地址，所以这里绝不能锚定前导斜杠，
# 否则原始 HTML 里一个都匹配不到。
ARTICLE_HREF_RE = re.compile(r"info/\d+/\d+\.htm", re.I)
# 列表/子栏目链接形如 /gzzd.htm、/gzzd/2.htm、/gzzd/xygl.htm
LIST_HREF_RE = re.compile(r"^https?://xuegong\.lut\.edu\.cn/gzzd(/[^?#]*)?\.htm$", re.I)

# 瑞数挑战页的特征，出现任意一个就说明还没拿到真正的页面
CHALLENGE_MARKERS = ("$_ts", "_$mb", "ldedHPE")

# 博达 CMS 常见的正文容器，取其中文字最多的那个
CONTENT_SELECTORS = (
    "#vsb_content",
    ".v_news_content",
    "#vsb_content_2",
    ".TRS_Editor",
    "#div_zhengwen",
    "#content",
    ".content",
    ".news_content",
    "#article",
    ".article",
    ".detail",
    ".show_content",
)

# 详情页里需要丢掉的噪音行
NOISE_PREFIXES = (
    "发布时间：",
    "发布时间:",
    "作　者：",
    "作者：",
    "来源：",
    "浏览次数：",
    "阅读：",
    "字体：",
    "分享到：",
    "上一条：",
    "下一条：",
    "上一篇：",
    "下一篇：",
    "关闭窗口",
    "打印本页",
)

CHAPTER_RE = re.compile(r"^(第[〇零一二三四五六七八九十百千]+章)\s*(.*)$")
ARTICLE_RE = re.compile(r"^(第[〇零一二三四五六七八九十百千]+条)\s*(.*)$")

# 一次性把主页 + 同源 iframe 的 HTML 和链接都取回来：
# 不少高校 CMS 的栏目页是 frameset / iframe 套壳，只读顶层 document 会一个链接都找不到。
SNAPSHOT_JS = r"""
(() => {
  const grab = (doc) => {
    const links = [];
    try {
      doc.querySelectorAll('a[href]').forEach(a => {
        links.push([a.getAttribute('href'), (a.getAttribute('title') || a.textContent || '').trim()]);
      });
    } catch (e) {}
    return links;
  };
  const out = {
    url: location.href,
    title: document.title,
    html: document.documentElement ? document.documentElement.outerHTML : '',
    len: document.documentElement ? document.documentElement.outerHTML.length : 0,
    frames: [],
    links: grab(document)
  };
  for (const f of document.querySelectorAll('iframe,frame')) {
    try {
      const d = f.contentDocument;
      if (d && d.documentElement) {
        out.frames.push(d.documentElement.outerHTML);
      }
    } catch (e) {}
  }
  return JSON.stringify(out);
})()
"""


# --------------------------------------------------------------------------- #
# 浏览器 / CDP
# --------------------------------------------------------------------------- #
def find_browser(explicit: str | None = None) -> str:
    """找一个可用的 Chromium 内核浏览器。"""
    if explicit:
        if Path(explicit).exists():
            return explicit
        sys.exit(f"指定的浏览器不存在：{explicit}")

    env = os.environ.get("BROWSER_PATH")
    if env and Path(env).exists():
        return env

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
    ]
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for path in candidates:
        if Path(path).exists():
            return path
    sys.exit("没找到 Edge/Chrome，请用 --browser 指定可执行文件路径")


class Browser:
    """用 CDP 驱动真实浏览器，绕过瑞数动态防护取回渲染后的 DOM。"""

    def __init__(
        self,
        browser_path: str,
        headless: bool = False,
        port: int = 9333,
        page_timeout: float = 40.0,
    ) -> None:
        self.browser_path = browser_path
        self.headless = headless
        self.port = port
        self.page_timeout = page_timeout
        self.proc: subprocess.Popen | None = None
        self.ws = None
        self._counter = 0
        self._profile = Path(tempfile.mkdtemp(prefix="gzzd_profile_"))

    # -- 生命周期 ---------------------------------------------------------- #
    def start(self) -> None:
        args = [
            self.browser_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self._profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-crash-reporter",
            "--window-size=1440,900",
            "--lang=zh-CN",
            "about:blank",
        ]
        if self.headless:
            args[1:1] = ["--headless=new", "--disable-gpu"]

        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._wait_for_target()

    def _wait_for_target(self) -> str:
        deadline = time.time() + 30
        last_err = None
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(
                    f"浏览器启动后立即退出（code={self.proc.returncode}）。"
                    "若在受限沙箱里运行，Chromium 需要命名管道权限，请在普通环境下执行。"
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2
                ) as resp:
                    targets = json.load(resp)
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_err = exc
            time.sleep(0.3)
        raise RuntimeError(f"等待浏览器调试端口超时：{last_err}")

    def close(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            shutil.rmtree(self._profile, ignore_errors=True)

    # -- CDP 基础能力 ------------------------------------------------------ #
    async def _cmd(self, method: str, timeout: float = 25.0, **params):
        assert self.ws is not None
        self._counter += 1
        mid = self._counter
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remain)
            except (asyncio.TimeoutError, TimeoutError):
                break
            msg = json.loads(raw)
            if msg.get("id") == mid:
                return msg
        return None

    async def _eval(self, expression: str):
        msg = await self._cmd("Runtime.evaluate", expression=expression, returnByValue=True)
        if not msg or "result" not in msg:
            return None
        return (msg["result"].get("result") or {}).get("value")

    async def open(self) -> None:
        ws_url = self._wait_for_target()
        self.ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
        for domain in ("Page.enable", "Runtime.enable"):
            await self._cmd(domain)

    async def snapshot(self) -> dict | None:
        raw = await self._eval(SNAPSHOT_JS)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def fetch(self, url: str, ready=None, timeout: float | None = None) -> dict | None:
        """导航到 url，等瑞数挑战跑完（并且 ready 满足）后返回快照。

        快照里同时带顶层 document 和所有同源 iframe 的 HTML / 链接。
        """
        timeout = timeout or self.page_timeout
        await self._cmd("Page.navigate", url=url)
        deadline = time.time() + timeout
        snap = None
        while time.time() < deadline:
            await asyncio.sleep(0.7)
            current = await self.snapshot()
            if not current:
                continue
            # 导航是异步的，刚发完指令时读到的可能还是上一个页面
            if not same_url(current.get("url", ""), url):
                continue
            snap = current
            blobs = blobs_of(snap)
            if any(looks_like_challenge(b) for b in blobs):
                continue  # 挑战还没跑完
            if ready is None or ready(snap, blobs):
                return snap
        return snap


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def same_url(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        u = u.split("#", 1)[0].split("?", 1)[0]
        return u.rstrip("/")

    return bool(a) and norm(a) == norm(b)


def looks_like_challenge(html: str) -> bool:
    return any(m in html for m in CHALLENGE_MARKERS)


def blobs_of(snap: dict) -> list[str]:
    """顶层 document + 所有同源 iframe 的 HTML。"""
    blobs = [snap.get("html") or ""]
    blobs.extend(snap.get("frames") or [])
    return [b for b in blobs if b]


def clean_list_title(text: str) -> str:
    """列表页的链接文字是「标题+点击数+日期」，例如 '学生伤害事故处理办法222025-02'。

    这只用于列表页的兜底标题；正文页的标题优先取详情页自己的 h1。
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\d{4}-\d{2}(-\d{2})?$", "", text).strip()  # 末尾日期
    text = re.sub(r"\d+$", "", text).strip()  # 末尾点击数
    return text


def safe_filename(title: str) -> str:
    """把标题变成合法的 Windows 文件名。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title)
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    return (name[:100] or "untitled").strip()


def extract_title(soup: BeautifulSoup, fallback: str) -> str:
    for sel in ("h1", ".arti_title", ".article_title", ".title", "#title"):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    raw = soup.title.get_text(strip=True) if soup.title else ""
    if raw:
        # 博达标题常写成「文章标题-兰州理工大学党委学生工作部(处)」
        head = re.split(r"[-—|_]", raw, maxsplit=1)[0].strip()
        if head:
            return head
    return fallback


def find_content(soup: BeautifulSoup):
    """挑出正文容器：优先博达惯用的 id/class，其次取段落文字最多的块。"""
    best, best_len = None, 0
    for sel in CONTENT_SELECTORS:
        for el in soup.select(sel):
            length = len(el.get_text(strip=True))
            if length > best_len:
                best, best_len = el, length
    if best is not None and best_len >= 80:
        return best

    # 退路：只考虑不含其它块级子元素的「叶子」块，避免选中包住整页的最外层 div
    for el in soup.find_all(["div", "article", "section", "td"]):
        if el.find(["div", "article", "section"]):
            continue
        length = len(el.get_text(strip=True))
        if length > best_len:
            best, best_len = el, length
    if best is not None and best_len >= 80:
        return best

    body = soup.body or soup
    for tag in body.find_all(["script", "style", "nav", "header", "footer", "form", "iframe"]):
        tag.decompose()
    return body


def text_lines(container) -> list[str]:
    """把正文块拆成按段落分行的纯文本。"""
    for br in container.find_all("br"):
        br.replace_with("\n")
    for tag in container.find_all(
        ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"]
    ):
        tag.append("\n")

    lines: list[str] = []
    blank = True
    for raw in container.get_text().split("\n"):
        line = re.sub(r"[ \t\u00a0\u3000]+", " ", raw).strip()
        if not line:
            if not blank and lines:
                lines.append("")
            blank = True
            continue
        lines.append(line)
        blank = False
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def to_markdown(title: str, lines: list[str]) -> str:
    """按参考文档的层级规则生成 Markdown。"""
    out: list[str] = [f"# {title}", ""]
    for line in lines:
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        if any(line.startswith(p) for p in NOISE_PREFIXES):
            continue
        if line == title:  # 正文里重复的大标题
            continue

        chapter = CHAPTER_RE.match(line)
        if chapter:
            heading = f"## {chapter.group(1)} {chapter.group(2)}".rstrip()
            out.extend([heading, ""])
            continue

        article = ARTICLE_RE.match(line)
        if article:
            # 与参考文档一致：条号单独成标题，正文另起一段
            out.append(f"### {article.group(1)}")
            if article.group(2):
                out.append(article.group(2))
            out.append("")
            continue

        out.extend([line, ""])

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def best_document(blobs: list[str]):
    """主页 + 各 iframe 中，正文最长的那个才是真正的内容页。"""
    best = None
    best_len = 0
    for blob in blobs:
        soup = BeautifulSoup(blob, "lxml")
        container = find_content(soup)
        length = len(container.get_text(strip=True))
        if length > best_len:
            best, best_len = (soup, container), length
    return best


def html_to_markdown(blobs: list[str], fallback_title: str) -> tuple[str, str]:
    best = best_document(blobs)
    if best is None:
        return fallback_title, f"# {fallback_title}\n"
    soup, container = best
    title = extract_title(soup, fallback_title)
    return title, to_markdown(title, text_lines(container))


def article_ready(snap: dict, blobs: list[str]) -> bool:
    """正文页：容器里已经有像样的文字量就算加载完成。"""
    for blob in blobs:
        soup = BeautifulSoup(blob, "lxml")
        if len(find_content(soup).get_text(strip=True)) >= 200:
            return True
    return False


def list_ready(snap: dict, blobs: list[str]) -> bool:
    """列表页：出现文章链接就算加载完成。

    链接既可能在 HTML 里，也可能是 CDP 从 iframe 里收集来的，两边都要看。
    """
    if any(ARTICLE_HREF_RE.search(b) for b in blobs):
        return True
    return any(ARTICLE_HREF_RE.search(href) for href, _ in snap.get("links") or [])


# --------------------------------------------------------------------------- #
# 抓取流程
# --------------------------------------------------------------------------- #
async def collect_articles(browser: Browser, max_pages: int = 200) -> dict[str, str]:
    """从 /gzzd.htm 出发，翻分页/子栏目，收集所有文章链接。"""
    found: dict[str, str] = {}
    queue = [LIST_URL]
    visited: set[str] = set()

    while queue and len(visited) < max_pages:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        snap = await browser.fetch(page_url, ready=list_ready, timeout=25)
        if not snap:
            print(f"  ! 列表页没拿到内容：{page_url}")
            continue

        blobs = blobs_of(snap)
        if any(looks_like_challenge(b) for b in blobs):
            print(f"  ! 仍在挑战页，放弃：{page_url}")
            continue

        before = len(found)
        for href, title in all_links(snap, page_url):
            if ARTICLE_HREF_RE.search(href):
                if title and href not in found:
                    found[href] = title
            elif LIST_HREF_RE.match(href) and href not in visited:
                queue.append(href)

        print(f"  列表页 {page_url} -> 新增 {len(found) - before} 条（累计 {len(found)}）")

    return found


def all_links(snap: dict, base_url: str) -> list[tuple[str, str]]:
    """顶层 document + 所有 iframe 里的链接（CDP 直接给了一份，这里再兜底解析 HTML）。"""
    pairs: list[tuple[str, str]] = []
    for href, title in snap.get("links") or []:
        if href:
            pairs.append((urljoin(base_url, href), clean_list_title(title)))

    if not pairs:
        for blob in blobs_of(snap):
            soup = BeautifulSoup(blob, "lxml")
            for a in soup.find_all("a", href=True):
                text = a.get("title") or a.get_text(strip=True) or ""
                pairs.append((urljoin(base_url, a["href"]), clean_list_title(text)))
    return pairs


def debug_report(snap: dict, page_url: str, out_dir: Path) -> None:
    """打印列表页结构诊断，并把 HTML 落盘方便排查。"""
    blobs = blobs_of(snap)
    print("\n=== 调试信息 ===")
    print(f"请求地址   : {page_url}")
    print(f"实际地址   : {snap.get('url')}")
    print(f"document.title: {snap.get('title')!r}")
    print(f"顶层 HTML  : {snap.get('len')} 字符")
    print(f"同源 iframe: {len(snap.get('frames') or [])} 个")
    for i, frame in enumerate(snap.get("frames") or [], 1):
        print(f"    frame{i}: {len(frame)} 字符")
    print(f"CDP 收集到链接: {len(snap.get('links') or [])} 个")

    soup = BeautifulSoup(blobs[0], "lxml")
    anchors = soup.find_all("a", href=True)
    print(f"顶层 <a> 标签 : {len(anchors)} 个")
    hits = [a for a in anchors if ARTICLE_HREF_RE.search(a["href"])]
    print(f"匹配文章规律  : {len(hits)} 个")

    print("--- 前 30 个 href ---")
    for a in anchors[:30]:
        print(f"    {a['href'][:110]}  |  {a.get_text(strip=True)[:30]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "list_top.html").write_text(blobs[0], encoding="utf-8")
    for i, frame in enumerate(snap.get("frames") or [], 1):
        (out_dir / f"list_frame{i}.html").write_text(frame, encoding="utf-8")
    print(f"\nHTML 已保存到 {out_dir}（list_top.html / list_frame*.html）")


def analyze_file(path: Path) -> int:
    """离线分析已保存的 HTML，用来判断列表链接到底是什么形式。"""
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    print(f"文件      : {path}（{len(html)} 字符）")
    print(f"title     : {title!r}")

    anchors = soup.find_all("a", href=True)
    uniq = list(dict.fromkeys(a["href"] for a in anchors))
    print(f"<a> 标签  : {len(anchors)} 个，去重后 {len(uniq)} 个 href")

    print("\n=== 全部 href（去重，按出现顺序）===")
    seen = set()
    for a in anchors:
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        text = (a.get("title") or a.get_text(strip=True) or "").strip()
        print(f"  {href}   |  {text[:28]}")

    body = BeautifulSoup(html, "lxml").body or soup
    for tag in body.find_all(["script", "style"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", body.get_text(" ", strip=True))
    print(f"\n=== 可见文字（前 600 字，共 {len(text)} 字）===")
    print(text[:600])

    print("\n=== 关键线索 ===")
    low = html.lower()
    for kw in ("/info/", "info/", "ajax", "vsb", "vsb_content", "json", "iframe", "loaddata"):
        print(f"  原文出现 {kw!r}: {low.count(kw.lower())} 次")
    scripts = [s.get("src") for s in BeautifulSoup(html, "lxml").find_all("script") if s.get("src")]
    print(f"  外链脚本: {len(scripts)} 个")
    for src in scripts[:10]:
        print(f"    {src[:110]}")
    return 0


async def run(args: argparse.Namespace) -> int:
    browser = Browser(
        find_browser(args.browser),
        headless=args.headless,
        port=args.port,
    )
    print(f"浏览器：{browser.browser_path}")
    print(f"模式：{'无头' if args.headless else '有窗口'}")

    browser.start()
    try:
        await browser.open()

        if args.debug:
            print(f"读取列表页：{LIST_URL}")
            snap = await browser.fetch(LIST_URL, ready=list_ready, timeout=30)
            if not snap:
                print("列表页没有返回任何内容。")
                return 1
            debug_report(snap, LIST_URL, Path("_debug"))
            return 0

        print(f"读取列表页：{LIST_URL}")
        articles = await collect_articles(browser)
        print(f"共找到 {len(articles)} 篇规章制度")

        if not articles:
            print(
                "没有解析到任何文章。请先跑一次结构诊断，把结果发出来：\n"
                "    python getting_data.py --debug"
            )
            return 1

        out_dir: Path = args.out
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.list:
            for url, title in articles.items():
                print(f"  {title}\t{url}")
            return 0

        items = list(articles.items())[: args.limit] if args.limit else list(articles.items())
        written = 0
        for index, (url, list_title) in enumerate(items, 1):
            snap = await browser.fetch(url, ready=article_ready)
            blobs = blobs_of(snap) if snap else []
            if not blobs or any(looks_like_challenge(b) for b in blobs):
                print(f"[{index}/{len(items)}] 跳过（未取到正文）：{list_title}")
                continue

            title, markdown = html_to_markdown(blobs, list_title)
            path = out_dir / f"{safe_filename(title)}.md"
            path.write_text(markdown, encoding="utf-8")
            written += 1
            print(f"[{index}/{len(items)}] {path.name}")

        print(f"\n完成：写出 {written}/{len(items)} 篇到 {out_dir}")
        return 0 if written else 1
    finally:
        if browser.ws is not None:
            await browser.ws.close()
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取 LUT 学工部规章制度并转成 Markdown")
    parser.add_argument("--headless", action="store_true", help="无头模式（更容易被站点拦截）")
    parser.add_argument("--debug", action="store_true", help="只抓列表页并打印结构诊断")
    parser.add_argument("--analyze", type=Path, default=None, help="离线分析已保存的 HTML 文件")
    parser.add_argument("--list", action="store_true", help="只列出文章，不写文件")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="CDP 调试端口")
    parser.add_argument("--browser", default=None, help="浏览器可执行文件路径")
    args = parser.parse_args()

    if args.analyze:
        if not args.analyze.exists():
            sys.exit(f"文件不存在：{args.analyze}")
        sys.exit(analyze_file(args.analyze))

    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit("已中断")


if __name__ == "__main__":
    main()
