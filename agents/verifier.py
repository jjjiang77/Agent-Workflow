"""验证 Agent：先跑确定性检查（编译 + 静态分析），再让模型判断问题是否真的解决。

模型说不通过就一定不通过；模型说通过还要编译通过才放行 —— 不把最终判断权交给 LLM。
"""
from __future__ import annotations

from agents.base import LLMAgent
from core.graph import NodeResult
from core.llm import parse_json
from core.message import Message
from tools.static_analysis import analyze_source

SYSTEM_PROMPT = """你是一个验证 Agent。判断修复后的代码是否满足要求。

判断标准：
1. 审查意见提到的问题是否真的被解决
2. 是否引入了新的问题（安全、正确性）
3. 代码是否满足原始任务的功能要求

注意：语法检查结果由系统给出，是确定性结论，不要推翻它。

只输出一个 JSON 对象，不要 Markdown 代码块：
{"passed": true 或 false, "report": "验证结论和依据"}
"""


def compile_check(code: str):
    try:
        compile(code, "<generated>", "exec")
        return True, "编译通过"
    except SyntaxError as exc:
        return False, f"语法错误（第 {exc.lineno} 行）：{exc.msg}"


class VerifierAgent(LLMAgent):
    name = "verifier"
    system_prompt = SYSTEM_PROMPT

    def build_prompt(self, state) -> str:
        issues = "\n".join(
            f"- 第 {i['line']} 行 [{i['severity']}] {i['message']}" for i in state.issues
        ) or "- 无"
        return (
            f"原始任务：{state.task}\n"
            f"需要确认已解决的问题：\n{issues}\n\n"
            f"修复后代码：\n```python\n{state.current_code}\n```"
        )

    def invoke(self, ctx, prompt: str, state) -> str:
        code = state.current_code
        ok, msg = compile_check(code)
        report = analyze_source(code)
        ctx.bus.emit("tool_call", node=self.name, tool="compile_check",
                     ok=ok, detail=msg)
        ctx.bus.emit("tool_call", node=self.name, tool="analyze_source")
        ctx.bus.emit("tool_result", node=self.name, tool="analyze_source", result=report)
        return super().invoke(
            ctx,
            f"{prompt}\n\n===== 确定性检查 =====\n编译检查：{msg}\n{report}",
            state,
        )

    def parse_output(self, raw: str) -> dict:
        return parse_json(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        ok, msg = compile_check(state.current_code)
        passed = bool(payload.get("passed", False)) and ok

        report = str(payload.get("report", ""))
        if not ok:
            report = f"编译失败：{msg}。{report}"

        state.write("verifier", "verified", passed)
        state.write("verifier", "verify_report", report)

        receiver = "end" if passed else "fixer"
        return NodeResult(messages=[
            Message("verifier", receiver, "result", {
                "passed": passed,
                "compiled": ok,
            })
        ])
