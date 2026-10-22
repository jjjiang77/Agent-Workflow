"""Agent 之间传递的结构化消息，以及运行日志。

消息不承载业务数据（业务数据在 state 里），只记录「谁把什么交给谁」，
所以评审打开 run.log 就能还原整个协作过程。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class Message:
    sender: str
    receiver: str
    type: str  # handoff / result / error
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventBus:
    """一次运行的事件总线。

    同一份事件同时写进 run.log（JSONL，可审计）并回调给前端（Web 看板）。
    """

    def __init__(self, path: str, callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.path = path
        self.callback = callback
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def emit(self, event: str, **payload: Any) -> Dict[str, Any]:
        record = {"ts": _now(), "event": event, **payload}
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        if self.callback:
            self.callback(record)
        return record

    def message(self, msg: Message) -> None:
        self.emit("message", message=msg.to_dict())

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()
