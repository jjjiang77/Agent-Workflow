"""编码 Agent：把任务变成一份可运行的代码。"""
from __future__ import annotations

from agents.base import LLMAgent
from core.graph import NodeResult
from core.llm import parse_json
from core.message import Message

SYSTEM_PROMPT = """你是一个编码 Agent，负责根据任务写出一段可直接运行的 Python 代码。

要求：
- 只写完成任务需要的函数或类，不要写测试代码和示例调用
- 参数校验、异常处理要完整
- 禁止用 eval / exec 执行外部输入
- 代码必须是完整可编译的模块级代码

只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字：
{"code": "完整代码", "language": "python", "notes": "一句话说明思路"}
"""


class CoderAgent(LLMAgent):
    name = "coder"
    system_prompt = SYSTEM_PROMPT

    def build_prompt(self, state) -> str:
        return f"任务：{state.task}"

    def parse_output(self, raw: str) -> dict:
        return parse_json(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        code = str(payload.get("code", "")).strip()
        if not code:
            raise ValueError("coder 返回的 code 为空")
        state.write("coder", "code", code)
        state.write("coder", "language", str(payload.get("language", "python")))
        return NodeResult(messages=[
            Message("coder", "reviewer", "handoff", {
                "chars": len(code),
                "notes": payload.get("notes", ""),
            })
        ])
