"""工具注册中心：培训教练可调用的工具都在这里声明（JSON Schema + 实现）。"""

import json

from .. import training
from ..agent.rag import knowledge_base
from ..runtime import current_audiences, current_employee


def search_knowledge(query: str = "", category: str = "") -> str:
    """检索公司知识库，返回带出处的章节片段。"""
    # 权限来自请求上下文，不由模型决定——模型没有"要不要给他看"的判断权
    results = knowledge_base.search(
        query or "公司制度",
        k=4,
        category=category or None,
        audiences=current_audiences(),
    )
    if not results:
        return "知识库中没有检索到相关内容（也可能是不在当前账号的权限范围内）"
    payload = [
        {
            "title": r["title"],
            "source": r["source"],
            "cite": r.get("cite", ""),
            "snippet": r["text"][:200],
        }
        for r in results
    ]
    return json.dumps(payload, ensure_ascii=False)


def make_quiz(chapter: str = "", count: int = 3) -> str:
    """为某一章生成测验题。chapter 传章节 id（文件名）或章节名。"""
    chapters = training.list_chapters()
    if not chapter:
        chapter = chapters[0]["id"] if chapters else ""
    matched = next((c for c in chapters if c["id"] == chapter or c["title"] == chapter), None)
    if matched is None:
        names = "、".join(f"{c['title']}({c['id']})" for c in chapters[:8])
        return f"没有找到这一章。可选：{names}"
    questions = training.generate_quiz(matched["id"], count=int(count))
    return json.dumps({"chapter": matched["title"], "questions": questions}, ensure_ascii=False)


def get_progress() -> str:
    """查看学习进度与薄弱章节。"""
    data = training.mastery(current_employee())
    summary = {
        "总章节": data["total_chapters"],
        "已学": data["studied"],
        "已通过": data["passed"],
        "完成率": data["completion"],
        "薄弱章节": [c["title"] for c in data["weak"]],
        "未开始": data["untouched"],
    }
    return json.dumps(summary, ensure_ascii=False)


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
]

TOOL_FUNCTIONS = {
    "search_knowledge": search_knowledge,
    "make_quiz": make_quiz,
    "get_progress": get_progress,
}
