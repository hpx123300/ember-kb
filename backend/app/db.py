"""SQLite 持久化：会话历史、学习记录、调用指标。

四张表各有明确职责：
- chat_messages：问答历史，同时用于回填对话记忆
- learn_records：章节测验结果，学情画像的唯一数据源
- llm_calls / tool_calls：成本与延迟记账，只存聚合字段，不存正文
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DB_PATH, ensure_dirs

ensure_dirs()

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LearnRecord(Base):
    """一次章节测验的记录。

    只存原始作答结果，不存「掌握度」这类派生值——掌握度由聚合算出来，
    口径改了可以重算，存死了就改不动。
    """

    __tablename__ = "learn_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee: Mapped[str] = mapped_column(String(32), index=True, default="local")
    chapter: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(default=0)
    total: Mapped[int] = mapped_column(default=0)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class LLMCall(Base):
    """一次模型调用的成本与延迟记录（可观测性的原始数据）。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class ToolCall(Base):
    """一次工具调用的结果记录，用于统计各工具的成功率与耗时。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    tool: Mapped[str] = mapped_column(String(64), index=True, default="")
    ok: Mapped[bool] = mapped_column(default=True)
    latency_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    with SessionLocal() as session:
        yield session
