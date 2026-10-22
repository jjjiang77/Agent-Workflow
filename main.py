"""命令行入口：跑一次完整的代码审查 Workflow。

阶段一使用脚本化 Agent（不消耗 API 额度），
阶段二会把 invoke 换成真实 LLM 调用，调度部分不动。
"""
from __future__ import annotations

import argparse
import os
import time

from agents.mock import MockCoder, MockFixer, MockReviewer, MockVerifier
from core.context import RunContext
from core.graph import Graph
from core.message import EventBus
from core.state import WorkflowState


def route_after_review(state) -> str:
    return "fix" if state.verdict == "reject" else "verify"


def route_after_verify(state) -> str:
    return "pass" if state.verified else "fix"


def build_graph(max_steps: int = 12) -> Graph:
    graph = Graph(max_steps=max_steps)
    graph.add_node("coder", MockCoder())
    graph.add_node("reviewer", MockReviewer())
    graph.add_node("fixer", MockFixer())
    graph.add_node("verifier", MockVerifier())

    graph.set_entry("coder")
    graph.add_edge("coder", "reviewer")
    graph.add_conditional_edge("reviewer", route_after_review, {
        "fix": "fixer",
        "verify": "verifier",
    })
    graph.add_edge("fixer", "reviewer")  # 修完重新审，形成闭环
    graph.add_conditional_edge("verifier", route_after_verify, {
        "pass": None,  # None = 流程结束
        "fix": "fixer",
    })
    return graph


def _print_event(record: dict) -> None:
    event = record.get("event")
    if event == "node_start":
        print(f"  [{record['step']:02d}] {record['node']} 开始")
    elif event == "edge":
        dst = record["dst"] or "END"
        print(f"       {record['src']} -> {dst}")
    elif event == "error":
        print(f"  [错误] {record.get('error')}")
    elif event == "run_end":
        print(f"  结束: status={record['status']} steps={record['steps']} reason={record['reason']}")


def run(task: str, runs_dir: str = "runs", max_steps: int = 12) -> WorkflowState:
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    run_dir = os.path.join(runs_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    bus = EventBus(os.path.join(run_dir, "run.log"), callback=_print_event)
    ctx = RunContext(run_id=run_id, run_dir=run_dir, bus=bus)

    state = WorkflowState()
    state.write("system", "task", task)
    state.save(ctx.state_path)

    print(f"run {run_id} -> {run_dir}")
    try:
        build_graph(max_steps).run(state, ctx)
    finally:
        bus.close()
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="多 Agent 代码审查 Workflow")
    parser.add_argument(
        "--task",
        default="写一个读取配置文件并解析 JSON 的函数，要求安全、有异常处理",
    )
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()

    state = run(args.task, args.runs_dir, args.max_steps)

    print("\n=== 运行结果 ===")
    print(f"status   : {state.status}")
    print(f"iteration: {state.iteration}")
    print(f"verdict  : {state.verdict}")
    print(f"verified : {state.verified}")
    print(f"报告     : {state.verify_report}")
    print(f"产物目录 : {args.runs_dir}/")


if __name__ == "__main__":
    main()
