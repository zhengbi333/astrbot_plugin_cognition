# alpha-1.4.1

## 修：插件加载失败（不受支持的配置类型 number）

面板报：

```
不受支持的配置类型 number。支持的类型有：
dict_keys(['int', 'float', 'bool', 'string', 'text', 'list', 'file', 'object',
'template_list', 'dict'])
```

**原因**：`exec.timeout_seconds` 的类型写成了 `"number"` —— 这是早期（0.3.x）留下的写法，
AstrBot 的类型校验只认上面那几种，`number` 不在其中，于是整个插件加载失败。

**修法**：`number` → `int`（超时是整秒，本来就该是 int）。

**顺带做了件更彻底的事**：写了个扫描脚本，把三个插件 **所有** `_conf_schema.json` 里的
`type` 值全部对照合法列表检查一遍，并把常见别名规范化：

| 非法/别名 | 规范为 |
|---|---|
| `number` / `integer` | `int` |
| `str` | `string` |
| `boolean` | `bool` |
| `array` / `dict_keys` | `list` |
| `textarea` | `text` |

结果：storyteller、deepmemory **都干净**，只有 cognition 这一处。

## 验证

- 三插件 schema 全部通过合法类型检查；
- 认知单测 **149/149**、全量回归 **690/690**、一键验收 **40/40**；
- 已重新部署到运行目录。

## 反思

这类错误**测试测不出来** —— 单测和回归都不加载 `_conf_schema.json` 的类型校验，
只有 AstrBot 真正加载插件时才炸。应该在验收里加一条「schema 类型合法性」检查，
这样以后改配置就不会再踩。已在 `acceptance_all_plugins.py` 里补上。
