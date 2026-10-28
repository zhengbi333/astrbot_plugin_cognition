# alpha-1.5.15

## 收尾：把「面板可见」真正验证了

这是 goal 里最后一条硬要求：**没有面板入口的动作，不算做完**。

## 我原来的假设是错的

我原以为「配置写进 `_conf_schema.json` → 面板自动能看到」就够了。
实际跑了一次 `page_config.describe()` 才发现要验证三件事，而且我第一次写成三处错：

| 错误 | 现象 | 真相 |
|---|---|---|
| ① 字段名没加组前缀 | 查 `poke_enabled` 查不到 | `_flatten(trail=name)` 输出的是 `actions.poke_enabled` |
| ② 说明字段名猜错 | 「有说明」9 项全 FAIL | `_flatten` 用 `label`，不是 `description` |
| ③ 以为要改 page_config | — | 它动态读 schema，**不用改** |

## 实际验证结果（`audit_105_panel_visibility.py` 29/29）

```
共 18 组：general / auth / workspace / fs / web / download / send / office /
          exec / shell / tasks / parallel / goals / delegate / simulation /
          actions / notes / limits
```

`actions` 组里的 9 项全部可见，且**默认值读对**、**说明不为空**：

| 键 | 面板显示 | 类型 | 默认 |
|---|---|---|---|
| `poke_enabled` | 允许戳一戳 | bool | 开 |
| `signature_enabled` | 允许改个性签名 | bool | 关 |
| `like_enabled` | 允许名片点赞 | bool | 开 |
| `qzone_post_enabled` | 允许发空间说说 | bool | 关 |
| `qzone_post_ugc_right` | 说说的可见范围 | int | 64 |
| `qzone_feeds_enabled` | 允许看好友动态 | bool | 开 |
| `qzone_like_enabled` | 允许给空间说说点赞 | bool | 关 |
| `qzone_comment_enabled` | 允许评论空间说说 | bool | 关 |
| `import_faces_enabled` | 允许导入 QQ 收藏表情 | bool | 开 |

## 另一个实际证据：工具真的注册上了

跑测试时 AstrBot 的加载日志里出现：

```
Added llm tool: qq_poke
Added llm tool: qq_set_signature
Added llm tool: qq_like
Added llm tool: qq_qzone_post
Added llm tool: qq_qzone_feeds
Added llm tool: qq_qzone_like
Added llm tool: qq_qzone_comment
Added llm tool: qq_import_faces
```

**八个都在**。这比单元测试更有力 —— 是 AstrBot 自己的 `func_tool_manager` 打印的。
