"""脚本化 Agent：不调用 LLM，用来验证调度骨架是否真的跑通。

它们走的是和真实 Agent 完全相同的 BaseAgent 模板，
阶段二只需要把 invoke 换成 LLM 调用，其余代码不动。
"""
from __future__ import annotations

import json
import re

from agents.base import BaseAgent
from core.graph import NodeResult
from core.message import Message

BUGGY_CODE = """def load_config(path):
    with open(path) as f:
        return eval(f.read())
"""

EVAL_PATTERN = re.compile(r"\beval\s*\(")


class MockCoder(BaseAgent):
    name = "coder"

    def build_prompt(self, state) -> str:
        return f"请根据任务写一段 Python 代码：{state.task}"

    def invoke(self, ctx, prompt: str, state) -> str:
        return BUGGY_CODE

    def parse_output(self, raw: str):
        return raw.strip()

    def apply(self, state, payload, ctx) -> NodeResult:
        state.write("coder", "code", payload)
        state.write("coder", "language", "python")
        return NodeResult(messages=[
            Message("coder", "reviewer", "handoff", {"chars": len(payload)})
        ])


class MockReviewer(BaseAgent):
    name = "reviewer"

    def build_prompt(self, state) -> str:
        return f"请审查以下代码，按 JSON 返回 issues/verdict/summary：\n{state.current_code}"

    def invoke(self, ctx, prompt: str, state) -> str:
        code = state.current_code
        issues = []
        for lineno, line in enumerate(code.splitlines(), 1):
            if EVAL_PATTERN.search(line):
                issues.append({
                    "line": lineno,
                    "severity": "high",
                    "message": "使用 eval 执行外部输入，存在代码注入风险",
                })
        result = {
            "issues": issues,
            "verdict": "reject" if issues else "pass",
            "summary": f"发现 {len(issues)} 个高等级问题" if issues else "未发现问题",
        }
        return json.dumps(result, ensure_ascii=False)

    def parse_output(self, raw: str):
        return json.loads(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        state.write("reviewer", "issues", payload["issues"])
        state.write("reviewer", "verdict", payload["verdict"])
        state.write("reviewer", "review_summary", payload["summary"])
        receiver = "fixer" if payload["verdict"] == "reject" else "verifier"
        return NodeResult(messages=[
            Message("reviewer", receiver, "handoff", {
                "verdict": payload["verdict"],
                "issues": len(payload["issues"]),
            })
        ])


class MockFixer(BaseAgent):
    name = "fixer"

    def build_prompt(self, state) -> str:
        return f"请修复以下问题并给出完整代码：{state.issues}\n{state.current_code}"

    def invoke(self, ctx, prompt: str, state) -> str:
        fixed = EVAL_PATTERN.sub("ast.literal_eval(", state.current_code)
        if "import ast" not in fixed:
            fixed = "import ast\n" + fixed
        return fixed

    def parse_output(self, raw: str):
        return raw

    def apply(self, state, payload, ctx) -> NodeResult:
        state.write("fixer", "patched_code", payload)
        state.write("fixer", "fix_notes", state.fix_notes + ["eval -> ast.literal_eval"])
        return NodeResult(messages=[
            Message("fixer", "reviewer", "handoff", {"chars": len(payload)})
        ])


class MockVerifier(BaseAgent):
    name = "verifier"

    def build_prompt(self, state) -> str:
        return f"请验证修复后的代码是否可用：\n{state.current_code}"

    def invoke(self, ctx, prompt: str, state) -> str:
        code = state.current_code
        problems = []
        try:
            compile(code, "<verify>", "exec")
        except SyntaxError as exc:
            problems.append(f"语法错误: {exc}")
        if EVAL_PATTERN.search(code):
            problems.append("仍存在 eval 调用")
        report = {
            "passed": not problems,
            "report": "编译通过，问题已消除" if not problems else "; ".join(problems),
        }
        return json.dumps(report, ensure_ascii=False)

    def parse_output(self, raw: str):
        return json.loads(raw)

    def apply(self, state, payload, ctx) -> NodeResult:
        state.write("verifier", "verified", payload["passed"])
        state.write("verifier", "verify_report", payload["report"])
        receiver = "end" if payload["passed"] else "fixer"
        return NodeResult(messages=[
            Message("verifier", receiver, "result", {"passed": payload["passed"]})
        ])
