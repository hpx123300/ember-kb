"""问答流程测试：拒答分支、引用映射、结构化校验。

LLM 全部 mock：这一层要测的是「流程判断」和「引用是否可信」，
模型答得好不好属于评测集的事，不在单元测试里重复。
"""

from backend.app import flow


def _fake_stream(answer: str):
    def fake(prompt, system, temperature=0.3):
        yield answer

    return fake


def test_out_of_scope_question_is_refused(monkeypatch):
    """语料外问题必须拒答，且不调用模型——拒答判定在代码里，不靠模型自觉。"""
    called = {"n": 0}

    def boom(prompt, system, temperature=0.3):
        called["n"] += 1
        yield "不该被调用"

    monkeypatch.setattr("backend.app.flow.llm.chat_text_stream", boom)
    result = flow.ask("公司股票什么时候上市？")
    assert result["refused"] is True
    assert called["n"] == 0
    assert "没有找到明确依据" in result["answer"]


def test_in_scope_answer_keeps_citations(monkeypatch):
    """答案里的 [n] 要能映射回真实来源，映射不到的编号不返回。"""
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_json",
        lambda prompt, system, temperature=0.0: {"can_answer": True, "reason": "资料写了"},
    )
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_text_stream",
        _fake_stream("满 1 年 5 天 [1]，满 3 年 7 天 [2]。"),
    )
    result = flow.ask("年假有几天？")
    assert result["refused"] is False
    assert [c["index"] for c in result["citations"]] == [1, 2]
    assert all(c["cite"].startswith(c["source"] + "#") for c in result["citations"])


def test_unknown_citation_index_is_dropped(monkeypatch):
    """模型编出 [9] 这类不存在的编号时，不能凭空造一条引用。"""
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_json",
        lambda prompt, system, temperature=0.0: {"can_answer": True, "reason": "资料写了"},
    )
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_text_stream",
        _fake_stream("年假 5 天 [1]，另外还有补贴 [9]。"),
    )
    result = flow.ask("年假有几天？")
    assert [c["index"] for c in result["citations"]] == [1]


def test_coverage_is_bounded():
    """覆盖率是 0–1 的绝对量，阈值才能固定下来。"""
    chunks = [{"title": "年假", "text": "入职满 1 年 5 天年假，几天可以申请"}]
    assert flow.coverage("年假几天", chunks) == 1.0
    assert flow.coverage("股票上市时间", chunks) == 0.0


def test_domain_term_survives_tokenization():
    """jieba 不认识的领域词（年假）要能被合并回来，否则好问题会被误拒。"""
    assert "年假" in flow._keywords("年假有几天？")
    # 「基础版」会被切成「基础/版」，留下「基础」已足够命中定价章节，
    # 这里不追求还原原词，只要求别把领域词整个丢掉
    assert "基础" in flow._keywords("基础版多少钱")


def test_category_is_respected(monkeypatch):
    """指定类别后，引用只能来自该类别。"""
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_json",
        lambda prompt, system, temperature=0.0: {"can_answer": True, "reason": "资料写了"},
    )
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_text_stream",
        _fake_stream("按资料回答 [1]。"),
    )
    result = flow.ask("基础版多少钱", category="产品")
    assert result["refused"] is False
    assert all(c["source"] == "product-overview.md" for c in result["citations"])


def test_judge_can_refuse_even_with_high_coverage(monkeypatch):
    """关键词沾边但资料答不了时，判官要能拦下来——这是「竞争对手定价」那类题。"""
    monkeypatch.setattr(
        "backend.app.flow.llm.chat_json",
        lambda prompt, system, temperature=0.0: {"can_answer": False, "reason": "资料是自家产品，不是竞争对手"},
    )
    monkeypatch.setattr("backend.app.flow.llm.chat_text_stream", _fake_stream("不该出现"))
    result = flow.ask("竞争对手的产品定价是多少？")
    assert result["refused"] is True
    assert result["answer"] == flow.REFUSE_MESSAGE
