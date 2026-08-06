---
name: lvke-source-evidence
description: >
  Source upload, security scan, parse jobs, page/cell evidence, and review gates
  for Lvke. Use when changing source_files_api, source_security, evidence reviews,
  parse jobs, OCR, partial, formal_use, 原始资料, 宏, 扫描, or whether OCR partial
  can enter approved finance. Trigger on source-files and evidence UI work.
---


# 原始资料与证据链

## 流程

```text
上传 → 净化文件名/原子落盘(只读原件)
  → source_security（magic/MIME/逻辑格式/ZIP bomb/宏/外链/OLE）
  → 高风险人工 security-review（未允许 formal_use 不得当正式证据）
  → 统一 parse job
  → 页/格 evidence（OCR 低置信度标记）
  → 人工 evidence review / batch-review
  → source_basis_snapshot 绑定正式工件
```

代码：`hermes_cli/source_files_api.py`、`source_security.py`。  
前端：`web/src/features/sources` + EvidencePage。

## 规则

- 原件不可变；解析失败可 partial，但须可查询、可重试。  
- partial（旧 DOC/OFD/扫描）**合法**，不得标成「全量自动通过」。  
- 证据默认 grade 保守；升级靠人工复核。  
- P0A：`golden_samples_manifest.py --verify` 只证明冻结原件一致。  
- 金标目录 gitignore；用 `LVKE_GOLDEN_DATA_ROOT`（`docs/ci-golden-corpus.md`）。

## 验证

```bash
uv run pytest -q tests/test_source_security.py tests/test_source_files_contract.py
LVKE_GOLDEN_DATA_ROOT=... uv run python scripts/golden_samples_manifest.py --verify
```

## 反模式

- 跳过安全扫描直接 formal  
- 把 OCR 未复核值写入批准财务输入  
- 提交金标二进制或扫描件进 git  
