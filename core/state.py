"""Workflow 共享状态：节点之间唯一的数据通道。

三条规则：
1. 节点之间不互相调用，只通过 state 传递数据；
2. 每个字段有唯一归属，越权写入直接抛异常，避免多个 Agent 互相污染；
3. 每步执行完落盘，state.json 是当前真相，run.log 是历史轨迹。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

# 字段 -> 唯一有权写入的节点。
# system 表示入口初始化，graph 表示调度器自身。
FIELD_OWNERS: Dict[str, str] = {
    "task": "system",
    "code": "coder",
    "language": "coder",
    "issues": "reviewer",
    "verdict": "reviewer",
    "review_summary": "reviewer",
    "patched_code": "fixer",
    "fix_notes": "fixer",
    "verified": "verifier",
    "verify_report": "verifier",
    "iteration": "graph",
    "status": "graph",
    "trace": "graph",
}


@dataclass
class WorkflowState:
    task: str = ""
    code: str = ""
    language: str = "python"
    issues: List[Dict[str, Any]] = field(default_factory=list)
    verdict: str = ""  # pass / reject
    review_summary: str = ""
    patched_code: str = ""
    fix_notes: List[str] = field(default_factory=list)
    verified: bool = False
    verify_report: str = ""
    iteration: int = 0
    status: str = "init"
    trace: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def current_code(self) -> str:
        """当前待审查 / 待验证的代码：修复过就用补丁，否则用初始代码。"""
        return self.patched_code or self.code

    def write(self, node: str, name: str, value: Any) -> None:
        """受控写入。字段未登记或写者不是归属节点时直接报错。"""
        owner = FIELD_OWNERS.get(name)
        if owner is None:
            raise KeyError(f"状态字段 {name!r} 未登记归属，拒绝写入")
        if owner != node:
            raise PermissionError(f"节点 {node!r} 无权写 {name!r}（归属 {owner!r}）")
        setattr(self, name, value)

    def record_step(self, step: int, node: str, ok: bool = True) -> None:
        self.trace.append({"step": step, "node": node, "ok": ok})

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: str) -> None:
        """原子写：先写临时文件再 replace，避免中途崩溃留下半个 JSON。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "WorkflowState":
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))
