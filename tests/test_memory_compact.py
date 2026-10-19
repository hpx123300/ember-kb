"""上下文压缩测试：摘要替代硬截断。

这套机制的失败模式很隐蔽——压缩之后对话仍然能跑，只是模型开始"忘记"早期约束。
所以测试要盯住的不是「有没有压缩」，而是三件事：
1. 只压缩安全前缀，绝不切断 tool_calls 与 tool 的配对；
2. 最近若干条原文必须留下，摘要是补充不是替代；
3. 压缩失败时要有兜底，不能让上下文无限增长。
"""

from backend.app.agent.memory import Memory


def _memory(count: int, max_messages: int = 10, keep_recent: int = 4) -> Memory:
    memory = Memory("", max_messages=max_messages, keep_recent=keep_recent)
    for i in range(count):
        memory.add({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"})
    return memory


def test_short_history_is_not_compacted():
    memory = _memory(8)
    assert memory.compact_candidates() == []


def test_candidates_are_the_early_prefix():
    memory = _memory(14)
    candidates = memory.compact_candidates()

    assert len(candidates) == 10, "14 条里保留最近 4 条，剩下 10 条可压缩"
    assert candidates[0]["content"] == "msg-0"


def test_compaction_replaces_prefix_and_keeps_recent():
    memory = _memory(14)
    candidates = memory.compact_candidates()

    consumed = memory.apply_compaction("用户在销售部，只关心销售话术。", candidates)

    assert consumed == 10
    assert memory.summary.startswith("用户在销售部")
    remaining = memory.get_messages()
    assert [m["content"] for m in remaining] == ["msg-10", "msg-11", "msg-12", "msg-13"]


def test_tool_call_boundary_is_never_crossed():
    """摘要碰到带 tool_calls 的消息必须停下，否则会留下孤儿 tool 消息。"""
    memory = Memory("", max_messages=10, keep_recent=4)
    for i in range(6):
        memory.add({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"})
    memory.add({"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]})
    memory.add({"role": "tool", "tool_call_id": "call-1", "content": "工具结果"})
    for i in range(6):
        memory.add({"role": "user", "content": f"tail-{i}"})

    candidates = memory.compact_candidates()

    assert len(candidates) == 6
    assert all(not c.get("tool_calls") for c in candidates)
    assert all(c.get("role") != "tool" for c in candidates)


def test_messages_sent_to_model_carry_no_internal_id():
    memory = _memory(3)
    assert all("id" not in m for m in memory.get_messages())


def test_trim_hard_is_the_fallback_not_the_default():
    """压缩不可用时才走硬截断，而且必须如实返回丢了多少条。"""
    memory = _memory(25)  # add 的上限是 2 倍阈值，实际保留 20 条

    dropped = memory.trim_hard()

    assert dropped == 10
    assert len(memory.get_messages()) == 10


def test_compaction_advances_covered_marker():
    """covered_until_id 是下次加载的起点；不前进就会重复摘要同一段。"""
    memory = Memory("", max_messages=4, keep_recent=2)
    memory.add({"role": "user", "content": "a", "id": 101})
    memory.add({"role": "assistant", "content": "b", "id": 102})
    memory.add({"role": "user", "content": "c", "id": 103})
    memory.add({"role": "assistant", "content": "d", "id": 104})
    memory.add({"role": "user", "content": "e", "id": 105})
    memory.add({"role": "assistant", "content": "f", "id": 106})

    candidates = memory.compact_candidates()
    memory.apply_compaction("摘要", candidates)

    assert memory.covered_until_id == 104
