---
name: lvke-delivery-guardrails
description: >
  Enforces 湖北绿科可研工作台 delivery discipline when implementing, reviewing,
  or claiming completion. Use whenever changing finance, research, source,
  lifecycle, deliverables, quality-review, or export code; when writing status reports;
  when the user mentions 正式交付, 生产级, 整机, 完成了吗, 算不算完成, P0B, 金标,
  fail-closed, 完成矩阵, dual-track, 单测都绿, 发布, or tries
  to treat tests/API success as client sign-off. Prefer this over generic
  "just make it pass" shortcuts.
---


# Lvke 交付护栏

## 何时加载更多

- 金标/双轨细节 → 读仓库记忆或 `docs/甲方需求当前实现完成矩阵_20260714.md`
- 阶段任务 → `生产级开发方案_20260715.md`
- 事实源 → `docs/README.md`

## 强制纪律

### 五档状态（完成 = 仅最高档）

1. 已实现且真实样本通过
2. 已实现但真实样本未通过
3. 部分实现
4. 尚未实现
5. 资料不足无法判定

禁止把「能生成 / 单测绿 / 构造金标 / API 200」说成正式交付。

### 红线（违反即打回）

1. **LLM 不填财务格** — 算术只走 `finance_model.compute_financials`；正式路径 `run_workspace_finance_model(..., allow_prepare_llm=False)`。
2. **质量门禁不旁路** — 保留 DR quality gate、报告 validate/readiness、finding 处置与带证据复测。
3. **不提供安全签审** — 产品没有登录、角色、权限、租户或专业签章流程。
4. **Codex 不直接覆盖正文** — 提案→差异→应用并生成不可变新 revision。
5. **双轨不塌缩** — 参考轨复现甲方（错误披露）；修正轨=批准口径；禁止抄甲方错误凑 IRR。
6. **本地 Git** — 默认不 push/PR；本仓为本地交付。
7. **联网研究须多来源** — Tavily 是唯一联网 provider，但禁止「单次查询即落结论或数值表」。须使用多个查询角度和多个独立来源，完成正文固化或显式阻断，否则完成态最高只能报「部分实现 / 资料不足」。
### 事实源优先级

代码与可重复测试 > docs 现行契约 > 带日期产物 JSON > 方案中的「目标」> archive。

## 改代码时的检查清单

```text
- [ ] 是否误加了登录、角色、租户、RBAC、权限或安全签审？如有则删除
- [ ] 是否改动阈值？只能收紧；须在 PR/说明写明
- [ ] 财务数字是否仍仅来自确定性引擎？
- [ ] 新「完成」表述是否落到五档状态 + 证据路径？
- [ ] 是否把一次性 output/ 金标二进制提交进 git？（禁止）
- [ ] 若含联网调研：是否完成 Tavily 多查询、多发布主体交叉验证 + 正文固化/显式阻断？有无单次搜索短路？
```
## 推荐验证

```bash
conda run -n lvke-mcp python -m pytest -q \
  tests/integration/test_asset_acquisition_artifact_gates.py \
  tests/integration/test_build_metadata.py \
  tests/integration/test_golden_samples_manifest.py
# 有金标根时：
LVKE_GOLDEN_DATA_ROOT=... conda run -n lvke-mcp python \
  scripts/golden_samples_manifest.py --verify
```

资产收购的 `estimate_preview` / `process_acceptance` 调用正式工件接口时，
稳定分类为 `EXPECTED_REJECTION`，业务码为
`FORMAL_ARTIFACT_QUALIFICATION_REQUIRED`；证据绑定变化使用
`EVIDENCE_BINDING_STALE`。预览报告必须走
`report_prepare(finance_binding.kind=asset_acquisition)`，技术校验通过也不
代表 `formal_release_eligible=true`。

## 与产品 Skills 的边界

仓库根 `skills/` 是 Codex 的业务与开发 Skills。MCP 负责确定性工具和对象，Codex 负责多步编排和正文生成。
