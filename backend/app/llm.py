"""LLM 调用封装：文本 / JSON / 流式三种形态。

统一放在这里而不是散在各模块，是为了让「用哪个模型、温度多少、重试几次」
只有一处定义。换模型只改环境变量，换供应商只改 base_url。
"""

import json

from openai import OpenAI

from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from .llm_utils import with_retry


class LLMError(Exception):
    """模型不可用或返回不合法。"""


def _client() -> OpenAI:
    if not DEEPSEEK_API_KEY:
        raise LLMError("未配置 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填写")
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


@with_retry(attempts=3)
def chat_text(prompt: str, system: str, temperature: float = 0.2) -> str:
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=900,
    )
    content = resp.choices[0].message.content or ""
    if not content.strip():
        raise LLMError("模型返回空内容")
    return content.strip()


@with_retry(attempts=3)
def chat_text_stream(prompt: str, system: str, temperature: float = 0.3):
    """流式输出。注意这里是普通函数返回生成器，重试只覆盖建连阶段。"""
    stream = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=900,
        stream=True,
    )
    for chunk in stream:
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


@with_retry(attempts=3)
def chat_json(prompt: str, system: str, temperature: float = 0.2) -> dict:
    """让模型只输出一个 JSON 对象，容忍 ```json 代码块包裹。"""
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )
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
