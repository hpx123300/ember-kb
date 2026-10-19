"""会话记忆：启动时从 SQLite 回填历史，保证跨轮对话不"失忆"。

截断在**安全边界**进行。大模型 API 对消息序列有两条硬约束：
1. 每条 tool 消息必须紧跟在带 tool_calls 的 assistant 消息之后；
2. 带 tool_calls 的 assistant 消息，其每个 tool_call_id 都必须有对应的 tool 响应。

按条数硬截断会破坏这两种配对，导致 API 直接报 400。因此出参前统一修剪一次。
"""

from typing import Any

_TOOL_ROLE = "tool"


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把消息序列修剪为 API 可接受的形态。

    丢弃：开头的孤儿 tool 消息；缺少任一 tool 响应的 assistant.tool_calls 组。
    """
    start = 0
    while start < len(messages) and messages[start].get("role") == _TOOL_ROLE:
        start += 1

    result: list[dict[str, Any]] = []
    total = len(messages)
    i = start
    while i < total:
        msg = messages[i]
        role = msg.get("role")
        tool_calls = msg.get("tool_calls")

        if role == "assistant" and tool_calls:
            expected = set()
            for tc in tool_calls:
                tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
                if tc_id:
                    expected.add(tc_id)
            j = i + 1
            answered: set[str] = set()
            while j < total and messages[j].get("role") == _TOOL_ROLE:
                answered.add(messages[j].get("tool_call_id"))
                j += 1
            if expected and expected.issubset(answered):
                result.extend(messages[i:j])
            i = j
            continue

        if role == _TOOL_ROLE:
            i += 1
            continue

        result.append(msg)
        i += 1

    return result


class Memory:
    """会话记忆：跨轮对话的上下文，超长时压缩而不是直接丢掉。

    硬截断的问题是它会安静地删掉最早说过的话——用户第一轮说「我只问销售相关」，
    聊到第二十轮这句话就没了，模型开始答客服问题，而且没有任何迹象表明它忘了。
    所以超过阈值时，把早期对话压成一段摘要留着，只丢原文不丢信息。

    压缩只处理**连续的、纯文本的 user/assistant 前缀**：
    一旦遇到带 tool_calls 的消息或 tool 消息就停下，避免切断配对导致 API 400。
    """

    def __init__(
        self,
        session_id: str,
        max_messages: int = 24,
        keep_recent: int = 8,
        employee: str | None = None,
    ):
        self.session_id = session_id
        self.employee = employee
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.summary = ""
        self.covered_until_id = 0
        self._messages: list[dict[str, Any]] = []
        self._load_from_db()

    def _load_from_db(self) -> None:
        try:
            from ..chat_history import load_messages_since, load_summary

            self.summary, self.covered_until_id = load_summary(
                self.session_id,
                employee=self.employee,
            )
            self._messages = load_messages_since(
                self.session_id,
                after_id=self.covered_until_id,
                limit=self.max_messages * 2,
                employee=self.employee,
            )
        except Exception:  # noqa: BLE001
            self._messages = []

    def add(self, message: dict[str, Any]) -> None:
        self._messages.append(dict(message, id=message.get("id", 0)))
        # 上限放在两倍：超过 max_messages 先由压缩处理，压缩失败才走这条兜底线
        if len(self._messages) > self.max_messages * 2:
            self._messages = self._messages[-self.max_messages * 2 :]

    def get_messages(self) -> list[dict[str, Any]]:
        # id 是内部记账用的，发给模型前必须剥掉，否则部分兼容实现会拒绝未知字段
        clean = [{k: v for k, v in m.items() if k != "id"} for m in self._messages]
        return sanitize_messages(clean)

    def compact_candidates(self) -> list[dict[str, Any]]:
        """返回可以送去摘要的早期消息；不够长或边界不安全时返回空。"""
        if len(self._messages) <= self.max_messages:
            return []
        cutoff = len(self._messages) - self.keep_recent
        if cutoff <= 0:
            return []

        candidates: list[dict[str, Any]] = []
        for message in self._messages[:cutoff]:
            # 只摘要纯文本轮次：工具调用与工具结果必须和它们的 assistant 消息待在一起
            if message.get("role") not in ("user", "assistant"):
                return candidates
            if message.get("tool_calls"):
                return candidates
            candidates.append(message)
        return candidates

    def apply_compaction(self, summary: str, consumed: list[dict[str, Any]]) -> int:
        """用摘要替换已消费的消息，返回被替换的条数。"""
        if not consumed:
            return 0
        ids = [m["id"] for m in consumed if m.get("id")]
        if ids:
            self.covered_until_id = max(self.covered_until_id, max(ids))
        self.summary = summary
        self._messages = self._messages[len(consumed) :]
        return len(consumed)

    def trim_hard(self) -> int:
        """压缩不可用时的降级：直接丢掉早期消息，保证上下文不会无限增长。

        只在「摘要失败」或「边界不安全」时走到这里。不是首选路径——
        它一定会丢信息，所以调用点要记一笔，让问题在日志里看得见。
        """
        if len(self._messages) <= self.max_messages:
            return 0
        dropped = len(self._messages) - self.max_messages
        self._messages = self._messages[-self.max_messages :]
        return dropped

    def clear(self) -> None:
        self._messages = []
        self.summary = ""
        self.covered_until_id = 0
