"""培训教练：Function Calling 循环 + 三个工具。

和知识问答的分工：
- 问答（flow.py）走确定性图，必须可控、可引用、可拒答；
- 教练（这里）走 Agent 工具循环，要能自己判断「该查资料还是该出题」，
  灵活性优先于确定性。

两条路径用不同技术是刻意的：不是所有场景都适合 Agent，也不是所有场景
都适合固定流程，选错的那个会既慢又难测。
"""

from .agent.loop import AgentLoop
from .agent.memory import Memory
from .agent.roles import COACH_PROMPT
from .tools import TOOL_FUNCTIONS, TOOLS


def build_coach(memory: Memory | None = None) -> AgentLoop:
    return AgentLoop(
        name="培训教练",
        system_prompt=COACH_PROMPT,
        tools=TOOLS,
        tool_functions=TOOL_FUNCTIONS,
        memory=memory or Memory("coach-default", max_messages=20),
        temperature=0.3,
    )


def coach_stream(message: str, session_id: str):
    """流式对话，事件格式与问答一致，前端可以复用同一套渲染。"""
    coach = build_coach(Memory(session_id, max_messages=20))
    for chunk in coach.chat_stream(message):
        if chunk:
            yield {"type": "token", "text": chunk}
    yield {"type": "meta", "trace": coach.trace, "usage": coach.usage}
    yield {"type": "done"}
