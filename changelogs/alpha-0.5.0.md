# alpha-0.5.0

## 一、后台任务：长活不再被 120 秒砍断

前台执行（`toolbox_python`）有超时上限，批量处理、爬取、渲染这类活做不完。本版新增最小可用的作业系统：

| 工具 | 作用 |
|---|---|
| `toolbox_task_start(code, label)` | 丢进后台，**立刻**返回任务号 |
| `toolbox_task_status(task_id)` | 还在跑吗 / 跑了多久 / 退出码 / 最近输出（保首尾） |
| `toolbox_task_stop(task_id)` | 强杀 |

实现要点（`tasks.py`）：

- 子进程 **stdout/stderr 直接重定向到 `.tasks/<id>.log`**，不占内存；
- 用一个内部 asyncio 任务 `await proc.wait()` 刷新退出码，所以查询不阻塞；
- **并发上限**（`tasks.max_concurrent`，默认 2）超了直接拒绝；
- **存活上限**（`tasks.max_age_seconds`，默认 1800 秒）超龄自动强杀，防止进程泄漏；
- 插件 `terminate()` 时全部清理；
- 全部 **owner-only**（沿用 `exec.owner_ids`），群友调不动。

## 二、长输出改为"保首尾"截断

以前超长输出只砍尾巴 —— 而异常堆栈最后一行、"共 N 条"、保存路径恰好都在结尾，砍掉后模型会误判成没做成。
现在统一为 `前 70% + …（中间省略 N 字）… + 后 30%`（`textutil.truncate_keep_ends`），
`exec.py` 的脚本输出与 `task_status` 的日志尾部都用它。

## 三、验证

| 测试 | 结果 |
|---|---|
| `test_cognition_unit.py` | **134/134**（0.4.0 的 112 项 + P 截断 5 项 + Q 后台任务 10 项 + R 接线 5 项 + 其它 2 项） |
| `audit_62_background_tasks.py`（真机） | **13/13**：启动即返回、运行中可读输出、结束退出码、停止、并发上限、超龄强杀、未知任务号、shutdown 清空 |
| `audit_61_loop_guard.py`（storyteller 侧） | **28/28** |

## 四、本轮修掉的一个真 bug

加"后台任务"时用 `def _today` 作插入锚点，结果把 `_quota_left` 的函数头替换掉了 ——
`_gate()` 调 `self._quota_left()` 会 **AttributeError**（所有工具都会挂）。
是单测 `K4 旧实现已清理` 发现的：它断言 `_today(` 不该再出现，正好命中。已恢复函数头并补 docstring。

## 五、已知边界

- 后台任务没有队列与重试，只是"起进程 + 查日志"。
- 任务表在内存里，插件重启后历史任务号失效（日志文件仍在 `.tasks/`）。
- 无沙箱：脚本拥有主人权限（只给主人用是唯一现实约束）。
