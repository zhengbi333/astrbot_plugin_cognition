# 更新日志

## alpha-0.4.0（当前）

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
