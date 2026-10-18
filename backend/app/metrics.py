"""可观测性：模型调用的 token / 成本 / 延迟，以及工具成功率。

为什么单独做这一层：

Agent 的「贵」和「慢」是两件必须能被量化的事。一次对话背后可能是
1 次路由 + 3 次专员调用 + 2 次工具执行，不记账就答不出「一次对话多少钱、
P95 延迟多少」。学生项目几乎都答不出这两个数，所以这一层本身就是差异点。

记录原则：
1. 只记聚合需要的字段，不落 prompt 原文（可能含客户合同、手机号等敏感信息）；
2. 任何统计失败都不能影响主流程——所有写入都吞异常并降级为日志。
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import LLMCall, ToolCall, get_session

logger = logging.getLogger("agent.metrics")

# DeepSeek 官方定价（元 / 百万 token），按 2026-10 的公开价配置。
# 供应商调价或换模型时改环境变量即可，不要改代码。
PRICE_INPUT_PER_M = float(os.getenv("LLM_PRICE_INPUT_PER_M", "2.0"))
PRICE_OUTPUT_PER_M = float(os.getenv("LLM_PRICE_OUTPUT_PER_M", "8.0"))


def _ts(dt: datetime) -> float:
    """统一成 epoch 秒，避免 SQLite 里时区有无混存导致比较出错。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """按当前单价折算成元，用于把「这轮对话花了多少钱」直接告诉前端。"""
    return round(
        max(0, prompt_tokens) / 1_000_000 * PRICE_INPUT_PER_M
        + max(0, completion_tokens) / 1_000_000 * PRICE_OUTPUT_PER_M,
        6,
    )


def record_llm_call(
    session_id: str,
    role: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    ok: bool = True,
) -> None:
    try:
        with get_session() as session:
            session.add(
                LLMCall(
                    session_id=session_id or "",
                    role=role or "",
                    model=model or "",
                    prompt_tokens=max(0, int(prompt_tokens or 0)),
                    completion_tokens=max(0, int(completion_tokens or 0)),
                    latency_ms=max(0, int(latency_ms or 0)),
                    ok=bool(ok),
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("记录模型调用失败（不影响主流程）：%s", exc)


def record_tool_call(
    session_id: str,
    role: str,
    tool: str,
    ok: bool,
    latency_ms: int = 0,
    error: str = "",
) -> None:
    try:
        with get_session() as session:
            session.add(
                ToolCall(
                    session_id=session_id or "",
                    role=role or "",
                    tool=tool or "",
                    ok=bool(ok),
                    latency_ms=max(0, int(latency_ms or 0)),
                    error=(error or "")[:200],
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("记录工具调用失败（不影响主流程）：%s", exc)


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def summary(days: int = 7) -> dict:
    """最近 N 天的调用汇总：成本、延迟分位、分角色与分工具明细。"""
    cutoff = _ts(datetime.now(timezone.utc) - timedelta(days=max(1, days)))

    with get_session() as session:
        llm_rows = session.execute(select(LLMCall)).scalars().all()
        tool_rows = session.execute(select(ToolCall)).scalars().all()

    llm_rows = [r for r in llm_rows if _ts(r.created_at) >= cutoff]
    tool_rows = [r for r in tool_rows if _ts(r.created_at) >= cutoff]

    prompt_tokens = sum(r.prompt_tokens for r in llm_rows)
    completion_tokens = sum(r.completion_tokens for r in llm_rows)
    cost = (
        prompt_tokens / 1_000_000 * PRICE_INPUT_PER_M
        + completion_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
    )
    latencies = [r.latency_ms for r in llm_rows if r.latency_ms]

    by_role: dict[str, dict] = {}
    for r in llm_rows:
        item = by_role.setdefault(
            r.role or "unknown",
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": []},
        )
        item["calls"] += 1
        item["prompt_tokens"] += r.prompt_tokens
        item["completion_tokens"] += r.completion_tokens
        item["latency_ms"].append(r.latency_ms)
    for item in by_role.values():
        lats = item.pop("latency_ms")
        item["avg_latency_ms"] = round(sum(lats) / len(lats)) if lats else 0
        item["cost"] = round(
            item["prompt_tokens"] / 1_000_000 * PRICE_INPUT_PER_M
            + item["completion_tokens"] / 1_000_000 * PRICE_OUTPUT_PER_M,
            4,
        )

    by_tool: dict[str, dict] = {}
    for r in tool_rows:
        item = by_tool.setdefault(
            r.tool or "unknown",
            {"calls": 0, "failed": 0, "latency_ms": [], "last_error": ""},
        )
        item["calls"] += 1
        if not r.ok:
            item["failed"] += 1
            item["last_error"] = r.error
        item["latency_ms"].append(r.latency_ms)
    for item in by_tool.values():
        lats = item.pop("latency_ms")
        item["success_rate"] = (
            round((item["calls"] - item["failed"]) / item["calls"], 3)
            if item["calls"]
            else None
        )
        item["avg_latency_ms"] = round(sum(lats) / len(lats)) if lats else 0

    calls = len(llm_rows)
    return {
        "window_days": days,
        "calls": calls,
        "failed_calls": sum(1 for r in llm_rows if not r.ok),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": round(cost, 4),
        "cost_per_call": round(cost / calls, 6) if calls else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "by_role": by_role,
        "by_tool": by_tool,
        "price": {
            "input_per_m": PRICE_INPUT_PER_M,
            "output_per_m": PRICE_OUTPUT_PER_M,
        },
    }
