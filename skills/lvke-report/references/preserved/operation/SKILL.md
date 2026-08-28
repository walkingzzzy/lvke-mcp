---
name: report-drafting-operation
description: 撰写政府或企业可研报告的运营模式、运营组织、生产经营、安全保障与绩效管理章节。
category: report-drafting
applicable_report_types: [gov9, ent9]
applicable_chapter_themes: [operation]
required_evidence: [archive]
output_contract: draft
review_level: drafting
expert_review_status: pending_review
---

# 项目运营方案 章节撰写规范（gov9/ent9 第五章）

> 对应发改委 2023 大纲"项目运营方案"。本 skill 补齐原 report-drafting 缺失的运营主题（PT-2）。

## 必含要素
1. **运营模式选择**：自主运营 vs 委托第三方运营，说明主要理由；委托的须提出对第三方能力要求。
2. **运营组织方案**：组织机构设置、人力资源配置、员工培训需求与计划；合规管理与治理体系。
3. **生产经营方案（企业/生产类）**：产品质量安全保障、原材料供应、燃料动力供应、维护维修方案；
   或运营服务类的服务内容、标准、流程、计量与运维要求。
4. **安全保障方案**：危险因素辨识、安全生产责任制、安全管理体系、劳动安全卫生、数据/网络/供应链安全、
   安全应急管理预案。
5. **绩效管理方案（政府类侧重）**：全生命周期关键绩效指标与绩效管理机制。

## 常见缺陷（须规避）
- 把"运营方案"写成"建设方案"的重复，缺运营期组织/人力/安全的具体安排。
- 无运营模式选择论证，直接假定自营。
- 安全保障停留口号，缺责任制与应急预案。

## 配套工具
- `archive_extract_structure(report_id=..., with_appendix=True)`：取同类项目结构，
  从返回章节清单里定位运营方案章，参考其组织/安全/绩效写法。
