"""会话历史读写：SQLite 落盘，页面刷新 / 重启不丢失。

除了消息，还维护一份「滚动摘要」：早期对话被压成一段话，
下次加载从摘要覆盖到的位置之后开始。这是硬截断的替代方案——
截断会把用户第一轮说的约束直接丢掉，压缩至少会把它留下来。
"""

import json

from sqlalchemy import select

from .db import ChatMessage, SessionSummary, get_session, utcnow


def save_message(session_id: str, role: str, content: str) -> None:
    with get_session() as session:
        session.add(ChatMessage(session_id=session_id, role=role, content=content))
        session.commit()


def load_messages(session_id: str, limit: int = 50) -> list[dict]:
    with get_session() as session:
        rows = (
            session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [{"role": r.role, "content": r.content} for r in rows]


def load_messages_since(session_id: str, after_id: int = 0, limit: int = 60) -> list[dict]:
    """加载指定位置之后的消息，带 id。

    为什么带 id：摘要要记「我覆盖到哪一条」，记条数会在并发写入时错位，
    记 id 不会——消息 id 单调递增，且只增不改。
    """
    with get_session() as session:
        rows = (
            session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id, ChatMessage.id > after_id)
                .order_by(ChatMessage.id.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [{"id": r.id, "role": r.role, "content": r.content} for r in rows]


def load_summary(session_id: str) -> tuple[str, int]:
    """返回 (摘要, 已覆盖到的消息 id)。没有摘要时是 ("", 0)。"""
    with get_session() as session:
        row = session.get(SessionSummary, session_id)
    return (row.summary, row.covered_until_id) if row else ("", 0)


def save_summary(session_id: str, summary: str, covered_until_id: int) -> None:
    with get_session() as session:
        row = session.get(SessionSummary, session_id)
        if row is None:
            session.add(
                SessionSummary(
                    session_id=session_id,
                    summary=summary,
                    covered_until_id=covered_until_id,
                    updated_at=utcnow(),
                )
            )
        else:
            row.summary = summary
            row.covered_until_id = covered_until_id
            row.updated_at = utcnow()
        session.commit()


def clear_messages(session_id: str) -> None:
    with get_session() as session:
        session.query(ChatMessage).filter_by(session_id=session_id).delete()
        # 摘要也要一起清：只清消息不清摘要，会出现「历史没了但摘要还在」的鬼状态
        session.query(SessionSummary).filter_by(session_id=session_id).delete()
        session.commit()
