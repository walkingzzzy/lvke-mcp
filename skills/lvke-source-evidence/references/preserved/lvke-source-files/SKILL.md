---
name: lvke-source-files
description: "Import, upload, scan, parse, retry, cancel, and read governed Lvke SourceFileSnapshot resources. Use for project PDFs, DOCX, XLSX, images, audio, or other controlled attachments before evidence extraction, finance import, report citation, or Deep Research."
---

# 受控附件

附件是来源，不是自动采信的证据。所有对象都使用调用方提供的 `workspace_id` 数据命名空间。

## 工作流

1. 小于等于 8 MiB 的内容使用 `source_import_content`；较大内容使用 begin/upload/commit 分块链。
2. 本地 stdio 可在宿主 allowlist 内使用 `source_import_local_path`；远程部署不得提交服务端路径。
3. 分块时保持 `upload_id`、offset、chunk hash 和总 hash，一块不缺后再 commit。
4. 上传与解析共用一个查询入口 `source_task_status`，按任务类型区分：分块上传传
   `task_kind="upload"` + `upload_id`（`ups_` 前缀），文件解析传 `task_kind="parse"` + `job_id`（`job_` 前缀）；
   任务类型必须与 ID 命名空间一致。只有安全扫描和解析满足下游要求时才读取 locator Resource。
5. 可重试解析失败调用 `source_parse_retry` 生成新任务；取消使用 `source_parse_cancel`，不覆盖旧任务。

## 禁止行为

- 不绕过 MIME/magic byte、hash、大小、路径、symlink 或设备文件检查。
- 不把“已解析”写成“已采信”；EvidencePack 仍需独立选择。
- 不暴露 staging、allowlist 或绝对存储路径。
- `blocked`、`expired`、`cancelled`、`unsafe` 均不得进入正式证据或财务导入。

完成条件是不可变 `file_id`、content hash、解析状态和可定位 Resource 齐全。
