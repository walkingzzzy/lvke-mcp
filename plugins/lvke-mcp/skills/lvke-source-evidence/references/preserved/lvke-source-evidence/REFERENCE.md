---
name: lvke-source-evidence
description: >
  Source-file import, format and integrity validation, parse jobs, page/cell evidence,
  and evidence-quality gates for Lvke MCP. Use when importing project files, parsing
  PDF/XLSX/DOCX/images, handling OCR partial results, or deciding whether extracted
  values can enter formal evidence and finance inputs. This is not a security-review,
  identity, role, or permission-management workflow.
---


# 原始资料与证据链

## 流程

```text
`source_import_content` / `source_upload_*` → 不可变 SourceFileSnapshot
  → 格式、MIME、文件大小和容器完整性校验
  → `source_parse_start` / `source_parse_status`
  → 页/格 evidence（OCR 低置信度标记）
  → 事实候选复核与 EvidencePack
  → 正式工件绑定 source/evidence lineage
```

本产品只提供本地 stdio MCP 和 Codex Skills，没有前端、安全审查、身份认证、角色或权限管理。文件格式与完整性校验只用于防止解析器接收损坏或不受支持的输入，不产生安全签审结论。

## 规则

- 原件不可变；解析失败可 partial，但须可查询、可重试。
- partial（旧 DOC/OFD/扫描）**合法**，不得标成「全量自动通过」。
- 证据默认 grade 保守；升级必须有可定位来源和显式复核结果。
- P0A：`golden_samples_manifest.py --verify` 只证明冻结原件一致。
- 金标目录 gitignore；用 `LVKE_GOLDEN_DATA_ROOT`（`docs/ci-golden-corpus.md`）。

## 验证

```bash
uv run pytest -q tests/test_source_security.py tests/test_source_files_contract.py
LVKE_GOLDEN_DATA_ROOT=... uv run python scripts/golden_samples_manifest.py --verify
```

## 反模式

- 跳过格式、完整性或证据资格校验直接 formal
- 把 OCR 未复核值写入批准财务输入
- 提交金标二进制或扫描件进 git
