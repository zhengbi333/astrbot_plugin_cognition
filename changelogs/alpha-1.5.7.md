# alpha-1.5.7

## P0-3 试运行：写操作只演不做

新增 `simulation.enabled`（默认关）。

打开后，这六个工具**不真的动磁盘**，直接返回：

```
（试运行，未执行）写文件：现在只演不做，关闭试运行后会真正执行。
```

| 工具 | 说明 |
|---|---|
| `toolbox_fs_write` | 写文件 |
| `toolbox_edit` | 编辑文件 |
| `toolbox_doc_write` | 生成文档 |
| `toolbox_archive` | 打包/解包 |
| `toolbox_python` | 执行代码 |
| `toolbox_shell_run` | 执行命令 |

## 与人格插件配合

人格插件也有同名的 `simulation.enabled`。两边都开时：

- 人格侧：**不发送到 QQ**（只写日志）；
- 认知侧：**不执行写操作**（只回话）。

只开一边也能用：只开认知侧 → 她照常回话，但干活是"演"的；
只开人格侧 → 她照常干活，但你看不到她发出去的内容。

## 验证

`audit_88_simulation.py` 的 E 组覆盖：试运行下 `toolbox_fs_write` 返回「试运行」且文件未生成。
