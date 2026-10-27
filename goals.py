"""长期目标（对应 DSH 的 goal）：跨会话记住"要做成什么事"。

和"约定（promise）"的区别：
- 约定是"我说过要做某件事"，来源于对话，会自动结算；
- 目标是"**要达成的结果**"，由人或她主动立，只有显式完成/放弃才结束，可以挂很久。

存储：`<插件数据目录>/goals.json`，结构简单、便于人直接看。

规则：
- 目标数量有上限（默认 50），超了拒绝新建；
- 完成/放弃是终态，不可回退（要重开就新建一个）；
- 每次读写都落盘（不做内存缓存），避免进程重启丢目标。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_GOALS = 50
STATES = ("active", "done", "dropped")


class GoalError(Exception):
    """目标操作失败（信息面向模型可读）。"""


@dataclass
class Goal:
    id: str
    title: str
    note: str = ""
    state: str = "active"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def is_open(self) -> bool:
        return self.state == "active"

    def line(self) -> str:
        mark = {"active": "○", "done": "✓", "dropped": "✗"}.get(self.state, "?")
        when = time.strftime("%m-%d", time.localtime(self.created))
        note = f" — {self.note}" if self.note else ""
        return f"{mark} [{self.id}] {self.title}{note}（{when} 立）"


class GoalStore:
    def __init__(self, path: Path, *, max_goals: int = MAX_GOALS) -> None:
        self.path = Path(path)
        self.max_goals = max(1, int(max_goals))

    # ------------------------------------------------------------ 存储
    def _load(self) -> list[Goal]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[Goal] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                goal = Goal(
                    id=str(item.get("id") or ""),
                    title=str(item.get("title") or ""),
                    note=str(item.get("note") or ""),
                    state=str(item.get("state") or "active"),
                    created=float(item.get("created") or time.time()),
                    updated=float(item.get("updated") or time.time()),
                )
            except (TypeError, ValueError):
                continue
            if goal.id and goal.title and goal.state in STATES:
                out.append(goal)
        return out

    def _save(self, goals: list[Goal]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(goal) for goal in goals]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 原子替换，避免写一半崩了留下坏文件

    # ------------------------------------------------------------ 操作
    def add(self, title: str, note: str = "") -> Goal:
        text = str(title or "").strip()
        if not text:
            raise GoalError("目标要有个说法（title）")
        if len(text) > 120:
            raise GoalError("目标太长（>120 字），写短一点")
        goals = self._load()
        if len([g for g in goals if g.is_open]) >= self.max_goals:
            raise GoalError(f"进行中的目标已有 {self.max_goals} 个，先完成或放弃一些")
        goal = Goal(id=secrets.token_hex(2), title=text, note=str(note or "").strip()[:200])
        goals.append(goal)
        self._save(goals)
        return goal

    def list(self, *, include_closed: bool = False, limit: int = 30) -> list[Goal]:
        goals = self._load()
        if not include_closed:
            goals = [g for g in goals if g.is_open]
        goals.sort(key=lambda g: (not g.is_open, -g.updated))
        return goals[: max(1, int(limit))]

    def get(self, goal_id: str) -> Goal:
        wanted = str(goal_id or "").strip().lower()
        for goal in self._load():
            if goal.id.lower() == wanted:
                return goal
        raise GoalError(f"没有这个目标：{goal_id or '（空）'}")

    def close(self, goal_id: str, *, state: str = "done", note: str = "") -> Goal:
        if state not in ("done", "dropped"):
            raise GoalError("只能标记为 done（完成）或 dropped（放弃）")
        goals = self._load()
        wanted = str(goal_id or "").strip().lower()
        for goal in goals:
            if goal.id.lower() != wanted:
                continue
            if not goal.is_open:
                raise GoalError(f"这个目标已经{'完成' if goal.state == 'done' else '放弃'}了")
            goal.state = state
            goal.updated = time.time()
            if note:
                goal.note = (goal.note + "；" if goal.note else "") + str(note)[:120]
            self._save(goals)
            return goal
        raise GoalError(f"没有这个目标：{goal_id or '（空）'}")

    def update(self, goal_id: str, *, note: str = "") -> Goal:
        goals = self._load()
        wanted = str(goal_id or "").strip().lower()
        for goal in goals:
            if goal.id.lower() != wanted:
                continue
            if note:
                goal.note = str(note)[:200]
            goal.updated = time.time()
            self._save(goals)
            return goal
        raise GoalError(f"没有这个目标：{goal_id or '（空）'}")
