# 一句话十三表与报告交付合同

本合同用于“基于某项目现有资料，生成技术估算版十三表及报告”类一句话任务。Codex 负责意图解释与编排，MCP 负责资料固化、计算、版本、导出和门禁。不新增总控 MCP。

## DeliveryIntent

用户指令必须显式包含项目名称。Codex 必须按 config/delivery_intent.schema.json 生成严格对象，不得添加额外字段：

    {
      "contract_version": "delivery-intent.v1",
      "project_name": "恒立酒店资产收购项目",
      "delivery_mode": "estimate_preview",
      "report_type": "investment_decision",
      "finance_route": "asset_acquisition",
      "web_policy": "policy_market_required",
      "interaction_policy": "single_consolidated_clarification",
      "language": "zh-CN",
      "deliverables": [
        "thirteen_tables_xlsx",
        "thirteen_tables_csv",
        "report_docx",
        "evidence_manifest",
        "assumption_register",
        "gap_register",
        "run_manifest"
      ]
    }

默认只生成 estimate_preview。用户未明确给出项目名称时必须停止，不得用“当前项目”或聊天历史猜测。

## 资料解析与路由

1. 建立全新 workspace 后，先调用 source_external_corpus_resolve(workspace_id, project_name)。它必须返回 success=true、唯一 finance_route 和非空 corpora/import_roots。
2. 资料清单固定为 config/external_corpora.v1.json，运行时由 LVKE_EXTERNAL_CORPUS_ROOT 解析。不得把整个仓库作为导入白名单。
3. 恒立路线为 asset_acquisition + investment_decision；黄鹰岩路线为 generic_feasibility + feasibility_study。
4. 优先导入 MD、JSON、JSONL、TXT、HTML 结构化文本。只有当结构化文本不足以定位项目事实时，才导入原 PDF、DOCX、XLSX；不重复精细 OCR。
5. 本地项目资料才能确定项目主体、资产、合同、许可、交易和经营事实。互联网资料只补充政策效力、市场和行业基准，不得推断项目专属事实。

source_external_corpus_resolve 返回未登记项目、缺少 marker、路径越界或多路线匹配时必须 fail closed。

## 一次性澄清

先完成资料目录、已有事实、单位、期间、路线和证据资格分析，再把所有会改变路线或使计算失真的问题合并为一次询问。仅以下情况允许询问：

- 资产收购与新建路线同时匹配。
- 金额单位、基准日、建设期或运营期存在无法裁决的关键冲突。
- 交易边界、建设范围或经营模式的选择会改变主模型。

其他缺失项建立 controlled_assumption，不再逐项追问。每项假设必须记录 field、value、unit、basis、impact、sensitivity、release_condition，且只能进入 estimate_preview。

## 强制执行顺序

1. 预检 build commit、工具清单、Skill 同步、资料根和 data_provider_status。
2. 建立全新 workspace 和 ProjectContext，禁止复用历史对象。
3. Tavily 与 data_discover 同轮并行，至少使用政策、市场、成本三个 query。DDGS 是 data_discover 的最低无密钥通道；任一通道不可用时标记 partial/upstream_failure，不得声称完成强制研究。
4. 将入选 URL 正文固化为 SourceSnapshot，摘要只作检索线索。
5. 建立 EvidencePack 和 ResearchPackage，再进入规划与财务链。
6. 通用路线执行 Planning Objects 到 FinanceRun；收购路线执行 acquisition spec/run/tables。
   收购路线允许将显式 `delivery_mode=estimate_preview` 且受控假设字段完整的候选 Spec
   确认为 `confirmation_scope=estimate_preview` 后运行；该确认不产生正式证据资格，run 必须
   保留 `formal_spec_valid=false` 及全部正式 blocker。
7. 十三表只消费同一 run_id，报告必须绑定同一 run_id/package_id。
8. 恒立技术稿通过 report_prepare.finance_binding.kind=asset_acquisition 生成，不调用需要 approved run 的正式收购制品接口。
9. 报告正文必须经过 propose -> diff -> apply -> validate -> readiness。
10. 技术稿可导出，但 professional review 与 formal release 必须继续阻断。

## 交付包

每次运行建立独立目录，固定包含：

    <project_slug>/<run_id>/
      十三表.xlsx
      csv/01_....csv ... csv/13_....csv
      技术估算报告.docx
      evidence_manifest.json
      assumption_register.json
      gap_register.json
      run_manifest.json

run_manifest.json 至少记录 generated_at、commit、build_commits、services、tools、workspace_id、object_ids、traces、artifacts、coverage。artifacts 每项记录相对路径、MIME、SHA-256、字节数、run_id/package_id 和 lineage。coverage 只允许 PASS、EXPECTED_REJECTION、UPSTREAM_FAILURE、SKIPPED。

实物门禁：XLSX 恰好 13 张正式附表且无控制页；13 个 CSV 为 UTF-8 BOM；DOCX 可打开、中文可见、包含“技术估算版，非正式发布”；三类产物的财务数字、hash 与 lineage 必须同源。
