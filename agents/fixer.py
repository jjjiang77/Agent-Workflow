"""修复 Agent：根据审查意见返回完整的新代码，而不是 diff。"""
from __future__ import annotations

from agents.base import LLMAgent
from core.graph import NodeResult
from core.llm import parse_json
from core.message import Message

SYSTEM_PROMPT = """你是一个修复 Agent。根据审查意见修改代码。

规则：
- 只改必要的地方，不要重写无关逻辑
- 必须返回修改后的完整代码，不要只给 diff
- 不要引入新的危险调用
- 如果审查意见互相矛盾，优先修复 high 等级问题

只输出一个 JSON 对象，不要 Markdown 代码块：
{"code": "修改后的完整代码", "notes": "改了什么、为什么"}
"""


class FixerAgent(LLMAgent):
    name = "fixer"
    system_prompt = SYSTEM_PROMPT

    def build_prompt(self, state) -> str:
        issues = "\n".join(
            f"- 第 {i['line']} 行 [{i['severity']}] {i['message']}" for i in state.issues
        ) or "- （无结构化问题，请根据总结自行判断）"
        return (
            f"审查总结：{state.review_summary}\n"
            f"问题列表：\n{issues}\n\n"
            f"当前代码：\n```python\n{state.current_code}\n```"
        )

    def parse_output(self, raw: str) -> dict:
        return parse_json(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        code = str(payload.get("code", "")).strip()
        if not code:
            raise ValueError("fixer 返回的 code 为空")
        notes = str(payload.get("notes", ""))

        state.write("fixer", "patched_code", code)
        state.write("fixer", "fix_notes", state.fix_notes + [notes])
        return NodeResult(messages=[
            Message("fixer", "reviewer", "handoff", {
                "chars": len(code),
                "notes": notes,
            })
        ])
