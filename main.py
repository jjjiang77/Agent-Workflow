"""命令行入口：跑一次完整的代码审查 Workflow。

默认走真实 LLM（qwen-plus），--mock 走脚本化 Agent，便于离线调试调度。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Callable, Optional

from dotenv import load_dotenv
from openai import OpenAI

from agents.coder import CoderAgent
from agents.fixer import FixerAgent
from agents.mock import MockCoder, MockFixer, MockReviewer, MockVerifier
from agents.reviewer import ReviewerAgent
from agents.verifier import VerifierAgent
from core.context import RunContext
from core.graph import Graph
from core.llm import LLM
from core.message import EventBus
from core.state import WorkflowState

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def route_after_review(state) -> str:
    return "fix" if state.verdict == "reject" else "verify"


def route_after_verify(state) -> str:
    return "pass" if state.verified else "fix"


def build_graph(max_steps: int = 12, max_iterations: int = 3, mock: bool = False) -> Graph:
    if mock:
        nodes = [MockCoder(), MockReviewer(), MockFixer(), MockVerifier()]
    else:
        nodes = [CoderAgent(), ReviewerAgent(), FixerAgent(), VerifierAgent()]

    graph = Graph(max_steps=max_steps, max_iterations=max_iterations,
                  iteration_node="fixer")
    for agent in nodes:
        graph.add_node(agent.name, agent)

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
    elif event == "tool_call":
        print(f"       [工具] {record['node']} 调用 {record['tool']}")
    elif event == "llm_retry":
        print(f"       [重试] 第 {record['attempt']} 次失败，{record['wait']}s 后重试")
    elif event == "node_skipped":
        print(f"       [跳过] {record['node']}：{record['reason']}")
    elif event == "error":
        print(f"  [错误] {record.get('error')}")
    elif event == "run_end":
        print(f"  结束: status={record['status']} steps={record['steps']} reason={record['reason']}")


def _make_llm(bus: EventBus, model: str) -> LLM:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("未找到 DASHSCOPE_API_KEY：请在 .env 中配置，或加 --mock 离线运行")
    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return LLM(
        client,
        model=model,
        on_retry=lambda attempt, err, wait: bus.emit(
            "llm_retry", attempt=attempt, error=err, wait=wait
        ),
    )


def run(
    task: str,
    runs_dir: str = "runs",
    max_steps: int = 12,
    max_iterations: int = 3,
    mock: bool = False,
    model: str = "qwen-plus",
    event_sink: Optional[Callable[[dict], None]] = None,
) -> WorkflowState:
    load_dotenv()
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    run_dir = os.path.join(runs_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    def _on_event(record: dict) -> None:
        _print_event(record)
        if event_sink:
            event_sink(record)

    bus = EventBus(os.path.join(run_dir, "run.log"), callback=_on_event)
    llm = None if mock else _make_llm(bus, model)
    ctx = RunContext(run_id=run_id, run_dir=run_dir, bus=bus, llm=llm, model=model)

    state = WorkflowState()
    state.write("system", "task", task)
    state.save(ctx.state_path)

    print(f"run {run_id} -> {run_dir}")
    print(f"mode: {'mock（不调用 LLM）' if mock else model}")
    try:
        build_graph(max_steps, max_iterations, mock).run(state, ctx)
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
    parser.add_argument("--max-iterations", type=int, default=3, help="最多修复几轮")
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--mock", action="store_true", help="用脚本化 Agent 跑，不调用 LLM")
    args = parser.parse_args()

    state = run(
        args.task, args.runs_dir, args.max_steps,
        args.max_iterations, args.mock, args.model,
    )

    print("\n=== 运行结果 ===")
    print(f"status   : {state.status}")
    print(f"iteration: {state.iteration}")
    print(f"verdict  : {state.verdict}")
    print(f"verified : {state.verified}")
    print(f"报告     : {state.verify_report}")
    print(f"产物目录 : {args.runs_dir}/")

    if state.status.startswith("failed") or state.status in ("max_steps", "max_iterations"):
        sys.exit(1)


if __name__ == "__main__":
    main()
