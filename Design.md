# Design — 多 Agent 代码审查闭环

## 1. 目标

实现一个 2-3 个 Agent 协作的 Workflow MVP。选定的业务是代码审查闭环：

```
coder ──► reviewer ──(reject)──► fixer ──► reviewer（复审）
              │                                  │
              └──────────(pass)──────────► verifier ──(pass)──► END
                                              │
                                           (fail)
                                              └──► fixer
```

选这个场景的理由：H1 已经积累了代码审查的单 Agent 和工具层，H2 可以把精力放在
「编排、通信、状态」三件事上，而不是重新发明业务。

## 2. 为什么不用现成框架

先做了一轮调研，结论是**不引入框架，自己写约 150 行调度器**。

| 方案 | 能力 | 为什么不用 |
|------|------|------------|
| CrewAI | 角色化多 Agent，内置任务编排 | 控制流由框架托管，想看清「第几步为什么走了这条边」要翻框架源码；与「显式编排」的作业要求相悖 |
| LangGraph | 图/状态机，支持条件边与循环 | 功能最贴合，但会把 state 的读写、检查点、持久化都交给框架，自己写的部分只剩 prompt，评审看不到编排实现 |
| AutoGen | 对话式多 Agent | 面向自由对话与辩论，H2 需要的是固定顺序 + 条件分支，属于过度设计 |
| MS Agent Framework | 企业级编排 | 依赖重、概念多，一个小 MVP 用不上 |
| Dify / n8n | 可视化编排平台 | 是产品不是库，无法作为代码作业交付 |

框架能省的是样板代码，代价是**控制流不可见**。本作业的评分重点恰好是流程与协作，
所以选择把调度器写出来，并在第 6 节说明它的边界。

## 3. 结构

```
core/
  state.py     WorkflowState：字段归属、受控写入、原子落盘
  message.py   Message：结构化交接记录；EventBus：事件写 run.log + 推前端
  graph.py     调度器：节点注册、条件边、循环上限、失败兜底
  context.py   RunContext：run 目录、事件总线、LLM 客户端
  llm.py       LLM 调用（指数退避重试）+ 容错 JSON 解析
agents/
  base.py      BaseAgent 模板：build_prompt → invoke → parse_output → apply
  coder.py     编码
  reviewer.py  审查（先跑静态分析工具，再让模型判断）
  fixer.py     修复（返回完整代码）
  verifier.py  验证（编译检查 + 模型判断）
  mock.py      脚本化 Agent，离线调试调度用
tools/
  static_analysis.py  H1 的 analyze_code 改写为直接分析字符串
main.py        CLI 入口 + 图定义
app.py         Flask + SSE，Web 看板
web/index.html Vue3 单文件前端
```

## 4. 三个核心机制

### 4.1 编排：Graph

`Graph` 只做四件事：注册节点、连边、按 state 选择下一条边、兜底。

- **条件边**：`add_conditional_edge(node, router, mapping)`，`router(state)` 返回键，
  `mapping` 把键映射到下一节点，`None` 表示结束。审查节点用 `verdict` 决定走修复还是验证。
- **两层上限**：
  - `max_iterations`（默认 3）按业务轮次计数，`iteration_node="fixer"`，每进一次 fixer 算一轮；
  - `max_steps`（默认 12）按节点执行次数兜底，防止路由写错导致死循环。
  - 达到上限时写入 `status=max_iterations`，并记录错误事件，而不是静默退出。
- **失败兜底**：单个节点抛异常时，把异常写进 run.log、state 落盘、状态置为
  `failed:<节点名>`，然后结束本次运行，不会让整条流水线带着半截数据继续跑。

调度器不 import 任何具体 Agent，只依赖 `agent.run(state, ctx) -> NodeResult` 这个鸭子类型契约。

### 4.2 通信：Message + EventBus

业务数据只走 state，消息只记录「谁把什么交给了谁」：

```python
Message(sender="reviewer", receiver="fixer", type="handoff",
        payload={"verdict": "reject", "issues": 2, "high": 1})
```

这样做的好处是：日志不会因为塞入整段代码而膨胀，评审只看消息就能还原协作顺序。
`EventBus.emit` 把同一份事件同时写 `run.log`（JSONL）并回调给 Web 前端，
保证「命令行看到的」和「浏览器看到的」是同一份数据。

事件类型：`run_start / node_start / agent_prompt / agent_raw / tool_call / tool_result /
message / node_end / edge / node_skipped / error / run_end`。

### 4.3 状态：显式读写 + 单一事实来源

```python
FIELD_OWNERS = {
    "code": "coder",
    "issues": "reviewer",
    "patched_code": "fixer",
    "verified": "verifier",
    ...
}
```

`state.write(node, field, value)` 会校验写者是否是字段归属节点，越权直接抛异常。
这条规则把「多 Agent 互相覆盖状态」这类 bug 从运行期隐患变成即时错误。

落盘分成两份：

| 文件 | 内容 | 用途 |
|------|------|------|
| `runs/<ts>/state.json` | 当前完整状态快照，每步原子覆盖写 | 当前真相、断点、评审直接读 |
| `runs/<ts>/run.log` | JSONL 事件流，只追加 | 历史轨迹、复盘、Web 数据源 |

原子写用「临时文件 + `os.replace`」，避免进程中途退出留下半个 JSON。

## 5. 关键决策与权衡

| 决策 | 选择 | 理由 |
|------|------|------|
| 节点组织 | 类继承 `BaseAgent` | 新增节点只需实现 4 个方法，重试/日志/状态校验留在基类，扩展点单一 |
| 输出格式 | 强制 JSON + 容错解析 | 下游字段稳定；解析器按「剥代码块 → 截取花括号 → 修复非法转义」逐级降级 |
| 工具使用 | 只给 reviewer / verifier 接确定性工具 | 静态分析与编译检查先跑，模型只做判断，避免模型编造行号；也不额外消耗 token |
| 复审策略 | 第二轮起收窄范围 | 实测发现开放式复审会每轮提出新的加固建议，永远不收敛；现在复审只判断上一轮问题是否解决、是否引入新 high 问题 |
| 最终放行 | 编译检查 + 模型判断，两者都通过才算 verified | 不把最终判断权完全交给 LLM |
| 模型输出异常 | 重试 3 次（1s/2s），仍失败则节点失败 | 网络与限流是常态，重试是必要成本；解析失败不重试，直接暴露问题 |
| 前端 | Vue3 CDN + SSE，无构建链 | 单文件即可交付，与 CLI 共用同一套事件，不维护第二份业务逻辑 |

## 6. 局限与下一步

- **并发**：当前一次只跑一个 run（`threading.Event` 互斥），多用户场景需要 job 队列与 run 隔离。
- **断点续跑**：state.json 已具备恢复所需字段，但还没有 `--resume` 入口。
- **收敛性**：复审收窄是经验策略，问题跟踪仍以「上一轮列表」为单位，没有做跨轮指纹去重。
- **成本**：一轮完整闭环 4-5 次 LLM 调用，尚未做 prompt 缓存或小模型分流。
