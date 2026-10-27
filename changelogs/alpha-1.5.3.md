# alpha-1.5.3

## 修：设置页保存报「这些配置项不存在：enabled、admins、members、members_open、mode」

**原因**：`page_config.describe()` 摊平配置时漏传了分组前缀：

```python
rows = _flatten(section)              # ← 错：key 变成 enabled
rows = _flatten(section, trail=name)  # ← 对：key 是 general.enabled
```

于是前端拿到的是**短名**（`enabled`），而后端 `_allowed_keys()` 用的是**带前缀**的
（`general.enabled`），两者对不上 → 保存被拒。

**修完实测**：52 项**全部带前缀**，`describe` 的 key 集合 ⊆ 允许集合（`True`），
保存往返正常、未知键仍会被拒绝。

## 新增测试 `audit_82_config_panel.py`（18/18）

专门盯这个回归点：

- A 组：52 项全部带点、样例如 `general.enabled` / `auth.admins`、每项都有说明与控件类型；
- B 组：`describe ⊆ allowed`、数量一致（**这条能直接抓住前缀丢失**）；
- C 组：保存往返（嵌套落盘、返回变更列表、生成备份）、未知键被拒、`"60"` 转成 `int`;
- D 组：前后端接线（路由、页签、调用、说明渲染）。

## 顺带

- 类型转换做了实测：`"60"` → `60`（int）、`"true"` → `True`（bool）、JSON 字符串 → 对象；
- 写前备份保留在配置目录，带时间戳，不覆盖。
