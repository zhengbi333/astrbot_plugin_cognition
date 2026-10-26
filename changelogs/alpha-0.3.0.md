# alpha-0.3.0

## 一、`toolbox_send_file` —— 能让它"把东西发我"了

之前的工具只能**写进工作区**，用户看不到；"把整理好的文档发我"这句话它做不到，只能干说"已经整理好了"。
本版新增发送能力：`toolbox_send_file(path, caption="")` 用 `File` 组件组一条消息链，
通过 `context.send_message(会话, 链)` 发到当前聊天。

- 只能发**工作区内**的文件（同一套路径监狱）；
- 体积上限 `send.max_bytes`（默认 50MB），超限拒发并说明；
- 开关 `send.enabled`（默认开）。

## 二、`.pptx` 读写 —— "帮我做个 PPT"

`python-pptx` 已装（1.0.2）。新增：

- `toolbox_doc_write` 支持 `.pptx`：按**简单大纲**生成幻灯片 ——
  单独一行 `---` 分页，`#` 开头是页标题，`- ` 开头是要点，其余行按正文要点处理；
  给了 `title` 就多一张封面页。
- `toolbox_doc_read` 支持 `.pptx`：逐页读出标题与文本（页数上限 `office.max_slides`，默认 30）。

排版是"标题 + 要点"的朴素版式，不做花哨模板 —— 但它真的能生成一个能打开的 PPT 文件。

## 三、验证

`_st_test/test_cognition_unit.py` —— **94/94**（0.2.1 的 79 项 + 新增 15 项）：

| 组 | 新增覆盖 |
|---|---|
| G10–G15 | python-pptx 可用、`parse_slides` 分页/去 `#`/要点与普通行归类、写 pptx 页数、读回含标题与要点 |
| M1–M6 | send_file 接线：注册、受 `send.enabled` 约束、用 `File` 组件、走 `send_message`、体积上限、环境里 `File` 可导入 |
| N1–N3 | schema：`send` 组默认开、`max_bytes` 默认 50MB、`office.max_slides` 默认 30 |

## 四、配合本轮配置调整

使用者侧已把 `capabilities.max_rounds` 调到 **3**、打开 `capabilities.astrbot_tools_enabled` 并配了 12 个工具的白名单
（含 `toolbox_send_file`）——这样"做个 PPT 然后发我"才是一条能走完的链路：
生成 → 写入工作区 → 发送。

## 五、已知边界

- pptx 只有两种版式（封面 + 标题与内容），不支持图表、图片、动画、主题配色。
- 文件名里的中文在部分适配器上可能被转义，若收到乱码请告诉我，我改成发送前重命名。
- 发送依赖 AstrBot 的 `File` 组件与当前适配器支持（aiocqhttp 已验证路径形态，未在真实群聊端到端验证）。
