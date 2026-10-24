"""不依赖 LLM 的单元测试：状态守卫、条件边、循环上限、失败兜底、JSON 容错。"""
from __future__ import annotations

import os
import tempfile
import unittest

from agents.base import BaseAgent
from core.context import RunContext
from core.graph import Graph, NodeResult
from core.llm import LLMOutputError, parse_json
from core.message import EventBus
from core.state import WorkflowState


class FunctionAgent(BaseAgent):
    """把普通函数包成节点，避免测试里构造 prompt。"""

    def __init__(self, name, fn):
        self.name = name
        self.fn = fn

    def run(self, state, ctx) -> NodeResult:
        self.fn(state, ctx)
        return NodeResult()


class StateTest(unittest.TestCase):
    def test_owner_guard(self):
        state = WorkflowState()
        with self.assertRaises(PermissionError):
            state.write("reviewer", "code", "print(1)")
        with self.assertRaises(KeyError):
            state.write("coder", "not_a_field", 1)

        state.write("coder", "code", "print(1)")
        self.assertEqual(state.code, "print(1)")

    def test_save_and_load(self):
        state = WorkflowState()
        state.write("system", "task", "demo")
        state.write("coder", "code", "x = 1")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state.save(path)
            loaded = WorkflowState.load(path)
            self.assertEqual(loaded.task, "demo")
            self.assertEqual(loaded.code, "x = 1")


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = EventBus(os.path.join(self.tmp.name, "run.log"))
        self.ctx = RunContext(run_id="test", run_dir=self.tmp.name, bus=self.bus)
        self.state = WorkflowState()
        self.state.write("system", "task", "t")

    def tearDown(self):
        self.bus.close()
        self.tmp.cleanup()

    def test_conditional_edge(self):
        visited = []
        flags = {"first": True}

        def reviewer(state, ctx):
            visited.append("reviewer")
            state.write("reviewer", "verdict", "reject" if flags["first"] else "pass")
            flags["first"] = False

        def fixer(state, ctx):
            visited.append("fixer")
            state.write("fixer", "patched_code", "fixed")

        def verifier(state, ctx):
            visited.append("verifier")
            state.write("verifier", "verified", True)

        graph = Graph(max_iterations=3, iteration_node="fixer")
        graph.add_node("reviewer", FunctionAgent("reviewer", reviewer))
        graph.add_node("fixer", FunctionAgent("fixer", fixer))
        graph.add_node("verifier", FunctionAgent("verifier", verifier))
        graph.set_entry("reviewer")
        graph.add_conditional_edge("reviewer", lambda s: s.verdict,
                                   {"reject": "fixer", "pass": "verifier"})
        graph.add_edge("fixer", "reviewer")
        graph.add_edge("verifier", None)

        graph.run(self.state, self.ctx)

        self.assertEqual(visited, ["reviewer", "fixer", "reviewer", "verifier"])
        self.assertEqual(self.state.iteration, 1)
        self.assertEqual(self.state.status, "done")

    def test_iteration_cap(self):
        calls = []

        def fixer(state, ctx):
            calls.append(1)
            state.write("fixer", "patched_code", "still bad")

        graph = Graph(max_steps=20, max_iterations=2, iteration_node="fixer")
        graph.add_node("fixer", FunctionAgent("fixer", fixer))
        graph.set_entry("fixer")
        graph.add_edge("fixer", "fixer")

        graph.run(self.state, self.ctx)

        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state.iteration, 2)
        self.assertEqual(self.state.status, "max_iterations")

    def test_node_failure_is_recorded(self):
        def boom(state, ctx):
            raise RuntimeError("boom")

        graph = Graph()
        graph.add_node("bad", FunctionAgent("bad", boom))
        graph.set_entry("bad")

        graph.run(self.state, self.ctx)

        self.assertTrue(self.state.status.startswith("failed"))
        self.assertTrue(os.path.exists(self.ctx.state_path))


class ParseJsonTest(unittest.TestCase):
    def test_plain_and_fenced(self):
        self.assertEqual(parse_json('{"a": 1}')["a"], 1)
        self.assertEqual(parse_json('```json\n{"a": 2}\n```')["a"], 2)

    def test_invalid_escape_is_repaired(self):
        # 模型经常把正则写进 JSON 字符串里，产生 \d 这种非法转义
        payload = parse_json(r'{"pattern": "\d+", "ok": true}')
        self.assertEqual(payload["pattern"], r"\d+")
        self.assertTrue(payload["ok"])

    def test_non_json_raises(self):
        with self.assertRaises(LLMOutputError):
            parse_json("这是一段普通文本，没有 JSON")


if __name__ == "__main__":
    unittest.main()
