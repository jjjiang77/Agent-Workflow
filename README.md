# Agent Workflow — 多 Agent 代码审查闭环

SE2026 Homework 2：实现一个 2-3 个 Agent 协作的 Workflow MVP。

四个 Agent 按固定顺序接力：**编码 → 审查 → 修复 → 复验**。
审查不通过就回到修复，修复后重新审查，形成带循环上限的闭环。

## 当前进度

阶段一：调度骨架已跑通（脚本化 Agent，不调用 LLM，用来验证控制流）。

## 运行

```bash
python main.py
```

每次运行会在 `runs/<时间戳>/` 下产出：

- `state.json` — 当前状态快照（唯一事实来源）
- `run.log` — JSONL 事件流（节点进出、消息交接、错误）

## 结构

```
core/
  state.py      # WorkflowState：字段归属 + 受控写入 + 原子落盘
  message.py    # Message 结构 + EventBus（写 run.log、推前端）
  graph.py      # 调度器：节点注册、条件边、循环上限、失败兜底
  context.py    # RunContext：run 目录、事件总线、LLM 客户端
agents/
  base.py       # BaseAgent：prompt -> invoke -> parse -> apply
  mock.py       # 脚本化 Agent，用于验证骨架
main.py         # CLI 入口 + 图定义
```
