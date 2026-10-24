# Agent Workflow — 多 Agent 代码审查闭环

SE2026 Homework 2：实现一个由 4 个 Agent 协作的 Workflow MVP。

```
coder ──► reviewer ──(reject)──► fixer ──► reviewer（复审）
              │                                  │
              └──────────(pass)──────────► verifier ──(pass)──► END
                                              │
                                           (fail)
                                              └──► fixer
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入 DASHSCOPE_API_KEY

python main.py                # 命令行跑一次完整闭环
python main.py --mock         # 不调 LLM，脚本化 Agent 验证调度
python main.py --task "写一个读取 CSV 并统计缺失值的函数"
python app.py --port 5001     # Web 看板，浏览器打开 http://127.0.0.1:5001
```

## 运行产物

每次运行写入 `runs/<时间戳>/`：

- **state.json** — 当前状态快照，每步原子覆盖写，是唯一事实来源
- **run.log** — JSONL 事件流，记录节点进入/退出、工具调用、Agent 交接消息、错误

同一个 run 在浏览器看板里看到的事件，与 run.log 里的记录完全一致（同一份 EventBus 输出）。

## 目录

```
core/
  state.py      # WorkflowState：字段归属 + 受控写入 + 原子落盘
  message.py    # Message 结构 + EventBus（run.log + SSE）
  graph.py      # 调度器：节点注册、条件边、两层循环上限、失败兜底
  context.py    # RunContext：run 目录、事件总线、LLM 客户端
  llm.py        # LLM 重试 + 容错 JSON 解析
agents/
  base.py       # BaseAgent：build_prompt → invoke → parse_output → apply
  coder.py      # 编码
  reviewer.py   # 审查（带静态分析工具）
  fixer.py      # 修复
  verifier.py   # 验证（编译检查 + 模型判断）
  mock.py       # 脚本化 Agent，离线调试用
tools/
  static_analysis.py
main.py         # CLI 入口 + 图定义
app.py          # Flask + SSE 看板
web/index.html  # Vue3 单文件前端
tests/          # 不依赖 LLM 的单元测试
```

## 三个关键机制

1. **编排**：`Graph` 支持条件边 `(node, router, mapping)`，`None` 表示结束；两层上限
   —— `max_iterations` 约束业务轮次、`max_steps` 兜底防死循环。
2. **通信**：`Message(sender, receiver, type, payload)` 只记录交接事实；业务数据全部走 state。
3. **状态**：`FIELD_OWNERS` 声明每个字段的归属节点，`state.write` 越权写入直接报错；
   `state.json` 存当前真相，`run.log` 存历史轨迹。

## 示例：一次真实的闭环

任务故意要求不安全的写法：

```bash
python main.py --task "写一个计算器函数，直接用 eval 执行用户输入的数学表达式字符串"
```

实际执行：

```
[01] coder      → reviewer
[02] reviewer   → fixer      # 静态分析 + 模型判断：eval 代码注入，high
[03] fixer      → reviewer
[04] reviewer   → verifier   # 复审：已改为 AST 受限求值，问题解决
[05] verifier   → END        # 编译通过 + 模型确认
status=done iteration=1
```

## 与 H1 的关系

- 复用：OpenAI SDK 调 DashScope 的方式、静态分析思路、前端视觉风格。
- 升级：H1 是单 Agent 的「推理-工具」循环；H2 把记忆换成显式 state，
  把循环换成图编排，新增节点间结构化通信与可审计日志。

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：状态越权守卫、条件边路由、迭代上限、节点异常兜底、JSON 容错解析。
