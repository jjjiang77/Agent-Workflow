"""极简图调度器：节点注册 + 条件边 + 循环上限 + 失败兜底。

刻意不引入 LangGraph / CrewAI：整个控制流不到 100 行，
评审可以一眼看完「谁在什么时候把控制权交给谁」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from core.message import Message


@dataclass
class NodeResult:
    """节点执行结果。

    约定：业务数据写进 state，messages 只记录交接事实，方便审计。
    """
    messages: List[Message] = field(default_factory=list)


class Graph:
    def __init__(self, max_steps: int = 12):
        self._nodes: Dict[str, Any] = {}
        self._edges: Dict[str, Optional[str]] = {}
        self._routers: Dict[str, tuple] = {}
        self._entry: Optional[str] = None
        self.max_steps = max_steps

    # ---------- 构图 API ----------

    def add_node(self, name: str, agent: Any) -> "Graph":
        if name in self._nodes:
            raise ValueError(f"节点 {name!r} 重复注册")
        self._nodes[name] = agent
        return self

    def add_edge(self, src: str, dst: Optional[str]) -> "Graph":
        self._require_node(src)
        if dst is not None:
            self._require_node(dst)
        self._edges[src] = dst
        return self

    def add_conditional_edge(
        self,
        src: str,
        router: Callable[[Any], str],
        mapping: Dict[str, Optional[str]],
    ) -> "Graph":
        """router(state) 返回一个键，mapping 把键映射到下一个节点；None 表示结束。"""
        self._require_node(src)
        for dst in mapping.values():
            if dst is not None:
                self._require_node(dst)
        self._routers[src] = (router, mapping)
        return self

    def set_entry(self, name: str) -> "Graph":
        self._require_node(name)
        self._entry = name
        return self

    def _require_node(self, name: str) -> None:
        if name not in self._nodes:
            raise KeyError(f"节点 {name!r} 未注册")

    # ---------- 执行 ----------

    def _next_node(self, current: str, state: Any) -> Optional[str]:
        if current in self._routers:
            router, mapping = self._routers[current]
            key = router(state)
            if key not in mapping:
                raise KeyError(
                    f"节点 {current!r} 的路由返回了未定义的键 {key!r}，"
                    f"可选键: {list(mapping)}"
                )
            return mapping[key]
        return self._edges.get(current)

    def run(self, state: Any, ctx: Any) -> None:
        if self._entry is None:
            raise RuntimeError("未设置入口节点")

        current: Optional[str] = self._entry
        steps = 0
        visits: Dict[str, int] = {}
        reason = "end"
        state.write("graph", "status", "running")
        ctx.bus.emit(
            "run_start",
            run_id=ctx.run_id,
            task=state.task,
            entry=current,
            max_steps=self.max_steps,
        )

        while True:
            if current is None:
                reason = "end"
                break
            if steps >= self.max_steps:
                reason = "max_steps"
                break

            steps += 1
            node_name = current
            agent = self._nodes[node_name]

            # 回头路走第二次就算一轮迭代，用于展示「循环上限」真的在生效
            if visits.get(node_name, 0) > 0:
                state.write("graph", "iteration", state.iteration + 1)
            visits[node_name] = visits.get(node_name, 0) + 1

            ctx.bus.emit("node_start", node=node_name, step=steps)
            try:
                result = agent.run(state, ctx) or NodeResult()
            except Exception as exc:  # 单节点失败不炸整条流水线，落盘后退出
                state.write("graph", "status", f"failed:{node_name}")
                state.record_step(steps, node_name, ok=False)
                state.save(ctx.state_path)
                ctx.bus.emit(
                    "error",
                    node=node_name,
                    step=steps,
                    error=f"{type(exc).__name__}: {exc}",
                )
                reason = "error"
                break

            for msg in result.messages:
                ctx.bus.message(msg)

            state.record_step(steps, node_name)
            state.save(ctx.state_path)
            ctx.bus.emit("node_end", node=node_name, step=steps)

            nxt = self._next_node(node_name, state)
            ctx.bus.emit("edge", src=node_name, dst=nxt, step=steps)
            current = nxt

        if reason == "max_steps":
            state.write("graph", "status", "max_steps")
            ctx.bus.emit("error", error="达到最大步数上限", max_steps=self.max_steps)
        elif reason == "end":
            state.write("graph", "status", "done" if state.verified else "stopped")

        state.save(ctx.state_path)
        ctx.bus.emit("run_end", status=state.status, steps=steps, reason=reason)
