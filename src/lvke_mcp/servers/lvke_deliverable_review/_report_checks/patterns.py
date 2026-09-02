"""规则注册表与静态模式表：数字、期间、公司、指标、路径与章节分组。"""

from __future__ import annotations

import re



REPORT_RULES = {
    "REPORT.SECTIONS.COMPLETE",
    "REPORT.CLAIM.EVIDENCE",
    "REPORT.NUMBERS.BOUND",
    "REPORT.INTERNAL.CONSISTENCY",
    "REPORT.REFERENCES.FRESH",
}


COMBINED_RULES = {"COMBINED.NUMBERS.MATCH", "COMBINED.CONCLUSIONS.MATCH"}


_NUMBER_PATTERN = re.compile(
    r"(?P<number>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(?:\+|以上|余|多)?\s*"
    r"(?P<unit>亿元|万元|平方米|平方|㎡|万吨/年|万吨|吨/年|公里|千米|km|KM|个月|%|％|元|间|吨|倍|年|月)"
)


_UNITLESS_RATIO_PATTERN = re.compile(
    r"(?:"
    r"(?P<label_before>偿债备付率|利息备付率|DSCR|ICR)\s*"
    r"(?:为|达到|不低于|最低为|至少)?\s*(?P<number_before>-?\d+(?:\.\d+)?)"
    r"|"
    r"(?P<number_after>-?\d+(?:\.\d+)?)\s*(?:的)?\s*"
    r"(?:最低|最小|目标|要求)?\s*(?P<label_after>偿债备付率|利息备付率|DSCR|ICR)"
    r")",
    re.I,
)


_PERIOD_PATTERN = re.compile(
    r"(?:20\d{2}年(?:\d{1,2}月)?|第\d+(?:期|年)|(?<![\d.])\d+年|建设期|运营期|达产年)"
)


_COMPANY_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,50}(?:有限责任公司|股份有限公司|有限公司|酒店管理公司|酒店|中心))")


_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 裸「内部收益率」「税后/全部投资内部收益率」都是国标可研的常见写法，
    # 此前一条都不认：IRR 数值因此既可能漏检（无 metric），也可能被同句的
    # 「基准收益率」(discount_rate) 以后置回退姿态吃掉 → 一个缺口同时制造
    # 漏检与假阳性。前缀 (?<!资本金) 保证不抢 capital_irr 的值。
    ("project_irr", re.compile(
        r"(?<!资本金)(?:项目|全部|所得税前|所得税后|税前|税后)?(?:投资)?(?:财务)?内部收益率"
        r"|项目\s*IRR|(?<![A-Za-z])IRR(?![A-Za-z])", re.I)),
    ("capital_irr", re.compile(r"资本金(?:财务)?内部收益率|资本金\s*IRR", re.I)),
    ("npv", re.compile(r"财务净现值|净现值|(?<![A-Za-z])NPV(?![A-Za-z])", re.I)),
    ("dscr", re.compile(r"偿债备付率|\bDSCR\b", re.I)),
    ("icr", re.compile(r"利息备付率|\bICR\b", re.I)),
    ("dynamic_payback", re.compile(r"动态(?:投资)?回收期", re.I)),
    ("static_payback", re.compile(r"静态(?:投资)?回收期", re.I)),
    ("payback", re.compile(r"投资回收期|回收期", re.I)),
    ("discount_rate", re.compile(r"折现率|基准收益率|目标收益率|要求收益率|收益率门槛", re.I)),
    ("bep", re.compile(r"盈亏平衡点|\bBEP\b", re.I)),
    ("purchase_price", re.compile(r"purchase_price|收购价|购买价|成交价", re.I)),
    ("valuation_value", re.compile(r"valuation_value|评估值|估值", re.I)),
    ("total_investment", re.compile(r"total_investment|项目总投资|总投资", re.I)),
    ("construction_investment", re.compile(r"建设投资|三段合计", re.I)),
    ("construction_interest", re.compile(r"建设期(?:贷款)?利息", re.I)),
    ("working_capital", re.compile(r"流动资金|营运资金", re.I)),
    ("capital", re.compile(r"项目资本金|资本金|企业自筹|自有资金|自筹资金", re.I)),
    ("debt", re.compile(r"银行贷款|贷款金额|借款金额|债务融资|借款", re.I)),
    ("revenue", re.compile(r"营业收入|销售收入|年收入|营收", re.I)),
    ("revenue_component", re.compile(r"租赁及广告收入|租赁广告收入|其他收入", re.I)),
    ("operating_cost", re.compile(r"现金经营成本|现金运营成本|经营成本|运营成本|营业成本", re.I)),
    ("total_cost", re.compile(r"总成本费用|总成本", re.I)),
    ("net_profit", re.compile(r"净利润", re.I)),
    ("income_tax", re.compile(r"企业所得税|所得税", re.I)),
    ("depreciation", re.compile(r"年折旧|折旧(?:费|额)?", re.I)),
    ("profit", re.compile(r"利润总额|利润", re.I)),
    ("annual_use", re.compile(r"年使用|年度使用|年投入|年度投入|年度资金使用", re.I)),
    # 正文常写"工程费用 54,000.00 万元"而不带"合计"二字；不补这一写法时它会
    # 落到更宽的 construction_investment（建设投资），把明细当成合计。
    # "工程费用"必须排除"建筑工程费/安装工程费"这类以它结尾的分项名，否则
    # engineering_cost 会抢走 civil_cost / installation_cost 的数值。
    ("engineering_cost", re.compile(r"(?<![建筑土安装])工程费用合计|(?<![建筑土安装])工程费合计|(?<![建筑土安装])工程费用|(?<![建筑土安装])工程费(?!用)", re.I)),
    ("civil_cost", re.compile(r"建筑工程费|土建工程费", re.I)),
    ("equipment_cost", re.compile(r"设备及工器具购置费|设备购置费|设备费", re.I)),
    ("installation_cost", re.compile(r"安装工程费|安装费", re.I)),
    ("other_investment_cost", re.compile(r"工程建设其他费", re.I)),
    ("contingency", re.compile(r"基本预备费|预备费", re.I)),
    ("wage_cost", re.compile(r"工资及福利|工资福利|工资及附加", re.I)),
    ("average_wage", re.compile(r"人均年工资|人均工资", re.I)),
    # 成本明细里常直接写"工资 1,050.00 万元"、"福利 315.00 万元"。
    ("salary_cost", re.compile(r"工资约|工资额|基本工资|工资", re.I)),
    # 「福利费」是「工资及福利费」的后缀，天然比 wage_cost 的「工资及福利」
    # 离数字更近，会抢走工资福利合计的数值（同 engineering_cost 靠
    # (?<![建筑土安装]) 解同类问题）。反向后顾让合计归 wage_cost。
    ("welfare_cost", re.compile(r"(?<!工资及)(?<!工资)(?:福利约|福利费|福利额|福利)", re.I)),
    ("maintenance_cost", re.compile(r"设备维护|维修费|维护费|修理与维护|修理费|修理", re.I)),
    ("raw_material_cost", re.compile(r"主要原材料|原材料|原料", re.I)),
    ("utility_cost", re.compile(r"水电能源|能源费|水电费", re.I)),
    ("insurance_cost", re.compile(r"保险费|保险", re.I)),
    ("marketing_cost", re.compile(r"营销费|营销", re.I)),
    ("lease_cost", re.compile(r"场地使用及租赁|场地租赁|租赁费", re.I)),
    ("management_cost", re.compile(r"管理费用|管理费", re.I)),
    ("ticket_price", re.compile(r"平均门票|门票价格|门票", re.I)),
    ("secondary_spend", re.compile(r"二次消费|人均二消", re.I)),
    ("room_count", re.compile(r"客房|房间", re.I)),
    ("area", re.compile(r"建筑面积|占地面积|用地面积|面积", re.I)),
    ("capacity", re.compile(r"年产量|设计产能|生产能力|产能|销量", re.I)),
    ("price", re.compile(r"销售单价|采购单价|单价|价格", re.I)),
    ("market_radius", re.compile(r"市场半径|运输半径|辐射半径", re.I)),
)


_FINANCIAL_METRICS = {
    "project_irr", "capital_irr", "npv", "dscr", "icr", "payback",
    "static_payback", "dynamic_payback",
    "discount_rate", "bep",
    "purchase_price", "valuation_value", "total_investment", "construction_investment", "working_capital", "capital",
    "construction_interest", "debt", "revenue", "operating_cost", "total_cost",
    "net_profit", "income_tax", "depreciation", "profit", "annual_use",
    "engineering_cost", "civil_cost", "equipment_cost", "installation_cost", "other_investment_cost",
    "contingency", "wage_cost", "maintenance_cost", "utility_cost", "insurance_cost",
    "raw_material_cost",
    "marketing_cost", "lease_cost", "management_cost", "salary_cost", "welfare_cost",
}


_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project_irr", re.compile(r"(?:project_irr|project.*irr)(?:_pct)?", re.I)),
    ("capital_irr", re.compile(r"(?:capital_irr|equity_irr)(?:_pct)?", re.I)),
    ("npv", re.compile(r"(?:^|[.\[])npv(?:_wan)?(?:$|[.\[])|net_present_value", re.I)),
    ("dscr", re.compile(r"(?:^|[.\[])dscr(?:$|[.\[])|debt_service_coverage", re.I)),
    ("icr", re.compile(r"(?:^|[.\[])icr(?:$|[.\[])|interest_coverage", re.I)),
    ("dynamic_payback", re.compile(r"dynamic_payback", re.I)),
    ("static_payback", re.compile(r"static_payback", re.I)),
    ("payback", re.compile(r"(?<!dynamic_)(?<!static_)payback", re.I)),
    ("discount_rate", re.compile(r"benchmark_rate|discount_rate", re.I)),
    ("bep", re.compile(r"bep(?:_pct)?", re.I)),
    ("purchase_price", re.compile(r"purchase_price", re.I)),
    ("valuation_value", re.compile(r"valuation_value", re.I)),
    ("total_investment", re.compile(r"total_investment|total_acquisition_cost|investment\.total(?:$|[.\[])|total_inv", re.I)),
    ("construction_investment", re.compile(r"construction_investment|investment\.construction", re.I)),
    ("construction_interest", re.compile(r"construction_interest|investment\.interest|interest_during_construction", re.I)),
    ("working_capital", re.compile(r"working_capital|investment\.working", re.I)),
    ("capital", re.compile(r"(?:funding\.)?(?:capital|equity)(?:_wan)?(?:$|[.\[])|capital_fund", re.I)),
    ("debt", re.compile(r"(?:funding\.)?(?:loan|debt)(?:_wan)?(?:$|[.\[])|borrow", re.I)),
    ("revenue", re.compile(r"(?:^|[.\[])revenue(?:$|[.\[])|annual_revenue", re.I)),
    ("operating_cost", re.compile(r"operating_cost|op_cash_cost", re.I)),
    ("total_cost", re.compile(r"total_cost|indicators\.op_cost", re.I)),
    ("net_profit", re.compile(r"net_profit", re.I)),
    ("income_tax", re.compile(r"income_tax", re.I)),
    ("depreciation", re.compile(r"annual_depreciation|indicators\.depreciation", re.I)),
    ("profit", re.compile(r"profit_total|total_profit|profit_before|(?:^|[.\[])profit(?:$|[.\[])", re.I)),
    ("annual_use", re.compile(r"(?:funding_annual_schedule|financial_plan).*?(?:finance_in|invest_out)", re.I)),
    ("engineering_cost", re.compile(r"breakdown_detail\.engineering_total", re.I)),
    # 工程费三项分项此前只有正文模式、没有 run 路径模式，于是被识别出来后
    # 在 run 里找不到值，照样报 REPORT.NUMBERS.BOUND 假阳性（昨天 civil_cost
    # 就是这么产生的）。run 里两处都有：invest_breakdown 扁平键与
    # breakdown_detail.engineering 的 [名称, 金额] 明细行。
    ("civil_cost", re.compile(r"invest_breakdown\.civil_wan|breakdown_detail\.engineering.*建筑工程费", re.I)),
    ("equipment_cost", re.compile(r"invest_breakdown\.equipment_wan|breakdown_detail\.engineering.*设备", re.I)),
    ("installation_cost", re.compile(r"invest_breakdown\.installation_wan|breakdown_detail\.engineering.*安装工程费", re.I)),
    ("other_investment_cost", re.compile(r"breakdown_detail\.other|invest_breakdown\.other_items|invest_breakdown\.other_wan", re.I)),
    ("contingency", re.compile(r"breakdown_detail\.contingency|invest_breakdown\.contingency_items|invest_breakdown\.reserve_wan", re.I)),
    ("wage_cost", re.compile(r"cost_items\.工资及(?:福利|附加)|wage_wan", re.I)),
    # 正文模式新增了"工资 1,050"、"福利 315"、"修理与维护 800" 这些写法后，
    # run 侧路径必须同步覆盖 `cost_items.<同名键>`，否则会从"不识别"变成
    # "识别出来但在 run 里找不到值"，照样报 REPORT.NUMBERS.BOUND 假阳性。
    # annual.wage[*] 是按工资率推算的分年明细，与 cost_items 里用户显式给的
    # 金额不是同一个数，两者都要认。
    ("salary_cost", re.compile(r"annual\.wage\[\d+\]\.wage$|cost_items\.工资$", re.I)),
    ("welfare_cost", re.compile(r"annual\.wage\[\d+\]\.welfare$|cost_items\.福利$", re.I)),
    # 成本项路径一律允许 cost_items 之后带前缀词：真实 cost_items 键名常写成
    # "销售与管理费用"、"外购原材料"、"场地使用及租赁"。旧写法把词锚在点号后
    # 紧邻位置，于是正文正则 `管理费用|管理费` 能识别出 management_cost，路径
    # 正则 `cost_items\.管理` 却匹配不到 `cost_items.销售与管理费用`——
    # candidate_paths 为 0，run 里明明有 1500 也被报"无法复现"。
    # 判据升级：两表不仅键要对齐，同一概念的正则覆盖范围也必须一致。
    ("maintenance_cost", re.compile(r"cost_items\.[^.]*?(?:修理与维护|设备维护|维修|维护|修理)", re.I)),
    ("utility_cost", re.compile(r"cost_items\.[^.]*?(?:水电能源|能源|水电)", re.I)),
    ("raw_material_cost", re.compile(r"cost_items\.[^.]*?(?:主要原材料|原材料|原料)", re.I)),
    ("insurance_cost", re.compile(r"cost_items\.[^.]*?保险", re.I)),
    ("marketing_cost", re.compile(r"cost_items\.[^.]*?营销", re.I)),
    ("lease_cost", re.compile(r"cost_items\.[^.]*?(?:场地使用及租赁|场地租赁|租赁)", re.I)),
    ("management_cost", re.compile(r"cost_items\.[^.]*?管理", re.I)),
)


_DEFAULT_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("项目概况", ("项目概况", "总论", "项目背景")),
    ("市场分析", ("市场分析", "市场需求", "市场预测")),
    ("建设或技术方案", ("建设方案", "技术方案", "工程方案", "实施方案")),
    ("投资与融资", ("投资估算", "资金筹措", "融资方案", "投资与融资")),
    ("财务分析", ("财务分析", "财务评价", "经济评价", "偿债分析")),
    ("风险分析", ("风险分析", "风险识别", "风险与对策")),
    ("结论与建议", ("结论与建议", "研究结论", "结论")),
)
