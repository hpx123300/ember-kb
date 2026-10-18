"""LLM 调用封装：文本 / JSON / 流式三种形态。

统一放在这里而不是散在各模块，是为了让「用哪个模型、温度多少、重试几次」
只有一处定义。换模型只改环境变量，换供应商只改 base_url。
"""

import json
import time

from openai import OpenAI

from . import metrics
from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from .llm_utils import with_retry


class LLMError(Exception):
    """模型不可用或返回不合法。"""


def _account(role: str, usage, latency_ms: int) -> None:
    """把一次调用的 token 与耗时记进指标表。

    放在 llm.py 这一层，是因为问答链路（flow / training）直接调这里，
    不经过 AgentLoop；记账只写在 AgentLoop 里的话，主路径的调用一次都记不上，
    学情页的成本卡片会永远显示 0。
    """
    if usage is None:
        metrics.record_llm_call("", role, DEEPSEEK_MODEL, 0, 0, latency_ms)
        return
    metrics.record_llm_call(
        "",
        role,
        DEEPSEEK_MODEL,
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        latency_ms,
    )


def _client() -> OpenAI:
    if not DEEPSEEK_API_KEY:
        raise LLMError("未配置 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填写")
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


@with_retry(attempts=3)
def chat_text(prompt: str, system: str, temperature: float = 0.2, role: str = "llm") -> str:
    started = time.perf_counter()
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=900,
    )
    _account(role, getattr(resp, "usage", None), int((time.perf_counter() - started) * 1000))
    content = resp.choices[0].message.content or ""
    if not content.strip():
        raise LLMError("模型返回空内容")
    return content.strip()


@with_retry(attempts=3)
def chat_text_stream(prompt: str, system: str, temperature: float = 0.3, role: str = "llm"):
    """流式输出。注意这里是普通函数返回生成器，重试只覆盖建连阶段。"""
    started = time.perf_counter()
    stream = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=900,
        stream=True,
        stream_options={"include_usage": True},
    )
    usage = None
    for chunk in stream:
        # 开了 include_usage 后，最后一个 chunk 的 choices 是空数组、只带 usage
        if not getattr(chunk, "choices", None):
            usage = getattr(chunk, "usage", None) or usage
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
    _account(role, usage, int((time.perf_counter() - started) * 1000))


@with_retry(attempts=3)
def chat_json(prompt: str, system: str, temperature: float = 0.2, role: str = "llm") -> dict:
    """让模型只输出一个 JSON 对象，容忍 ```json 代码块包裹。"""
    started = time.perf_counter()
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )
    _account(role, getattr(resp, "usage", None), int((time.perf_counter() - started) * 1000))
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise LLMError("模型返回空内容")
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("模型没有返回 JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 解析失败：{exc}") from exc
