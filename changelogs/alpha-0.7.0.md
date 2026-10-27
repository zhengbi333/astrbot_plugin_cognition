# alpha-0.7.0

## 补上「认知 → 人格 → 记忆」这条断边

之前：认知抓到的网页、做出的文档、下载的文件**只躺在工作区**，不会留下任何记忆痕迹。
她今天做的事，明天不记得。三条联动边里，人格↔记忆、人格↔认知都有，**认知→记忆这一条是断的**。

本版按"转接"方案补上（不是让认知直接写记忆库）：

```
认知（notify.py）──> 人格（note_external_event）──> deepmemory（record_bot_action）
```

### 认知侧（notify.py）

- `find_persona(context)`：通过 `context.get_all_stars()` 找到人格实例（`StarMetadata.star_cls`）；
- `notify_persona(...)`：把"我做了什么"转告人格，**超时 6 秒、异常全吞**；
- 新增 `_remember_action()` 助手，在 5 类**成功**路径上调用：
  `toolbox_learn`、`doc_write`（docx/xlsx/pptx 三种）、`download`、`send_file`。

### 人格侧（note_external_event）

- 统一的记忆写入口：`bridge.record_bot_action()`（`self_action`，source=`astrbot_plugin_cognition`）；
- **去重**（kind + 文本前 80 字，最近 80 条）；
- **绝不抛异常**给调用方（工具执行路径不能被记忆问题拖垮），只返回 `{ok, reason, memory_id}`；
- 没有记忆桥时返回 `no_bridge`，不影响工具结果。

### 为什么不直接写记忆库

记忆只有一个写入口：措辞、去重、会话归属由人格统一决定；认知保持"手"的分工，
即使记忆插件没装/没启用，也只是少记一笔，工具照常工作。

## 验证

`_st_test/audit_65_link_bridge.py` —— **20/20**：

- 认知能找到人格实例、参数原样传到；
- 未装人格 / 无入口 / 超时 / 人格内部异常，**全部安静降级**；
- 人格写入走 `record_bot_action`、source 正确、带会话上下文、重复内容去重、无桥与写入异常都不抛；
- 认知 5 类成功路径接线完整（doc_write 三种格式都在）。

## 边界

- 只在**成功**路径留痕，失败不写（避免记忆库被失败记录灌满）。
- 只写"做了什么 + 在哪 + 要点"，不写长文（≤1200 字）。
- 人格若未加载，则本轮行动不会留痕（这是有意的降级，不是 bug）。
