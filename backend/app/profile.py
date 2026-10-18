"""员工画像：把学习记录变成一段可以塞进提示词的「长期记忆」。

为什么不用向量库：

要记的是「谁在哪一章没过线、谁还没开始、最近在问什么」——这些是结构化
事实，一条 SQL 就能算准。把它们 embedding 进向量库，反而要承担检索不准
和幻觉的风险：模型可能把「张三没过」说到李四头上。事实用表存，语义用检索，
两者不要混。

画像的作用是让教练"记得你"：同一句「考考我」，新人和老员工的题目难度、
章节选择应该不同。
"""

from sqlalchemy import select

from . import training
from .db import ChatMessage, get_session


def recent_questions(employee: str, limit: int = 5) -> list[str]:
    """最近问过的问题。只取 user 角色，且只取问句本身。"""
    with get_session() as session:
        rows = (
            session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id.like(f"%{employee}%"))
                .where(ChatMessage.role == "user")
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [r.content[:60] for r in rows if r.content]


def build_profile(employee: str, audiences: set[str] | None = None) -> dict:
    """汇总一个人的学习状态。字段都来自库，不做推断。"""
    data = training.mastery(employee)
    # 画像也要服从权限：看不到的章节不该出现在「你没学」的清单里
    visible_ids = {c["id"] for c in training.list_chapters(audiences)}
    chapters = [c for c in data["chapters"] if c["chapter"] in visible_ids]
    weak = sorted(
        [c for c in chapters if c["attempts"] > 0 and not c["passed"]],
        key=lambda c: c["best"],
    )
    untouched = [c["title"] for c in chapters if c["attempts"] == 0]

    return {
        "employee": employee,
        "total_chapters": data["total_chapters"],
        "studied": data["studied"],
        "passed": data["passed"],
        "completion": data["completion"],
        # 前端要按章节渲染掌握度，这里把原始明细一并带出，避免再请求一次
        "chapters": [c for c in data["chapters"] if c["chapter"] in visible_ids],
        "weak": [{"title": c["title"], "best": c["best"], "attempts": c["attempts"]} for c in weak[:3]],
        "untouched": untouched[:4],
        "recent_questions": recent_questions(employee),
    }


def profile_prompt(profile: dict) -> str:
    """把画像压成一段给模型看的事实，控制在 200 字内——
    画像太长会稀释系统提示词里真正重要的规矩。
    """
    lines = [
        f"【这位员工的情况】共 {profile['total_chapters']} 章，已通过 {profile['passed']} 章。"
    ]
    if profile["weak"]:
        detail = "、".join(f"《{w['title']}》最好 {w['best']} 分" for w in profile["weak"])
        lines.append(f"没过线的章节：{detail}。")
    if profile["untouched"]:
        lines.append(f"还没开始的章节：{'、'.join(profile['untouched'])}。")
    if profile["recent_questions"]:
        lines.append(f"最近问过：{'；'.join(profile['recent_questions'][:3])}。")
    if not profile["weak"] and not profile["untouched"]:
        lines.append("所有章节都已通过，可以出更细的场景题。")
    return "\n".join(lines)
