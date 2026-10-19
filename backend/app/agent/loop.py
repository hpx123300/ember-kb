"""通用 Agent 循环：Function Calling 引擎（多 Agent 共用，提示词/工具/记忆可注入）。"""

import json
import re
import time
from types import SimpleNamespace
from typing import Callable

from openai import OpenAI

from .. import metrics
from ..config import AGENT_MAX_ITERATIONS, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from ..llm_utils import with_retry
from .memory import Memory

MAX_ITERATIONS = AGENT_MAX_ITERATIONS


def strip_thought(text: str) -> str:
    text = re.sub(r"<thought>.*?</thought>\s*", "", text, flags=re.DOTALL).strip()
    if "<thought>" in text:
        text = text.split("<thought>")[0].strip()
    return text


class AgentError(Exception):
    pass


def _result_flags(result: str) -> dict:
    """从工具返回里抽出可统计的信号。

    不解析完整 JSON：工具的返回有 list、dict、纯文本几种历史形态，
    解析会在某一种上炸掉，而评测只关心「这次调用是不是白跑」。
    代价是子串判断比较粗，所以字段名取窄一点，避免误判。
    """
    text = result or ""
    return {
        "empty": '"results": []' in text or "没有命中" in text or "没有检索到" in text,
        "degraded": '"degraded": "' in text and '"degraded": null' not in text,
        "failed": text.startswith("错误") or text.startswith("工具执行失败"),
    }



class AgentLoop:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[dict],
        tool_functions: dict,
        memory: Memory | None = None,
        max_iterations: int = MAX_ITERATIONS,
        temperature: float = 0.3,
    ):
        if not DEEPSEEK_API_KEY:
            raise AgentError("未配置 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填写")
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_functions = tool_functions
        self.memory = memory or Memory("default")
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        self.model = DEEPSEEK_MODEL
        self.trace: list[dict] = []
        # 结构化轨迹：trace 是给人看的字符串，tool_events 是给评测看的记录。
        # 两者都要，因为「人读得懂」和「程序数得清」是同一条链路的两种用途。
        self.tool_events: list[dict] = []
        self.fallback_used = False
        # 观测用：把会话与角色带到指标层，才能回答「哪个角色最贵、哪个工具最慢」
        self.session_id = getattr(self.memory, "session_id", "") or ""
        self.role = name
        # 本轮累计的 token 与耗时，供上层读取（流式路径只能在这里汇总）
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}

    def _record(self, step: str, content: str) -> None:
        self.trace.append({"step": step, "content": content})

    def _account_usage(self, usage, latency_ms: int) -> None:
        """把一次调用的 token 与耗时累加进本轮统计，并落一条明细。"""
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        self.usage["prompt_tokens"] += prompt_tokens
        self.usage["completion_tokens"] += completion_tokens
        self.usage["latency_ms"] += latency_ms
        metrics.record_llm_call(
            session_id=self.session_id,
            role=self.role,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    def _build_messages(self) -> list[dict]:
        """拼装本轮请求的消息序列（系统提示 + 经过安全修剪的记忆）。"""
        return [{"role": "system", "content": self.system_prompt}] + self.memory.get_messages()

    def _fallback_reply(self) -> str:
        """达到步数上限时的兜底：用已拿到的工具结果作答，不让整轮白跑。"""
        notes = [t["content"] for t in self.trace if t["step"] == "tool" and t["content"]]
        if not notes:
            return "抱歉，这个问题比较复杂，我没能在有限步骤内完成，请换个问法试试。"
        joined = "\n".join(f"- {n}" for n in notes[:5])
        return f"未能在限定步数内给出完整结论，但已检索到以下信息，供你参考：\n{joined}"

    @with_retry()
    def _call_llm(self, messages: list[dict]) -> dict:
        started = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools,
            tool_choice="auto",
            temperature=self.temperature,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._account_usage(getattr(resp, "usage", None), latency_ms)
        return resp.choices[0].message

    @with_retry()
    def _create_stream(self, messages: list[dict]):
        # include_usage 让最后一个 chunk 带上 token 用量；部分兼容实现不支持，
        # 调用方会退化成「本次不计 token」，不会因此失败。
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools,
            tool_choice="auto",
            temperature=self.temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

    def _log_tool_event(self, name: str, arguments: dict, result: str, latency_ms: int, ok: bool) -> None:
        """记一条结构化工具事件。

        评测脚本靠它算「选对工具没有、有没有重复调用、有没有白跑」。
        在工具层就记，而不是事后从 trace 字符串里正则抠——抠出来的东西
        一旦日志措辞改了就会静默失效。
        """
        flags = _result_flags(result)
        self.tool_events.append(
            {
                "tool": name,
                "arguments": arguments,
                "ok": ok,
                "latency_ms": latency_ms,
                "result_chars": len(result or ""),
                **flags,
            }
        )

    def _execute_tool(self, tool_call) -> str:
        name = tool_call.function.name
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        func: Callable | None = self.tool_functions.get(name)
        if func is None:
            # 未知工具也要记一笔：它通常意味着模型在幻觉工具名
            message = f"错误：未知工具 {name}"
            metrics.record_tool_call(self.session_id, self.role, name or "unknown", False, 0, "未知工具")
            self._log_tool_event(name or "unknown", arguments, message, 0, False)
            return message
        started = time.perf_counter()
        try:
            result = func(**arguments)
            latency_ms = int((time.perf_counter() - started) * 1000)
            metrics.record_tool_call(self.session_id, self.role, name, True, latency_ms)
            self._record("tool", f"{name}({json.dumps(arguments, ensure_ascii=False)}) -> {result}")
            self._log_tool_event(name, arguments, result, latency_ms, True)
            return result
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            message = f"工具执行失败：{exc}"
            metrics.record_tool_call(self.session_id, self.role, name, False, latency_ms, str(exc))
            self._log_tool_event(name, arguments, message, latency_ms, False)
            return message

    def chat(self, user_message: str, on_tool: Callable[[str], None] | None = None) -> tuple[str, list[dict]]:
        self.trace = []
        self.tool_events = []
        self.fallback_used = False
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}
        self.memory.add({"role": "user", "content": user_message})
        messages = self._build_messages()
        for _ in range(self.max_iterations):
            self._record("think", "正在分析问题并决定是否调用工具…")
            message = self._call_llm(messages)
            if not message.tool_calls:
                reply = strip_thought(message.content or "（模型未返回内容）")
                self.memory.add({"role": "assistant", "content": reply})
                self._record("answer", reply)
                return reply, self.trace
            self.memory.add({"role": "assistant", "content": message.content or "", "tool_calls": message.tool_calls})
            for tool_call in message.tool_calls:
                if on_tool:
                    on_tool(tool_call.function.name)
                result = self._execute_tool(tool_call)
                self.memory.add({"role": "tool", "tool_call_id": tool_call.id, "content": result})
            messages = self._build_messages()
        self.fallback_used = True
        reply = self._fallback_reply()
        self.memory.add({"role": "assistant", "content": reply})
        self._record("answer", reply)
        return reply, self.trace

    def chat_stream(self, user_message: str, on_tool: Callable[[str], None] | None = None):
        """流式版：工具调用轮不产出文本，最终回答逐块 yield。"""
        self.trace = []
        self.tool_events = []
        self.fallback_used = False
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}
        self.memory.add({"role": "user", "content": user_message})
        messages = self._build_messages()
        for _ in range(self.max_iterations):
            self._record("think", "正在分析问题并决定是否调用工具…")
            content = ""
            tool_calls: list[dict] = []
            usage = None
            started = time.perf_counter()
            stream = self._create_stream(messages)
            for chunk in stream:
                # 开了 include_usage 后，最后一个 chunk 的 choices 是空数组、只带 usage，
                # 直接取 choices[0] 会 IndexError，必须先判空。
                if not getattr(chunk, "choices", None):
                    usage = getattr(chunk, "usage", None) or usage
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    content += delta.content
                    yield delta.content
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        while len(tool_calls) <= idx:
                            tool_calls.append({"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls[idx]["arguments"] += tc.function.arguments
            self._account_usage(usage, int((time.perf_counter() - started) * 1000))
            if not tool_calls:
                content = strip_thought(content)
                self.memory.add({"role": "assistant", "content": content})
                self._record("answer", content)
                return
            tool_call_dicts = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in tool_calls
            ]
            self.memory.add({"role": "assistant", "content": content, "tool_calls": tool_call_dicts})
            call_objects = [
                SimpleNamespace(
                    id=tc["id"],
                    function=SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
                )
                for tc in tool_calls
            ]
            for tc in call_objects:
                if on_tool:
                    on_tool(tc.function.name)
                result = self._execute_tool(tc)
                self.memory.add({"role": "tool", "tool_call_id": tc.id, "content": result})
            messages = self._build_messages()
        self.fallback_used = True
        reply = self._fallback_reply()
        self.memory.add({"role": "assistant", "content": reply})
        self._record("answer", reply)
        yield reply
