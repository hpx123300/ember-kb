"""工具自愈测试：检索空手而归时，工具自己要能降级重试。

为什么这一层值得单独测：Agent 的鲁棒性不能寄希望于「模型会换个说法再试」——
模型经常不重试，重试也未必换得对。降级必须发生在代码里，而且必须是确定的：
同样的输入，走同样的路径，有同样的可观测信号。

这三个用例分别覆盖三段降级：放宽类别 -> 换关键词 -> 认输并给提示。
"""

import json

from backend.app import tools, training
from backend.app.runtime import user_scope


def _encode(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_category_fallback_to_whole_library(monkeypatch):
    """模型把类别猜错时，应该放宽到全库再查一次，而不是直接说没找到。"""
    calls = []

    def fake_search(query, k=4, category=None, audiences=None):
        calls.append({"query": query, "category": category})
        if category:
            return []
        return [{"title": "考勤与假期", "source": "handbook-attendance.md",
                 "cite": "handbook-attendance.md#年假", "text": "满 1 年 5 天"}]

    monkeypatch.setattr(tools.knowledge_base, "search", fake_search)

    payload = json.loads(tools.search_knowledge("年假有几天", category="销售"))

    assert payload["results"], "放宽类别后应该拿到结果"
    assert "放宽到全库" in payload["degraded"]
    assert len(calls) == 2, "应该只多花一次检索，不要反复重试"


def test_keyword_rewrite_when_sentence_misses(monkeypatch):
    """整句检索不到、又没有类别可放宽时，压成关键词再试一次。"""
    calls = []

    def fake_search(query, k=4, category=None, audiences=None):
        calls.append(query)
        if " " in query:  # 关键词化之后的形态
            return [{"title": "报销", "source": "handbook-expense.md",
                     "cite": "handbook-expense.md#标准", "text": "住宿每晚上限 600 元"}]
        return []

    monkeypatch.setattr(tools.knowledge_base, "search", fake_search)

    payload = json.loads(tools.search_knowledge("请问出差住宿的报销标准到底是多少呀"))

    assert payload["results"]
    assert "关键词" in payload["degraded"]
    # 第一次原句，第二次关键词，正好两次
    assert calls[0] != calls[-1]


def test_gives_up_with_hint_not_silence(monkeypatch):
    """三段都空才认输，而且必须给模型一个可执行的下一步，而不是一句"没找到"。"""
    monkeypatch.setattr(tools.knowledge_base, "search", lambda *a, **kw: [])

    payload = json.loads(tools.search_knowledge("公司股票什么时候上市"))

    assert payload["results"] == []
    assert payload["hint"], "空结果必须带 hint，否则模型会拿空白去编答案"


def test_success_leaves_degraded_null(monkeypatch):
    """正常命中时不能标记降级，否则「降级率」这个指标就废了。"""
    monkeypatch.setattr(
        tools.knowledge_base, "search",
        lambda *a, **kw: [{"title": "考勤", "source": "handbook-attendance.md",
                           "cite": "handbook-attendance.md#年假", "text": "满 1 年 5 天"}],
    )

    payload = json.loads(tools.search_knowledge("年假有几天"))

    assert payload["results"]
    assert payload["degraded"] is None


def test_make_quiz_rejects_foreign_department(monkeypatch):
    """销售不能通过教练工具给客服专属章节出题，否则部门过滤只保护了问答入口。"""
    called = {"quiz": False}

    def fake_quiz(*args, **kwargs):
        called["quiz"] = True
        return [{"question": "客服题库", "options": ["A", "B", "C"], "answer_index": 0,
                 "explanation": "e", "knowledge_point": "k"}]

    monkeypatch.setattr(training, "generate_quiz", fake_quiz)

    with user_scope("sales", {"全员", "新员工", "销售"}):
        result = tools.make_quiz("support-faq.md", 1)

    assert "没有找到这一章" in result
    assert called["quiz"] is False


def test_get_progress_counts_only_visible_chapters():
    """教练进度工具必须和章节列表使用相同的 13 个销售可见章节。"""
    with user_scope("sales", {"全员", "新员工", "销售"}):
        payload = json.loads(tools.get_progress())

    assert payload["总章节"] == 13
    assert "客户常见问题" not in payload["未开始"]
