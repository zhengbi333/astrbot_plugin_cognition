# alpha-1.5.14

## 第 7 条（最后一条）：表情导入（`qq_import_faces`）

## 这是唯一跨插件的一条

```
QQ 那边的数据（fetch_custom_face 只读）
        ↓
   认知插件取 + 下载
        ↓
陪伴插件的 EmojiStore（add_emoji 写盘）
```

**为什么不放陪伴**：陪伴不该调 OneBot 动作（那是"动手"）。
**为什么不放认知独占**：表情库存放在陪伴那边（`emoji.py` 的 `EmojiStore`）。

## 怎么找陪伴实例

`notify.py` 里已经有 `find_persona()`，用 `context.get_all_stars()` + `StarMetadata.star_cls`
拿实例。我照同样模式加了 `find_storyteller()`：

```python
for meta in context.get_all_stars() or []:
    if "storyteller" in (module + name + module_name):
        return getattr(meta, "star_cls", None)
```

## 五种失败都如实说，绝不假装导了

| 情况 | 返回 |
|---|---|
| 找不到陪伴插件 | 「没找到陪伴插件的表情库（可能没加载）」 |
| EmojiStore 没 `add_emoji` | 「版本不支持」 |
| 取表情超时（40s） | 「取表情超时」 |
| 取表情抛错 | 「取表情失败：<原因>（如实说没导成）」 |
| 下载全失败 | 「一张都没导成 —— 如实说没导成」 |

**单张失败跳过**，最后报告「成功 N / 试了 M」。

## 分类处理

优先复用已有分类（标题含「QQ」的），没有就 `add_category("QQ 收藏表情")` 建一个。

## 验证（`audit_104_import_faces.py` 25/25）

- **A** 五件套 + `fetch_custom_face` + `find_storyteller`；
- **B** 权限 member（只影响她自己的表情库，无对外痕迹）；
- **C** 配置默认开；
- **D** 找不到陪伴 → 明说「不在线」；
- **E** EmojiStore 不支持 → 明说；
- **F** 取表情失败 → 明说；
- **G** 空列表 → 明说「没取到」；
- **H** 下载全失败 → 明说「一张都没导成」；
- **I** 三个文件语法；
- **J** **`find_storyteller` 本身**：能挑出陪伴、没有时返 None、`get_all_stars` 抛错也不崩。

## 已知边界

- **下载用 `urllib`**（同步）—— 图片小、张数少（上限 20），可接受；如果以后要导很多，改异步；
- 格式只认 png/gif（按 URL 后缀猜），其他格式可能存成 .png 但内容是别的；
- 重复导入会产生重复表情（没有去重）；靠 EmojiStore 自己的文件名规则避免覆盖；
- **依赖陪伴插件的 `EmojiStore` 接口不变**（`add_emoji(cid, filename, content)`）。
