#!/usr/bin/env python3
"""薪火 Ember 的 MCP Server：把知识库能力暴露给任意智能体。

为什么是 MCP，而不是再写一个 HTTP 接口：

OpenClaw、Claude Desktop、IDE 里的助手，认的都是 MCP。写一个 MCP server，
这些客户端不用改一行代码就能用上知识库；写一个 HTTP 接口，每个客户端都得
单独适配一次。MCP 的价值不在协议本身，在「一次实现，到处可挂」。

设计上的两个边界：

1. 权限跟着环境变量走（EMBER_MCP_AUDIENCE），不跟着调用方走。
   智能体不能通过参数给自己提权——它连"有哪些部门"都不该知道。
2. 检索仍然走 flow/search 那条路，带出处、会拒答。
   MCP 只是换了个入口，不会绕过知识库自己的判断。

启动：
    EMBER_MCP_AUDIENCE=销售 python mcp_server.py

挂到 OpenClaw：
    openclaw mcp add ember --command "python" --args "/path/to/mcp_server.py"
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from backend.app import training  # noqa: E402
from backend.app.agent.rag import knowledge_base  # noqa: E402
from backend.app.auth import UNIVERSAL_AUDIENCES  # noqa: E402
from backend.app.flow import ask  # noqa: E402
from backend.app.profile import build_profile  # noqa: E402

# 挂在 MCP 上的这份服务以某个身份运行，权限在启动时固定。
DEFAULT_DEPARTMENT = os.getenv("EMBER_MCP_AUDIENCE", "全员")
# 没有对应账号时按最保守处理：只能看全员资料。
AUDIENCES = UNIVERSAL_AUDIENCES | ({DEFAULT_DEPARTMENT} if DEFAULT_DEPARTMENT != "全员" else set())

server = MCPServer(
    name="ember-kb",
    version="1.0.0",
    instructions=(
        "薪火 Ember 企业知识库。回答公司制度、流程、产品、销售话术、客服口径时，"
        "先调用 ask_knowledge；需要原始片段时用 search_knowledge。"
        "资料里没有的内容，它会明确拒答——不要用你自己的知识补全。"
    ),
)


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@server.tool(description="回答公司制度/流程/产品/话术类问题，返回带出处的答案；资料不足时拒答。")
def ask_knowledge(question: str) -> str:
    if not question or not question.strip():
        return _dump({"error": "问题不能为空"})
    result = ask(question.strip(), audiences=AUDIENCES)
    return _dump(
        {
            "answer": result["answer"],
            "refused": result["refused"],
            "citations": [
                {"title": c["title"], "source": c["source"], "snippet": c["snippet"]}
                for c in result["citations"]
            ],
        }
    )


@server.tool(description="按关键词检索知识库片段，返回标题、来源与原文摘录，用于自己组织回答。")
def search_knowledge(query: str, category: str = "", top_k: int = 4) -> str:
    results = knowledge_base.search(
        query or "公司制度",
        k=max(1, min(int(top_k or 4), 8)),
        category=category or None,
        audiences=AUDIENCES,
    )
    if not results:
        return _dump({"results": [], "note": "没有命中，可能是问题超出资料范围或不在当前权限内"})
    return _dump(
        {
            "results": [
                {
                    "title": r["title"],
                    "source": r["source"],
                    "cite": r.get("cite", ""),
                    "text": r["text"][:400],
                }
                for r in results
            ]
        }
    )


@server.tool(description="列出可用的培训章节（章节 id、标题、类别、小节数）。")
def list_chapters() -> str:
    chapters = training.list_chapters(AUDIENCES)
    return _dump(
        [
            {
                "id": c["id"],
                "title": c["title"],
                "category": c["category"],
                "sections": len(c["sections"]),
            }
            for c in chapters
        ]
    )


@server.tool(description="为指定章节生成测验题。chapter 传章节 id（如 handbook-attendance）；不传则随机一章。")
def generate_quiz(chapter: str = "", count: int = 3) -> str:
    chapters = training.list_chapters(AUDIENCES)
    if not chapters:
        return _dump({"error": "没有可用章节"})
    matched = next((c for c in chapters if c["id"] == chapter or c["title"] == chapter), None)
    if matched is None:
        matched = chapters[0]
    questions = training.generate_quiz(matched["id"], count=max(1, min(int(count or 3), 8)))
    return _dump(
        {
            "chapter": matched["title"],
            "chapter_id": matched["id"],
            "questions": [
                {
                    "question": q["question"],
                    "options": q["options"],
                    "answer_index": q["answer_index"],
                    "explanation": q["explanation"],
                }
                for q in questions
            ],
        }
    )


@server.tool(description="查看某位员工的学习进度：已通过章节、薄弱章节、未开始章节。")
def get_progress(employee: str = "local") -> str:
    data = build_profile(employee, AUDIENCES)
    return _dump(
        {
            "employee": data["employee"],
            "passed": data["passed"],
            "total_chapters": data["total_chapters"],
            "completion": data["completion"],
            "weak": [w["title"] for w in data["weak"]],
            "untouched": data["untouched"],
        }
    )


if __name__ == "__main__":
    # stdio 传输：由客户端（OpenClaw / Claude Desktop）拉起，
    # 不监听端口，所以不存在"暴露到公网"这个风险面。
    server.run("stdio")
