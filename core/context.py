"""一次运行的上下文：run 目录、事件总线、LLM 客户端。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from core.message import EventBus


@dataclass
class RunContext:
    run_id: str
    run_dir: str
    bus: EventBus
    client: Optional[Any] = None
    model: str = "qwen-plus"

    @property
    def state_path(self) -> str:
        return os.path.join(self.run_dir, "state.json")

    @property
    def log_path(self) -> str:
        return os.path.join(self.run_dir, "run.log")
