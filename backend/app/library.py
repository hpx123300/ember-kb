"""知识库资料管理：列表、上传、删除、重建索引。

只允许 Markdown，且必须带 category 元信息——没有类别的文档会破坏
「按类别检索」，宁可拒绝上传也不要让脏数据进库。
"""

import re
from datetime import datetime
from pathlib import Path

from .agent.rag import _parse_frontmatter, knowledge_base
from .config import KNOWLEDGE_DIR, ensure_dirs

ALLOWED_SUFFIX = ".md"
MAX_BYTES = 200_000
_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]{1,64}\.md$")


def _safe_path(filename: str) -> Path:
    """只允许纯文件名，挡住 ../ 之类的路径穿越。"""
    raw = filename or ""
    # 带路径分隔符的一律拒绝，而不是「取 basename 后放行」——
    # 后者虽然也安全，但会让调用方误以为自己的路径被接受了。
    if "/" in raw or "\\" in raw or raw != Path(raw).name:
        raise ValueError("文件名不能包含路径")
    if not _NAME_RE.match(raw):
        raise ValueError("文件名只能包含中英文、数字、下划线和短横线，且必须以 .md 结尾")
    return KNOWLEDGE_DIR / raw


def list_docs() -> list[dict]:
    ensure_dirs()
    knowledge_base.load()
    counts: dict[str, int] = {}
    for chunk in knowledge_base.chunks:
        counts[chunk["source"]] = counts.get(chunk["source"], 0) + 1

    docs = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        docs.append(
            {
                "filename": path.name,
                "title": _title_of(body, path.name),
                "category": meta.get("category", ""),
                "audience": meta.get("audience", ""),
                "sections": counts.get(path.name, 0),
                "chars": len(body),
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return docs


def _title_of(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback.replace(".md", "")


def save_doc(filename: str, content: str) -> dict:
    path = _safe_path(filename)
    data = (content or "").strip()
    if not data:
        raise ValueError("内容不能为空")
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise ValueError("文档过大，请拆分后再上传")
    meta, body = _parse_frontmatter(data)
    if not meta.get("category"):
        raise ValueError("文档头部需要 category 元信息，例如：\n---\ncategory: 制度\n---")
    if not body.strip():
        raise ValueError("元信息之后没有正文")
    path.write_text(data + "\n", encoding="utf-8")
    knowledge_base.load()  # 指纹变化会自动重建索引
    return {"filename": path.name, "sections": len([c for c in knowledge_base.chunks if c["source"] == path.name])}


def delete_doc(filename: str) -> None:
    path = _safe_path(filename)
    if not path.exists():
        raise ValueError("文档不存在")
    path.unlink()
    knowledge_base.load()


def stats() -> dict:
    knowledge_base.load()
    docs = list_docs()
    return {
        "docs": len(docs),
        "chunks": len(knowledge_base.chunks),
        "categories": knowledge_base.categories(),
        "chars": sum(d["chars"] for d in docs),
    }
