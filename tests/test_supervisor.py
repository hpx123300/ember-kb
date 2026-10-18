"""多 Agent 协作测试：路由、兜底、链路。

重点不是「能回答」，而是三件事：
1. 路由错了不能把请求卡住（模型不可用要有关键词兜底）；
2. 学情数字不能由模型生成；
3. 每次协作都要留下可回放的链路。
"""

from backend.app import observability, supervisor


def test_fallback_route_by_keyword():
    assert supervisor._fallback_route("我学到哪了") == "progress"
    assert supervisor._fallback_route("考考我考勤那章") == "coach"
    assert supervisor._fallback_route("年假有几天") == "knowledge"


def test_route_uses_model_when_available(monkeypatch):
    monkeypatch.setattr(
        "backend.app.llm.chat_json",
        lambda prompt, system, temperature=0.0, **kw: {"agent": "coach", "reason": "要练习"},
    )
    agent, reason = supervisor.route("随便问问")
    assert agent == "coach"
    assert reason == "要练习"


def test_route_falls_back_when_model_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("backend.app.llm.chat_json", boom)
    agent, reason = supervisor.route("我哪章最弱")
    assert agent == "progress"
    assert "兜底" in reason


def test_progress_answer_is_computed_not_generated(monkeypatch):
    """学情走数据库聚合，模型不参与——数字错了没人能解释。"""

    def boom(*args, **kwargs):
        raise RuntimeError("不应被调用")

    monkeypatch.setattr("backend.app.llm.chat_json", boom)
    result = supervisor.answer("我学到哪了", employee="nobody-xyz")
    assert result["agent"] == "学情助手"
    # 数字来自库：新用户应为 0 通过，文案里直接说清楚还没开始
    assert "还没有开始" in result["answer"]
    assert result["citations"] == []


def test_answer_records_trace(monkeypatch):
    monkeypatch.setattr(
        "backend.app.llm.chat_json",
        lambda prompt, system, temperature=0.0, **kw: {"agent": "progress", "reason": "问进度"},
    )
    result = supervisor.answer("我还差哪些没学", employee="trace-user")
    assert result["trace_id"]
    steps = [s["name"] for s in result["steps"]]
    assert "route" in steps
    assert any(s.startswith("dispatch:") for s in steps)

    saved = observability.get_trace(result["trace_id"])
    assert saved is not None
    assert saved["kind"] == "assistant"
    assert saved["agent"] == "学情助手"


def test_knowledge_agent_returns_citations(monkeypatch):
    """知识问答专员必须把引用带出来——协作层不能把出处弄丢。"""
    monkeypatch.setattr(
        "backend.app.llm.chat_json",
        lambda prompt, system, temperature=0.0, **kw: {"agent": "knowledge", "reason": "问制度"},
    )
    monkeypatch.setattr(
        "backend.app.supervisor.flow.ask",
        lambda question, audiences=None: {
            "answer": "年假 5 天 [1]",
            "citations": [{"index": 1, "title": "年假", "source": "handbook-attendance.md", "snippet": "5 天"}],
            "refused": False,
        },
    )
    result = supervisor.answer("年假有几天", employee="sales", audiences={"全员", "销售"})
    assert result["agent"] == "知识问答"
    assert result["citations"][0]["source"] == "handbook-attendance.md"
