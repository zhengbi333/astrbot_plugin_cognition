# alpha-1.5.8

## 架构调整：认知插件 = 统一工具库

用户明确了一条原则：**第三个插件就是统一的工具库**，所有"动手/交互"动作都放这里，
陪伴插件只管说话。

工程上这个决定是对的：

| | 陪伴 capabilities | 认知工具 |
|---|---|---|
| 权限门 | **没有**（群友也能触发） | `_allow` 三级权限 |
| 审计 | 无 | `audit.jsonl` |
| 试运行 | 无 | 已有 `_sim_block` |
| 面板配置 | 被排除在设置页外 | `page_config` 17 组 52 项 |

## 第 1 个迁移：戳一戳 → `qq_poke`

从陪伴插件的 `capabilities.poke` 迁过来，套齐认知的五件套：

```python
block = self._gate("poke", "actions.poke_enabled")   # 总开关 + 分项开关 + 日配额
deny  = self._allow(event, "poke")                    # 权限（member 起）
sim   = self._sim_block("poke", "戳一戳")              # 试运行只演不做
await caller("send_poke", **args)                     # 真调用
self._note_call("poke", True, "已戳")                  # 审计
await self._remember_action(event, kind="poke", ...)  # 认知→人格→记忆
```

**命名**：`qq_poke`。`qq_*` = 她自己的小动作；`toolbox_*` = 替主人干活。

**权限**：`policy._ACTION_MIN_LEVEL` 新增 `"poke": LEVEL_MEMBER` —— **访客不能用**，
这是迁移最大的收益（原来放陪伴插件时群友也能让她戳）。

**配置**：`actions.poke_enabled`（默认开），在面板「设置」页可见。

## 验证（`audit_98_poke_in_cognition.py` 19/19）

- **A** 五件套齐全：`_gate` / `_allow` / `_sim_block` / `_note_call` / `_remember_action` / `send_poke`；
- **B** 权限档：`required_level("poke") == "member"`，访客 `can()` 为 False、成员为 True；
- **C** 配置：`actions.poke_enabled` 在、默认开、有 hint；
- **D** 陪伴插件已清理（没有 `poke` 能力、没有 `poke_daily`），不会出现两个重复工具；
- **E** 三个文件语法通过。

## 已知边界

- 陪伴插件里的 `_impl_poke` 函数体还留着（现在是死代码），下一轮清理时一起删；
- 迁移只做了 poke，`set_signature` / `like_contact` 仍在陪伴插件，按计划逐个迁。
