"""培训闭环：章节 → 测验 → 学习记录 → 掌握度。

章节不单独建表，直接从知识库文档派生——加一份文档就多一章，
「资料管理」和「培训课程」是同一份数据的两种看法。这样避免了两处维护、
两份数据打架的经典问题。
"""

import re

from sqlalchemy import select

from . import llm
from .agent.rag import knowledge_base
from .db import LearnRecord, get_session

PASS_RATIO = 0.8  # 答对 80% 视为通过，和《培训课程目录》里的口径一致


def list_chapters() -> list[dict]:
    """把知识库文档聚成章节列表，附小节数与学习状态。"""
    knowledge_base.load()
    grouped: dict[str, dict] = {}
    for chunk in knowledge_base.chunks:
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


def _chapter_chunks(chapter_id: str) -> list[dict]:
    knowledge_base.load()
    return [c for c in knowledge_base.chunks if c["source"] == chapter_id]


def generate_quiz(chapter_id: str, count: int = 5) -> list[dict]:
    """为某一章生成测验题。

    优先让模型出题（题干自然、干扰项合理）；模型不可用时退化为填空题，
    保证培训流程不会因为一次 API 失败而中断——这是内部工具的基本要求。
    """
    chunks = _chapter_chunks(chapter_id)
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
        raw = llm.chat_json(prompt, "你是企业培训出题老师，只根据给定资料出题，不出资料外的内容。")
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


def submit_quiz(
    chapter_id: str,
    answers: list[int],
    correct: list[int],
    employee: str = "local",
) -> dict:
    """判分并落库。只存分数，不存题目正文——题目由前端持有，服务端无需重复保存。"""
    total = len(correct)
    score = sum(1 for i, c in enumerate(correct) if i < len(answers) and answers[i] == c)
    passed = total > 0 and score / total >= PASS_RATIO
    with get_session() as session:
        session.add(
            LearnRecord(
                employee=employee,
                chapter=chapter_id,
                score=score,
                total=total,
                passed=passed,
            )
        )
        session.commit()
    return {"score": score, "total": total, "passed": passed, "ratio": round(score / max(total, 1), 2)}


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
