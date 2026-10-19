"""工具注册中心：培训教练可调用的工具都在这里声明（JSON Schema + 实现）。

工具返回值统一是 JSON 字符串，形状固定为 `{"results": [...], "degraded": ...}`
或 `{"results": [], "hint": ...}`。为什么要定契约：模型读工具结果的方式是
「猜这段文本里哪个是数据」，形状一变它就可能在下一轮把提示语当数据处理。
固定字段名比固定文案更可靠。

`degraded` 不为空，说明这次结果是降级路径拿到的（放宽类别、换关键词）。
把它显式返回给模型，而不是偷偷兜底，是因为模型需要知道"这条答案的来源比平时弱"，
才能在回答里带上保留语气。
"""

import json
import re

from .. import actions, training
from ..agent.rag import knowledge_base
from ..profile import build_profile
from ..runtime import current_audiences, current_employee, current_session

# 问句里的虚词与疑问成分：参与检索只会让 BM25 的候选变脏，不带来召回
_QUERY_NOISE = {
    "请问", "我想", "想知道", "帮我", "看看", "查一下", "说一下", "问一下",
    "是什么", "是多少", "有哪些", "怎么样", "怎么办", "为什么", "多少",
    "可以", "能不能", "是否", "一下", "我们", "公司",
}
_SINGLE_CHAR_NOISE = set("的了是在有和与及吗呢啊吧呀哦嗯这那我你他她它")


def _keywords_only(text: str) -> str:
    """把整句压成关键词串。

    为什么在工具里做，而不是让模型自己想「换个说法再查」：
    模型未必会重试，重试也未必换得对；这是兜底路径，必须在代码里确定地发生。
    用 jieba 的搜索模式，能把「年假有几天」拆出「年假」这种领域词。
    """
    try:
        import jieba

        tokens = [t.strip() for t in jieba.lcut_for_search(text)]
    except Exception:  # noqa: BLE001
        tokens = [t for t in re.split(r"[\s，。；、？！,.;?!]+", text) if t]

    words: list[str] = []
    for token in tokens:
        if not token or token in _QUERY_NOISE:
            continue
        if len(token) == 1 and (token in _SINGLE_CHAR_NOISE or not re.fullmatch(r"[\u4e00-\u9fa5]", token)):
            continue
        if len(token) >= 2 and token not in words:
            words.append(token)
    return " ".join(words[:8]) or text


def search_knowledge(query: str = "", category: str = "") -> str:
    """检索公司知识库，返回带出处的章节片段。

    检索本身可能空手而归，这里有三段降级，按代价从低到高：
    1. 原样检索（带模型给的类别）；
    2. 类别下没命中 -> 放宽到全库（模型经常把类别猜错）；
    3. 仍然没有 -> 把问句压成关键词再查一次（分词错误或虚词太多）。
    三步都空才认输，并把 hint 交给模型，让它换个说法，而不是拿空结果去编。
    """
    # 权限来自请求上下文，不由模型决定——模型没有"要不要给他看"的判断权
    audiences = current_audiences()
    text = query or "公司制度"

    results = knowledge_base.search(text, k=4, category=category or None, audiences=audiences)
    degraded = ""

    if not results and category:
        results = knowledge_base.search(text, k=4, category=None, audiences=audiences)
        degraded = f"类别「{category}」下没有命中，已放宽到全库"

    if not results:
        rewritten = _keywords_only(text)
        if rewritten and rewritten != text:
            results = knowledge_base.search(rewritten, k=4, category=None, audiences=audiences)
            degraded = f"原句未命中，已改用关键词「{rewritten}」重试"

    if not results:
        return json.dumps(
            {
                "results": [],
                "hint": "没有命中。换用更具体的名词再查，或指定类别（制度 / 产品 / 销售 / 客服 / 培训）。"
                        "如果确实不在知识库里，直接告诉用户没有，不要推测。",
            },
            ensure_ascii=False,
        )

    payload = [
        {
            "title": r["title"],
            "source": r["source"],
            "cite": r.get("cite", ""),
            "snippet": r["text"][:200],
        }
        for r in results
    ]
    return json.dumps({"results": payload, "degraded": degraded or None}, ensure_ascii=False)



def make_quiz(chapter: str = "", count: int = 3) -> str:
    """为某一章生成测验题。chapter 传章节 id（文件名）或章节名。"""
    audiences = current_audiences()
    chapters = training.list_chapters(audiences)
    if not chapter:
        chapter = chapters[0]["id"] if chapters else ""
    matched = next((c for c in chapters if c["id"] == chapter or c["title"] == chapter), None)
    if matched is None:
        names = "、".join(f"{c['title']}({c['id']})" for c in chapters[:8])
        return f"没有找到这一章。可选：{names}"
    questions = training.generate_quiz(matched["id"], count=int(count), audiences=audiences)
    return json.dumps({"chapter": matched["title"], "questions": questions}, ensure_ascii=False)


def get_progress() -> str:
    """查看学习进度与薄弱章节。"""
    data = build_profile(current_employee(), current_audiences())
    summary = {
        "总章节": data["total_chapters"],
        "已学": data["studied"],
        "已通过": data["passed"],
        "完成率": data["completion"],
        "薄弱章节": [c["title"] for c in data["weak"]],
        "未开始": data["untouched"],
    }
    return json.dumps(summary, ensure_ascii=False)


def propose_faq(chapter: str = "", question: str = "", answer: str = "") -> str:
    """提议往某章资料末尾补一条问答。

    注意它**不写文件**。工具一旦能直接改知识库，模型一次幻觉就会改掉线上资料，
    而且改坏了没有痕迹。所以这里只登记一条待确认动作，由人在界面上点确认才执行。

    返回值要明确告诉模型「还没写」，否则它会顺着"我先查一下、再补进去"的语境
    直接回复用户"已经加好了"——这是这套机制里最容易出现的一种谎话。
    """
    result = actions.propose(
        session_id=current_session(),
        employee=current_employee(),
        kind="append_faq",
        payload={"chapter": chapter, "question": question, "answer": answer},
        preview=f"在《{chapter}》末尾补一条：{question}",
    )
    if not result.get("ok"):
        return json.dumps(
            {"status": "not_proposed", "error": result.get("error", "提交失败")},
            ensure_ascii=False,
        )

    action = result["action"]
    return json.dumps(
        {
            "status": "pending_confirmation",
            "action_id": action["id"],
            "expires_at": action["expires_at"],
            "must_tell_user": "已提交，等待用户在界面上确认后才会写入。",
            "do_not_say": "不要说「已经加进去了」「已经更新了」。",
        },
        ensure_ascii=False,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "检索公司知识库（制度/产品/销售/客服/培训资料）。用户问公司规定、产品信息、话术口径时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的问题或关键词"},
                    "category": {
                        "type": "string",
                        "description": "可选类别：制度 / 产品 / 销售 / 客服 / 培训，不填则全库检索",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_quiz",
            "description": "为某一章培训资料生成测验题。用户说「考考我」「出几道题」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter": {
                        "type": "string",
                        "description": "章节 id 或章节名，如 handbook-attendance / 考勤与假期",
                    },
                    "count": {"type": "number", "description": "题目数量，默认 3，最多 8"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_progress",
            "description": "查看当前用户的学习进度、通过情况与薄弱章节。用户问「我学到哪了」「我哪章最弱」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_faq",
            "description": (
                "提议往某一章资料末尾补一条问答。只在用户明确说「把这个补进去」"
                "「帮我记到知识库」时调用。它不会立刻写入，只提交一条待确认动作，"
                "需要用户在界面上点确认。调用后必须告诉用户「已提交、待确认」，"
                "不许说已经加好了。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter": {"type": "string", "description": "章节文件名，如 handbook-attendance.md"},
                    "question": {"type": "string", "description": "要补的问题，不超过 200 字"},
                    "answer": {"type": "string", "description": "标准答案，不超过 1000 字"},
                },
                "required": ["chapter", "question", "answer"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "search_knowledge": search_knowledge,
    "make_quiz": make_quiz,
    "get_progress": get_progress,
    "propose_faq": propose_faq,
}
