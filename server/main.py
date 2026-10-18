"""薪火 · FastAPI 后端。

三个产品面：
- /api/ask      知识问答（LangGraph 条件边：检索 → 判断 → 回答/拒答，SSE 流式）
- /api/quiz     培训测验（结构化出题 + 判分 + 学情）
- /api/library  资料管理（上传/删除/重建索引）

构建后的前端由本服务静态托管（单页回退到 index.html）。
"""

import json
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import library, metrics, training  # noqa: E402
from backend.app.agent.loop import AgentError  # noqa: E402
from backend.app.agent.roles import PROMPT_VERSION  # noqa: E402
from backend.app.chat_history import clear_messages, load_messages, save_message  # noqa: E402
from backend.app.coach import coach_stream  # noqa: E402
from backend.app.db import init_db  # noqa: E402
from backend.app.flow import ask_stream  # noqa: E402

init_db()

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


# ---------------- 知识问答 ----------------
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    category: str = ""


@app.post("/api/ask")
def ask(req: AskRequest):
    """流式问答。事件：token → meta（引用/拒答/覆盖率）→ done。"""
    question = req.question.strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)

    def gen():
        try:
            for event in ask_stream(question, req.category):
                yield _sse(event)
        except AgentError as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "error": f"生成失败：{exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- 培训教练 ----------------
class CoachRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = None


@app.post("/api/coach")
def coach(req: CoachRequest):
    session_id = (req.session_id or "").strip() or f"coach-{uuid.uuid4().hex[:8]}"
    save_message(session_id, "user", req.message.strip())

    def gen():
        full = ""
        try:
            yield _sse({"type": "session", "session_id": session_id})
            for event in coach_stream(req.message.strip(), session_id):
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
def coach_history(session_id: str):
    return load_messages(session_id, limit=50)


@app.delete("/api/coach/history")
def coach_history_clear(session_id: str):
    clear_messages(session_id)
    return {"ok": True}


# ---------------- 培训 ----------------
@app.get("/api/chapters")
def chapters():
    return training.list_chapters()


class QuizRequest(BaseModel):
    chapter: str
    count: int = 5


@app.post("/api/quiz")
def quiz(req: QuizRequest):
    try:
        questions = training.generate_quiz(req.chapter, req.count)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    if not questions:
        return JSONResponse({"error": "这一章没有可用的内容，换个章节试试"}, status_code=404)
    return {
        "chapter": req.chapter,
        "questions": [
            {"question": q["question"], "options": q["options"], "knowledge_point": q["knowledge_point"]}
            for q in questions
        ],
        "answers": [q["answer_index"] for q in questions],
        "explanations": [q["explanation"] for q in questions],
    }


class QuizSubmit(BaseModel):
    chapter: str
    answers: list[int]
    correct: list[int]


@app.post("/api/quiz/submit")
def quiz_submit(req: QuizSubmit):
    if len(req.answers) != len(req.correct):
        return JSONResponse({"error": "作答数量与题目数量不一致"}, status_code=400)
    return training.submit_quiz(req.chapter, req.answers, req.correct)


@app.get("/api/mastery")
def mastery():
    return training.mastery()


# ---------------- 资料管理 ----------------
@app.get("/api/library/docs")
def docs():
    return library.list_docs()


@app.get("/api/library/stats")
def library_stats():
    return library.stats()


class DocPayload(BaseModel):
    filename: str
    content: str


@app.post("/api/library/docs")
def doc_create(payload: DocPayload):
    try:
        return library.save_doc(payload.filename, payload.content)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/api/library/docs/{filename}")
def doc_delete(filename: str):
    try:
        library.delete_doc(filename)
        return {"ok": True}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# ---------------- 可观测性 ----------------
@app.get("/api/metrics")
def api_metrics(days: int = 7):
    return metrics.summary(days=max(1, min(days, 90)))


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
