"""检索层测试：元信息解析、分块、按类别过滤、引用定位。"""

from backend.app.agent.rag import _parse_frontmatter, _split_chunks, knowledge_base


def test_frontmatter_parsed_out_of_body():
    """元信息不进入正文，否则会污染关键词得分。"""
    raw = "---\ncategory: 制度\naudience: 全员\n---\n# 标题\n\n正文内容"
    meta, body = _parse_frontmatter(raw)
    assert meta["category"] == "制度"
    assert meta["audience"] == "全员"
    assert body.startswith("# 标题")
    assert "category" not in body


def test_split_by_headers():
    chunks = _split_chunks("# 一\n内容一\n## 二\n内容二", "doc.md", {"category": "制度"})
    assert [c["title"] for c in chunks] == ["一", "二"]  # H1 本身也是一节
    assert all(c["cite"].startswith("doc.md#") for c in chunks)


def test_chunks_carry_metadata_and_citation():
    knowledge_base.load()
    chunk = knowledge_base.chunks[0]
    assert chunk["meta"].get("category")
    assert chunk["cite"].startswith(chunk["source"] + "#")


def test_category_filter_excludes_other_categories():
    """指定类别后，结果里不能出现别的类别——这是「不串部门」的硬约束。"""
    results = knowledge_base.search("定价 多少钱", k=4, category="产品")
    assert results, "产品类目下应该能召回定价内容"
    assert all(r["meta"]["category"] == "产品" for r in results)


def test_category_filter_keeps_recall_within_category():
    """过滤不能把本该命中的同类别内容筛掉。"""
    results = knowledge_base.search("年假 几天", k=3, category="制度")
    titles = [r["title"] for r in results]
    assert any("年假" in t for t in titles)


def test_keyword_recall_hits_expected_doc():
    results = knowledge_base.search("住宿标准 一线城市", k=3)
    assert any(r["source"] == "handbook-expense.md" for r in results)


def test_unknown_category_returns_empty():
    """不存在的类别应返回空，而不是悄悄退回全库。"""
    assert knowledge_base.search("年假", k=3, category="不存在的类别") == []
