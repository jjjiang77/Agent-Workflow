"""审查 Agent：带静态分析工具，对代码给出结构化问题列表。"""
from __future__ import annotations

from agents.base import LLMAgent
from core.graph import NodeResult
from core.llm import parse_json
from core.message import Message
from tools.static_analysis import analyze_source

SYSTEM_PROMPT = """你是一个代码审查 Agent。只依据给出的代码和静态分析结果判断，不要臆造不存在的行号。

审查范围（按优先级）：
1. 会导致功能错误的问题
2. 真实可利用的安全问题（注入、危险调用、硬编码密钥）
3. 明显的资源泄漏、异常处理缺失

规则：
- 只报你有把握、能具体指出代码位置的真实问题；不确定就不要报
- 不要提出超出任务范围的加固建议，不要把「理论上可能」当成 high
- 每轮最多报 3 个问题，优先报 high
- severity 只能是 high / medium / low
- 只要存在 high 问题，verdict 必须是 reject；issues 为空时 verdict 必须是 pass
- line 用静态分析结果里的真实行号

只输出一个 JSON 对象，不要 Markdown 代码块：
{"issues": [{"line": 3, "severity": "high", "message": "问题描述"}], "verdict": "pass 或 reject", "summary": "一两句话总结"}
"""


class ReviewerAgent(LLMAgent):
    name = "reviewer"
    system_prompt = SYSTEM_PROMPT

    def build_prompt(self, state) -> str:
        prompt = (
            f"待审查代码（{state.language}）：\n"
            f"```python\n{state.current_code}\n```"
        )
        # 复审时收窄范围，否则模型会每轮提出新的加固建议，永远不收敛
        if state.iteration > 0:
            previous = "\n".join(
                f"- 第 {i['line']} 行 [{i['severity']}] {i['message']}"
                for i in state.issues
            ) or "- （上一轮无问题）"
            prompt += (
                f"\n\n===== 这是第 {state.iteration} 轮修复后的复审 =====\n"
                f"上一轮问题：\n{previous}\n\n"
                "复审规则：只判断上述问题是否已解决、这次修复是否引入了新的 high 问题；"
                "不要再提出新的加固建议，也不要把理论上的极端输入当成 high。"
            )
        return prompt

    def invoke(self, ctx, prompt: str, state) -> str:
        # 工具调用是确定性的，先跑再交给模型判断，避免模型自己猜行号
        report = analyze_source(state.current_code)
        ctx.bus.emit("tool_call", node=self.name, tool="analyze_source",
                     chars=len(state.current_code))
        ctx.bus.emit("tool_result", node=self.name, tool="analyze_source", result=report)
        return super().invoke(
            ctx,
            f"{prompt}\n\n===== 静态分析结果（工具产出）=====\n{report}",
            state,
        )

    def parse_output(self, raw: str) -> dict:
        return parse_json(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        raw_issues = payload.get("issues") or []
        issues = []
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            issues.append({
                "line": int(item.get("line") or 0),
                "severity": str(item.get("severity", "medium")).lower(),
                "message": str(item.get("message", "")),
            })

        # 硬规则兜底：有 high 问题就不允许 pass，防止模型自相矛盾
        verdict = str(payload.get("verdict", "")).lower()
        if any(i["severity"] == "high" for i in issues):
            verdict = "reject"
        elif verdict not in ("pass", "reject"):
            verdict = "reject" if issues else "pass"

        state.write("reviewer", "issues", issues)
        state.write("reviewer", "verdict", verdict)
        state.write("reviewer", "review_summary", str(payload.get("summary", "")))

        receiver = "fixer" if verdict == "reject" else "verifier"
        return NodeResult(messages=[
            Message("reviewer", receiver, "handoff", {
                "verdict": verdict,
                "issues": len(issues),
                "high": len([i for i in issues if i["severity"] == "high"]),
            })
        ])
