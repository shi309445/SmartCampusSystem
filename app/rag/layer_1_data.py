"""RAG 建库 —— 第 1 层：数据层

对齐 data/RAG 项目六层总图.md 的第 1 层：
    1.1 来源识别 / 1.2 接入 / 1.3 采集 / 1.4 登记 / 1.5 治理

约定（第 3 层是「契约面」，离线在线两侧必须一致）：
    - 语料根目录：项目根/docs，目录下**只允许 md**，其余格式在外部先转成 md
    - docs/ 的**一级目录 = 知识库标识（kb）**，不是存储分区，也不做检索过滤
    - 本层只产出**文件级身份元数据**：source / kb / file_name
    - 块级元数据（doc_id / parent_id / chunk_level / chunk_index / start_index）
      由第 2 层 data_treating() 追加

为什么不用 langchain-community 的 DirectoryLoader：
    - 它已进入 sunset，导入即告警「no longer actively maintained」
    - 它的默认 loader_cls 是 UnstructuredFileLoader，读个 md 要拖进 unstructured
      及其系统依赖（poppler / tesseract），不划算
    这里直接用 langchain_core 的 Document，读文件、编码回落、元数据都自己控制。
"""

from __future__ import annotations

import hashlib
import loguru
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document

logger = loguru.logger.bind(name=__name__)

# ------------------------------------------------------------------ 1.1 来源识别

# 语料根目录：项目根/docs（app/rag/groundwork.py 上溯三级）
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

# 允许入库的文本格式，其余格式请在外部转换
TEXT_SUFFIXES = {".md", ".markdown"}

# md 里引用的图片等附件：静默忽略，不算「未转换」
ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico", ".avif",
}

# 文件直接放在 docs/ 根下（没有一级目录）时使用的知识库名
DEFAULT_KB = "未分类"

# 解码顺序：utf-8-sig 同时兼容带 BOM 与不带 BOM 的 utf-8；gb18030 兜住老文件
ENCODINGS = ("utf-8-sig", "gb18030")

# 去空白后字数少于该值，视为可疑文件（空文件 / 只有标题的占位文件）
MIN_CHARS = 20


# ------------------------------------------------------------------ 登记与报告


@dataclass
class SourceFile:
    """登记条目（1.4 登记）：一个待入库的 md 文件"""

    path: Path       # 绝对路径，用于实际读取
    source: str      # 相对 docs 的 posix 路径，入库后的稳定文件身份
    kb: str          # 一级目录名（知识库标识）
    size: int        # 字节数
    char_count: int  # 字符数
    digest: str      # 正文 sha256 前 16 位，用于查重
    encoding: str    # 实际解码成功的编码


@dataclass
class DocReport:
    """体检报告（1.5 治理）：采集前的登记与质量问题清单"""

    root: Path
    files: list[SourceFile] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)                  # 忽略的图片等附件
    unconverted: list[str] = field(default_factory=list)             # 非 md 非附件
    too_short: list[str] = field(default_factory=list)               # 字数过少
    duplicates: list[tuple[str, str]] = field(default_factory=list)   # (重复文件, 首次出现)
    unreadable: list[tuple[str, str]] = field(default_factory=list)   # (读不了的文件, 原因)

    @property
    def total_chars(self) -> int:
        return sum(f.char_count for f in self.files)

    def kb_stats(self) -> dict[str, int]:
        """每个知识库的文件数，按文件数降序"""
        stats: dict[str, int] = {}
        for entry in self.files:
            stats[entry.kb] = stats.get(entry.kb, 0) + 1
        return dict(sorted(stats.items(), key=lambda kv: (-kv[1], kv[0])))

    def summary(self) -> str:
        """给人看的单页体检结论，管理接口可直接打印"""
        lines = ["语料体检（1.4 登记 / 1.5 治理）", f"  根目录  ：{self.root}"]
        if self.files:
            lines.append(
                f"  待入库  ：{len(self.files)} 个 md，共 {self.total_chars:,} 字"
                f"（平均 {self.total_chars // len(self.files):,} 字/文件）"
            )
            lines.append(
                "  知识库  ：" + " / ".join(f"{k} {v} 个" for k, v in self.kb_stats().items())
            )
        else:
            lines.append("  待入库  ：0 个 md")
        if self.assets:
            lines.append(f"  忽略附件：{len(self.assets)} 个（图片等，md 引用用）")
        if self.too_short:
            lines.append(f"  ! 字数过少：{len(self.too_short)} 个 → {self.too_short}")
        if self.duplicates:
            pairs = "；".join(f"{a} == {b}" for a, b in self.duplicates)
            lines.append(f"  ! 内容重复：{len(self.duplicates)} 组 → {pairs}")
        if self.unconverted:
            lines.append(
                f"  X 未转换  ：{len(self.unconverted)} 个非 md 非附件 → {self.unconverted}"
            )
        if self.unreadable:
            detail = "；".join(f"{name}（{why}）" for name, why in self.unreadable)
            lines.append(f"  X 读不了  ：{len(self.unreadable)} 个 → {detail}")
        return "\n".join(lines)


# ------------------------------------------------------------------ 接入与采集


def _read_text(path: Path) -> tuple[str, str]:
    """按 ENCODINGS 顺序解码，返回 (正文, 实际编码)

    utf-8-sig 能同时吃掉带 BOM 和不带 BOM 的 utf-8 —— 带 BOM 的文件若用 utf-8 读，
    首行会留下 \\ufeff，污染第一个文本块。
    """
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"{path} 无法用 {' / '.join(ENCODINGS)} 解码，请确认编码后重新导出为 utf-8"
    )


def _iter_candidate_files(root: Path):
    """1.1 来源识别：遍历 docs，跳过隐藏文件和 Office 临时文件"""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") or name.startswith("~$"):  # 隐藏文件 / Word 临时文件
            continue
        yield path


def scan_docs(root: Path | str = DOCS_DIR) -> DocReport:
    """1.4 登记 + 1.5 治理：登记所有 md，并给出体检报告

    :param root: 语料根目录，默认项目根/docs
    :return: DocReport —— 登记条目 + 质量问题清单
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"语料目录不存在：{root}")

    report = DocReport(root=root)
    seen: dict[str, str] = {}  # digest -> 首次出现的 source

    for path in _iter_candidate_files(root):
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()

        if suffix not in TEXT_SUFFIXES:
            if suffix in ASSET_SUFFIXES:
                report.assets.append(rel)
            else:
                report.unconverted.append(rel)
            continue

        try:
            text, encoding = _read_text(path)
        except OSError as exc:
            # 治理阶段只登记问题、不中断：否则一个坏文件会让整份报告都拿不到
            report.unreadable.append((rel, f"读文件失败：{exc.strerror or exc}"))
            continue
        except ValueError:  # UnicodeDecodeError 是其子类
            report.unreadable.append(
                (rel, f"编码无法识别（试过 {' / '.join(ENCODINGS)}）")
            )
            continue

        parts = path.relative_to(root).parts
        entry = SourceFile(
            path=path,
            source=rel,
            kb=parts[0] if len(parts) > 1 else DEFAULT_KB,
            size=path.stat().st_size,
            char_count=len(text),
            digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            encoding=encoding,
        )
        report.files.append(entry)

        if len(text.strip()) < MIN_CHARS:
            report.too_short.append(rel)
        if entry.digest in seen:
            report.duplicates.append((rel, seen[entry.digest]))
        else:
            seen[entry.digest] = rel

    return report


def get_data(root: Path | str = DOCS_DIR, report: DocReport | None = None) -> list[Document]:
    """1.3 采集：把 docs 下所有 md 读成 Document 列表

    :param root: 语料根目录，默认项目根/docs
    :param report: 复用已有的体检报告（管理接口可以先体检再入库，省一次扫描）
    :return: 每个 md 一个 Document，metadata 只带文件级身份 source / kb / file_name
    """
    report = report or scan_docs(root)

    # 违反「系统内只留 md」的约定就直接拦住 —— 静默忽略等于静默丢内容
    if report.unconverted:
        raise ValueError(
            f"docs 下存在 {len(report.unconverted)} 个非 md 文件，请先在外部转成 md；"
            f"若确认是无需入库的附件，把它加进 ASSET_SUFFIXES：{report.unconverted}"
        )
    # 读不了的文件同样会静默丢内容，一并拦住
    if report.unreadable:
        raise ValueError(
            f"docs 下有 {len(report.unreadable)} 个 md 读不了，请先修复：{report.unreadable}"
        )
    if not report.files:
        raise FileNotFoundError(f"{report.root} 下没有找到任何 md 文件")

    # 这两个是质量问题，不拦入库，但要留痕（真正的去重属于第 2 层清洗）
    if report.too_short:
        logger.warning("字数过少的文件（可能没有内容）：{}", report.too_short)
    if report.duplicates:
        logger.warning("内容重复的文件（可能是同一份文件的两个副本）：{}", report.duplicates)

    docs: list[Document] = []
    for entry in report.files:
        text, _ = _read_text(entry.path)
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "source": entry.source,      # 相对路径：换机器、换部署目录都不失效
                    "kb": entry.kb,              # 一级目录 = 知识库标识
                    "file_name": entry.path.name,
                },
            )
        )

    logger.info(
        "已采集 {} 个 md，共 {} 字（{}）",
        len(docs),
        report.total_chars,
        " / ".join(f"{k} {v} 个" for k, v in report.kb_stats().items()),
    )
    return docs