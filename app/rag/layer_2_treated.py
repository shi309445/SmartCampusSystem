import re
from dataclasses import dataclass, field

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
import loguru




# 2.3 切分参数（单位是字符；中文约 1 字 ≈ 1 token）
CHILD_MAX_CHARS = 400     # 子块上限：拿去向量检索，越小越精准
PARENT_MAX_CHARS = 2000   # 父块上限：只做上下文，不参与检索
CHILD_OVERLAP = 60        # 仅用于「超长条款被硬切」时的重叠
MIN_CHUNK_CHARS = 100     # 硬切出的碎块下限，低于它就并进邻块

# 中文断句分隔符：默认分隔符按空格切，中文长段根本切不开
SEPARATORS = ["\n### ", "\n## ", "\n\n", "\n", "。", "！", "？", "；", "：", "，", "、", " ", ""]

# md 标题层级 → 「文档 / 章 / 条」
MD_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]

# 结构锚点：「第X条 / 第X章 / 第X节 / 第X款」
ANCHOR_RE = re.compile(r"第[一二三四五六七八九十百千零〇0-9]+[条章节款项]")

# 硬切会把上一句的句末标点推到续块开头，生成续块时抹掉
LEADING_PUNCT = "。！？；：，、"


@dataclass
class _Section:
    """一个「条」级片段"""

    text: str
    headers: dict[str, str]  # h1 / h2 / h3，缺的补空串（元数据键必须齐）
    anchor: str              # 结构锚点，如「第四十三条」


def _clean_text(text: str) -> str:
    """2.1 清洗：归一化空白、去 HTML 注释、图片语法只留 alt"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)          # HTML 注释
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)       # 图片只留 alt，去掉路径噪声
    text = re.sub(r"[ \t\u3000]+$", "", text, flags=re.M)       # 行尾空白（含全角）
    text = re.sub(r"^[ \t\u3000]+", "", text, flags=re.M)       # 行首缩进（中文常用全角空格）
    text = re.sub(r"\n{3,}", "\n\n", text)                      # 3 个以上换行压成 1 个空行
    return text.strip()


def _anchor(heading: str) -> str:
    """从标题里抽出结构锚点；抽不到就截断保留原文"""
    heading = (heading or "").strip()
    match = ANCHOR_RE.match(heading)
    return match.group(0) if match else heading[:60]


def _split_sections(text: str, splitter: MarkdownHeaderTextSplitter) -> list[_Section]:
    """2.3 结构切分：按 md 标题层级切出「条」级片段

    注意 strip_headers=False：规章类文档常把整条正文写在标题行上，
    剥掉标题就会连条文一起丢掉（实测同一份文档只剩 38.7% 正文）。
    """
    sections: list[_Section] = []
    for doc in splitter.split_text(text):
        headers = {name: doc.metadata.get(name, "") for name in ("h1", "h2", "h3")}
        body = doc.page_content.strip()
        if not body:
            continue
        sections.append(
            _Section(body, headers, _anchor(headers["h3"]) or _anchor(headers["h2"]))
        )
    return sections


def _pack(pieces: list[str], min_chars: int) -> list[str]:
    """把过小的片段并进邻块：硬切可能切出只含标题的碎块"""
    blocks: list[str] = []
    buffer = ""
    for piece in pieces:
        buffer = f"{buffer}\n{piece}" if buffer else piece
        if len(buffer) >= min_chars:
            blocks.append(buffer)
            buffer = ""
    if buffer:
        if blocks:
            blocks[-1] = f"{blocks[-1]}\n{buffer}"
        else:
            blocks.append(buffer)
    return blocks


def _enforce_max(sections: list[_Section], splitter) -> list[_Section]:
    """超长片段按字符硬切；续块补回结构锚点，否则「第X条」的后半段会脱离上下文"""
    result: list[_Section] = []
    for section in sections:
        if len(section.text) <= CHILD_MAX_CHARS:
            result.append(section)
            continue
        for index, block in enumerate(_pack(splitter.split_text(section.text), MIN_CHUNK_CHARS)):
            if index == 0:
                text = block
            else:
                body = block.lstrip(LEADING_PUNCT)
                text = f"【{section.anchor}】\n{body}" if section.anchor else body
            result.append(_Section(text, section.headers, section.anchor))
    return result


def _group_parents(sections: list[_Section], parent_max: int) -> list[list[_Section]]:
    """把「条」按章聚合成父块：换章就换父块，同章内超过上限也换，保证父块不超长"""
    groups: list[list[_Section]] = []
    current: list[_Section] = []
    current_len = 0
    current_chapter: str | None = None

    for section in sections:
        chapter = section.headers.get("h2", "")
        if current and (chapter != current_chapter or current_len + len(section.text) > parent_max):
            groups.append(current)
            current, current_len = [], 0
        current.append(section)
        current_len += len(section.text)
        current_chapter = chapter

    if current:
        groups.append(current)
    return groups


def _build_chunk(
    text: str,
    *,
    base: dict[str, str],
    doc_id: str,
    parent_id: str,
    level: str,
    index: int,
    doc_title: str,
    chapter: str,
    article: str,
) -> Document:
    """父块与子块的唯一出口：保证两者的元数据键集合完全一致

    第 3 层建 Milvus schema 时，所有块必须是同一套键；这里统一出口是为了
    不让「某个字段只有子块有」这种事漏出去。
    """
    return Document(
        page_content=text,
        metadata={
            **base,               # source / kb / file_name（第 1 层的文件身份）
            "doc_title": doc_title,   # 文档名 → 「依据《XXX规定》」
            "chapter": chapter,       # 章   → 「第四章 校园秩序与课外活动」
            "article": article,       # 条   → 「第四十三条」（父块为空串）
            "doc_id": doc_id,
            "parent_id": parent_id,   # 父块指向自己，保持键集合一致
            "chunk_level": level,
            "chunk_index": index,
        },
    )


def data_treating(docs: list[Document]) -> tuple[list[Document], list[Document]]:
    """第 2 层：清洗 → 结构切分 → 父子块 → 标注

    :param docs: get_data() 返回的 Document 列表
    :return: (parents, children) —— 父块给上下文，子块给检索
    """
    if not docs:
        return [], []

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=MD_HEADERS,
        strip_headers=False,  # 必须 False，理由见 _split_sections
    )
    hard_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_MAX_CHARS,
        chunk_overlap=CHILD_OVERLAP,
        separators=SEPARATORS,
    )

    parents: list[Document] = []
    children: list[Document] = []

    for doc in docs:
        base = {key: doc.metadata.get(key, "") for key in ("source", "kb", "file_name")}
        cleaned = _clean_text(doc.page_content)
        if not cleaned:
            loguru.logger.warning("清洗后为空，跳过：{}", base["source"])
            continue

        sections = _enforce_max(_split_sections(cleaned, header_splitter), hard_splitter)
        for p_index, group in enumerate(_group_parents(sections, PARENT_MAX_CHARS)):
            parent_id = f"{base['source']}::p{p_index}"
            parents.append(
                _build_chunk(
                    "\n\n".join(section.text for section in group),
                    base=base, doc_id=parent_id, parent_id=parent_id,
                    level="parent", index=p_index,
                    doc_title=group[0].headers["h1"], chapter=group[0].headers["h2"],
                    article="",
                )
            )
            for c_index, section in enumerate(group):
                children.append(
                    _build_chunk(
                        section.text,
                        base=base, doc_id=f"{parent_id}::c{c_index}", parent_id=parent_id,
                        level="child", index=c_index,
                        doc_title=section.headers["h1"], chapter=section.headers["h2"],
                        article=section.anchor,
                    )
                )

    loguru.logger.info(
        "切分完成：父块 {} 个 / 子块 {} 个（平均 {} / {} 字）",
        len(parents), len(children),
        (sum(len(p.page_content) for p in parents) // len(parents)) if parents else 0,
        (sum(len(c.page_content) for c in children) // len(children)) if children else 0,
    )
    return parents, children