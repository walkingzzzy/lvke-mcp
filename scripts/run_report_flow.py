"""执行 report_prepare → report_start → report_validate 流程，生成负面可研草稿。

注意：只传 finance_binding 对象，不传 run_id/finance_tables_package_id 顶层字段。
"""
import sys
import json

sys.path.insert(0, "/Users/mac/Desktop/mcp_servers/src")

WORKSPACE_ID = "xianan-low-altitude-agri-tourism-2026"

from lvke_mcp.domains.reports import application as report_service

# ── 1. report_prepare ────────────────────────────────────────────────
# 绑定 EvidencePack、ResearchPackage、FinanceRun、十三表包
# 注意：只传 finance_binding，不传 run_id/finance_tables_package_id 顶层字段
print("=== report_prepare ===")
result = report_service.prepare({
    "workspace_id": WORKSPACE_ID,
    "evidence_pack_ids": [
        "evp_45173b511fca72bd2a102e95",
        "evp_9d02edb7a35df38ed6b99fd5",
    ],
    "research_package_ids": [
        "drp_2e6289e4bd28efae82e0ad35",
    ],
    "finance_binding": {
        "kind": "generic_feasibility",
        "run_id": "run_1b6c69bdb02e",
        "package_id": "ftp_ef902927baf1698f445484ee",
    },
    # 不再传 run_id 和 finance_tables_package_id 顶层字段
    "evidence_policy": "real",
    "project_metadata": {
        "project_type": "low_altitude_economy_agriculture_tourism",
        "industry": "agriculture_tourism",
        "valuation_date": "2026-08-16",
        "currency": "CNY",
        "amount_unit": "万元",
        "tax_basis": "standard",
        "forecast_period": 10,
    },
    "unresolved_inputs": [
        "项目红线图及权属文件",
        "三类用地性质与规划条件",
        "设施农业用地备案或论证",
        "工业厂房用地与建设条件",
        "空域使用及飞行活动审批进度",
        "载人飞行起降设施审查条件",
        "低空合作方资质与合作协议",
        "设备报价",
        "温室厂房加工线报价",
        "专项资金文件",
    ],
    "release_limitations": [
        "研究包为 partial 状态，未经独立质量审计",
        "项目土地、空域、合作等关键证据尚未取得",
        "财务数字来自 FinanceRun，结论为不可行",
    ],
})
print(json.dumps(result, indent=2, ensure_ascii=False))

if not result.get("draft_ready"):
    print("\nWARN: report_prepare 未就绪，但 preparation 已创建", file=sys.stderr)
    # 准备仍然创建了，可以继续尝试 report_start

PREPARATION_ID = result.get("report_preparation_id")
print(f"\nPreparation ID: {PREPARATION_ID}")
print(f"draft_ready: {result.get('draft_ready')}")
print(f"formal_ready: {result.get('formal_ready')}")

if not PREPARATION_ID:
    print("FATAL: 无 preparation_id", file=sys.stderr)
    sys.exit(1)

# ── 2. report_start ──────────────────────────────────────────────────
# 创建不可变草稿修订，包含完整的负面可研正文
print("\n=== report_start ===")

# 负面可研正文（九章完整）
report_content = """# 咸安区低空经济+农业+文旅融合项目可行性研究报告

## 第一章 项目概况

### 1.1 项目名称
咸安区低空经济+农业+文旅融合项目。

### 1.2 建设单位
咸安区人民政府（拟）。

### 1.3 建设地点
咸宁北高速出口附近区域。

### 1.4 建设内容与规模
本项目拟建设集蓝莓草莓种植、果汁加工、低空旅游、研学培训为一体的农文旅融合示范区。主要建设内容包括：
- 蓝莓草莓种植基地（约200亩）
- 果汁加工厂房及生产线
- 低空飞行体验及培训设施
- 研学基地及相关配套设施

### 1.5 投资估算与资金筹措
项目总投资约4.8亿元，资金来源包括企业自筹、银行贷款及政府专项资金。

### 1.6 建设期
建设期2年，运营期10年。

## 第二章 项目背景与建设必要性

### 2.1 项目背景
咸安区位于湖北省咸宁市，地处长江中游城市群，具备发展低空经济、现代农业和文旅融合的区位条件。近年来，国家大力推动低空经济发展，湖北省出台多项低空经济支持政策，咸宁市将农业与旅游融合作为重点发展方向。

### 2.2 建设必要性
本项目的建设有利于推动咸安区农业产业结构升级，促进低空经济新业态发展，打造农文旅融合示范区，带动区域经济增长和就业。

### 2.3 可行性研究范围
本报告重点对项目的市场前景、建设方案、投资估算、财务效益及风险进行系统分析，为投资决策提供依据。

## 第三章 需求分析与建设规模

### 3.1 市场需求
低空旅游、研学培训、蓝莓草莓精深加工产品具有较大的市场潜力，但本项目的具体客流量、产品定价、市场份额等关键数据缺乏可靠来源支撑。

### 3.2 建设规模论证
项目建设规模的确定依据不足，缺乏同类项目的可对比数据。种植面积、加工产能、飞行设施规模等关键参数未取得权威依据。

## 第四章 建设方案

### 4.1 总体方案
项目规划建设种植基地、加工厂房、低空飞行设施和研学基地四大功能区。

### 4.2 技术方案
蓝莓草莓种植拟采用设施栽培技术，果汁加工采用标准化生产线，低空飞行涉及无人机及轻型飞行器运营。

### 4.3 建设条件
项目用地性质、规划条件、空域审批等关键建设条件尚未落实，存在较大的实施不确定性。

## 第五章 投资估算与资金筹措

### 5.1 投资估算
根据财务模型测算，项目总投资约4.8亿元，其中：
- 工程建设费用约3.2亿元
- 设备购置及安装约1.0亿元
- 其他费用约0.6亿元

### 5.2 资金筹措
资金来源包括企业自筹30%、银行贷款60%、政府专项资金10%。但政府专项资金的具体来源和额度尚未确认。

## 第六章 财务分析

### 6.1 财务评价基础数据
项目计算期12年（建设期2年，运营期10年），基准收益率8%。

### 6.2 财务评价指标
根据财务模型计算，本项目主要财务指标如下：
- 项目投资财务内部收益率（IRR）：-32.09%
- 财务净现值（NPV）：-28,903.9 万元
- 经营现金流量：-1,508.86 万元/年
- 投资回收期：无法回收（NPV为负且IRR远低于基准收益率）

### 6.3 偿债能力分析
利息备付率（ICR）<0.8，项目不具备偿债能力。

### 6.4 财务评价结论
项目财务内部收益率-32.09%，远低于行业基准收益率8%，财务净现值为-28,903.9万元，经济上不可行。

## 第七章 风险分析

### 7.1 政策风险
低空经济政策尚处于发展初期，政策变化可能影响项目运营。

### 7.2 市场风险
客流量、产品价格、市场份额等假设缺乏可靠来源，市场风险较高。

### 7.3 技术风险
蓝莓草莓种植技术适应性、果汁加工工艺、低空飞行技术等存在技术不确定性。

### 7.4 财务风险
项目IRR为-32.09%，财务效益极差，投资回收期无法确定，财务风险极高。

### 7.5 实施风险
项目用地、规划、空域审批等关键前置条件尚未落实，实施风险大。

### 7.6 运营风险
项目运营团队、管理经验、市场拓展能力等尚未验证，运营风险较高。

### 7.7 社会环境风险
项目对周边环境、居民生活的影响有待评估。

## 第八章 保障措施

### 8.1 政策保障
积极争取地方政府低空经济、农业产业化、乡村振兴等政策支持。

### 8.2 资金保障
多渠道筹措资金，争取政府专项资金和金融机构支持。

### 8.3 技术保障
引进专业种植、加工和低空运营技术团队。

### 8.4 管理保障
建立专业化项目管理团队，完善运营管理制度。

## 第九章 结论与建议

### 9.1 结论
经综合论证，本项目在当前方案条件下：
- 财务内部收益率（IRR）为-32.09%，远低于8%的基准收益率
- 财务净现值（NPV）为-28,903.9万元
- 项目经济上不可行，不具备投资价值

### 9.2 建议
基于上述分析，建议：
1. 按当前方案本项目建设不具备经济可行性，不建议直接实施。
2. 如需继续推进，须重新审视项目收入模式、成本结构和融资方案，大幅度提升项目盈利能力。
3. 补充完善项目用地、空域审批、合作协议等关键证据，取得可靠的设备报价和专项资金文件后重新进行财务测算。
4. 建议考虑调整项目规模、优化投资结构、引入战略合作伙伴等方式改善项目财务指标。

### 9.3 总体评价
本项目按当前方案财务不可行，不建议直接实施，需重构收入、成本、融资及证据后重算。
"""

result2 = report_service.start({
    "workspace_id": WORKSPACE_ID,
    "report_preparation_id": PREPARATION_ID,
    "document_snapshot": {
        "content": report_content,
        "report_type": "feasibility_study",
    },
})
print(json.dumps(result2, indent=2, ensure_ascii=False))

if not result2.get("success"):
    print("\nFATAL: report_start failed", file=sys.stderr)
    sys.exit(1)

REVISION_ID = result2["report_revision_id"]
print(f"\nRevision ID: {REVISION_ID}")

# ── 3. report_validate ───────────────────────────────────────────────
print("\n=== report_validate ===")
result3 = report_service.validate(WORKSPACE_ID, REVISION_ID)
print(json.dumps(result3, indent=2, ensure_ascii=False))

print("\n=== 完成 ===")
print(f"Preparation ID: {PREPARATION_ID}")
print(f"Revision ID: {REVISION_ID}")
print(f"Validate success: {result3.get('success')}")