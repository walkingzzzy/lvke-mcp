---
name: report-drafting-site-and-factors
description: 撰写政府或企业可研报告的选址选线、建设条件与土地资源环境等要素保障章节。
category: report-drafting
applicable_report_types: [gov9, ent9]
applicable_chapter_themes: [site-and-factors]
required_evidence: [archive, policy, map_geo]
output_contract: draft
review_level: drafting
expert_review_status: pending_review
---

# 项目选址与要素保障 章节撰写规范（gov9/ent9 第三章）

> 对应发改委 2023 大纲"项目选址与要素保障"。本 skill 补齐原 report-drafting 缺失的选址/要素保障主题（PT-2）。

## 必含要素
1. **项目选址/选线**：多方案比选，明确推荐场址/线路及理由；说明土地权属、供地方式、土地利用现状、
   矿产压覆、占用耕地及永久基本农田情况、是否涉及生态保护红线、地质灾害危险性评估。
2. **项目建设条件**：自然环境（地形地貌、气象、水文、地质、地震、防洪）；交通运输（铁路/公路/港口/机场）；
   公用工程（水、电、气、热、消防、通信）；施工条件与生活配套。改扩建须分析现有设施能力。
3. **要素保障分析**：土地要素（国土空间规划符合性、用地指标、节约集约用地论证）；资源环境要素
   （水资源、能源、大气环境、生态承载力，取水/能耗/碳排放/污染减排控制要求）；重大项目列示要素保障指标。

## 常见缺陷（须规避）
- 只写"选址合理"而无方案比选与权属/红线核查。
- 建设条件泛泛而谈，缺当地真实自然/交通/公用工程数据。
- 要素保障不落到指标（用地/用水/能耗/环境承载）。

## 配套工具
- `mcp_lvke_archive_find_similar_projects` / `get_chapter`：取同区域同行业标杆的选址与建设条件写法。
- `mcp_lvke_map_geo_*`：区位与距离测算。
- `mcp_lvke_policy_search_*`：国土空间规划、耕地保护、生态红线相关政策核验。
