# 为你而在的认知

`astrbot_plugin_cognition`（中文名：**为你而在的认知**）让 Bot 自己去**经历、查阅、获取**——给 AstrBot 一套**真正能动手**的工具：在工作区里读写文件、按正则搜内容、打开网页读正文、联网搜索、下载文件、执行白名单命令。

它不是"嘴上说一句"——每次调用都在你的机器上**真的发生**，并把结果如实交回给模型。
它还能**汲取经验**：把读过的网页整理成带来源的条目存下来，日后可以再搜到；
**也能把做好的文档直接发给你**（`toolbox_send_file`）；
**更能接住「没有现成工具」的活**：`toolbox_python` 让她当场写一段脚本把事做完——**只对你的账号开放**。

- 版本：`alpha-1.2.0`
- AstrBot 版本：`>= 4.22.0`
- 更新日志：见 [changelogs/](changelogs/)

## 它解决什么问题

LLM 本身不能读盘、不能联网。没有工具时，它会"假装"做过这件事（比如回复"我读过那篇了"，其实什么都没读）。
装上本插件后，模型可以**真的**去读、去搜、去下载，而且失败时会拿到明确的错误文案，从而如实告诉你"没查到"。

## 工具清单

| 工具 | 作用 | 默认 |
|---|---|---|
| `toolbox_info` | 看工作区现状（路径/文件数/占用/各工具开关/今日用量/最近调用） | 开 |
| `toolbox_fs_list` | 列目录 | 开 |
| `toolbox_fs_read` | 读文本文件（可只读开头/结尾 N 行） | 开 |
| `toolbox_fs_write` | 写文件（自动建目录） | 开 |
| `toolbox_fs_search` | 按正则搜内容（返回文件:行号:内容） | 开 |
| `toolbox_web_fetch` | 抓网页正文（HTML → 文本） | 开 |
| `toolbox_web_search` | 联网搜索（Tavily，需填 Key） | 开 |
| **`toolbox_learn`** | **汲取经验**：读一篇网页（或搜索首条结果）→ 整理成带来源的经验条目存进 `notes/` | 开 |
| **`toolbox_doc_read`** | 读 `.docx` / `.xlsx` / **`.pptx`**（段落、表格、工作表、幻灯片文字） | 开 |
| **`toolbox_doc_write`** | 新建 `.docx` / `.xlsx` / **`.pptx`**（段落 / 单元格 / 幻灯片大纲） | 开 |
| **`toolbox_send_file`** | **把工作区里的文件发到当前会话**（「把这个文档发我」） | 开 |
| **`toolbox_poke`** | **戳一戳/拍一拍**：戳当前说话的人（群聊走 group_poke、私聊走 send_poke） | 开 |
| **`toolbox_like`** | **给名片点赞**（标准 OneBot send_like，单次上限可配） | 开 |
| **`toolbox_doc_export`** | **导出 PDF**（docx/xlsx/pptx/文本 → PDF，**不需要 LibreOffice**） | 开 |
| **`toolbox_image`** | 图片缩放 / 压缩 / 水印 / 格式转换（Pillow） | 开 |
| **`toolbox_archive`** | 打包 / 解包（zip、tar；解包防路径穿越与压缩炸弹） | 开 |
| **`toolbox_skill_list`** | 列出可用「技能」（一类活的标准做法），可按关键词过滤 | 开 |
| **`toolbox_skill`** | 取某份技能的完整流程，照着做 | 开 |
| **`toolbox_parallel`** | **并行跑**几段互不依赖的脚本，一起等结果 | 开（仅主人） |
| **`toolbox_task_wait`** | 等后台任务跑完（省得反复查） | 开（仅主人） |
| **`toolbox_python`** | **任意任务**：在工作区写并运行 Python 脚本（pandas/numpy/docx/openpyxl/pptx/Pillow） | 开（**仅主人**） |
| **`toolbox_task_start`** | **后台任务**：把耗时脚本丢后台跑，立刻拿任务号 | 开（**仅主人**） |
| **`toolbox_task_status`** | 查后台任务：状态、耗时、退出码、最近输出 | 开（**仅主人**） |
| **`toolbox_task_stop`** | 停掉后台任务（强杀） | 开（**仅主人**） |
| **`toolbox_audit`** | 查最近调用记录（谁成功、谁失败、按工具统计） | 开 |
| `toolbox_download` | 把网址里的文件下载进工作区 | 开 |
| `toolbox_shell_run` | 执行白名单命令 | **关** |

## 安装

1. 把整个 `astrbot_plugin_cognition` 目录放进 `~/.astrbot/data/plugins/`
2. 在 AstrBot 面板里**启用**本插件（会自动创建数据目录与工作区）
3. 想用联网搜索的话，在插件配置里填 `web.tavily_api_key`
4. 重载插件

工作区位置：

```
~/.astrbot/data/plugin_data/astrbot_plugin_cognition/workspace/
```

**模型只能在这个目录里读写**——外面的文件它碰不到（路径监狱）。

## 安全设计（请务必读一遍）

聊天窗口后面是**群里的所有人**，所以这个插件的默认值是保守的：

- **路径监狱**：任何路径都在 `resolve()` 后检查是否仍在工作区内；`..`、绝对路径、符号链接逃逸一律拒绝。
- **拒绝内网地址**：抓网页/下载都会校验目标 IP，`127.x / 10.x / 172.16-31.x / 192.168.x / 169.254.x / localhost` 等直接拒绝——否则任何人都能借 Bot 去读你自己的面板和局域网设备（SSRF）。
- **敏感文件拒读**：`.env` / `*.key` / `*.pem` / `id_rsa` / `*secret*` 等即便在工作区内也不读。
- **体积与超时**：读/写/下载/抓取都有上限，命令有超时强杀。
- **命令默认关闭**：开启后也只允许白名单里的**程序名**（不带路径），并且 `bash` / `cmd` / `powershell` / `python` / `node` 这类"任意执行入口"**写进白名单也一律拒绝**。
- **每日剂量**：`limits.daily_calls`（默认 200 次/天）防止被刷爆。

> 建议：只让 Bot 读资料时，把 `fs.allow_write` 关掉；`shell` 保持关闭。

## 与「为你续写的故事」「为你篆刻的历史」的关系

- 本插件只提供**工具**，不参与聊天回复的人格与记忆。
- 「为你续写的故事」在**主链放行**模式下能直接用到这些工具（AstrBot 统一注入）。
- 它的「接管改换」模式用的是插件自己的工具通道，需要额外的桥接（后续版本处理）。

## 谁能用（权限分级）

| 等级 | 谁能 | 能做什么 |
|---|---|---|
| **管理员** | `auth.admins` 白名单（留空则回退 `exec.owner_ids`） | 全部：代码执行、后台任务、shell、全盘访问 |
| **成员** | `auth.members` 白名单 | 读写工作区、做文档、发文件、导出 PDF、处理图片 |
| **访客** | 其他人（默认） | 只能聊天、搜索、抓网页、读资料 |

`auth.members_open` 打开后，未登记的人算成员（不建议）。
判不出身份 → 按**访客**处理（fail-closed）。

## 工作区（每个会话可以有自己的）

| 配置 | 说明 |
|---|---|
| `workspace.mode=shared` | 所有会话共用一个工作区（默认） |
| `workspace.mode=per_session` | 每个会话在 `workspace/sessions/<哈希>/` 下独立，互不可见 |
| `workspace.mode=mapped` | 用 `workspace.mapped` 的 JSON 把某个会话分到指定目录，键写 `*` 表示默认 |
| `workspace.access=workspace` | 只能访问本会话工作区（默认） |
| `workspace.access=full` | 可访问工作区之外，**只有管理员能用**，非管理员自动降级；再用 `workspace.full_roots` 限定可访问的根目录（留空=真全盘，慎用） |

工作区只放**运行产物**（脚本、生成的文档、下载的文件、日志）。会话数据归记忆插件，不要混放。

## 技能库（skill）

把"一类活怎么干"写成文件，模型**按需取用**（对应 DSH 的 skill 机制）：

- 位置：`~/.astrbot/data/plugin_data/astrbot_plugin_cognition/skills/*.md`
- 格式：一级标题当名字，`> ` 引言当一句话说明，正文写步骤
- **只有人能加/改**（直接放文件），模型只能读 —— 避免它给自己写"绕过规则"的流程
- 内置三个示例（首次启动写入，不覆盖已有）：做周报 / 整理会议记录 / 批量改文件名
- 单个技能超 6000 字会截断

工作流：`toolbox_skill_list` 看有哪些 → `toolbox_skill(name)` 取流程 → 照着调工具。

## 许可

- 本项目采用 **GNU AGPL-3.0**（与 AstrBot 一致）：Copyright (C) 2026 证毕 (zhengbi333)
- 可自由使用、修改、分发；若通过网络提供服务，需同样以 AGPL-3.0 开放对应源码。
- 完整条款见仓库根目录 LICENSE；上游 AstrBot：https://github.com/AstrBotDevs/AstrBot
