"""薪火 · FastAPI 后端。

五个产品面：
- /api/ask        知识问答（LangGraph 条件边：检索 → 判断 → 回答/拒答，SSE 流式）
- /api/coach      培训教练（Function Calling，带员工画像）
- /api/assistant  多 Agent 协作（调度员 → 知识问答 / 培训教练 / 学情助手）
- /api/quiz       培训测验（服务端出题与判分）
- /api/library    资料管理（上传/删除/重建索引）

横切的两层：
- /api/auth/*     登录与当前用户，所有业务接口都要带 Bearer token
- /api/traces     单次请求的链路回放（管理员）

构建后的前端由本服务静态托管（单页回退到 index.html）。
"""

import json
import sys
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import actions, library, metrics, observability, supervisor, training  # noqa: E402
from backend.app.agent.loop import AgentError  # noqa: E402
from backend.app.agent.roles import PROMPT_VERSION  # noqa: E402
from backend.app.auth import (  # noqa: E402
    CurrentUser,
    create_token,
    get_current_user,
    require_admin,
    seed_users,
    verify_password,
)
from backend.app.chat_history import clear_messages, load_messages, save_message  # noqa: E402
from backend.app.coach import coach_stream  # noqa: E402
from backend.app.db import User, get_session, init_db  # noqa: E402
from backend.app.flow import ask_stream  # noqa: E402
from backend.app.profile import build_profile, profile_prompt  # noqa: E402

init_db()
seed_users()

app = FastAPI(title="薪火 Ember API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _err(exc: Exception, status: int = 500) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=status)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------- 健康检查 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "prompt_version": PROMPT_VERSION, "app": "ember"}


# ---------------- 登录 ----------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@app.post("/api/auth/login")
def login(req: LoginRequest):
    with get_session() as session:
        user = session.query(User).filter(User.username == req.username.strip()).first()
        if user is None or not verify_password(req.password, user.password_hash):
            # 不区分「用户不存在」和「密码错」，避免被用来枚举账号
            return JSONResponse({"error": "账号或密码不正确"}, status_code=401)
        token = create_token(user)
        payload = CurrentUser(user.id, user.username, user.name, user.department, user.role).to_dict()
    return {"token": token, "user": payload}


@app.get("/api/auth/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return user.to_dict()


# ---------------- 知识问答 ----------------
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    category: str = ""


@app.post("/api/ask")
def ask(req: AskRequest, user: CurrentUser = Depends(get_current_user)):
    """流式问答。事件：token → meta（引用/拒答/覆盖率/trace_id）→ done。"""
    question = req.question.strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)

    def gen():
        try:
            for event in ask_stream(
                question,
                req.category,
                audiences=user.allowed_audiences(),
                user=user.employee_key,
            ):
                yield _sse(event)
        except AgentError as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "error": f"生成失败：{exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- 多 Agent 协作 ----------------
class AssistantRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/assistant")
def assistant(req: AssistantRequest, user: CurrentUser = Depends(get_current_user)):
    """调度员决定找谁，专员作答，返回用了哪个 Agent 与整条链路。"""
    try:
        return supervisor.answer(
            req.question.strip(),
            employee=user.employee_key,
            name=user.name,
            audiences=user.allowed_audiences(),
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------- 培训教练 ----------------
class CoachRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = None


@app.post("/api/coach")
def coach(req: CoachRequest, user: CurrentUser = Depends(get_current_user)):
    session_id = (req.session_id or "").strip() or f"coach-{uuid.uuid4().hex[:8]}"
    save_message(session_id, "user", req.message.strip())
    profile_text = profile_prompt(build_profile(user.employee_key, user.allowed_audiences()))

    def gen():
        full = ""
        try:
            yield _sse({"type": "session", "session_id": session_id})
            for event in coach_stream(
                req.message.strip(),
                session_id,
                employee=user.employee_key,
                audiences=user.allowed_audiences(),
                profile_text=profile_text,
            ):
                if event.get("type") == "token":
                    full += event["text"]
                yield _sse(event)
            if full:
                save_message(session_id, "assistant", full)
        except AgentError as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "error": f"对话失败：{exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/coach/history")
def coach_history(session_id: str, user: CurrentUser = Depends(get_current_user)):
    return load_messages(session_id, limit=50)


@app.delete("/api/coach/history")
def coach_history_clear(session_id: str, user: CurrentUser = Depends(get_current_user)):
    clear_messages(session_id)
    return {"ok": True}


# ---------------- 待确认动作（Agent 写操作的闸门） ----------------
@app.get("/api/actions")
def list_actions(status: str = "", user: CurrentUser = Depends(get_current_user)):
    """列出待确认动作。普通员工只看自己的，管理员看全部。"""
    return actions.list_actions(user.employee_key, is_admin=user.is_admin, status=status)


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, user: CurrentUser = Depends(get_current_user)):
    """确认并执行。重复调用不会重复执行——前端多点两下是常态。"""
    result = actions.approve(action_id, user.employee_key, is_admin=user.is_admin)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str, reason: str = "", user: CurrentUser = Depends(get_current_user)):
    result = actions.reject(action_id, user.employee_key, reason=reason, is_admin=user.is_admin)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# ---------------- 培训 ----------------
@app.get("/api/chapters")
def chapters(user: CurrentUser = Depends(get_current_user)):
    return training.list_chapters(user.allowed_audiences())


class QuizRequest(BaseModel):
    chapter: str
    count: int = 5


@app.post("/api/quiz")
def quiz(req: QuizRequest, user: CurrentUser = Depends(get_current_user)):
    """开一次测验。响应里只有题干和选项——答案留在服务端。"""
    chapters_ = training.list_chapters(user.allowed_audiences())
    matched = next((c for c in chapters_ if c["id"] == req.chapter or c["title"] == req.chapter), None)
    if matched is None:
        return JSONResponse({"error": "没有这一章，或没有权限"}, status_code=404)
    try:
        data = training.create_quiz_session(
            matched["id"],
            count=req.count,
            employee=user.employee_key,
            chapter_title=matched["title"],
        )
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    if not data["questions"]:
        return JSONResponse({"error": "这一章没有可用的内容，换个章节试试"}, status_code=404)
    return data


class QuizSubmit(BaseModel):
    session_id: str
    answers: list[int]


@app.post("/api/quiz/submit")
def quiz_submit(req: QuizSubmit, user: CurrentUser = Depends(get_current_user)):
    """按会话判分。前端只能提交作答，改不了标准答案。"""
    try:
        return training.submit_quiz_session(req.session_id, req.answers, employee=user.employee_key)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/mastery")
def mastery(user: CurrentUser = Depends(get_current_user)):
    return build_profile(user.employee_key, user.allowed_audiences())


# ---------------- 资料管理 ----------------
@app.get("/api/library/docs")
def docs(user: CurrentUser = Depends(get_current_user)):
    return library.list_docs()


@app.get("/api/library/stats")
def library_stats(user: CurrentUser = Depends(get_current_user)):
    return library.stats()


class DocPayload(BaseModel):
    filename: str
    content: str


@app.post("/api/library/docs")
def doc_create(payload: DocPayload, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    try:
        return library.save_doc(payload.filename, payload.content)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/api/library/docs/{filename}")
def doc_delete(filename: str, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    try:
        library.delete_doc(filename)
        return {"ok": True}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# ---------------- 可观测性 ----------------
@app.get("/api/metrics")
def api_metrics(days: int = 7, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    return metrics.summary(days=max(1, min(days, 90)))


@app.get("/api/traces")
def api_traces(limit: int = 20, kind: str = "", user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    return observability.list_traces(limit=limit, kind=kind or None)


@app.get("/api/traces/{trace_id}")
def api_trace(trace_id: str, user: CurrentUser = Depends(get_current_user)):
    require_admin(user)
    trace = observability.get_trace(trace_id)
    if trace is None:
        return JSONResponse({"error": "链路不存在"}, status_code=404)
    return trace


# ---------------- 静态前端 ----------------
WEB_DIST = PROJECT_ROOT / "web" / "dist"

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "接口不存在"}, status_code=404)
    index = WEB_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {"error": "前端尚未构建：请先运行 npm install && npm run build（或在 web/ 下运行 npm run dev）"},
        status_code=404,
    )
