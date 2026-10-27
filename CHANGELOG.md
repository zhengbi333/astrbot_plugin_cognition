# 更新日志

## alpha-0.7.0（当前）

- **补上「认知 → 人格 → 记忆」的断边**：`learn` / `doc_write` / `download` / `send_file` 成功后，认知通过 `notify_persona` 转告人格，由人格统一写入 `record_bot_action`（self_action）。她做的事终于会留下记忆痕迹。
- 人格新增 `note_external_event` 统一写入口（去重、异常不抛、无桥降级）。
- 新用例 `audit_65` **20/20**。详见 [changelogs/alpha-0.7.0.md](changelogs/alpha-0.7.0.md)。

## alpha-0.6.0

- **验收整改**：后台任务强制无缓冲（不再"看起来卡死"）、重载插件不再杀任务（新增 `kill_all`）、shell 升级为「程序+只读子命令+拒危险参数」三层校验且默认白名单改为空。
- 打转计数跨轮累计、历史工具结果自动压缩、审计落盘 `audit.jsonl`（重启可查）、`doc_write` 独立 `subtitle`。
- 新增真驱动循环的 `audit_63`（15/15）与 `audit_64`（21/21）；单测 134 → **149/149**。
- 抓出并修掉两个真 bug：`_compact_tool_messages` 缺 `import json`（一压缩就崩）、`shutdown()` 清空任务表导致孤儿进程失控。详见 [changelogs/alpha-0.6.0.md](changelogs/alpha-0.6.0.md)。

## alpha-0.5.0

- **后台任务**：新增 `toolbox_task_start` / `toolbox_task_status` / `toolbox_task_stop`，耗时脚本丢后台跑、按需查输出与退出码、可随时停；owner-only，并发 2、存活 1800 秒自动清理，日志写文件不占内存。
- **长输出改为保首尾截断**（前 70% + 省略标记 + 后 30%），不再把异常堆栈最后一行砍掉。
- 单测 112 → **134/134**，后台任务真机验证 **13/13**；另修掉一个真 bug：`_quota_left` 函数头被误替换会让所有工具 AttributeError。详见 [changelogs/alpha-0.5.0.md](changelogs/alpha-0.5.0.md)。

## alpha-0.4.0

- **新增 `toolbox_python`：任意任务能力**。她可以当场写一段 Python 在工作区里跑完——批量转换、统计表格、生成图表/PPT/Excel、清洗抓来的数据——用的就是 AstrBot 自带的 pandas/numpy/python-docx/openpyxl/python-pptx/Pillow。
- **只对主人的账号开放**（`exec.owner_ids`，留空=谁都不能用）：群友调用直接拒绝并记审计；另有限时强杀、输出与代码长度上限。
- 单测 94 → **112/112**（含真跑脚本、真产出 xlsx、pandas、超时、owner 门禁）。详见 [changelogs/alpha-0.4.0.md](changelogs/alpha-0.4.0.md)。

## alpha-0.3.0

- **新增 `toolbox_send_file`**：把工作区里的文件（docx/xlsx/pptx/zip…）发到当前会话——「把这个文档发我」终于能真的做到，而不是嘴上说「已经整理好了」。只能发工作区内文件，默认 50MB 上限。
- **新增 `.pptx` 读写**：`doc_write` 按简单大纲（`---` 分页、`#` 标题、`- ` 要点）生成幻灯片，`doc_read` 逐页读文字；依赖已安装的 `python-pptx 1.0.2`。
- 单测 79 → **94/94**（pptx 往返 6 项、send_file 接线 6 项、schema 3 项）。详见 [changelogs/alpha-0.3.0.md](changelogs/alpha-0.3.0.md)。

## alpha-0.2.1

- **新增「汲取经验」闭环**：`toolbox_learn` 读网页/搜首条 → 整理成带来源（front-matter）的经验条目落盘 `notes/`，再回要点摘要；抓不到就如实说抓不到。
- **新增 Office 读写**：`toolbox_doc_read` / `toolbox_doc_write`（docx 段落与表格、xlsx 工作表），带字数/行列上限，缺依赖时给可读错误。
- **新增调用审计**：`toolbox_audit` 查最近调用与按工具统计（故意无门禁，用于自知）。
- **重构**：审计与配额抽成 `audit.py`（`AuditLog` + `Quota`），`toolbox_info` 增加用量与最近调用；清掉旧实现残留。
- 单测 36 → **79/79**（新增 Office 往返、经验条目、审计配额、learn 组合、接线、schema 六组）。详见 [changelogs/alpha-0.2.1.md](changelogs/alpha-0.2.1.md)。

## alpha-0.1

- 首个版本：9 个工具（工作区文件读写/搜索、抓网页、联网搜索、下载、白名单命令）。
- 安全骨架：路径监狱、SSRF 拦截、敏感文件拒读、体积/超时上限、命令默认关闭+高危名单、每日剂量。
- 单元测试 36/36。详见 [changelogs/alpha-0.1.md](changelogs/alpha-0.1.md)。
