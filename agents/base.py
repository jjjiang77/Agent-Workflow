"""所有 Agent 的统一骨架。

固定四步：build_prompt -> invoke -> parse_output -> apply。
- invoke 是唯一和「智能」有关的一步，阶段二会换成真实 LLM 调用；
- 其余三步是纯工程代码，子类只关心自己负责的字段。

这样新增一个节点只需要写一个子类，不用碰调度器。
"""
from __future__ import annotations

from core.graph import NodeResult


class BaseAgent:
    name = "base"

    def run(self, state, ctx) -> NodeResult:
        prompt = self.build_prompt(state)
        ctx.bus.emit("agent_prompt", node=self.name, prompt=prompt)
        raw = self.invoke(ctx, prompt, state)
        ctx.bus.emit("agent_raw", node=self.name, raw=raw)
        payload = self.parse_output(raw)
        return self.apply(state, payload, ctx)

    # ---------- 子类实现 ----------

    def build_prompt(self, state) -> str:
        raise NotImplementedError

    def invoke(self, ctx, prompt: str, state) -> str:
        """产出原始文本。阶段一由脚本化的 Mock Agent 实现。"""
        raise NotImplementedError

    def parse_output(self, raw: str):
        raise NotImplementedError

    def apply(self, state, payload, ctx) -> NodeResult:
        raise NotImplementedError
