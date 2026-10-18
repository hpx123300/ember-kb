"""知识问答流程：用 LangGraph 的条件边表达「检索 → 判断 → 回答 / 拒答」。

为什么这一层值得用图，而不是一个 if：

企业知识库最怕的不是答得不好，而是答得像真的。所以「资料里到底有没有」
必须是流程里的一个显式分支，而不是埋在提示词里求模型自觉。图把这件事
写成了可测试的边：judge 节点的结论决定走 answer 还是 refuse，
两条路的输出结构不同（一个带引用，一个带建议），前端能明确区分。

检索质量判定用「问题关键词覆盖率」而不是模型打分：
确定性、零成本、可解释，评测时能直接对上账。
"""

import re
import time
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from . import llm
from .agent.rag import knowledge_base
from .observability import TraceRecorder, record_step

# 覆盖率只用于兜底：判官模型不可用时，用 0.5 这个偏保守的线决定答还是拒。
# 正常路径下由 judge 节点判断，覆盖率只作为观测指标记录。
COVERAGE_FALLBACK = 0.5

# 这些词在问句里几乎不携带信息，参与覆盖率计算只会虚高
_STOPWORDS = {
    "什么", "怎么", "如何", "为什么", "哪些", "哪个", "多少", "是否", "可以",
    "需要", "我们", "公司", "请问", "一下", "的话", "这个", "那个", "还有",
    "以及", "或者", "如果", "但是", "就是", "应该", "能不能", "有没有",
}

REFUSE_MESSAGE = (
    "这个问题在现有资料里没有找到明确依据。\n"
    "可以换个说法，或指定资料类别再问一次；如果确实需要这部分内容，"
    "建议补充到知识库后再问，我不做没有出处的推测。"
)

ANSWER_SYSTEM = """你是企业知识助手，只根据给定资料回答，不使用资料之外的知识。

规则：
1. 每个结论后面标注来源编号，例如 [1]；引用多处写 [1][3]。
2. 资料没覆盖的部分直接说「资料里没有写到」，不要补全、不要推测。
3. 先给结论，再给细节，不要复述问题。
4. 金额、天数、时限、比例必须与原文完全一致，不要改写数字。
5. 用中文，语气克制，不用感叹号，不写「作为 AI」这类话。"""


class AskState(TypedDict, total=False):
    question: str
    category: str
    audiences: list[str] | None
    chunks: list[dict]
    coverage: float
    refused: bool
    answer: str
    citations: list[dict]
    ungrounded: bool


def _keywords(text: str) -> list[str]:
    """抽问题里的实词，用于算覆盖率。

    一个坑：jieba 的通用词典不认识「年假」「基础版」这类公司术语，
    会切成「年/假」「基础/版」。如果只保留长度 >= 2 的词，
    关键词会掉到只剩「几天」，覆盖率直接算成 0，好问题也会被拒答。
    所以这里把相邻的单个汉字合并成词，把领域词捞回来。
    """
    try:
        import jieba

        # lcut_for_search 会把「上班时间」拆成「上班 / 时间 / 上班时间」，
        # 语料里写的是「工作时间」「上下班」，只有拆开才匹配得上
        tokens = [t.strip() for t in jieba.lcut_for_search(text)]
    except Exception:  # noqa: BLE001
        tokens = re.split(r"[\s，。；、？！,.;?!]+", text)

    words: list[str] = []
    run: list[str] = []
    for token in tokens:
        if token in words:
            continue
        if len(token) == 1 and re.fullmatch(r"[\u4e00-\u9fa5]", token) and token not in _STOPWORDS:
            run.append(token)
            continue
        if len(run) >= 2:
            # 只取前两个字组成词：「年假有」这种连读里，领域词几乎总在前两位，
            # 把整段拼进去会造出「年假有」这种永远匹配不上的假词，反而压低覆盖率。
            words.append("".join(run[:2]))
        run = []
        if len(token) >= 2 and token not in _STOPWORDS and not re.fullmatch(r"[\W_]+", token):
            words.append(token)
    if len(run) >= 2:
        words.append("".join(run[:2]))
    return words


def coverage(question: str, chunks: list[dict]) -> float:
    """问题关键词被检索结果覆盖的比例。

    比「看检索分数」可靠：BM25 分数受文档长度影响，跨文档不可比；
    覆盖率是 0–1 的绝对量，阈值可以固定下来。
    """
    words = _keywords(question)
    if not words:
        return 0.0
    corpus = "\n".join(f"{c['title']}{c['text']}" for c in chunks)
    hit = sum(1 for w in words if w in corpus)
    return round(hit / len(words), 3)


def _build_context(chunks: list[dict]) -> str:
    lines = []
    for i, chunk in enumerate(chunks, 1):
        lines.append(f"[{i}] {chunk['title']}（{chunk['source']}）\n{chunk['text']}")
    return "\n\n".join(lines)


def _extract_citations(answer: str, chunks: list[dict]) -> list[dict]:
    """把答案里的 [n] 映射回真实来源。只返回被真正引用到的段落。"""
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer) if n.isdigit()})
    citations = []
    for n in used:
        if 1 <= n <= len(chunks):
            chunk = chunks[n - 1]
            citations.append(
                {
                    "index": n,
                    "title": chunk["title"],
                    "source": chunk["source"],
                    "cite": chunk.get("cite", ""),
                    "snippet": chunk["text"][:160],
                }
            )
    return citations


def _retrieve_node(state: AskState) -> AskState:
    started = time.perf_counter()
    question = state["question"]
    audiences = state.get("audiences")
    chunks = knowledge_base.search(
        question,
        k=5,
        category=state.get("category") or None,
        audiences=set(audiences) if audiences is not None else None,
    )
    cov = coverage(question, chunks)
    record_step("retrieve", int((time.perf_counter() - started) * 1000), detail=f"{len(chunks)} 段")
    return {"chunks": chunks, "coverage": cov}


JUDGE_SYSTEM = """你是企业知识库的质检员。给你一个问题和若干资料片段，
判断这些资料能否回答这个问题。

判断标准（严格）：
- 只有资料里明确写了答案，才算「能」。
- 需要推理、补充常识、或者资料只沾边的，一律算「不能」。
- 问的对象和资料的主体不一致（比如问竞争对手、问集团总部），算「不能」。

只输出 JSON：{"can_answer": true/false, "reason": "一句话理由"}"""


def _judge_node(state: AskState) -> AskState:
    """让模型判断「资料够不够答」，而不是只看关键词覆盖率。

    纯词面匹配挡不住「问竞争对手的定价」这种题：资料里有「产品」「定价」，
    覆盖率很高，但答出来的是自家价格，属于典型的张冠李戴。
    多花一次模型调用换判断准确，是这类产品必须付的成本。
    """
    chunks = state["chunks"]
    if not chunks:
        record_step("judge", 0, detail="无候选，直接拒答")
        return {"refused": True}
    prompt = f"问题：{state['question']}\n\n资料：\n{_build_context(chunks)}"
    started = time.perf_counter()
    try:
        verdict = llm.chat_json(prompt, JUDGE_SYSTEM, temperature=0.0, role="judge")
        can_answer = bool(verdict.get("can_answer"))
        record_step("judge", int((time.perf_counter() - started) * 1000), detail="可答" if can_answer else "不足以答")
    except Exception:  # noqa: BLE001
        # 判官挂了不能把用户晾着：退回覆盖率兜底，阈值放高一点偏保守
        can_answer = state.get("coverage", 0) >= COVERAGE_FALLBACK
        record_step("judge", int((time.perf_counter() - started) * 1000), ok=False, detail="判官不可用，用覆盖率兜底")
    return {"refused": not can_answer}


def _answer_node(state: AskState) -> AskState:
    chunks = state["chunks"]
    prompt = f"资料：\n{_build_context(chunks)}\n\n问题：{state['question']}"
    writer = get_stream_writer()
    full = ""
    started = time.perf_counter()
    for piece in llm.chat_text_stream(prompt, ANSWER_SYSTEM, temperature=0.2, role="answer"):
        full += piece
        writer(piece)
    answer = full.strip()
    record_step("answer", int((time.perf_counter() - started) * 1000), detail=f"{len(chunks)} 段资料")
    citations = _extract_citations(answer, chunks)

    if not citations:
        # 没有引用等于没有依据。重试一次，把要求说得更死板一点；
        # 仍拿不到引用就把这次回答标记为不可信，交给前端提示。
        strict = f"{prompt}\n\n注意：每个结论后面必须标注来源编号，例如 [1]。"
        retry_started = time.perf_counter()
        retry = llm.chat_text(strict, ANSWER_SYSTEM, temperature=0.0, role="answer-retry")
        record_step("answer-retry", int((time.perf_counter() - retry_started) * 1000), detail="无引用")
        retry_citations = _extract_citations(retry, chunks)
        if retry_citations:
            writer(retry)
            return {"answer": retry, "citations": retry_citations}
        return {"answer": answer, "citations": [], "ungrounded": True}

    return {"answer": answer, "citations": citations}


def _refuse_node(state: AskState) -> AskState:
    writer = get_stream_writer()
    writer(REFUSE_MESSAGE)
    return {"answer": REFUSE_MESSAGE, "citations": []}


def _route(state: AskState) -> str:
    return "refuse" if state.get("refused") else "answer"


def _after_retrieve(state: AskState) -> str:
    """覆盖率归零的明显无关问题直接拒，省一次判官调用；其余交给判官。"""
    if not state.get("chunks"):
        return "refuse"
    return "judge"


def build_flow():
    builder = StateGraph(AskState)
    builder.add_node("retrieve", _retrieve_node)
    builder.add_node("judge", _judge_node)
    builder.add_node("answer", _answer_node)
    builder.add_node("refuse", _refuse_node)
    builder.set_entry_point("retrieve")
    builder.add_conditional_edges("retrieve", _after_retrieve, {"judge": "judge", "refuse": "refuse"})
    builder.add_conditional_edges("judge", _route, {"answer": "answer", "refuse": "refuse"})
    builder.add_edge("answer", END)
    builder.add_edge("refuse", END)
    return builder.compile()


FLOW = build_flow()


def ask_stream(question: str, category: str = "", audiences: set[str] | None = None, user: str = ""):
    """流式问答。事件序列：token* → meta（引用/拒答/覆盖率/trace_id）→ done。"""
    final: dict = {}
    recorder = TraceRecorder("ask", user=user, question=question)
    with recorder:
        for mode, chunk in FLOW.stream(
            {
                "question": question,
                "category": category,
                # 权限范围随请求传入，节点里不再关心"谁是当前用户"
                "audiences": list(audiences) if audiences is not None else None,
            },
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                if chunk:
                    yield {"type": "token", "text": chunk}
            else:
                final = chunk
        trace = recorder.finish(agent="知识问答", refused=bool(final.get("refused")))

    yield {
        "type": "meta",
        "refused": bool(final.get("refused")),
        "coverage": final.get("coverage", 0),
        "citations": final.get("citations", []),
        "trace_id": trace["trace_id"],
        "total_ms": trace["total_ms"],
    }
    yield {"type": "done"}


def ask(question: str, category: str = "", audiences: set[str] | None = None) -> dict:
    """非流式版本，供评测与 MCP 复用，保证和线上走同一条路径。"""
    result = FLOW.invoke(
        {
            "question": question,
            "category": category,
            "audiences": list(audiences) if audiences is not None else None,
        }
    )
    return {
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "refused": bool(result.get("refused")),
        "coverage": result.get("coverage", 0),
    }
