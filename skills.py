"""技能库：把"一类活怎么干"写成文件，模型按需取用。

对应 DSH 的 skill 机制：不是把所有知识塞进 system（那样既贵又冲淡人设），
而是让模型在**需要时**主动取一份操作流程，照着做。

文件放 `plugin_data/<插件>/skills/<名字>.md`，格式：

    # 做周报
    > 一句话说明（可选）

    1. 先读 notes/ 里本周的记录……
    2. 用 toolbox_doc_write 生成 docx……
    3. 用 toolbox_send_file 发出去。

规则：
- 只有**人**能加/改技能（直接放文件），模型只能读 —— 避免它给自己写"绕过规则"的流程；
- 单个技能有长度上限，超长截断并说明；
- 技能描述里不允许出现执行类动作的"放宽"承诺，那是配置的事，不是流程能改的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_CHARS = 6000
_FRONT_NAME = re.compile(r"^\s*name\s*[:：]\s*(.+)$", re.M)
_FRONT_DESC = re.compile(r"^\s*description\s*[:：]\s*(.+)$", re.M)
_TITLE = re.compile(r"^\s*#\s+(.+)$", re.M)


class SkillError(Exception):
    """技能读取失败（信息面向模型可读）。"""


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path

    @property
    def chars(self) -> int:
        return len(self.body)


def _parse(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = raw.lstrip("\ufeff")
    name_match = _FRONT_NAME.search(text[:800])
    desc_match = _FRONT_DESC.search(text[:800])
    title_match = _TITLE.search(text)
    name = (name_match.group(1) if name_match else (title_match.group(1) if title_match else path.stem)).strip()
    description = (desc_match.group(1) if desc_match else "").strip()
    if not description:
        # 优先取 markdown 的 > 引言（惯例上的"一句话说明"），其次取正文第一句
        quote = ""
        plain = ""
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            if candidate.startswith(">"):
                if not quote:
                    quote = candidate.lstrip(">").strip()
                continue
            if not plain and not candidate.startswith(("-", "*")):
                plain = candidate
        description = (quote or plain)[:80]
    body = text.strip()
    if len(body) > MAX_SKILL_CHARS:
        body = body[:MAX_SKILL_CHARS].rstrip() + "\n…（技能过长，已截断）"
    return Skill(name=name, description=description, body=body, path=path)


def scan(root: Path) -> list[Skill]:
    """列出所有技能（按名字排序）。目录不存在时返回空列表。"""
    directory = Path(root)
    if not directory.is_dir():
        return []
    out: list[Skill] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("."):
            continue
        skill = _parse(path)
        if skill is not None:
            out.append(skill)
    return out


def find(root: Path, name: str) -> Skill | None:
    """按名字（或文件名）找技能，大小写不敏感。"""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for skill in scan(root):
        if wanted in (skill.name.lower(), skill.path.stem.lower()):
            return skill
    return None


# ---------------------------------------------------------------- 内置示例
DEFAULT_SKILLS: dict[str, str] = {
    "做周报": """# 做周报

> 把本周的零散记录整理成一份能发出去的周报。

1. 先用 `toolbox_fs_search` 在工作区里搜本周的日期或 `notes/` 下的记录，把素材找齐；
2. 按「本周做了什么 / 遇到的问题 / 下周计划」三段归类，每段 2~4 条，**不要写空话**；
3. 用 `toolbox_doc_write` 生成 docx（`path` 用 `reports/周报-YYYY-MM-DD.docx`，`content` 一行一段）；
4. 想同时要 PDF 就调 `toolbox_doc_export` 指向刚生成的 docx；
5. 最后用 `toolbox_send_file` 发给对方，并在消息里说一句「已生成，共 N 条」。

注意：素材不够就先问对方要，别硬凑。
""",
    "整理会议记录": """# 整理会议记录

> 把一段混乱的对话/语音转写整理成结论清晰的会议纪要。

1. 先确认原始材料在哪（工作区文件用 `toolbox_fs_read`，聊天里的内容直接引用）；
2. 抽出三样东西：**结论**、**待办（谁/做什么/什么时候）**、**悬而未决的问题**；
3. 用 `toolbox_doc_write` 写 docx，标题写会议名，待办用 `- ` 开头（会自动变成列表项）；
4. 需要发出去就用 `toolbox_send_file`。

注意：不确定的地方标「待确认」，不要替别人拍板。
""",
    "批量改文件名": """# 批量改文件名

> 按规则批量重命名工作区里的一批文件。

1. 先用 `toolbox_fs_list` 看清目录里到底有什么，别凭印象；
2. 用 `toolbox_python` 写脚本：只处理匹配的文件，**先打印将要改的清单**；
3. 确认无误后在同一脚本里执行改名（`Path.rename`），并打印成功/失败各几条；
4. 如果改坏了要能还原：脚本里先记录「旧名→新名」到 `rename-log.json`。

注意：不要跨目录乱改，不要动 `.tmp/` 和 `sessions/` 下的东西。
""",
}


def ensure_defaults(root: Path) -> int:
    """首次运行时把内置示例技能写进目录（已存在的文件不覆盖）。返回新写入数量。"""
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, body in DEFAULT_SKILLS.items():
        path = directory / f"{name}.md"
        if path.exists():
            continue
        try:
            path.write_text(body, encoding="utf-8")
            written += 1
        except OSError:
            continue
    return written


def summary_lines(skills: list[Skill], *, limit: int = 20) -> list[str]:
    """给工具返回用的简要清单。"""
    lines: list[str] = []
    for skill in skills[: max(1, int(limit))]:
        desc = skill.description[:60]
        lines.append(f"- {skill.name}：{desc}" if desc else f"- {skill.name}")
    return lines
