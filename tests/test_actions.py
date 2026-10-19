"""待确认动作测试：确认闸门的四条硬约束。

这四条里任何一条破了，这套机制就只是装饰：
1. 只能执行一次；2. 过期不能执行；3. 别人不能替你确认；4. 非法路径写不出去。

测试把知识库目录换到临时目录——否则跑一次测试会真的往 data/knowledge 里追加内容。
"""

import uuid
from datetime import timedelta

import pytest

from backend.app import actions
from backend.app.db import PendingAction, get_session


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """临时知识库 + 阻断索引重建（重建会读真实目录，在测试里没有意义）。"""
    chapter = tmp_path / "handbook-attendance.md"
    chapter.write_text("# 考勤与假期\n\n### 年假\n\n满 1 年 5 天。\n", encoding="utf-8")
    monkeypatch.setattr(actions, "KNOWLEDGE_DIR", tmp_path)
    monkeypatch.setattr(actions, "_apply_reindex", lambda: None, raising=False)
    return tmp_path


def _propose(employee: str, chapter: str = "handbook-attendance.md"):
    return actions.propose(
        session_id=f"test-{uuid.uuid4().hex[:6]}",
        employee=employee,
        kind="append_faq",
        payload={"chapter": chapter, "question": "晚到 10 分钟算迟到吗？", "answer": "每月 3 次以内不计。"},
        preview="在《handbook-attendance.md》末尾补一条",
    )


def test_approve_writes_once(kb):
    employee = f"u-{uuid.uuid4().hex[:6]}"
    created = _propose(employee)
    assert created["ok"]

    result = actions.approve(created["action"]["id"], employee)
    assert result["ok"], result
    assert result["status"] == "applied"

    text = (kb / "handbook-attendance.md").read_text(encoding="utf-8")
    assert "晚到 10 分钟算迟到吗" in text


def test_second_approve_is_idempotent(kb):
    """前端多点两下是常态，第二次不能又追加一遍。"""
    employee = f"u-{uuid.uuid4().hex[:6]}"
    action_id = _propose(employee)["action"]["id"]

    first = actions.approve(action_id, employee)
    second = actions.approve(action_id, employee)

    assert first["ok"] and second["ok"]
    assert second.get("idempotent") is True
    text = (kb / "handbook-attendance.md").read_text(encoding="utf-8")
    assert text.count("晚到 10 分钟算迟到吗") == 1


def test_expired_action_is_refused(kb):
    employee = f"u-{uuid.uuid4().hex[:6]}"
    action_id = _propose(employee)["action"]["id"]

    # 把过期时间改到过去，模拟「昨天提的今天才点」
    with get_session() as session:
        row = session.get(PendingAction, action_id)
        row.expires_at = row.created_at - timedelta(minutes=1)
        session.commit()

    result = actions.approve(action_id, employee)

    assert not result["ok"]
    assert result["status"] == "expired"
    text = (kb / "handbook-attendance.md").read_text(encoding="utf-8")
    assert "晚到 10 分钟" not in text


def test_other_user_cannot_approve(kb):
    owner = f"u-{uuid.uuid4().hex[:6]}"
    other = f"u-{uuid.uuid4().hex[:6]}"
    action_id = _propose(owner)["action"]["id"]

    result = actions.approve(action_id, other)

    assert not result["ok"]
    assert "无权" in result["error"]
    text = (kb / "handbook-attendance.md").read_text(encoding="utf-8")
    assert "晚到 10 分钟" not in text


def test_reject_keeps_library_untouched(kb):
    employee = f"u-{uuid.uuid4().hex[:6]}"
    action_id = _propose(employee)["action"]["id"]

    assert actions.reject(action_id, employee, reason="口径还没定")["ok"]

    after = actions.approve(action_id, employee)
    assert not after["ok"]
    assert after["status"] == "rejected"
    text = (kb / "handbook-attendance.md").read_text(encoding="utf-8")
    assert "晚到 10 分钟" not in text


def test_path_traversal_is_blocked(kb):
    """模型完全可能给出 `../.env` 这种章节名，写操作必须挡住。"""
    employee = f"u-{uuid.uuid4().hex[:6]}"
    action_id = _propose(employee, chapter="../.env")["action"]["id"]

    result = actions.approve(action_id, employee)

    assert not result["ok"]
    assert result["status"] == "failed"


def test_pending_limit_per_user(kb):
    employee = f"u-{uuid.uuid4().hex[:6]}"
    for _ in range(actions.MAX_PENDING_PER_USER):
        assert _propose(employee)["ok"]

    overflow = _propose(employee)
    assert not overflow["ok"]
    assert "先处理完" in overflow["error"]
