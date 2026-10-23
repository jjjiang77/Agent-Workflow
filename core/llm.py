"""LLM 调用封装：重试 + JSON 解析。

独立一层的目的：Agent 只关心 prompt 和字段，
网络抖动、限流、模型把 JSON 包进 Markdown 代码块这些脏活都在这里消化。
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional


class LLMOutputError(ValueError):
    """模型输出无法解析成约定结构。"""


def _repair_escapes(text: str) -> str:
    r"""模型偶尔会在字符串里写 \d、\_ 这类非法转义，补一个反斜杠让它合法。"""
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)


def parse_json(text: str) -> dict:
    """容错解析：剥代码块 -> 截取首尾花括号 -> 修复非法转义，逐级降级。"""
    s = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()

    candidates = [s]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        candidates.append(s[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    for candidate in candidates:
        try:
            return json.loads(_repair_escapes(candidate))
        except json.JSONDecodeError:
            continue

    raise LLMOutputError(f"模型输出无法解析为 JSON：{s[:200]}")


class LLM:
    def __init__(
        self,
        client,
        model: str = "qwen-plus",
        max_retries: int = 3,
        on_retry: Optional[Callable[[int, str, int], None]] = None,
    ):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.on_retry = on_retry

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        """调用一次对话接口；失败按指数退避重试，最终失败向上抛。"""
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                if attempt == self.max_retries - 1:
                    raise
                wait = 2 ** attempt
                if self.on_retry:
                    self.on_retry(attempt + 1, str(exc), wait)
                time.sleep(wait)
        raise RuntimeError("unreachable")
