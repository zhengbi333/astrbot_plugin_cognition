# alpha-1.5.12

## 第 5 条：看好友动态（`qq_qzone_feeds`）

**只读工具**，让她能看到朋友最近发了什么 —— 这是「接上别人话题」的途径。

## 参数

```
get_qzone_feeds({ page_num: 1, count: 10 })   首页
get_qzone_feeds({ cursor, count })             续页（cursor 用上次的 next_cursor）
```

我们只用首页 + `count`（夹 1~10），**不做续页** —— 一次看几条就够聊天用了，
翻页既慢又没必要。

## 三个设计决定

**① 权限 member（不是 admin）**

这是**只读**动作，不会改动任何东西，所以给成员的权限就够。
对比：`qzone_post` 是 admin（会公开留下痕迹）。

**② 40 秒超时**

空间接口走腾讯网页 API，比普通动作慢得多。不设超时必然失败
（这是上一轮从 Aurabot 学到的教训）。

**③ 只把可读正文给模型，不给 html**

SnowLuma 的返回里同时有 `content` 和 `html`。整页 html 塞给模型既浪费 token
又干扰判断。我只抽 `content`（空则用 `summary`），截 120 字。

## 返回结构兼容

SnowLuma 的返回可能是 `{list: []}`、`{feeds: []}`、`{data: []}`、`{items: []}`，
也可能直接是数组。四种字段名都试，避免它改结构就崩。

## 验证（`audit_102_qzone_feeds.py` 23/23）

- **A** 定义：`_gate` / `_allow` / `_note_call` / `get_qzone_feeds` / **40 秒超时** / count 夹紧；
- **B** 权限：**member 起**（且断言「只读工具不该要 admin」）、访客 False、成员 True；
- **C** 配置：`qzone_feeds_enabled` 默认开、hint 说明只读；
- **D** 行为：解析 `list` / `feeds` 两种结构、空返回如实说、失败时提「可能是登录态」；
- **E** 语法。

## 已知边界

- 只读首页，不做翻页；
- 图片不描述（只给正文）；
- 好友动态内容**不可控**（别人发什么她看到什么），如果担心内容安全，把
  `actions.qzone_feeds_enabled` 关掉。
