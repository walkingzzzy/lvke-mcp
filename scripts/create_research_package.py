"""创建 ResearchPackage 并固化到工作区。

流程: dr_start -> dr_submit -> dr_get_bundle
"""
import sys
import json

sys.path.insert(0, "/Users/mac/Desktop/mcp_servers/src")

WORKSPACE_ID = "xianan-low-altitude-agri-tourism-2026"

from lvke_mcp.domains.research import application as dr

# ── 1. dr_start ──────────────────────────────────────────────────────
result = dr.start_agent({
    "workspace_id": WORKSPACE_ID,
    "topic": "咸安区低空经济+农业+文旅融合项目可行性研究 — 证据缺口与市场依据分析",
    "industry": "agriculture_tourism",
    "region": "xianning_hubei",
    "profile": "quick",
    "subqueries": [
        "项目用地、空域审批、合作协议等关键证据缺口",
        "蓝莓草莓种植、低空旅游、果汁加工的收入假设依据",
        "设备报价、建设成本、专项资金的来源可查性",
    ],
    "idempotency_key": "xianan-research-package-v1",
})
print("=== dr_start ===")
print(json.dumps(result, indent=2, ensure_ascii=False))

if not result.get("success"):
    print("FATAL: dr_start failed", file=sys.stderr)
    sys.exit(1)

TASK_ID = result["task_id"]
print(f"\nTask ID: {TASK_ID}")

# ── 2. dr_submit ─────────────────────────────────────────────────────
report_md = """# 咸安区低空经济+农业+文旅融合项目 — 研究依据与证据缺口分析

## 一、项目概况
咸安区计划在咸宁北高速出口附近建设低空经济+农业+文旅融合项目，包含蓝莓草莓种植、果汁加工、低空旅游、研学培训等业态。

## 二、证据基础
本项目证据主要来自两个 EvidencePack：

### EvidencePack 1 (evp_45173b511fca72bd2a102e95)
- 来源：13 个 Web 快照（含政府网站、行业门户、第三方数据平台）
- 证据轨迹：real（实际数据）
- 项目事实认证：false（未达到认证标准）
- 主要缺口：项目红线图、三种用地性质与规划条件、设施农业用地备案、空域审批、合作协议、设备报价、专项资金文件均"无事实候选"

### EvidencePack 2 (evp_9d02edb7a35df38ed6b99fd5)
- 来源：13 个 Web 快照
- 证据轨迹：real
- 项目事实认证：false
- 主要缺口：湖北低空经济政策目标、咸宁农业与旅游规划方向、本地蓝莓栽培案例、用地政策、无人机合规规则、生产许可分类等均无事实候选

## 三、核心证据缺口
1. **土地与规划**：无红线图、无规划条件、无设施农业用地备案
2. **空域审批**：无飞行活动审批进度、无起降设施审查条件
3. **合作协议**：无低空合作方资质与协议、无品牌授权
4. **成本报价**：无设备报价、无温室厂房报价、无游乐设施报价
5. **收入假设**：蓝莓草莓产量、票价、客流量等假设缺乏可查来源
6. **资金来源**：专项资金/补贴文件未获取

## 四、财务可行性结论
基于 FinanceSpec (fsp_5d3500f3af6a22ac713752de) 和 FinanceRun (run_1b6c69bdb02e) 的计算结果：
- IRR: -32.09%（远低于基准收益率 8%）
- NPV: -28,903.9 万元
- 经营现金流: -1,508.86 万元/年
- 经济不可行（ICR<0.8），viability_status=infeasible

## 五、建议
在当前证据条件下，项目财务不可行，不建议直接实施。需重构收入、成本、融资方案并补充关键证据后重算。
"""

citations = [
    {
        "source_id": "evp_45173b511fca72bd2a102e95",
        "locator": "证据包 #1 — 经济与市场数据（13个Web快照）",
        "resource_uri": "lvke://data-analysis/workspaces/xianan-low-altitude-agri-tourism-2026/evidence-packs/evp_45173b511fca72bd2a102e95",
        "content_hash": "sha256:45173b511fca72bd2a102e959faa91d83580232a6a3e95d4c304bee9be69cdee",
        "evidence_policy": "real",
        "source_type": "evidence_pack",
        "title": "经济与市场数据证据包",
    },
    {
        "source_id": "evp_9d02edb7a35df38ed6b99fd5",
        "locator": "证据包 #2 — 政策与产业数据（13个Web快照）",
        "resource_uri": "lvke://data-analysis/workspaces/xianan-low-altitude-agri-tourism-2026/evidence-packs/evp_9d02edb7a35df38ed6b99fd5",
        "content_hash": "sha256:9d02edb7a35df38ed6b99fd58693bbf954c53e96da75a4150ef57727ce7d5ea1",
        "evidence_policy": "real",
        "source_type": "evidence_pack",
        "title": "政策与产业数据证据包",
    },
]

result2 = dr.submit_agent({
    "workspace_id": WORKSPACE_ID,
    "task_id": TASK_ID,
    "report_md": report_md,
    "citations": citations,
    "evidence_pack_ids": [
        "evp_45173b511fca72bd2a102e95",
        "evp_9d02edb7a35df38ed6b99fd5",
    ],
    "quality_summary": {
        "query_rounds": 0,
        "usable_source_count": 2,
        "citation_coverage": 1.0,
        "missing_fields": [
            "项目红线图及权属文件",
            "三类用地性质与规划条件",
            "设施农业用地备案或论证",
            "工业厂房用地与建设条件",
            "空域使用及飞行活动审批进度",
            "载人飞行起降设施审查条件",
            "低空合作方资质与合作协议",
            "设备报价",
            "温室厂房加工线报价",
        ],
        "conflicts": [],
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
        "正文由 Agent 基于现有证据包撰写，未经独立深度研究质量审计",
        "项目土地、空域、合作等关键证据尚未取得",
        "财务数字来自 FinanceRun，本研究不重新计算",
    ],
})
print("\n=== dr_submit ===")
print(json.dumps(result2, indent=2, ensure_ascii=False))

if not result2.get("success"):
    print("FATAL: dr_submit failed", file=sys.stderr)
    sys.exit(1)

RESEARCH_PACKAGE_ID = result2["research_package_id"]
print(f"\nResearch Package ID: {RESEARCH_PACKAGE_ID}")

# ── 3. dr_get_bundle ────────────────────────────────────────────────
result3 = dr.bundle(WORKSPACE_ID, TASK_ID)
print("\n=== dr_get_bundle ===")
print(json.dumps(result3, indent=2, ensure_ascii=False))

print("\n=== 完成 ===")
print(f"TASK_ID: {TASK_ID}")
print(f"RESEARCH_PACKAGE_ID: {RESEARCH_PACKAGE_ID}")