"""链路追踪：把一次请求拆成若干阶段，记下每段耗时。

和 metrics 的分工：

- metrics 是聚合视角（平均延迟、P95、成本），用来回答「这几天贵不贵、慢不慢」；
- traces 是单次视角，用来回答「这一单为什么慢了 4 秒」。

只有聚合没有单次，排障时只能猜；只有单次没有聚合，看不出趋势。两个都要。

trace_id 用 ContextVar 传递，模型调用与工具调用不需要额外参数就能挂到
当前请求上——改一版就要动十几个函数签名，那种设计没人愿意维护。
"""

import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select

from .db import Trace, get_session

_current_trace: ContextVar[str] = ContextVar("ember_trace_id", default="")
_current_recorder: ContextVar["TraceRecorder | None"] = ContextVar("ember_recorder", default=None)


def current_trace_id() -> str:
    return _current_trace.get()


def record_step(name: str, ms: int, ok: bool = True, detail: str = "") -> None:
    """给当前请求的链路记一步。没有活跃的 recorder 时静默跳过——
    节点代码不需要知道自己是在线上被追踪，还是在评测脚本里裸跑。
    """
    rec = _current_recorder.get()
    if rec is not None:
        rec.step(name, ms, ok=ok, detail=detail)


class TraceRecorder:
    """一次请求的账本。用法：

        rec = TraceRecorder("ask", user.employee_key, question)
        with rec:
            rec.step("retrieve", ms=12)
        rec.finish(agent="知识问答", refused=False)
    """

    def __init__(self, kind: str, user: str = "", question: str = ""):
        self.id = uuid.uuid4().hex[:16]
        self.kind = kind
        self.user = user
        self.question = (question or "")[:500]
        self.steps: list[dict] = []
        self.agent = ""
        self.refused = False
        self.ok = True
        self.error = ""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._started = 0.0
        self._token = None
        self._recorder_token = None

    def __enter__(self) -> "TraceRecorder":
        self._started = time.perf_counter()
        self._token = _current_trace.set(self.id)
        self._recorder_token = _current_recorder.set(self)
        return self

    def __exit__(self, *exc_info) -> bool:
        # 流式响应会被 Starlette 丢到线程池里逐块产出，进入和退出可能不在同一个
        # context 副本里，此时 reset(token) 会抛 "created in a different Context"。
        # 能 reset 就 reset（嵌套场景语义正确），不能就直接覆盖成默认值。
        try:
            if self._token is not None:
                _current_trace.reset(self._token)
        except (ValueError, RuntimeError):
            _current_trace.set("")
        try:
            if self._recorder_token is not None:
                _current_recorder.reset(self._recorder_token)
        except (ValueError, RuntimeError):
            _current_recorder.set(None)
        return False

    def step(self, name: str, ms: int, ok: bool = True, detail: str = "") -> None:
        self.steps.append({"name": name, "ms": max(0, int(ms)), "ok": bool(ok), "detail": detail[:200]})

    @contextmanager
    def stage(self, name: str):
        """给一段代码计时，异常也会落一步失败记录，方便定位卡在哪。"""
        started = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.step(name, int((time.perf_counter() - started) * 1000), ok=False, detail=str(exc))
            raise
        else:
            self.step(name, int((time.perf_counter() - started) * 1000))

    def finish(self, agent: str = "", refused: bool = False, ok: bool = True, error: str = "") -> dict:
        self.agent = agent or self.agent
        self.refused = refused
        self.ok = ok
        self.error = error
        total_ms = int((time.perf_counter() - self._started) * 1000) if self._started else 0
        # token 在模型调用处已经按 trace_id 落过库，这里汇总回来，
        # 让一条链路自己就能回答「这一步花了多少」
        try:
            from sqlalchemy import func, select

            from .db import LLMCall

            with get_session() as session:
                row = session.execute(
                    select(func.sum(LLMCall.prompt_tokens), func.sum(LLMCall.completion_tokens)).where(
                        LLMCall.trace_id == self.id
                    )
                ).first()
            if row:
                self.prompt_tokens = int(row[0] or 0)
                self.completion_tokens = int(row[1] or 0)
        except Exception:  # noqa: BLE001
            pass
        payload = {
            "trace_id": self.id,
            "agent": self.agent,
            "refused": refused,
            "total_ms": total_ms,
            "steps": self.steps,
        }
        try:
            with get_session() as session:
                session.add(
                    Trace(
                        id=self.id,
                        kind=self.kind,
                        user=self.user,
                        agent=self.agent,
                        question=self.question,
                        refused=bool(refused),
                        ok=bool(ok),
                        error=(error or "")[:300],
                        total_ms=total_ms,
                        prompt_tokens=self.prompt_tokens,
                        completion_tokens=self.completion_tokens,
                        steps=json.dumps(self.steps, ensure_ascii=False),
                    )
                )
                session.commit()
        except Exception:  # noqa: BLE001
            # 记账失败不能影响主流程，这里刻意吞掉
            pass
        return payload


def _row_to_dict(row: Trace) -> dict:
    try:
        steps = json.loads(row.steps or "[]")
    except json.JSONDecodeError:
        steps = []
    return {
        "trace_id": row.id,
        "kind": row.kind,
        "user": row.user,
        "agent": row.agent,
        "question": row.question,
        "refused": bool(row.refused),
        "ok": bool(row.ok),
        "error": row.error,
        "total_ms": row.total_ms,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "steps": steps,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_traces(limit: int = 20, kind: str | None = None) -> list[dict]:
    with get_session() as session:
        stmt = select(Trace).order_by(Trace.created_at.desc()).limit(max(1, min(limit, 100)))
        rows = session.execute(stmt).scalars().all()
    items = [_row_to_dict(r) for r in rows]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    return items


def get_trace(trace_id: str) -> dict | None:
    with get_session() as session:
        row = session.get(Trace, trace_id)
        return _row_to_dict(row) if row else None
