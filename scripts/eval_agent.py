"""Agent 轨迹评测：评过程，不评文笔。

为什么需要这一套，而不是复用 eval_qa.py：

问答评测只回答「最终答案对不对」。但一个 Agent 可能在答案对的情况下
过程完全不可用——查了三次同一个词、调了不该调的工具、靠步数兜底猜出来。
这些在最终答案里看不出来，上线后就会变成「偶尔答得慢」「偶尔答非所问」。

所以这里只测确定性信号：工具选没选对、步数有没有超、有没有重复调用、
有没有触发兜底。全部不用模型判分，跑一次几毛钱，改完就能重跑。

与 qa_cases.json 的分工：
    qa    -> 生成质量（要点命中、引用准确、拒答）
    agent -> 行为正确（工具选择、步数、冗余、降级）

用法：
    python scripts/eval_agent.py                 # 全量
    python scripts/eval_agent.py --limit 3       # 冒烟
    python scripts/eval_agent.py --save          # 落盘到 data/eval/results/
    python scripts/eval_agent.py --verbose       # 打印每次工具调用
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agent.roles import PROMPT_VERSION  # noqa: E402
from backend.app.coach import build_coach  # noqa: E402
from backend.app.runtime import user_scope  # noqa: E402
# 从 coach 里拿不到 Memory，直接引用它的来源，避免脚本自己造一份
from backend.app.agent.memory import Memory  # noqa: E402

CASES_PATH = PROJECT_ROOT / "data" / "eval" / "agent_cases.json"
RESULT_DIR = PROJECT_ROOT / "data" / "eval" / "results"


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _is_redundant(events: list[dict]) -> list[str]:
    """同工具、同参数调第二次，就是白跑。

    为什么单独统计：这是 Agent 最常见的退化方式——第一次结果不满意，
    它不换策略，而是把同一句话再问一遍。答案可能勉强对，成本翻倍。
    """
    seen: set[tuple] = set()
    dups: list[str] = []
    for event in events:
        key = (
            event.get("tool", ""),
            json.dumps(event.get("arguments") or {}, sort_keys=True, ensure_ascii=False),
        )
        if key in seen:
            dups.append(event.get("tool", ""))
        seen.add(key)
    return dups


def run_case(case: dict, verbose: bool = False) -> dict:
    """跑一条用例：真实调用模型，记录轨迹，再按规则判定。"""
    session = f"eval-agent-{case['id']}-{int(time.time() * 1000) % 100000}"
    started = time.perf_counter()
    with user_scope("eval-bot", None):
        coach = build_coach(Memory(session, max_messages=10))
        reply, _ = coach.chat(case["question"])
    latency = int((time.perf_counter() - started) * 1000)

    events = coach.tool_events
    called = [e["tool"] for e in events]
    failures: list[str] = []

    missing = [t for t in case.get("expect_tools", []) if t not in called]
    if missing:
        failures.append(f"未调用期望工具 {missing}")

    forbidden = [t for t in called if t in case.get("forbid_tools", [])]
    if forbidden:
        failures.append(f"调用了不该调的工具 {forbidden}")

    if len(called) > case.get("max_steps", 6):
        failures.append(f"工具调用 {len(called)} 次，超过上限 {case['max_steps']}")

    if coach.fallback_used:
        failures.append("触发步数兜底（没在限定步数内完成）")

    dups = _is_redundant(events)
    if dups:
        failures.append(f"重复调用 {dups}")

    absent = [w for w in case.get("must_mention", []) if w not in reply]
    if absent:
        failures.append(f"回答缺少 {absent}")

    if verbose:
        for e in events:
            mark = "!"
            if e["ok"] and not e["empty"]:
                mark = "+"
            print(f"      {mark} {e['tool']}({json.dumps(e['arguments'], ensure_ascii=False)})"
                  f" {e['latency_ms']}ms empty={e['empty']} degraded={e['degraded']}")

    return {
        "id": case["id"],
        "question": case["question"],
        "note": case.get("note", ""),
        "tools_called": called,
        "steps": len(called),
        "degraded": any(e["degraded"] for e in events),
        "empty_results": sum(1 for e in events if e["empty"]),
        "failed_tools": sum(1 for e in events if not e["ok"] or e["failed"]),
        "fallback": coach.fallback_used,
        "latency_ms": latency,
        "reply": reply[:300],
        "failures": failures,
        "events": events,
    }


def summarize(records: list[dict]) -> dict:
    total = len(records)
    if not total:
        return {"cases": 0}

    clean = [r for r in records if not r["failures"]]
    forbidden_free = [r for r in records if not any("不该调" in f for f in r["failures"])]
    redundant_free = [r for r in records if not any("重复调用" in f for f in r["failures"])]
    latencies = sorted(r["latency_ms"] for r in records)

    return {
        "cases": total,
        # 任务成功率：过程与结果都无问题
        "task_success": round(len(clean) / total, 3),
        # 工具选择：没调禁止项（期望项缺失另算在 failures 里）
        "tool_selection": round(len(forbidden_free) / total, 3),
        "no_redundant_calls": round(len(redundant_free) / total, 3),
        "avg_steps": round(sum(r["steps"] for r in records) / total, 2),
        "fallback_rate": round(sum(1 for r in records if r["fallback"]) / total, 3),
        "degraded_rate": round(sum(1 for r in records if r["degraded"]) / total, 3),
        "avg_latency_ms": int(sum(latencies) / total),
        "p95_latency_ms": latencies[min(total - 1, int(total * 0.95))],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 轨迹评测")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"][: args.limit] if args.limit else payload["cases"]

    print("=" * 62)
    print("薪火 · Agent 轨迹评测")
    print("=" * 62)
    print(f"用例 {len(cases)} 条 | 提示词版本 {PROMPT_VERSION} | commit {_git_commit()}\n")

    records = []
    for i, case in enumerate(cases, 1):
        record = run_case(case, verbose=args.verbose)
        records.append(record)
        flag = "通过" if not record["failures"] else "不通过"
        tools = ", ".join(record["tools_called"]) or "（无工具）"
        print(f"{i:>2}. {case['question'][:24]:<26} {flag:<5} [{tools}]")
        for f in record["failures"]:
            print(f"      - {f}")

    summary = summarize(records)

    print("\n" + "=" * 62)
    print("指标")
    print("=" * 62)
    print(f"{'指标':<24}{'实测':>8}")
    for key, value in summary.items():
        if key == "cases":
            continue
        print(f"{key:<24}{value:>8}")

    if args.save:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        report = {
            "meta": {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "prompt_version": PROMPT_VERSION,
                "commit": _git_commit(),
                "cases": len(cases),
                "kind": "agent",
            },
            "summary": summary,
            "records": records,
        }
        out = RESULT_DIR / f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已保存：{out.relative_to(PROJECT_ROOT)}")

    return 0 if summary.get("task_success", 0) >= 0.75 else 1


if __name__ == "__main__":
    raise SystemExit(main())
