# alpha-1.5.9

## 第 2 个迁移：改签名 → `qq_set_signature`

从陪伴插件的 `capabilities.set_signature` 迁过来，五件套齐。

```python
_gate("signature", "actions.signature_enabled")
_allow(event, "signature")          # member 起
# 长度校验（先验参数，再试运行）
_sim_block("signature", "改签名")
await caller("set_self_longnick", long_nick=text)
_note_call("signature", True, text[:20])
_remember_action(event, kind="signature", text=f"把签名改成了「{text}」")
```

**参数名是 `long_nick`**，不是 `text` —— SnowLuma 要求严格 string。

## 默认关（重要）

```json
"actions.signature_enabled": { "default": false }
```

**为什么默认关**：个性签名是**对外可见**的，别人点开她资料就能看到。
改太勤、写得太营销，很容易被当成号商。你要开就自己去面板开。

对比：`poke` 默认开（只是戳一下，无害），`signature` 默认关（会留下公开痕迹）。

## 校验顺序（有讲究）

```
① _gate      → 开关/配额
② _allow     → 权限
③ 参数校验   → 空文本、超 60 字
④ _sim_block → 试运行
⑤ 真调用
```

**参数校验放在试运行之前**：试运行时也应该告诉模型"你写太长了"，
而不是让它以为"演一遍就通过了"。

## 验证（`audit_99_signature_in_cognition.py` 21/21）

- **A** 五件套 + 参数名 `long_nick` + 调 `set_self_longnick`；
- **B** 权限：`required_level("signature") == "member"`，访客 False、成员 True；
- **C** 配置：`actions.signature_enabled` 在、**默认关**、hint 说明"对外可见"；
- **D** 陪伴已清理（无 `set_signature`、无 `signature_daily`）；
- **E** 60 字上限与空文本拒绝都在代码里；
- **F** 三个文件语法通过。

## 已知边界

- 陪伴插件的 `_impl_set_signature` 函数体还留着（死代码），三个都迁完后统一清；
- 签名改完**真的对外可见**，默认关是我保守的选择。
