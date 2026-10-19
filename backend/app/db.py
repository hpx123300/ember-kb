"""SQLite 持久化：账号、会话历史、学习记录、测验会话、调用指标、链路追踪。

七张表各有明确职责：
- users：账号与部门，决定「能看哪些资料」
- chat_messages：问答历史，同时用于回填对话记忆
- learn_records：章节测验结果，学情画像的唯一数据源
- quiz_sessions：一次测验的题目与作答（答案只存在服务端）
- llm_calls / tool_calls：成本与延迟记账，只存聚合字段，不存正文
- traces：一次请求的完整链路，用于回放「这一单为什么慢了 4 秒」
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL, DB_PATH, ensure_dirs

ensure_dirs()

# check_same_thread 只对 SQLite 有意义，喂给 MySQL 驱动会直接报错
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """账号。department 是权限的根：检索时按它做硬过滤。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    department: Mapped[str] = mapped_column(String(32), default="全员")
    role: Mapped[str] = mapped_column(String(16), default="employee")  # admin / employee
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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


class QuizSession(Base):
    """一次测验的完整状态：题目、正确答案、作答。

    正确答案只存在这张表里，接口响应里永远不会带出去。
    客户端拿到的只有一个 session_id——它能提交答案，但改不了答案。
    """

    __tablename__ = "quiz_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    employee: Mapped[str] = mapped_column(String(32), index=True, default="local")
    chapter: Mapped[str] = mapped_column(String(64), index=True, default="")
    chapter_title: Mapped[str] = mapped_column(String(128), default="")
    questions: Mapped[str] = mapped_column(Text, default="[]")  # 含 answer_index，不下发
    answers: Mapped[str] = mapped_column(Text, default="[]")
    score: Mapped[int] = mapped_column(default=0)
    total: Mapped[int] = mapped_column(default=0)
    passed: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(default=None)


class LLMCall(Base):
    """一次模型调用的成本与延迟记录（可观测性的原始数据）。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, default="")
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
    trace_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    tool: Mapped[str] = mapped_column(String(64), index=True, default="")
    ok: Mapped[bool] = mapped_column(default=True)
    latency_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Trace(Base):
    """一次请求的链路：谁问的、走了哪个 Agent、各阶段耗时、花了多少 token。

    指标表回答「平均多快」，这张表回答「这一单为什么慢」——
    线上排障时前者没用，后者才有用。
    """

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True, default="ask")  # ask/coach/assistant
    user: Mapped[str] = mapped_column(String(64), index=True, default="")
    agent: Mapped[str] = mapped_column(String(32), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    refused: Mapped[bool] = mapped_column(default=False)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    total_ms: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    steps: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class PendingAction(Base):
    """Agent 提议、等人确认的写操作。

    为什么单独建表而不是塞进 chat_messages：它是有状态机的东西
    （pending → applying → applied/failed/rejected/expired），
    而且要能被「另一次请求」读到并处理。消息日志承担不了这个。
    """

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    employee: Mapped[str] = mapped_column(String(32), index=True, default="local")
    kind: Mapped[str] = mapped_column(String(32), default="append_faq")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 预览文案给前端直接渲染，省得前端为了显示一句话去解析 payload
    preview: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str] = mapped_column(String(32), default="")


class SessionSummary(Base):
    """会话的滚动摘要：早期对话压缩成一段话，替代硬截断。

    存 covered_until_id 而不是消息条数：消息 id 单调递增，
    用它标记「摘要已经覆盖到哪一条」，下次加载从这条之后开始，
    不会因为并发写入而错位。
    """

    __tablename__ = "session_summaries"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    covered_until_id: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    with SessionLocal() as session:
        yield session
