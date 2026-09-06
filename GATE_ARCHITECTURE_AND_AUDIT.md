# 门禁体系说明与审查结论

> **审查基准**:HEAD `e193c4d`(`fix/delivery-honesty-and-skill-coverage` 分支)的**干净检出**。
> 审查方法为只读 + 运行时探针,全程未改动工作区。
>
> **本文档与能力总文档的分工**:`dev-docs/architecture/CAPABILITY_AND_PROCESS_GUIDE.md`
> §3.3 给出"口径非法阻断 vs 置信度不足放行"这条**产品语义**;本文档给出该语义在代码里的
> **实现地图**(哪些机制、哪些门、哪些码)与**实测缺陷清单**。两者不重复:改产品口径改 §3.3,
> 改门禁实现或修缺陷看本文档。

## 0. 一句话结论

系统有**三套彼此独立的门禁机制**,不是一套。其中两套结构上 fail-closed 且健壮;
**全部已实证缺陷集中在第三套(交付质量分类器),以及它与另两套的交界处**。

理解这个区分是读懂后文每条缺陷的前提。把三套混为一谈,会得出"门禁很多所以很安全"
或"有 fail-open 所以门禁全废"两种同样错误的结论。

---

## 1. 三套门禁机制

| | 机制 | 实现方式 | 默认行为 | 结论 |
|---|---|---|---|---|
| **M1** | 工具入口硬拒绝 | `_blocked(code, msg)` 直接返回拒绝信封 | fail-closed,构造上不可绕 | 健壮 |
| **M2** | 专用门禁 | `release_preflight.py` 四道门;`formal_promotion.py` 四十个 `_fail()` | fail-closed,自带 `release_ok = all(pass)` | 健壮 |
| **M3** | 交付质量分类器 | `quality_severity.py` 注册表,经 `split_quality_codes` / `is_blocking` | **fail-open**:未登记码默认放行 | **缺陷集中于此** |

### 1.1 M1 — 工具入口硬拒绝

全树扫到 346 处码级字面量,**绝大多数属于 M1**:`delivery_run_not_found`、
`export_format_invalid`、`disposition_invalid`、`idempotency_conflict` 等。

这些码经 `_blocked()` 直接构造拒绝信封返回,**不查 M3 分类器**,因此它们
不需要、也不应该登记进 `BLOCKING_CODES`。

> **审查陷阱(务必记住)**:用 grep 统计"哪些码没登记进分类器"会把 M1 的码全部
> 算成 fail-open,得出几百条假阳性。判断某个码是否需要登记,唯一正确的方法是
> 看它**是否流入 `split_quality_codes()` / `is_blocking()`**。本次审查第一版
> 普查就犯了这个错(346 条里 327 条"疑似漏登记"),收窄后真实数字是 56 条流经、
> 35 条落默认放行。

### 1.2 M2 — 专用门禁

**`runtime/release_preflight.py`** 四道门,各自独立评级后取合:

| 门 | 发出的码 |
|---|---|
| calculation | 计算失败类 |
| artifact | `artifact_gate_incomplete`、`formal_artifacts_not_configured` |
| evidence | `sim_a_not_formal`、`hash_only_evidence`、`formal_evidence_incomplete`、`p0_not_fully_formalized`、`evidence_distribution_missing` |
| release | `build_metadata_incomplete`、`stale_build_metadata`、`<gate>_skipped` |

这些码同样没登记进 M3 分类器,但该模块自己算
`release_ok = all(gate.status == "pass")`,独立 fail-closed。**这是正确设计**,
不要"顺手"把它们补登记进 M3。

**`runtime/formal_promotion.py`** 四十个硬 `_fail()`,覆盖签名谱系、内容哈希、
父对象、工作区归属。完整码族:

- `formal_lineage_*`(12 条):basis hash / content hash / identity / metadata /
  mixed_promotions / object_invalid / object_not_found / parent_required /
  policy_required / unsigned_history / workspace_mismatch
- `formal_source_*`(5 条)、`formal_finance_run_*`(8 条)、
  `formal_research_*`(5 条)、`formal_tables_package_binding_mismatch`
- `formal_promotion_*`(3 条)、`template_pack_*`(6 条)

**`runtime/evidence_qualification.py`** 是证据认证唯一入口,26 个模块消费,
公开面仅 4 个函数(`evidence_payload`、`declared_evidence_policy`、
`combine_evidence_policies`、`project_fact_may_be_certified`)。收敛良好。

### 1.3 M3 — 交付质量分类器

`runtime/quality_severity.py`。登记 **24 个全码 + 32 个前缀**,由 **10 个模块**消费:

```
domains/finance/_tables_service/base.py          aggregate_quality_status, classify_quality
domains/finance/_tables_service/render.py        classify_quality
domains/project_planning/_service/factories.py   split_quality_codes
runtime/outcomes.py                              aggregate_quality_status
servers/lvke_feasibility_delivery/service.py     三个都用
servers/lvke_project_planning/_lifecycle/build_scale.py   is_blocking
servers/lvke_project_planning/_lifecycle/cost.py          is_blocking, split_quality_codes
servers/lvke_zero_material_delivery/_service/acceptance.py     split_quality_codes
servers/lvke_zero_material_delivery/_service/lifecycle.py      split_quality_codes
servers/lvke_zero_material_delivery/_service/orchestration.py  split_quality_codes
```

这 10 个模块共发出 **56 个码**流经分类器:**21 判阻断,35 落默认放行**。

`is_blocking()` 的判定顺序是 `NON_BLOCKING_BY_DESIGN` → `BLOCKING_CODES`(全码)
→ `BLOCKING_PREFIXES`(前缀)→ **默认 False**。最后那个默认值是全部 fail-open
缺陷的根源:**新增一个诊断码而忘记登记,它就静默降级成"仅标注"**。

这个默认值本身是**刻意**的(把默认设成阻断会让任何新码意外掐断整条链),
所以修复方向不是改默认值,而是保证注册表与发码点同步。

---

## 2. 零材料交付域的分层门禁

这一域门禁密度最高也最完整,单独成节。路径省略前缀
`src/lvke_mcp/servers/lvke_zero_material_delivery/`。

| 层 | 门数 | 代表门禁 | 真阻断? |
|---|---|---|---|
| 起链与路由 | 9 | 行业路由歧义/未命中、报告配置不可唯一确定、配置已停用、profile 与 template_set 冲突、章节树结构、required_fields 未被消费、字段缺元数据、论证链词组、快照 hash 复算 | 全部真阻断 |
| 假设包与追问 | 6 | 未知确认字段、未知跳过字段、lineage 缺失、自动重算未成链 | 前 4 真阻断,关键字段未答为标注(经 F 层转正式阻断) |
| 生命周期状态 | 8 | 已取消不得 start、行业未解析不得建假设、对象不存在、cancel/resume 状态机、幂等冲突、跨工作区读取 | 全部真阻断 |
| 交付产物完整性 | 3 | 无 FinanceRun 不产报告、配置解析错误早退、必需组件登记 | 见缺陷 P1-4 / P1-5 |
| 技术验收 | 12 | 必需组件缺失、配置 hash 缺失、谱系断裂、manifest 缺失、勾稽不通、审查未跑起来、verdict 非 pass 根因分流 | 多数真阻断 |
| 内部七域与正式资格 | 9 | 技术未过则内部不可过、人工确认判据、维度 failed/incomplete、关键字段未答阻断正式资格、两段验收均须通过 | 见缺陷 P0 |
| 模板包生成 | 8 | 未绑假设包、答案快照乐观并发、假设未确认、配置覆盖冲突、标准需求解析、候选 spec 结构、披露注入不可绕 | 全部真阻断 |
| 晋升 | 10 | 责任声明必填、TemplatePack 完整性、配置身份一致、两段验收重读、预演一致性、晋升后谱系复验 | 全部真阻断 |

### 2.1 已核验成立的关键约束

| 约束 | 结论 |
|---|---|
| 拟定模板包永不自动获得正式资格,两段验收从 `pending` 起步 | **成立** |
| 晋升不得原地升级 `zmr_*`,必须产生新对象 | **成立**(`promotion.py` 全函数无 `RUN_STORE.put`) |
| 晋升后保留 `sim_a_template` 模拟来源 | **对象/文件层成立**,run 读路径不体现(P1-6) |
| 晋升需 `responsible_party` 与确认声明 | **成立** |
| `role_confirmed` 不得单独当人工确认 | **成立**,判据是 `confirmation_id` 非空 |
| `query_success` 不被读成交付状态 | **成立** |
| 豁免必须有期限/范围/失效条件/补偿控制 | **成立**(`disposition.py:325-334` 四项强制) |
| 每工件 `usable`/`release_grade` 与真实状态一致 | **不成立**(P1-3) |
| 两段验收折叠不 fail-open | **在审查失效一路上 fail-open**(P0) |

<!-- SECTION_DEFECTS -->
