"""多 Agent 协作：调度员 → 专员 → 汇总。

三个专员的边界不是按"用了什么技术"划的，而是按"出了问题谁能负责"划的：

- 知识问答：必须能拒答、必须带出处。答错了是事实错误。
- 培训教练：可以自由发挥，但要能调工具、要记得这个人的进度。
- 学情查询：只读库，不让模型参与，因为数字不能让模型生成。

调度员只做一件事——判断该找谁，不参与作答。这样即使路由错了，
错的也只是"找错人"，不会把事实题编成一段看起来合理的建议。
"""

import time

from . import flow, llm, training
from .agent.memory import Memory
from .coach import build_coach
from .observability import TraceRecorder
from .profile import build_profile, profile_prompt
from .runtime import user_scope

AGENTS = {
    "knowledge": "知识问答",
    "coach": "培训教练",
    "progress": "学情助手",
}

ROUTER_SYSTEM = """你是调度员，只判断问题该交给哪位专员，不回答问题。

可选专员：
- knowledge：查公司制度、流程、产品信息、销售话术、客服口径等事实性问题。
- coach：想练题、想听讲解、想要学习建议、说「考考我」。
- progress：问自己学到哪、哪章最弱、还差哪些没学、进度如何。

只输出 JSON：{"agent": "knowledge|coach|progress", "reason": "一句话理由"}"""


def _fallback_route(question: str) -> str:
    """模型不可用时的关键词兜底。宁可路由保守，也不能让请求卡住。"""
    text = question or ""
    if any(k in text for k in ("进度", "学到", "还差", "哪章", "薄弱", "通过率", "没学")):
        return "progress"
    if any(k in text for k in ("考考", "出题", "练习", "考题", "测验", "做题")):
        return "coach"
    return "knowledge"


def route(question: str) -> tuple[str, str]:
    """返回 (agent_key, 理由)。路由本身也是一次模型调用，要记进链路。"""
    try:
        raw = llm.chat_json(f"用户问题：{question}", ROUTER_SYSTEM, temperature=0.0, role="router")
        agent = str(raw.get("agent", "")).strip()
        if agent in AGENTS:
            return agent, str(raw.get("reason", ""))[:80]
    except Exception:  # noqa: BLE001
        pass
    return _fallback_route(question), "关键词兜底"


def _progress_answer(employee: str, audiences=None) -> dict:
    data = build_profile(employee, audiences)
    if data["passed"] == 0 and data["studied"] == 0:
        lead = f"你还没有开始任何章节，共 {data['total_chapters']} 章可以学。"
    else:
        lead = f"你已经通过 {data['passed']}/{data['total_chapters']} 章。"
    parts = [lead]
    if data["weak"]:
        parts.append("最该补的是" + "、".join(f"《{w['title']}》（最好 {w['best']} 分）" for w in data["weak"]) + "。")
    if data["untouched"]:
        parts.append("还没开始：" + "、".join(data["untouched"]) + "。")
    if not data["weak"] and not data["untouched"]:
        parts.append("所有章节都已通过，可以让我出几道场景题巩固一下。")
    return {"answer": "\n".join(parts), "citations": [], "refused": False}


def answer(question: str, employee: str = "local", name: str = "", audiences=None) -> dict:
    """一次完整的协作：路由 → 专员作答 → 汇总。返回带链路的完整结果。"""
    recorder = TraceRecorder("assistant", user=employee, question=question)
    with recorder, user_scope(employee, audiences):
        started = time.perf_counter()
        agent_key, reason = route(question)
        recorder.step("route", int((time.perf_counter() - started) * 1000), detail=f"{AGENTS[agent_key]}｜{reason}")

        started = time.perf_counter()
        citations: list[dict] = []
        refused = False
        if agent_key == "knowledge":
            result = flow.ask(question, audiences=audiences)
            citations = result["citations"]
            refused = result["refused"]
        elif agent_key == "coach":
            coach = build_coach(
                Memory(f"assistant-{employee}", max_messages=20),
                profile_text=profile_prompt(build_profile(employee, audiences)),
            )
            reply, _ = coach.chat(question)
            result = {"answer": reply, "citations": [], "refused": False}
        else:
            result = _progress_answer(employee, audiences)

        recorder.step(
            f"dispatch:{agent_key}",
            int((time.perf_counter() - started) * 1000),
            detail=f"{len(citations)} 条引用" if citations else "",
        )
        trace = recorder.finish(agent=AGENTS[agent_key], refused=refused)

    return {
        "agent": AGENTS[agent_key],
        "agent_key": agent_key,
        "reason": reason,
        "answer": result["answer"],
        "citations": citations,
        "refused": refused,
        "trace_id": trace["trace_id"],
        "total_ms": trace["total_ms"],
        "steps": trace["steps"],
    }
