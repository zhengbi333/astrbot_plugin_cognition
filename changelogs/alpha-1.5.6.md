# alpha-1.5.6

## 修硬编码路径

`page_config.py` 里有：

```python
Path("C:/Users/阡然/.astrbot/data/config") / f"{PLUGIN_NAME}_config.json"
```

**换台电脑、换个 Windows 账户，认知面板读配置就直接崩。**

现在改成动态解析：

1. 以插件数据目录为锚（`<data>/plugin_data/<插件名>` → 向上找 `<data>/config/`）；
2. 环境变量 `ASTRBOT_DATA`；
3. 家目录 `~/.astrbot/data/config/`。

三处兜底，不写死任何用户名。三个插件的 `阡然` 字样已全部清零（各 0 处）。
