---
name: lvke-golden-p0b
description: >
  Golden-sample P0A verify and P0B freeze/record-build workflow for Lvke. Use when
  editing golden_samples_manifest, freeze-p0b, record-build, last_passing_build,
  expected_results, LVKE_GOLDEN_DATA_ROOT, 金标, P0A, P0B, 黄鹰岩, 三组回归, skip
  golden, or claiming formal regression passed. Never record a passing build while
  golden verify is skipped or P0B is still pending.
---


# 金标 P0A / P0B

## 两层含义（勿混）

| 层 | 含义 | 当前（2026-07-15） |
|---|---|---|
| **P0A** | 原件路径/文件名/大小/SHA-256/locator 冻结 | `frozen`，46 份 |
| **P0B** | 业务期望结果 + 双轨批准元数据 + 通过构建戳 | `pending_business_approval`，`last_passing_build=null` |

P0A 通过 **≠** 业务批准 **≠** 正式交付。

## 组名（必须齐全）

`EXPECTED_GROUPS`（代码硬编码）：

- `huangyingyan`
- `finance_templates`
- `hengli_hotel`

## 语料挂载

原件目录 gitignore，不进公共 git。

```bash
export LVKE_GOLDEN_DATA_ROOT=/absolute/path/to/corpus
# 或
uv run python scripts/golden_samples_manifest.py --data-root "$LVKE_GOLDEN_DATA_ROOT" --verify
```

约定见 `docs/ci-golden-corpus.md`。无挂载时 **不得** 写 G7 `last_passing_build`。

## 命令

```bash
# 1) 校验冻结原件 + 合法 P0B 状态
uv run python scripts/golden_samples_manifest.py --verify

# 2) 业务批准材料齐备后冻结 P0B（APPROVED.json 见 references）
uv run python scripts/golden_samples_manifest.py --freeze-p0b path/to/APPROVED.json

# 3) 仅当 p0b.status 已是 frozen 后记录通过构建
uv run python scripts/golden_samples_manifest.py --record-build path/to/PASSED_BUILD.json
```

脚本：`scripts/golden_samples_manifest.py`  
清单：`config/golden_samples_manifest.json`

## P0B 冻结 JSON 必填（validate_p0b）

顶层：

- `status` 最终由脚本设为 `frozen`（输入文件也会被写成 frozen）
- `expected_results`: 数组，**三组都要有**
- `last_passing_build`: 冻结时可 null；pending 时 **禁止** 非 null

`expected_results[]` 每项：

- `sample_id`, `group`, `parser`, `parser_version`, `tolerances`, `test_cases`（皆必填非空）
- `reference_track`: `version`, `hash`, `approval_id`, `approved_by`, `approved_at`
- `corrected_track`: 同上五字段
- `difference_decisions`: **必须是 list**（可为空列表，但不能缺类型）

字段清单样例：`references/p0b-freeze-template.md`

## last_passing_build 必填（validate_build_record）

- `build_id`, `commit_sha`, `passed_at`, `test_report_sha256` 非空  
- `status` 必须是 `"passed"`  
- `groups` 集合必须等于三组  
- `skipped` / `timed_out` / `temporary_dependencies` 必须为 0、`[]`、false 或 null  

**禁止**在 golden skip、单测 skip、临时依赖未就绪时 record-build。

## 工作流清单

```text
- [ ] 语料根可解析 46 份 relative_path
- [ ] --verify 通过且 p0b 仍 pending 或已 frozen（与预期一致）
- [ ] 三组业务裁决/签字材料齐 → 写 APPROVED.json
- [ ] --freeze-p0b 成功
- [ ] 完整回归（focused + 三组场景）无 skip → 写 PASSED_BUILD.json
- [ ] --record-build 成功
- [ ] 汇报使用五档状态，不说「金标自动过了」
```

## 反模式

- 把 P0A verify 说成 P0B 完成  
- pending 时塞 `last_passing_build`  
- 缺一组 expected_results 仍 freeze  
- 无 `LVKE_GOLDEN_DATA_ROOT` 却 record-build  
- 提交金标 PDF/XLSX 进 git  
