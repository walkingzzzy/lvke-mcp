---
name: lvke-source-import-route-selection
description: >
  Choose between source_import_local_path and source_external_corpus_resolve when
  bringing client materials into a Lvke workspace. Use for new project intake, local
  file import, external corpus routing, LVKE_SOURCE_IMPORT_ROOTS configuration, or
  when external_corpus_unavailable blocks a run.
---

# 来源导入路径选择

导入项目原始资料有两条独立路径。选错会撞上 `external_corpus_unavailable`，并容易误判为"需要登记项目"。

## 判定规则

| 场景 | 工具 | 前提 |
|---|---|---|
| **新项目**，资料在本机任意允许目录 | `source_import_local_path` | 设置 `LVKE_SOURCE_IMPORT_ROOTS` 指向资料目录；**不需要**登记项目名称 |
| **用户直接提交内容**（对话粘贴、Base64） | `source_import_content` | 无需任何环境变量 |
| **大文件分块上传** | `source_upload_begin/chunk/commit` | 无需任何环境变量 |
| **预置样本项目**（崇阳香苑、潜山国家森林公园、恒立酒店） | `source_external_corpus_resolve` | 项目名称必须已登记在 `external_corpora.v1.json`，且 `LVKE_EXTERNAL_CORPUS_ROOT` 已配置 |

**默认走 `source_import_local_path`。** 只有明确处理上述三个预置样本项目时才用 corpus resolve。

## external_corpora.v1.json 的性质

它是**固定外部语料目录 + 预置项目路由表**，不是所有项目的业务数据库。当前只登记 3 个项目。

- 新项目**不要**写入这个文件。
- 只有 `source_external_corpus_resolve` 会读取项目清单；其他导入工具都不读。
- `source_external_corpus_resolve` 只返回 corpus 根目录、marker、路由信息，**不枚举也不导入文件**。导入仍需逐文件调用导入工具。

## 标准新项目流程

```
1. project_context_create(workspace_id, context={…}, idempotency_key)
   → project_context_id

2. 配置 LVKE_SOURCE_IMPORT_ROOTS 指向资料目录（server env，需重启生效）

3. 对每个文件：
   source_import_local_path(workspace_id, local_path=绝对路径, idempotency_key)
   → source_file_id

4. source_task_status / source_file_get 确认解析状态
   → "已解析" 不等于 "已采信"

5. analysis_ingest(source_snapshot_ids) → 进入证据链
```

`source_import_local_path` 的约束（与项目登记无关）：

- 仅允许 stdio/local transport
- `local_path` 必须是绝对路径
- 文件必须位于 `LVKE_SOURCE_IMPORT_ROOTS` 允许目录内
- 必须是单链接普通文件（拒绝符号链接、硬链接、设备文件）

## external_corpus_unavailable 诊断

该错误码覆盖多种原因，读响应中的 `detail` 字段定位：

| `detail` | 原因 | 处置 |
|---|---|---|
| `root_not_configured` | `LVKE_EXTERNAL_CORPUS_ROOT` 未设置 | 配置环境变量，或改用 `source_import_local_path` |
| `root_not_found` | 根路径不存在 | 检查路径是否已挂载 |
| `manifest_invalid` | manifest 不可读或 schema 错误 | 检查 `external_corpora.v1.json` 完整性 |
| `corpus_missing` | 语料目录或 marker 文件缺失 | 检查根目录下的文件结构 |
| `project_not_registered` | 项目名未登记 | **优先改用 `source_import_local_path`**；确需登记才改 manifest |
| `project_ambiguous` | 项目名匹配多条路由 | 在 manifest 中消除别名冲突 |
| `import_roots_invalid` | `LVKE_SOURCE_IMPORT_ROOTS` 路径无效 | 检查路径存在且为目录 |

## 输入与产出目录不要混淆

```
甲方原始资料（输入）
  → 任意允许目录，经 LVKE_SOURCE_IMPORT_ROOTS 授权

~/.lvke/workspaces/<workspace>/source-files
  → MCP 导入后的受控副本与解析状态

lvke产出/<workspace>/...
  → MCP 新生成的报告、十三表、提案、正式工件
```

产出根由 `LVKE_DELIVERABLE_DIR` → `<LVKE_MCP_DATA_DIR>/lvke产出` → 仓库根 `lvke产出/` 三级回退决定。不要把输入目录当产出目录，也不要把产出目录加进 `LVKE_SOURCE_IMPORT_ROOTS`。

## 反模式

- 为新项目修改 `external_corpora.v1.json`
- 把 `LVKE_EXTERNAL_CORPUS_ROOT` 指向任意新项目资料目录，期望 resolve 能识别未登记项目
- 以为 `source_external_corpus_resolve` 会自动导入全部文件
- 撞到 `project_not_registered` 就去登记项目，而不先考虑 `source_import_local_path`
- 把"已解析"当作"已采信"，跳过证据资格判定
