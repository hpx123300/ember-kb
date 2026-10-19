"""培训闭环：章节 → 测验 → 学习记录 → 掌握度。

章节不单独建表，直接从知识库文档派生——加一份文档就多一章，
「资料管理」和「培训课程」是同一份数据的两种看法。这样避免了两处维护、
两份数据打架的经典问题。
"""

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from . import llm
from .agent.rag import knowledge_base
from .db import LearnRecord, QuizSession, get_session

PASS_RATIO = 0.8  # 答对 80% 视为通过，和《培训课程目录》里的口径一致


def _visible(chunk: dict, audiences: set[str] | None) -> bool:
    if audiences is None:
        return True
    meta = chunk.get("meta") or {}
    tokens = {t.strip() for t in str(meta.get("audience", "")).split("/") if t.strip()}
    return not tokens or bool(tokens & audiences)


def list_chapters(audiences: set[str] | None = None) -> list[dict]:
    """把知识库文档聚成章节列表，附小节数与学习状态。

    audiences 决定"这个人能看到哪些章节"——销售不该在课程列表里
    看到客服的处理口径，哪怕只是标题。
    """
    knowledge_base.load()
    grouped: dict[str, dict] = {}
    for chunk in knowledge_base.chunks:
        if not _visible(chunk, audiences):
            continue
        source = chunk["source"]
        item = grouped.setdefault(
            source,
            {
                "id": source,
                "title": chunk["title"] if not grouped else chunk["title"],
                "category": (chunk.get("meta") or {}).get("category", ""),
                "audience": (chunk.get("meta") or {}).get("audience", ""),
                "sections": [],
            },
        )
        if chunk["title"] not in item["sections"]:
            item["sections"].append(chunk["title"])

    chapters = list(grouped.values())
    # 用文档的第一个小节标题做章节名不好读，改取文件名里的语义部分
    for c in chapters:
        c["title"] = _chapter_title(c["id"], c["sections"])
    chapters.sort(key=lambda c: (c["category"], c["id"]))
    return chapters


def _chapter_title(source: str, sections: list[str]) -> str:
    """章节名优先取文档 H1（第一个小节之前的标题常被当作 source），
    取不到就用文件名中文化。
    """
    stem = source.replace(".md", "")
    mapping = {
        "handbook-attendance": "考勤与假期",
        "handbook-expense": "报销与采购",
        "product-overview": "产品手册",
        "sales-playbook": "销售话术与异议处理",
        "onboarding-first-week": "新人第一周",
        "support-faq": "客户常见问题",
        "security-policy": "信息安全与合规",
        "training-catalog": "培训课程与考核",
        "team-collaboration": "部门职责与协作",
    }
    return mapping.get(stem, sections[0] if sections else stem)


def _chapter_chunks(chapter_id: str, audiences: set[str] | None = None) -> list[dict]:
    knowledge_base.load()
    return [
        c
        for c in knowledge_base.chunks
        if c["source"] == chapter_id and _visible(c, audiences)
    ]


def generate_quiz(
    chapter_id: str,
    count: int = 5,
    audiences: set[str] | None = None,
) -> list[dict]:
    """为某一章生成测验题。

    优先让模型出题（题干自然、干扰项合理）；模型不可用时退化为填空题，
    保证培训流程不会因为一次 API 失败而中断——这是内部工具的基本要求。
    """
    chunks = _chapter_chunks(chapter_id, audiences)
    if not chunks:
        return []
    count = max(1, min(count, 8))
    context = "\n\n".join(f"【{c['title']}】\n{c['text']}" for c in chunks[:8])

    prompt = f"""以下是公司培训资料《{chapter_id}》的内容：
{context}

请基于以上内容出 {count} 道单选题，只考察资料里明确写到的信息，不要考资料外的知识。
只输出 JSON：{{"questions": [{{"question": "题干", "options": ["A选项","B选项","C选项","D选项"], "answer_index": 0, "explanation": "为什么选它，引用原文要点", "knowledge_point": "对应小节标题"}}]}}
"""
    try:
        raw = llm.chat_json(prompt, "你是企业培训出题老师，只根据给定资料出题，不出资料外的内容。", role="quiz")
        questions = _validate_quiz(raw)
        if questions:
            return questions[:count]
    except Exception:  # noqa: BLE001
        pass
    return _fallback_quiz(chunks, count)


def _validate_quiz(raw: dict) -> list[dict]:
    """结构化校验：模型返回的题必须字段齐全、答案下标合法、选项不重复。

    这一层比提示词可靠得多——模型偶尔会漏 answer_index 或给出 5 个选项，
    直接丢给前端会渲染成一道没法作答的题。
    """
    items = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    valid: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
        try:
            answer_index = int(item.get("answer_index", -1))
        except (TypeError, ValueError):
            continue
        if not question or len(options) < 3 or not (0 <= answer_index < len(options)):
            continue
        if len(set(options)) != len(options):
            continue
        valid.append(
            {
                "question": question,
                "options": options[:4],
                "answer_index": answer_index,
                "explanation": str(item.get("explanation", "")).strip(),
                "knowledge_point": str(item.get("knowledge_point", "")).strip(),
            }
        )
    return valid


def _fallback_quiz(chunks: list[dict], count: int) -> list[dict]:
    """兜底：从原文里挖空生成填空题式单选题，保证「至少能考」。"""
    questions: list[dict] = []
    for chunk in chunks:
        text = chunk["text"]
        match = re.search(r"(\d{1,4})\s*(元|天|个工作日|次|小时|个月|%)", text)
        if not match:
            continue
        number = match.group(1)
        sentence = text[max(0, match.start() - 30) : match.start() + 40].replace("\n", " ")
        distractors = {str(int(number) + 1), str(max(1, int(number) - 1)), str(int(number) + 10)}
        options = [number] + sorted(distractors - {number})[:3]
        questions.append(
            {
                "question": f"根据资料填空：「{sentence}」中缺失的数字是？",
                "options": options,
                "answer_index": 0,
                "explanation": f"原文为「{sentence}」。",
                "knowledge_point": chunk["title"],
            }
        )
        if len(questions) >= count:
            break
    return questions


def create_quiz_session(
    chapter_id: str,
    count: int = 5,
    employee: str = "local",
    chapter_title: str = "",
    audiences: set[str] | None = None,
) -> dict:
    """出一套题并开一次测验会话。

    关键点：正确答案和解析只写进 quiz_sessions 表，响应里只有题干和选项。
    旧版本把 correct 交给前端、提交时再传回来，改一个请求体就能拿满分——
    培训数据一旦能刷，学情就没有任何意义。
    """
    questions = generate_quiz(chapter_id, count=count, audiences=audiences)
    if not questions:
        return {"session_id": "", "questions": [], "chapter": chapter_id, "count": 0}

    session_id = uuid.uuid4().hex[:16]
    with get_session() as session:
        session.add(
            QuizSession(
                id=session_id,
                employee=employee,
                chapter=chapter_id,
                chapter_title=chapter_title or chapter_id,
                questions=json.dumps(questions, ensure_ascii=False),
                total=len(questions),
                status="open",
            )
        )
        session.commit()

    return {
        "session_id": session_id,
        "chapter": chapter_id,
        "chapter_title": chapter_title or chapter_id,
        "count": len(questions),
        # 只下发题干与选项，answer_index / explanation 留在服务端
        "questions": [
            {"question": q["question"], "options": q["options"], "knowledge_point": q.get("knowledge_point", "")}
            for q in questions
        ],
    }


def submit_quiz_session(session_id: str, answers: list[int], employee: str = "local") -> dict:
    """按会话判分。答案从库里取，前端传什么都不影响对错。"""
    with get_session() as session:
        record = session.get(QuizSession, session_id)
        if record is None:
            raise ValueError("测验不存在或已过期")
        if record.employee != employee:
            raise ValueError("这不是你的测验")
        if record.status != "open":
            raise ValueError("这套题已经提交过了")

        questions = json.loads(record.questions or "[]")
        correct = [int(q.get("answer_index", -1)) for q in questions]
        total = len(correct)
        details = []
        score = 0
        for i, expected in enumerate(correct):
            given = answers[i] if i < len(answers) else -1
            ok = given == expected
            score += int(ok)
            details.append(
                {
                    "index": i,
                    "your_answer": given,
                    "correct_answer": expected,
                    "ok": ok,
                    "explanation": questions[i].get("explanation", ""),
                }
            )

        passed = total > 0 and score / total >= PASS_RATIO
        record.answers = json.dumps(answers[:total])
        record.score = score
        record.passed = passed
        record.status = "submitted"
        record.submitted_at = datetime.now(timezone.utc)

        session.add(
            LearnRecord(
                employee=employee,
                chapter=record.chapter,
                score=score,
                total=total,
                passed=passed,
            )
        )
        session.commit()

    return {
        "score": score,
        "total": total,
        "passed": passed,
        "ratio": round(score / max(total, 1), 2),
        "pass_ratio": PASS_RATIO,
        "details": details,
    }


def mastery(employee: str = "local") -> dict:
    """学情画像：按章节聚合最好成绩与通过情况，找出最该补的章节。

    用「最好成绩」而不是「平均分」，因为培训的目标是掌握，
    考两次一次 60 一次 90，应该记 90。
    """
    with get_session() as session:
        rows = session.execute(
            select(LearnRecord).where(LearnRecord.employee == employee)
        ).scalars().all()

    by_chapter: dict[str, dict] = {}
    for r in rows:
        item = by_chapter.setdefault(
            r.chapter, {"chapter": r.chapter, "attempts": 0, "best": 0, "passed": False, "last": None}
        )
        item["attempts"] += 1
        item["best"] = max(item["best"], r.score)
        item["passed"] = item["passed"] or bool(r.passed)
        item["last"] = r.created_at.isoformat()

    chapters = list_chapters()
    titles = {c["id"]: c["title"] for c in chapters}
    items = []
    for chapter in chapters:
        record = by_chapter.get(chapter["id"])
        items.append(
            {
                "chapter": chapter["id"],
                "title": chapter["title"],
                "category": chapter["category"],
                "sections": len(chapter["sections"]),
                "attempts": record["attempts"] if record else 0,
                "best": record["best"] if record else 0,
                "passed": bool(record and record["passed"]),
                "last": record["last"] if record else None,
            }
        )

    studied = [i for i in items if i["attempts"] > 0]
    passed = [i for i in items if i["passed"]]
    weak = sorted(
        [i for i in items if i["attempts"] > 0 and not i["passed"]],
        key=lambda i: i["best"],
    )
    untouched = [i for i in items if i["attempts"] == 0]
    return {
        "total_chapters": len(items),
        "studied": len(studied),
        "passed": len(passed),
        "completion": round(len(passed) / max(len(items), 1), 3),
        "chapters": items,
        "weak": weak[:3],
        "untouched": [i["title"] for i in untouched][:4],
    }
