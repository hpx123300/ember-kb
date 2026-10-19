"""待确认动作：Agent 提议，人确认，系统执行。

为什么要把「提议」和「执行」拆成两个时刻：

工具一旦既能读又能写，模型的一次幻觉就会变成线上资料的一次真实修改。
中间加一次人的确认，不是流程繁琐，是把不可逆操作变成可审计的一步——
谁提的、提了什么、谁批的、什么时候批的，全都留痕。

四条必须成立的约束，缺一条这套机制就只是装饰：

1. **只能执行一次**。用条件更新（WHERE status='pending'）抢占，
   而不是先查后改——先查后改在并发下会执行两次。
2. **过期不能执行**。过期不是"还能批但提醒一下"，是直接拒绝。
3. **只有发起人能确认**，管理员例外。否则 A 可以批准 B 的写操作。
4. **执行失败要留下失败状态**，不能回滚成 pending 让人反复点。

状态机：
    pending ──approve──> applying ──成功──> applied
       │                     └──失败──> failed
       ├──reject──> rejected
       └──超时────> expired
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .config import KNOWLEDGE_DIR
from .db import PendingAction, get_session

# 确认窗口。给太短来不及看，给太长会让「昨天批的」在今天生效——都不好。
ACTION_TTL_SECONDS = 30 * 60

# 一个人同时挂着的待确认动作上限，防止模型每轮都提一条把界面刷爆
MAX_PENDING_PER_USER = 5


def _naive_utcnow() -> datetime:
    """SQLite 存的是不带时区的时间，比较前统一成 naive UTC。

    直接用 aware 的 datetime 去比 naive 的列值会抛 TypeError，
    而这个错误只会在「刚好有过期动作」时出现——测试很难覆盖到。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str:
    """序列化成带时区的 ISO 串。

    SQLite 取回来的是 naive datetime，直接 isoformat() 会得到
    `2026-10-19T07:30:00` 这种没有时区标记的串，前端 `new Date()` 会按**本地时间**解析，
    东八区就整整差 8 小时——表现为倒计时一打开就是「已过期」。
    """
    if not value:
        return ""
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.isoformat()


def _row_to_dict(row: PendingAction) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "preview": row.preview,
        "employee": row.employee,
        "session_id": row.session_id,
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "result": row.result,
    }


def propose(
    *,
    session_id: str,
    employee: str,
    kind: str,
    payload: dict,
    preview: str,
) -> dict:
    """登记一条待确认动作。只写库，不动任何文件。"""
    now = _naive_utcnow()
    with get_session() as session:
        pending_count = len(
            session.execute(
                select(PendingAction.id).where(
                    PendingAction.employee == employee,
                    PendingAction.status == "pending",
                )
            ).all()
        )
        if pending_count >= MAX_PENDING_PER_USER:
            return {
                "ok": False,
                "error": f"你还有 {pending_count} 条待确认的动作，先处理完再提新的",
            }

        action = PendingAction(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            employee=employee,
            kind=kind,
            payload=json.dumps(payload, ensure_ascii=False),
            status="pending",
            preview=preview[:500],
            created_at=now,
            expires_at=now + timedelta(seconds=ACTION_TTL_SECONDS),
        )
        session.add(action)
        session.commit()
        return {"ok": True, "action": _row_to_dict(action)}


def list_actions(employee: str, is_admin: bool = False, status: str = "") -> list[dict]:
    with get_session() as session:
        stmt = select(PendingAction).order_by(PendingAction.created_at.desc())
        if not is_admin:
            stmt = stmt.where(PendingAction.employee == employee)
        if status:
            stmt = stmt.where(PendingAction.status == status)
        rows = session.execute(stmt.limit(50)).scalars().all()
    return [_row_to_dict(r) for r in rows]


def approve(action_id: str, employee: str, is_admin: bool = False) -> dict:
    """确认并执行。重复调用不报错，也不重复执行。"""
    with get_session() as session:
        row = session.get(PendingAction, action_id)
        if row is None:
            return {"ok": False, "error": "动作不存在"}
        if row.employee != employee and not is_admin:
            return {"ok": False, "error": "无权确认别人的动作"}

        # 已经处理过的，直接把当前状态回去——重复点击不该是错误
        if row.status != "pending":
            return {
                "ok": row.status == "applied",
                "status": row.status,
                "idempotent": True,
                "result": row.result,
                "action": _row_to_dict(row),
            }

        if row.expires_at and row.expires_at < _naive_utcnow():
            row.status = "expired"
            row.decided_at = _naive_utcnow()
            row.result = "已超过确认窗口，未执行"
            session.commit()
            return {"ok": False, "status": "expired", "error": "已超过确认窗口，请让 Agent 重新提交"}

        # 抢占：只有把 pending 改成 applying 成功的那一次请求才有权执行
        claimed = session.execute(
            update(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.status == "pending")
            .values(status="applying", decided_at=_naive_utcnow(), decided_by=employee)
        ).rowcount
        session.commit()
        if claimed == 0:
            session.refresh(row)
            return {
                "ok": row.status == "applied",
                "status": row.status,
                "idempotent": True,
                "error": "该动作正在被处理或已处理完",
                "action": _row_to_dict(row),
            }

        payload = json.loads(row.payload or "{}")

    # 执行放在事务外：写文件是慢操作，不该持有数据库事务
    try:
        result = _apply(row.kind, payload)
        status, message = "applied", result
    except Exception as exc:  # noqa: BLE001
        status, message = "failed", f"执行失败：{exc}"

    with get_session() as session:
        row = session.get(PendingAction, action_id)
        row.status = status
        row.result = message
        row.decided_at = row.decided_at or _naive_utcnow()
        session.commit()
        return {
            "ok": status == "applied",
            "status": status,
            "result": message,
            "action": _row_to_dict(row),
        }


def reject(action_id: str, employee: str, reason: str = "", is_admin: bool = False) -> dict:
    with get_session() as session:
        row = session.get(PendingAction, action_id)
        if row is None:
            return {"ok": False, "error": "动作不存在"}
        if row.employee != employee and not is_admin:
            return {"ok": False, "error": "无权处理别人的动作"}
        if row.status != "pending":
            return {"ok": False, "status": row.status, "idempotent": True, "action": _row_to_dict(row)}

        row.status = "rejected"
        row.result = reason[:200] or "已拒绝"
        row.decided_at = _naive_utcnow()
        row.decided_by = employee
        session.commit()
        return {"ok": True, "status": "rejected", "action": _row_to_dict(row)}


def _apply(kind: str, payload: dict) -> str:
    """真正执行写操作。目前只有一种：追加一条 FAQ 到章节文档。"""
    if kind != "append_faq":
        raise ValueError(f"不支持的动作类型：{kind}")

    chapter = str(payload.get("chapter", "")).strip()
    question = str(payload.get("question", "")).strip()
    answer = str(payload.get("answer", "")).strip()

    if not question or not answer:
        raise ValueError("问题和答案都不能为空")
    if len(question) > 200 or len(answer) > 1000:
        raise ValueError("内容过长：问题不超过 200 字，答案不超过 1000 字")

    # 只允许追加到已有的 .md，不接受任意路径——否则模型给个 ../../.env 就写进别处了
    safe_name = chapter.rsplit("/", 1)[-1]
    path = (KNOWLEDGE_DIR / safe_name).resolve()
    if path.parent != KNOWLEDGE_DIR.resolve():
        raise ValueError("非法的章节路径")
    if not path.exists() or path.suffix != ".md":
        raise ValueError(f"章节不存在：{safe_name}")

    body = path.read_text(encoding="utf-8").rstrip()
    block = f"\n\n### {question}\n\n{answer}\n"
    path.write_text(body + block, encoding="utf-8")

    # 文档指纹变了，下一次检索会自动重建索引；这里主动触发一次，让改动立即可见
    _apply_reindex()
    return f"已写入《{safe_name}》，检索索引已重建"


def _apply_reindex() -> None:
    """让检索立刻看到新内容。

    抽成独立函数是为了测试能替换掉它——测试里的知识库目录是临时的，
    真去重建索引会读到另一份语料，测出来的东西没有意义。
    """
    from .agent.rag import knowledge_base

    knowledge_base.load()
