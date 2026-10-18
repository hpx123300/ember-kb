"""MCP Server 的工具层测试。

不测协议握手（那是 SDK 的事），测的是「挂到别的智能体上以后，
权限和拒答还在不在」——这才是把能力开放出去时真正会丢的东西。
"""

import json

import mcp_server


def test_list_chapters_returns_visible_only():
    """默认以「全员」身份运行，只能看到全员资料——
    销售话术、客服口径这些部门专属章节不该出现在列表里。
    """
    chapters = json.loads(mcp_server.list_chapters())
    ids = {c["id"] for c in chapters}
    assert "handbook-attendance.md" in ids
    assert "sales-playbook.md" not in ids
    assert "support-faq.md" not in ids
    assert all(c["id"].endswith(".md") for c in chapters)


def test_search_returns_cite():
    payload = json.loads(mcp_server.search_knowledge("年假有几天", top_k=3))
    assert payload["results"]
    first = payload["results"][0]
    assert "#" in first["cite"]
    assert first["text"]


def test_search_respects_audience(monkeypatch):
    """MCP 侧的身份在启动时固定，调用方传参数也提不了权。"""
    monkeypatch.setattr(mcp_server, "AUDIENCES", {"全员", "新员工", "销售"})
    payload = json.loads(mcp_server.search_knowledge("客户投诉处理口径", top_k=8))
    assert all(item["source"] != "support-faq.md" for item in payload["results"])


def test_generate_quiz_shape():
    payload = json.loads(mcp_server.generate_quiz("handbook-attendance.md", 2))
    assert payload["chapter"]
    assert 1 <= len(payload["questions"]) <= 2
    assert all(len(q["options"]) >= 3 for q in payload["questions"])


def test_empty_question_is_rejected():
    payload = json.loads(mcp_server.ask_knowledge("   "))
    assert payload["error"]


def test_progress_is_plain_numbers():
    payload = json.loads(mcp_server.get_progress("nobody-xyz"))
    assert payload["passed"] == 0
    assert payload["total_chapters"] >= 8
