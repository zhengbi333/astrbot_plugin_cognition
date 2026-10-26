# alpha-0.6.0

本轮不是加功能，而是按《验收报告与一次性整改方案》把 5 个 P0 与 5 个 P1 改完。

## P0（必须改）

| 编号 | 问题 | 改法 |
|---|---|---|
| P0-2 | 后台任务输出被块缓冲，`task_status` 长期"还没有输出" | `PYTHONUNBUFFERED=1` + `python -u`（**不改脚本本体**，traceback 行号不偏移） |
| P0-3 | 重载插件会把正在跑的任务杀掉 | `shutdown()` 只关句柄、清已结束记录，**保留运行中任务**；新增 `kill_all()` 供显式全停 |
| P0-4 | `shell_run` 默认放行 `git`，而 git 本身能变成任意执行 | 三层校验：程序名在白名单 → 子命令属于只读集合 → 拒绝 `-c/--exec-path/--upload-pack/--git-dir/-C` 与 `!` 开头参数；**默认白名单改为空** |
| P0-1 | 后台任务三个工具没进桥接白名单 | 新增防漏测试：注册的每个 `toolbox_*` 必须出现在 README（README 即推荐白名单来源），并在报告里列出"你配置里还缺哪些" |
| P0-5 | 防打转只有静态断言 | 新增 `audit_63_loop_behavior.py`：替换 `chat_with_tools` 真驱动 tool loop |

## P1（应该改）

- **P1-1** 打转计数提升到实例级（按会话），**跨 `cap_round` 重启累计**；
- **P1-2** 历史工具结果压缩：保留最近 6 条完整，更早的压成一行摘要（`_compact_tool_messages`）；
- **P1-3** 审计落盘 `audit.jsonl`（追加写、超 2MB 滚动、启动 `load()` 回填），重启后仍能回答"你之前动过什么"；
- **P1-4** `doc_write` 新增独立 `subtitle` 参数（pptx 封面副标题），不再复用 `sheet_name`；
- **P1-5** 一键验收的版本基线改为**从工作区读取**，校验"工作区 = final = 备份目录 = 已部署"四处一致（发版不用再手改脚本）。

## P2

- `_TOOL_TITLES` 补全 18 个工具（拒绝文案不再显示裸名）；
- `TaskRecord.killer` 改名 `watcher`。

## 本轮测试抓到的两个真 bug

1. **`_compact_tool_messages` 用了 `json` 但 `main.py` 模块级没有 `import json`**（tool loop 里是局部 `_json` 别名）→ 一压缩就 `NameError`。是 `audit_63` 真驱动循环时炸出来的，字符串断言完全抓不到。
2. **`shutdown()` 把任务表整个清空**，导致 `kill_all()` 再也管不到那些孤儿进程。是 `audit_64` 的 D3 断言抓到的，已改为"只清已结束记录、保留运行中记录"。

## 验证

| 测试 | 结果 |
|---|---|
| `test_cognition_unit.py` | **149/149**（0.5.0 的 134 + S 组 15） |
| `audit_63_loop_behavior.py`（真驱动循环） | **15/15** |
| `audit_64_shell_and_allowlist.py` | **21/21** |
| `audit_61_loop_guard.py` / `audit_62_background_tasks.py` | 28/28 / 13/13 |
| 全量回归 | 见验收记录 |

## 升级提示

`shell.allowlist` 默认值从 `git` 改为**空**（谁都不能跑）。这是刻意的：git 的三层校验只是兜底，
真要开命令执行，请明确写出程序名并确认风险。
