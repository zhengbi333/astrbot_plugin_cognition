# 更新日志




## alpha-1.5.8（当前）

- 架构调整：认知插件 = 统一工具库
- 详见 [changelogs/alpha-1.5.8.md](changelogs/alpha-1.5.8.md)。

## alpha-1.5.7（当前）

- P0-3 试运行：写操作只演不做
- 详见 [changelogs/alpha-1.5.7.md](changelogs/alpha-1.5.7.md)。

## alpha-1.5.6（当前）

- `page_config.py` 里有：
- 详见 [changelogs/alpha-1.5.6.md](changelogs/alpha-1.5.6.md)。

## alpha-1.5.4（当前）

- **修设置页取值**：`plugin.config.get("auth.admins")` 返回 None（点号路径不可靠），导致面板显示空、保存后又被读成空（用户以为「填的 QQ 号消失了」，其实文件里一直在）。现在面板**直接读配置文件**，文件是唯一真相。
- `audit_82` 增至 **22/22**，新增 4 条专门盯这个坑（config 打桩成永远 None 也能读对）。
- 详见 [changelogs/alpha-1.5.4.md](changelogs/alpha-1.5.4.md)。

## alpha-1.5.3

- **修设置面板保存失败**（「这些配置项不存在：enabled、admins…」）：`describe()` 摊平配置时漏传分组前缀，前端拿到短名、后端校验带前缀的键，对不上。现在 52 项全部带前缀，保存往返正常。
- 新增 `audit_82_config_panel.py` **18/18**，专门盯这个回归点。
- 详见 [changelogs/alpha-1.5.3.md](changelogs/alpha-1.5.3.md)。

## alpha-1.5.2

- **面板新增「设置」页签**：17 组 / 52 个配置项全部可在面板里改，**每项都带中文说明**（0 项缺说明）；布尔用拨杆、枚举用下拉、长文本用多行框；保存时自动备份 + 原子写，只接受 schema 声明过的键。从此不用再点齿轮。
- 详见 [changelogs/alpha-1.5.2.md](changelogs/alpha-1.5.2.md)。

## alpha-1.5.1

- **修面板 `Failed to fetch`**：`bridge.apiGet` 会自己加插件前缀，之前重复传前缀导致 404；已改为相对路径 + 等 bridge 就绪 + 兜底报 HTTP 状态。
- **修面板太黑**：整体提亮一档（底色/面板/边框/文字/强调色），卡片改浅渐变实心块，仍保留黑底亮黄骨架。

## alpha-1.5.0

- **新增 WebUI 面板**（终末地风格：黑底亮黄强调块、直角、几何网格、等宽字体、编号与坐标读数、25ms 闪入、easeInOutExpo 缓动）：六个页签 —— 总览 / 专家 / 目标 / 技能 / 记录 / 关于；目标可在面板里直接立与收尾。
- 修：`.cell` 入场动画在 headless 下卡在 opacity 0 导致空白（已移除会隐藏内容的动画）。
- 新用例 `audit_76`（配置类型合法性，防上次的 `number` 事故）+ 面板接口与页面存在性检查。
- 详见 [changelogs/alpha-1.5.0.md](changelogs/alpha-1.5.0.md)。

## alpha-1.4.1

- **修：插件加载失败** —— `exec.timeout_seconds` 的类型写成了 `number`，AstrBot 只认 int/float/bool/string/text/list/file/object/template_list/dict，已改为 `int`；顺带扫描三插件全部 schema 类型并规范化别名。
- 详见 [changelogs/alpha-1.4.1.md](changelogs/alpha-1.4.1.md)。

## alpha-1.4.0

- **子代理升级为多专家**（对标 AstrBot handoff，但自己实现）：`delegate.agents` 配专家（name/description/system/provider_id/max_chars），`toolbox_delegate(agent=...)` 按名字选派，新增 `toolbox_delegate_list`；支持**每专家指定模型**（省钱）与**每日 30 次**成本护栏 + 审计。
- 设计分工：主代理取数、子代理思考（子代理不给工具，避免乱动工作区）。
- 新用例 `audit_75` **37/37**。详见 [changelogs/alpha-1.4.0.md](changelogs/alpha-1.4.0.md)。

## alpha-1.3.0

- **全面审查后补 4 个 DSH 缺口**：`toolbox_edit`（精确编辑，不用重写整份文件）、`toolbox_glob`（`**` 递归查找、按修改时间倒序）、`toolbox_goal`（长期目标，跨会话持久化、原子写入）、`toolbox_delegate`（子代理：同模型独立推理一次，不共享对话上下文）。
- 审查还列出未做的 4 项：工作流编排、中途提问、用量台账、会话环境卡。
- 新用例 `audit_74` **47/47**。详见 [changelogs/alpha-1.3.0.md](changelogs/alpha-1.3.0.md)。

## alpha-1.2.0

- **并行执行** `toolbox_parallel`：几段互不依赖的脚本同时跑（默认 3 路，可配到 6），每路独立超时与输出上限，单路失败不影响其它；实测三段各 1.2s 的脚本总耗时 <2.6s。
- **等任务** `toolbox_task_wait`：一次等到后台任务结束，不用反复查。
- 新用例 `audit_71` **23/23**。详见 [changelogs/alpha-1.2.0.md](changelogs/alpha-1.2.0.md)。

## alpha-1.1.0

- **新增技能库**：`toolbox_skill_list` / `toolbox_skill`，把「一类活的标准做法」写成 `skills/*.md`，模型按需取用；内置三个示例（做周报 / 整理会议记录 / 批量改文件名）。**模型只能读不能写**，技能也不能改变权限。
- 新用例 `audit_70` **22/22**。详见 [changelogs/alpha-1.1.0.md](changelogs/alpha-1.1.0.md)。

## alpha-1.0.0

- **权限分级**：管理员（`auth.admins`，留空回退 `exec.owner_ids`）/ 成员 / 访客；代码执行、后台任务、shell、全盘访问只给管理员；认不出身份按访客处理（fail-closed）。
- **工作区按会话分配**：`shared` / `per_session` / `mapped`（可映射到任意绝对路径）；访问级别 `workspace`（默认路径监狱）与 `full`（仅管理员，可配 `full_roots` 限定根目录）。
- **用 Python 补齐缺失软件**：`toolbox_doc_export`（docx/xlsx/pptx→PDF，**不需要 LibreOffice**）、`toolbox_image`（缩放/压缩/水印/转换）、`toolbox_archive`（打包解包，防 zip slip 与压缩炸弹）。
- 新用例 `audit_68` **63/63**、`audit_69` **34/34**；单测 149/149。详见 [changelogs/alpha-1.0.0.md](changelogs/alpha-1.0.0.md)。

## alpha-0.8.0

- **新增 QQ 平台社交动作**：`toolbox_poke`（戳一戳/拍一拍）、`toolbox_like`（名片点赞）。走 `bot.call_action` 通用动作通道；**目标只允许当前说话的人或机器人自己**，戳一戳与点赞各自每日限额（默认 20），平台不支持时明确回报。
- **说明**：QQ 说说（Qzone）一族不在标准 OneBot 协议里（属 SnowLuma 扩展 API），本版**做不到**，不做假装支持。
- 新用例 `audit_66` **24/24**。详见 [changelogs/alpha-0.8.0.md](changelogs/alpha-0.8.0.md)。

## alpha-0.7.0

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
