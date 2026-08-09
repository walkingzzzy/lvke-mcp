# P0B freeze / build 模板说明

与 `scripts/golden_samples_manifest.py` 中 `validate_p0b` / `validate_build_record` 对齐。

## APPROVED.json 形状（示意）

```json
{
  "status": "frozen",
  "definition": "业务复核及真实回归通过后的正式期望",
  "expected_results": [
    {
      "sample_id": "gold_…",
      "group": "huangyingyan",
      "parser": "source_parser.v1",
      "parser_version": "source_parser.v1",
      "tolerances": { "irr_pp": 0.01, "amount": 0.01 },
      "test_cases": ["backend_e2e_huangyingyan"],
      "reference_track": {
        "version": "ref-…",
        "hash": "sha256:…",
        "approval_id": "appr_…",
        "approved_by": "biz-owner-id",
        "approved_at": "2026-07-15T12:00:00+08:00"
      },
      "corrected_track": {
        "version": "corr-…",
        "hash": "sha256:…",
        "approval_id": "appr_…",
        "approved_by": "biz-owner-id",
        "approved_at": "2026-07-15T12:00:00+08:00"
      },
      "difference_decisions": []
    }
  ],
  "last_passing_build": null
}
```

`expected_results` 必须覆盖 `huangyingyan`、`finance_templates`、`hengli_hotel` 三组（可多条，但 group 集合必须等于这三组）。

## PASSED_BUILD.json 形状（示意）

```json
{
  "build_id": "local-20260715-1",
  "commit_sha": "<git rev-parse HEAD>",
  "passed_at": "2026-07-15T15:00:00+08:00",
  "test_report_sha256": "sha256 of verify json report",
  "status": "passed",
  "groups": ["huangyingyan", "finance_templates", "hengli_hotel"],
  "skipped": [],
  "timed_out": [],
  "temporary_dependencies": []
}
```

`groups` 也可用集合等价的三元素列表；脚本用 `set(...)` 比较。
