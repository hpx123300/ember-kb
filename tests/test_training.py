"""培训与资料管理测试：章节派生、出题校验、判分、学情聚合、上传校验。"""

import pytest

from backend.app import library, training


def test_chapters_derived_from_docs():
    """章节由文档派生：加一份资料就多一章，不需要单独维护课程表。"""
    chapters = training.list_chapters()
    assert len(chapters) >= 8
    assert all(c["sections"] for c in chapters)
    assert any(c["id"] == "handbook-attendance.md" for c in chapters)


def test_quiz_validation_drops_bad_items(monkeypatch):
    """模型返回的脏题要丢掉：缺字段、答案越界、选项重复都不能下发给前端。"""
    monkeypatch.setattr(
        "backend.app.training.llm.chat_json",
        lambda prompt, system, temperature=0.2, **kw: {
            "questions": [
                {"question": "年假满 1 年几天？", "options": ["5", "7", "10", "3"], "answer_index": 0,
                 "explanation": "原文写明 5 天", "knowledge_point": "年假"},
                {"question": "缺答案下标", "options": ["A", "B", "C", "D"]},
                {"question": "答案越界", "options": ["A", "B", "C", "D"], "answer_index": 9},
                {"question": "选项重复", "options": ["A", "A", "B"], "answer_index": 0},
            ]
        },
    )
    questions = training.generate_quiz("handbook-attendance.md", 5)
    assert len(questions) == 1
    assert questions[0]["options"] == ["5", "7", "10", "3"]


def test_quiz_falls_back_when_llm_fails(monkeypatch):
    """模型不可用时退化为挖空题，培训流程不能因为一次 API 失败而中断。"""

    def boom(prompt, system, temperature=0.2, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr("backend.app.training.llm.chat_json", boom)
    questions = training.generate_quiz("handbook-expense.md", 3)
    assert questions, "兜底出题不应返回空"
    assert all(q["options"] for q in questions)


def _fake_questions(monkeypatch, count=4):
    """固定一套题，避免测试依赖模型输出。"""
    items = [
        {
            "question": f"题 {i + 1}",
            "options": ["A", "B", "C", "D"],
            "answer_index": i % 4,
            "explanation": "见原文",
            "knowledge_point": "小节",
        }
        for i in range(count)
    ]
    monkeypatch.setattr(
        "backend.app.training.llm.chat_json",
        lambda prompt, system, temperature=0.2, **kw: {"questions": items},
    )


def test_quiz_response_never_leaks_answers(monkeypatch):
    """下发给前端的题不能带 answer_index —— 带了就等于把答案给了客户端。"""
    _fake_questions(monkeypatch, 4)
    data = training.create_quiz_session("handbook-attendance.md", 4, employee="leak-user")
    assert data["session_id"]
    assert len(data["questions"]) == 4
    for q in data["questions"]:
        assert "answer_index" not in q
        assert "explanation" not in q


def test_submit_scores_and_records(monkeypatch):
    """判分与通过线：答对 80% 及以上算通过，正好压在边界上也要对。"""
    _fake_questions(monkeypatch, 4)
    session = training.create_quiz_session("handbook-attendance.md", 4, employee="test-user")
    result = training.submit_quiz_session(session["session_id"], [0, 1, 2, 3], employee="test-user")
    assert result["score"] == 4
    assert result["total"] == 4
    assert result["passed"] is True

    _fake_questions(monkeypatch, 5)
    borderline = training.create_quiz_session("handbook-attendance.md", 5, employee="test-user")
    outcome = training.submit_quiz_session(borderline["session_id"], [0, 1, 2, 3, 9], employee="test-user")
    assert outcome["score"] == 4
    assert outcome["passed"] is True  # 4/5 = 80%，刚好达到通过线


def test_quiz_is_single_use_and_owner_only(monkeypatch):
    """一套题只能交一次，别人也交不了——否则可以把别人的成绩刷掉。"""
    _fake_questions(monkeypatch, 3)
    session = training.create_quiz_session("handbook-expense.md", 3, employee="alice")
    with pytest.raises(ValueError):
        training.submit_quiz_session(session["session_id"], [0, 0, 0], employee="bob")
    training.submit_quiz_session(session["session_id"], [0, 1, 2], employee="alice")
    with pytest.raises(ValueError):
        training.submit_quiz_session(session["session_id"], [0, 1, 2], employee="alice")


def test_mastery_uses_best_score(monkeypatch):
    """考两次取最好成绩：培训目标是掌握，不是平均。"""
    _fake_questions(monkeypatch, 2)
    first = training.create_quiz_session("security-policy.md", 2, employee="mastery-user")
    training.submit_quiz_session(first["session_id"], [0, 0], employee="mastery-user")  # 50%

    _fake_questions(monkeypatch, 2)
    second = training.create_quiz_session("security-policy.md", 2, employee="mastery-user")
    training.submit_quiz_session(second["session_id"], [0, 1], employee="mastery-user")  # 100%

    data = training.mastery("mastery-user")
    row = next(c for c in data["chapters"] if c["chapter"] == "security-policy.md")
    assert row["attempts"] == 2
    assert row["best"] == 2
    assert row["passed"] is True


def test_mastery_completion_ratio():
    data = training.mastery("nobody-user")
    assert data["completion"] == 0
    assert data["studied"] == 0
    assert data["total_chapters"] >= 8


def test_upload_requires_category():
    with pytest.raises(ValueError):
        library.save_doc("bad-doc.md", "# 没有元信息的文档\n正文")


def test_upload_rejects_path_traversal():
    with pytest.raises(ValueError):
        library.save_doc("../../etc/passwd.md", "---\ncategory: 制度\n---\n正文")


def test_upload_and_delete_roundtrip(tmp_path, monkeypatch):
    """上传再删除要能回到原状态。

    知识库目录必须换成临时目录：原实现直接往真实 KNOWLEDGE_DIR 里写文件。
    平时看不出问题（写完立刻删），但只要另一个进程同时读知识库——比如评测脚本
    正好在检索——它就会读到这个刚被删掉的临时文件而崩溃。
    """
    monkeypatch.setattr(library, "KNOWLEDGE_DIR", tmp_path)
    name = "tmp-test-doc.md"
    content = "---\ncategory: 制度\naudience: 全员\n---\n# 临时文档\n\n## 小节\n这是临时内容。\n"
    library.save_doc(name, content)
    try:
        docs = library.list_docs()
        assert any(d["filename"] == name and d["category"] == "制度" for d in docs)
    finally:
        library.delete_doc(name)
    assert not any(d["filename"] == name for d in library.list_docs())
