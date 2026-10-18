"""可观测性测试：token 记账、成本换算、延迟分位与工具成功率。

注意：指标表和业务表共用一个 SQLite 文件，测试必须自己清掉写入的行，
否则会把假的 token 数留在开发库里，让 /api/metrics 显示一堆不存在的成本。
"""

import pytest
from sqlalchemy import delete

from backend.app import metrics
from backend.app.db import LLMCall, ToolCall, get_session

TEST_SESSION = "metrics_test"


@pytest.fixture(autouse=True)
def clean_metrics_rows():
    def wipe():
        with get_session() as session:
            session.execute(delete(LLMCall).where(LLMCall.session_id == TEST_SESSION))
            session.execute(delete(ToolCall).where(ToolCall.session_id == TEST_SESSION))
            session.commit()

    wipe()
    yield
    wipe()


def _delta(before: dict, after: dict, key: str) -> float:
    return after[key] - before[key]


def test_llm_call_accounting_and_cost():
    """记录两次调用后，token 与成本按单价正确累加。"""
    before = metrics.summary(days=1)
    metrics.record_llm_call(TEST_SESSION, "router", "deepseek-chat", 1_000_000, 0, 120)
    metrics.record_llm_call(TEST_SESSION, "知识助手", "deepseek-chat", 0, 1_000_000, 800)
    after = metrics.summary(days=1)

    assert _delta(before, after, "calls") == 2
    assert _delta(before, after, "prompt_tokens") == 1_000_000
    assert _delta(before, after, "completion_tokens") == 1_000_000
    # 输入 1M 按输入单价、输出 1M 按输出单价，合计应等于两者之和
    expected = metrics.PRICE_INPUT_PER_M + metrics.PRICE_OUTPUT_PER_M
    assert abs(_delta(before, after, "cost") - expected) < 1e-6

    assert after["by_role"]["router"]["calls"] >= 1
    assert after["by_role"]["知识助手"]["avg_latency_ms"] >= 1


def test_tool_success_rate_and_error_kept():
    """工具成功率按调用次数算，失败时保留最后一条错误。"""
    before = metrics.summary(days=1)
    metrics.record_tool_call(TEST_SESSION, "tutor", "search_knowledge", True, 30)
    metrics.record_tool_call(TEST_SESSION, "tutor", "search_knowledge", False, 90, "知识库不可用")
    after = metrics.summary(days=1)

    tool = after["by_tool"]["search_knowledge"]
    assert _delta(before["by_tool"].get("search_knowledge", {"calls": 0}), tool, "calls") == 2
    assert tool["failed"] >= 1
    assert tool["last_error"] == "知识库不可用"
    assert 0 < tool["success_rate"] <= 1


def test_latency_percentiles_are_ordered():
    """P95 必须不小于 P50，这是分位计算最基本的自洽性检查。"""
    summary = metrics.summary(days=7)
    assert summary["p95_latency_ms"] >= summary["p50_latency_ms"] >= 0


def test_metrics_write_never_breaks_flow(monkeypatch):
    """统计层出问题不能影响主流程：写入异常应被吞掉。"""

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.app.metrics.get_session", boom)
    metrics.record_llm_call(TEST_SESSION, "router", "m", 1, 1, 1)
    metrics.record_tool_call(TEST_SESSION, "router", "t", False, 1, "x")
