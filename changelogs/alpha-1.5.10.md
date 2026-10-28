# alpha-1.5.10

## 第 3 个迁移：名片赞 → `qq_like`

第三个也是最后一个「小动作」。到这里三个都齐了：

| 工具 | SnowLuma 动作 | 权限 | 默认 |
|---|---|---|---|
| `qq_poke` | `send_poke` | member | 开 |
| `qq_set_signature` | `set_self_longnick` | member | **关**（对外可见） |
| `qq_like` | `send_like` | member | 开 |

## `qq_like` 的细节

```python
_gate("like", "actions.like_enabled")
_allow(event, "like")            # member 起
times = max(1, min(5, times))    # 模型填 99 也只点 5 次
_sim_block(...)
await caller("send_like", user_id=int(sender), times=times)
_note_call(...)
_remember_action(event, kind="like", ...)
```

`user_id` 从事件取（不让模型猜 QQ 号）。

## 验证（`audit_100_like_in_cognition.py` 24/24）

- **A** 五件套 + `send_like` + `times` 夹紧；
- **B** 权限：member 起，访客 False；
- **C** 配置：`actions.like_enabled` 默认开；
- **D** 陪伴插件三个动作**全部清空**（能力与日限都没有了）；
- **E** 语法通过；
- **F** 认知现在有**三个** `qq_*` 工具。

## 阶段小结：「认知 = 统一工具库」落实到什么程度

| 项 | 状态 |
|---|---|
| 三个小动作全部迁入认知 | ✅ |
| 每个都有五件套（门禁/权限/试运行/审计/联动） | ✅ |
| 陪伴插件不再承载动作能力 | ✅（`capabilities` 回到纯"说话相关"：画图、表情、记忆、搜索） |
| 配置项在面板可见 | ✅ 走 `page_config`（设置页能改） |

## 已知边界

- 三个动作都**只做了"能调"**，没有做"什么时候该调"的策略（模型看到工具自己判断）；
- `qq_set_signature` 默认关，你要用自己去面板开；
- 还没做 qzone 那四个（发说说、逛空间、看动态、导表情）——那是下一阶段。
