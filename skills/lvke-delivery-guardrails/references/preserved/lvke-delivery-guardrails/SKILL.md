---
name: lvke-delivery-guardrails
description: >
  Enforces 湖北绿科可研工作台 delivery discipline when implementing, reviewing,
  or claiming completion. Use whenever changing finance, research, source,
  lifecycle, deliverables, RBAC, or release code; when writing status reports;
  when the user mentions 正式交付, 生产级, 整机, 完成了吗, 算不算完成, P0B, 金标,
  fail-closed, 签字, 签完字, 完成矩阵, dual-track, 单测都绿, 发布, 报批, or tries
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
2. **门禁只收紧不放宽** — `gate.py`、DR `contracts.py` / quality_gate、lifecycle 人工门、统一审查发布门禁（`review_release` / `report_release` / `acquisition_release_artifact` 已 fail-closed：缺 `review_id` 或未 released 即 `professional_review_required`；P0 不可豁免、职责分离、shadow 禁 release 均不得放宽）。  
3. **不生成真实签名** — 只登记/核验 envelope。  
4. **AI 不直接覆盖正式正文** — 提案→差异→人工应用；`approvals_never_automated`。  
5. **双轨不塌缩** — 参考轨复现甲方（错误披露）；修正轨=批准口径；禁止抄甲方错误凑 IRR。  
6. **本地 Git** — 默认不 push/PR；本仓为本地交付。  
7. **联网研究须多源** — 凡依赖公开网检索的研究/套件/市场规模/可比参数：禁止「单次搜索即落结论或数值表」。须满足 `lvke-source-acquisition` / `lvke-tool-coordination` 的多源门禁（≥2 通道、≥3 查询角、正文固化或显式阻断），否则完成态最高只能报「部分实现 / 资料不足」。
### 事实源优先级

代码与可重复测试 > docs 现行契约 > 带日期产物 JSON > 方案中的「目标」> archive。

## 改代码时的检查清单

```text
- [ ] 是否触及批准/发布/签字？认证 actor + RBAC 是否仍 fail-closed？
- [ ] 是否改动阈值？只能收紧；须在 PR/说明写明
- [ ] 财务数字是否仍仅来自确定性引擎？
- [ ] 新「完成」表述是否落到五档状态 + 证据路径？
- [ ] 是否把一次性 output/ 金标二进制提交进 git？（禁止）
- [ ] 若含联网调研：是否多源并行 + 正文固化/显式阻断？有无单次搜索短路？
```
## 推荐验证

```bash
uv run python scripts/verify_backend_requirements.py --profile smoke
# 有金标根时：
LVKE_GOLDEN_DATA_ROOT=... uv run python scripts/golden_samples_manifest.py --verify
```

## 与产品 skills 的边界

仓库根 `skills/`（doc-review、financial-modeling 等）是**研报/Agent 运行时**技能，服务业务 Agent。  
本目录 `.claude/skills/` 是 **Claude Code 开发时**技能。两者勿混写、勿互相覆盖红线。
