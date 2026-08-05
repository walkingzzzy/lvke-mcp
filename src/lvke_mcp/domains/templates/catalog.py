"""lvke-templates 的模板目录(内置)。

每个模板包含:
- ``template_id``: 唯一标识
- ``category``:    分类
- ``name``:        中文显示名
- ``description``: 用途说明
- ``columns``:     列(顺序敏感),每列含 ``key`` / ``label`` / ``unit`` / ``type``
- ``rows``:        可选 —— 预定义行(如投资估算的标准科目),每行含 ``key`` / ``label``;
                   如果模板是动态行(如敏感性分析的因子表),则不预定义 ``rows``。
- ``notes``:       使用注意(报告写作时的口径提示)

填充逻辑见 ``filler.py``。
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    # ── 5.2 / 5.4 投资估算 ────────────────────────────────────────────
    "investment-estimation": {
        "template_id": "investment-estimation",
        "category": "investment-estimation",
        "name": "建设投资估算汇总表",
        "description": "对应「投融资与财务方案-投资估算」的建设投资估算与总投资构成。按「工程构成 + 工程建设其他 + 预备费」三段式。",
        "columns": [
            {"key": "category", "label": "费用类别", "unit": "", "type": "string"},
            {"key": "amount", "label": "金额", "unit": "万元", "type": "number"},
            {"key": "ratio", "label": "占比", "unit": "%", "type": "number"},
            {"key": "remark", "label": "备注", "unit": "", "type": "string"},
        ],
        "rows": [
            {"key": "construction.civil", "label": "建筑工程费"},
            {"key": "construction.equipment", "label": "设备及工器具购置费"},
            {"key": "construction.installation", "label": "安装工程费"},
            {"key": "other.land", "label": "土地使用费 / 拆迁补偿"},
            {"key": "other.management", "label": "项目管理费"},
            {"key": "other.design", "label": "勘察设计费"},
            {"key": "other.consulting", "label": "咨询服务费(含可研编制)"},
            {"key": "other.supervision", "label": "工程监理费"},
            {"key": "other.bidding", "label": "招标代理费"},
            {"key": "other.test_run", "label": "联合试运转费"},
            {"key": "contingency.basic", "label": "基本预备费"},
            {"key": "contingency.price", "label": "价差预备费"},
            {"key": "interest_during_construction", "label": "建设期利息"},
            {"key": "working_capital", "label": "流动资金"},
            {"key": "total", "label": "项目总投资合计"},
        ],
        "notes": [
            "设备购置费要与第 4.3 章节设备清单逐项对应",
            "建设期 ≤ 1 年时不计算价差预备费",
            "基本预备费一般取建筑+设备+安装的 5-10%",
            "建设期利息按贷款建模结果回填",
        ],
    },
    # ── 5.5 资金筹措 ────────────────────────────────────────────────
    "financing": {
        "template_id": "financing",
        "category": "financing",
        "name": "资金筹措方案表",
        "description": "对应「投融资与财务方案-融资方案」的资金筹措与资金使用计划。",
        "columns": [
            {"key": "source", "label": "资金来源", "unit": "", "type": "string"},
            {"key": "amount", "label": "金额", "unit": "万元", "type": "number"},
            {"key": "ratio", "label": "占比", "unit": "%", "type": "number"},
            {"key": "terms", "label": "条件 / 期限 / 利率", "unit": "", "type": "string"},
            {"key": "evidence", "label": "依据文件", "unit": "", "type": "string"},
        ],
        "rows": [
            {"key": "equity", "label": "项目资本金(自有)"},
            {"key": "loan_bank", "label": "银行贷款"},
            {"key": "loan_other", "label": "其他融资(债券/租赁)"},
            {"key": "subsidy_gov", "label": "政府配套资金"},
            {"key": "total", "label": "合计"},
        ],
        "notes": [
            "资本金比例必须满足行业最低(化工 30% / 一般工业 20% / 公共服务 30%)",
            "政府配套资金必须有具体政策文件依据",
            "贷款条件必须列利率、期限、还款方式",
        ],
    },
    # ── 6.2 / 6.3 营业收入与成本 ─────────────────────────────────────
    "income-statement": {
        "template_id": "income-statement",
        "category": "income-statement",
        "name": "营业收入与成本费用预测表(运营期)",
        "description": "对应「投融资与财务方案-财务效益评价」的营业收入与成本费用估算。按年份逐年列。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "revenue", "label": "营业收入", "unit": "万元", "type": "number"},
            {"key": "operating_cost", "label": "经营成本", "unit": "万元", "type": "number"},
            {"key": "depreciation", "label": "折旧", "unit": "万元", "type": "number"},
            {"key": "amortization", "label": "摊销", "unit": "万元", "type": "number"},
            {"key": "tax_surtax", "label": "销售税金及附加", "unit": "万元", "type": "number"},
            {"key": "ebit", "label": "息税前利润", "unit": "万元", "type": "number"},
            {"key": "interest", "label": "利息支出", "unit": "万元", "type": "number"},
            {"key": "income_tax", "label": "所得税", "unit": "万元", "type": "number"},
            {"key": "net_profit", "label": "净利润", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "全表统一不含税口径",
            "销售税金及附加 ≈ 增值税 × 12%(市区:城建税 7% + 教育附加 5%)",
            "所得税率默认 25%,高新技术企业 15%(按 3 年复审切换)",
        ],
    },
    # ── 6.5 现金流量 ────────────────────────────────────────────────
    "cashflow": {
        "template_id": "cashflow",
        "category": "cashflow",
        "name": "项目投资现金流量表",
        "description": "对应「投融资与财务方案-财务效益评价」的现金流量分析(项目视角,用于计算项目 IRR/NPV)。",
        "columns": [
            {"key": "year", "label": "计算期", "unit": "", "type": "string"},
            {"key": "revenue_inflow", "label": "营业收入", "unit": "万元", "type": "number"},
            {"key": "salvage", "label": "回收固定资产残值", "unit": "万元", "type": "number"},
            {"key": "wc_recovery", "label": "回收流动资金", "unit": "万元", "type": "number"},
            {"key": "construction_invest", "label": "建设投资", "unit": "万元", "type": "number"},
            {"key": "working_capital_out", "label": "流动资金投入", "unit": "万元", "type": "number"},
            {"key": "operating_cost", "label": "经营成本", "unit": "万元", "type": "number"},
            {"key": "tax_surtax", "label": "销售税金及附加", "unit": "万元", "type": "number"},
            {"key": "income_tax", "label": "所得税(按全投资假设)", "unit": "万元", "type": "number"},
            {"key": "net_cashflow", "label": "净现金流", "unit": "万元", "type": "number"},
            {"key": "cumulative_cashflow", "label": "累计净现金流", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "项目 IRR 现金流不含借贷利息(假设全投资)",
            "末年要含残值与流动资金回收(正值)",
            "折旧/摊销不算现金流出(已通过利润扣除体现)",
        ],
    },
    # ── 6.6 敏感性分析 ─────────────────────────────────────────────
    "sensitivity": {
        "template_id": "sensitivity",
        "category": "sensitivity",
        "name": "单因素敏感性分析表",
        "description": "对应「风险管控方案-敏感性分析」的单因素敏感性分析,至少 5 个因子。",
        "columns": [
            {"key": "factor", "label": "因子", "unit": "", "type": "string"},
            {"key": "minus_20", "label": "-20%", "unit": "%", "type": "number"},
            {"key": "minus_10", "label": "-10%", "unit": "%", "type": "number"},
            {"key": "base", "label": "基准", "unit": "%", "type": "number"},
            {"key": "plus_10", "label": "+10%", "unit": "%", "type": "number"},
            {"key": "plus_20", "label": "+20%", "unit": "%", "type": "number"},
            {"key": "elasticity", "label": "弹性系数", "unit": "", "type": "number"},
        ],
        "notes": [
            "至少包含 5 因子:销售收入、经营成本、建设投资、产能利用率、销售税金/价格",
            "基准列必须 = 现金流量表计算的基准 IRR(数据一致性硬约束)",
            "弹性系数 = |ΔIRR/Δ因子|,数值越大越敏感",
        ],
    },
    # ── 9.2 主要技术经济指标汇总 ───────────────────────────────────
    "key-indicators": {
        "template_id": "key-indicators",
        "category": "key-indicators",
        "name": "主要技术经济指标汇总表",
        "description": "对应「研究结论及建议-主要研究结论」的主要技术经济指标汇总。评审专家做汇报材料的标准来源。",
        "columns": [
            {"key": "seq", "label": "序号", "unit": "", "type": "string"},
            {"key": "indicator", "label": "指标", "unit": "", "type": "string"},
            {"key": "unit", "label": "单位", "unit": "", "type": "string"},
            {"key": "value", "label": "数值", "unit": "", "type": "string"},
            {"key": "remark", "label": "备注", "unit": "", "type": "string"},
        ],
        "rows": [
            {"key": "total_investment", "label": "项目总投资"},
            {"key": "construction_investment", "label": "建设投资"},
            {"key": "working_capital", "label": "流动资金"},
            {"key": "equity_capital", "label": "资本金"},
            {"key": "equity_ratio", "label": "资本金比例"},
            {"key": "build_period", "label": "建设期"},
            {"key": "total_floor_area", "label": "总建筑面积"},
            {"key": "land_area", "label": "占地面积"},
            {"key": "design_capacity", "label": "设计产能"},
            {"key": "revenue_at_full", "label": "营业收入(达产年)"},
            {"key": "profit_at_full", "label": "净利润(达产年)"},
            {"key": "project_irr", "label": "项目 IRR(税后)"},
            {"key": "payback_years", "label": "投资回收期(动态)"},
            {"key": "capital_irr", "label": "资本金 IRR"},
            {"key": "headcount", "label": "员工数"},
        ],
        "notes": [
            "本表所有数值必须与正文逐字对应",
            "单位列要明确(万元 / 人 / 月 / % 等)",
        ],
    },
    # ── 7.X 风险评级 ────────────────────────────────────────────────
    "risk-matrix": {
        "template_id": "risk-matrix",
        "category": "risk-matrix",
        "name": "风险识别与评级矩阵",
        "description": "对应「风险管控方案」的风险章节,按 7 类风险输出识别 → 评级 → 对策 → 残余风险。",
        "columns": [
            {"key": "category", "label": "风险大类", "unit": "", "type": "string"},
            {"key": "risk", "label": "具体风险", "unit": "", "type": "string"},
            {"key": "probability", "label": "发生概率", "unit": "", "type": "string"},
            {"key": "impact", "label": "影响程度", "unit": "", "type": "string"},
            {"key": "level", "label": "综合等级", "unit": "", "type": "string"},
            {"key": "measures", "label": "应对措施", "unit": "", "type": "string"},
            {"key": "residual", "label": "残余风险", "unit": "", "type": "string"},
        ],
        "notes": [
            "概率 × 影响 → 等级矩阵(参考 skills/report-drafting/chapter-7-risk)",
            "每项风险必须有对策与残余风险评级",
            "禁止整张表全是低 / 中等级",
        ],
    },
    # ── M8：重大设备比选（建设方案章）────────────────────────────────
    "equipment-comparison": {
        "template_id": "equipment-comparison",
        "category": "scheme-comparison",
        "name": "重大设备比选表",
        "description": "对应「项目建设方案-设备方案」的重大设备多方案技术经济比选。",
        "columns": [
            {"key": "item", "label": "设备/方案", "unit": "", "type": "string"},
            {"key": "model", "label": "型号/方案", "unit": "", "type": "string"},
            {"key": "invest", "label": "投资", "unit": "万元", "type": "number"},
            {"key": "energy", "label": "能耗", "unit": "", "type": "string"},
            {"key": "maintenance", "label": "维护性", "unit": "", "type": "string"},
            {"key": "pros_cons", "label": "优劣分析", "unit": "", "type": "string"},
            {"key": "conclusion", "label": "比选结论", "unit": "", "type": "string"},
        ],
        "notes": [
            "仅对重大/关键设备做比选，普通设备列清单即可",
            "比选须给出推荐方案及理由（技术+经济）",
        ],
    },
    # ── M8：建筑方案比选（建设方案章）────────────────────────────────
    "scheme-comparison": {
        "template_id": "scheme-comparison",
        "category": "scheme-comparison",
        "name": "建筑/技术方案比选表",
        "description": "对应「项目建设方案」的建筑或技术方案多方案技术经济比选。",
        "columns": [
            {"key": "scheme", "label": "方案", "unit": "", "type": "string"},
            {"key": "tech", "label": "技术特点", "unit": "", "type": "string"},
            {"key": "invest", "label": "投资", "unit": "万元", "type": "number"},
            {"key": "period", "label": "工期", "unit": "月", "type": "number"},
            {"key": "pros_cons", "label": "优劣分析", "unit": "", "type": "string"},
            {"key": "conclusion", "label": "比选结论", "unit": "", "type": "string"},
        ],
        "notes": [
            "至少 2 个方案对比，给出推荐方案",
            "比选维度含投资、工期、运营成本、可靠性",
        ],
    },
    # ── 9.4 行政手续进度 ────────────────────────────────────────────
    "regulatory-schedule": {
        "template_id": "regulatory-schedule",
        "category": "regulatory-schedule",
        "name": "行政手续进度安排表",
        "description": "对应「研究结论及建议」的行政手续与实施计划。各专项手续必须早于开工时间。",
        "columns": [
            {"key": "seq", "label": "序号", "unit": "", "type": "string"},
            {"key": "task", "label": "行政事项", "unit": "", "type": "string"},
            {"key": "authority", "label": "主管部门", "unit": "", "type": "string"},
            {"key": "due", "label": "计划完成时间", "unit": "", "type": "string"},
            {"key": "status", "label": "当前状态", "unit": "", "type": "string"},
        ],
        "notes": [
            "环评 / 节能 / 用地预审 必须早于立项",
            "施工许可必须早于开工",
        ],
    },
    # ── 收入成本明细：总成本费用估算表 ───────────────────────────────
    "total-cost": {
        "template_id": "total-cost",
        "category": "income-statement",
        "name": "总成本费用估算表(运营期)",
        "description": "对应「投融资与财务方案-财务效益评价」的总成本费用测算，按年份逐年列，区分固定/可变成本。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "material", "label": "原材料及燃料动力费", "unit": "万元", "type": "number"},
            {"key": "wage", "label": "工资及福利费", "unit": "万元", "type": "number"},
            {"key": "repair", "label": "修理费", "unit": "万元", "type": "number"},
            {"key": "depreciation", "label": "折旧费", "unit": "万元", "type": "number"},
            {"key": "amortization", "label": "摊销费", "unit": "万元", "type": "number"},
            {"key": "interest", "label": "利息支出", "unit": "万元", "type": "number"},
            {"key": "other", "label": "其他费用", "unit": "万元", "type": "number"},
            {"key": "total_cost", "label": "总成本费用", "unit": "万元", "type": "number"},
            {"key": "operating_cost", "label": "其中：经营成本", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "经营成本 = 总成本费用 − 折旧 − 摊销 − 利息(现金流量表用)",
            "折旧/摊销要与固定资产折旧表、无形资产摊销表口径一致",
        ],
    },
    # ── 附表6-1 工资及附加估算表（晏批注交付子表）────────────────────────
    "wage": {
        "template_id": "wage",
        "category": "income-statement",
        "name": "工资及附加估算表",
        "description": "对应晏批注交付附表6-1。按劳动定员、人均工资和福利附加逐年测算人工成本，汇入总成本费用表。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "wage", "label": "工资", "unit": "万元", "type": "number"},
            {"key": "welfare", "label": "职工福利及附加", "unit": "万元", "type": "number"},
            {"key": "total", "label": "工资及附加合计", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "工资及附加合计回填至总成本费用表的「工资及福利费」行，口径须一致",
            "缺定员/人均工资输入时，按现金经营成本比例估算（可研简化）",
        ],
    },
    # ── 附表6-2 固定资产折旧费估算表（晏批注交付子表）────────────────────
    "depreciation": {
        "template_id": "depreciation",
        "category": "income-statement",
        "name": "固定资产折旧费估算表",
        "description": "对应晏批注交付附表6-2。按固定资产原值、残值率、折旧年限逐年测算折旧费，汇入总成本费用表。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "original_value", "label": "固定资产原值", "unit": "万元", "type": "number"},
            {"key": "salvage_rate", "label": "残值率", "unit": "%", "type": "number"},
            {"key": "dep_years", "label": "折旧年限", "unit": "年", "type": "number"},
            {"key": "depreciation", "label": "当期折旧费", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "折旧费逐年合计须与总成本费用表折旧行、现金流量表口径一致",
            "折旧 + 摊销 = 总成本费用中的非现金摊提额（勾稽约束）",
        ],
    },
    # ── 附表6-3 无形资产及其他资产摊销估算表（晏批注交付子表）────────────
    "amortization": {
        "template_id": "amortization",
        "category": "income-statement",
        "name": "无形资产和其他资产摊销估算表",
        "description": "对应晏批注交付附表6-3。按无形及其他资产摊销基数、摊销年限逐年测算摊销费，汇入总成本费用表。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "base", "label": "摊销基数", "unit": "万元", "type": "number"},
            {"key": "amort_years", "label": "摊销年限", "unit": "年", "type": "number"},
            {"key": "amortization", "label": "当期摊销费", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "无形及其他资产摊销逐年合计须与总成本费用表摊销行口径一致",
            "缺无形资产原值输入时，摊销从非现金摊提额中拆分，不改变总成本口径",
        ],
    },
    # ── 建设期利息估算表 ──────────────────────────────────────────────
    "interest-during-construction": {
        "template_id": "interest-during-construction",
        "category": "financing",
        "name": "建设期利息估算表",
        "description": "对应「投融资与财务方案-投资估算」的建设期利息。按建设期各年借款与利率计算。",
        "columns": [
            {"key": "period", "label": "建设期", "unit": "", "type": "string"},
            {"key": "begin_balance", "label": "期初借款余额", "unit": "万元", "type": "number"},
            {"key": "draw", "label": "当期借款", "unit": "万元", "type": "number"},
            {"key": "rate", "label": "年利率", "unit": "%", "type": "number"},
            {"key": "interest", "label": "当期利息", "unit": "万元", "type": "number"},
            {"key": "end_balance", "label": "期末借款余额", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "当期借款按半年计息(建设期借款一般假设年中支用)",
            "建设期利息汇总回填至建设投资估算表",
        ],
    },
    # ── 还本付息测算表 ────────────────────────────────────────────────
    "debt-service": {
        "template_id": "debt-service",
        "category": "financing",
        "name": "借款还本付息测算表",
        "description": "对应「投融资与财务方案-财务可持续性分析」的偿债能力。按运营期逐年列。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "begin_balance", "label": "期初借款余额", "unit": "万元", "type": "number"},
            {"key": "repay_principal", "label": "当期还本", "unit": "万元", "type": "number"},
            {"key": "pay_interest", "label": "当期付息", "unit": "万元", "type": "number"},
            {"key": "end_balance", "label": "期末借款余额", "unit": "万元", "type": "number"},
            {"key": "dscr", "label": "偿债备付率(DSCR)", "unit": "", "type": "number"},
        ],
        "notes": [
            "还款方式需与融资方案一致(等额本金/等额本息)",
            "DSCR 一般应 ≥ 1.2，否则偿债能力不足",
        ],
    },
    # ── 利润与利润分配表 ──────────────────────────────────────────────
    "profit-distribution": {
        "template_id": "profit-distribution",
        "category": "income-statement",
        "name": "利润与利润分配表",
        "description": "对应「投融资与财务方案-财务效益评价」的利润测算与分配。",
        "columns": [
            {"key": "year", "label": "运营年", "unit": "", "type": "string"},
            {"key": "revenue", "label": "营业收入", "unit": "万元", "type": "number"},
            {"key": "total_cost", "label": "总成本费用", "unit": "万元", "type": "number"},
            {"key": "tax_surtax", "label": "销售税金及附加", "unit": "万元", "type": "number"},
            {"key": "total_profit", "label": "利润总额", "unit": "万元", "type": "number"},
            {"key": "income_tax", "label": "所得税", "unit": "万元", "type": "number"},
            {"key": "net_profit", "label": "净利润", "unit": "万元", "type": "number"},
            {"key": "surplus_reserve", "label": "盈余公积", "unit": "万元", "type": "number"},
            {"key": "undistributed", "label": "未分配利润", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "利润总额 = 营业收入 − 总成本费用 − 销售税金及附加",
            "所得税以利润总额为税基(可弥补以前年度亏损)",
        ],
    },
    # ── 资本金现金流量表 ──────────────────────────────────────────────
    "capital-cashflow": {
        "template_id": "capital-cashflow",
        "category": "cashflow",
        "name": "项目资本金现金流量表",
        "description": "对应「投融资与财务方案-财务效益评价」的资本金 IRR(股东视角，含借款还本付息)。",
        "columns": [
            {"key": "year", "label": "计算期", "unit": "", "type": "string"},
            {"key": "cash_in", "label": "现金流入", "unit": "万元", "type": "number"},
            {"key": "equity_out", "label": "资本金投入", "unit": "万元", "type": "number"},
            {"key": "operating_cost", "label": "经营成本", "unit": "万元", "type": "number"},
            {"key": "tax_surtax", "label": "销售税金及附加", "unit": "万元", "type": "number"},
            {"key": "repay_principal", "label": "借款本金偿还", "unit": "万元", "type": "number"},
            {"key": "pay_interest", "label": "借款利息支付", "unit": "万元", "type": "number"},
            {"key": "income_tax", "label": "所得税", "unit": "万元", "type": "number"},
            {"key": "net_cashflow", "label": "净现金流", "unit": "万元", "type": "number"},
        ],
        "notes": [
            "资本金现金流含借款还本付息(与项目投资现金流的区别)",
            "用于计算资本金 IRR，通常高于项目 IRR(财务杠杆)",
        ],
    },
    # ── 流动资金估算表 ────────────────────────────────────────────────
    "working-capital": {
        "template_id": "working-capital",
        "category": "investment-estimation",
        "name": "流动资金估算表",
        "description": "对应「投融资与财务方案-投资估算」的流动资金，按分项详细估算法。",
        "columns": [
            {"key": "item", "label": "项目", "unit": "", "type": "string"},
            {"key": "turnover_days", "label": "周转天数", "unit": "天", "type": "number"},
            {"key": "turnover_times", "label": "年周转次数", "unit": "次", "type": "number"},
            {"key": "amount", "label": "占用额", "unit": "万元", "type": "number"},
        ],
        "rows": [
            {"key": "ar", "label": "应收账款"},
            {"key": "inventory", "label": "存货"},
            {"key": "cash", "label": "现金"},
            {"key": "ap", "label": "减：应付账款"},
            {"key": "working_capital", "label": "流动资金合计"},
        ],
        "notes": [
            "流动资金 = 流动资产 − 流动负债",
            "首年投入铺底流动资金，达产年补足",
        ],
    },
}


# ── PT-7：附表映射（appendix_no / 章节主题 / 指标）───────────────────────────
# 集中登记每张制式表的：
#   - appendix_no    附表序号（正文内表如风险矩阵/行政手续进度序号为空串）
#   - chapter_theme  对应 gov9/ent9 章节主题（供 appendix_manifest 归章）
#   - indicators     该表提供/支撑的关键技经指标 key（供 fact_pack 勾稽核对）
# P0：附表序号按【晏批注 13 张交付附表】编号对齐（交付口径，非甲方 Excel 模板编号）。
# 交付编号语义：附表2=建设期利息、附表4=资金筹措、附表5=收入、附表6=总成本、
# 6-1=工资、6-2=折旧、6-3=摊销。折旧/摊销对外用交付编号 6-2/6-3，与甲方模板 6-5/6-6
# 靠 standard_appendix_dict() 桥接。敏感性/技经指标为展示表，不占 13 张基础编号。
_APPENDIX_META: dict[str, dict[str, Any]] = {
    "investment-estimation": {"appendix_no": "附表1", "chapter_theme": "financial",
                              "indicators": ["total_investment", "construction_investment", "working_capital"]},
    "interest-during-construction": {"appendix_no": "附表2", "chapter_theme": "financial",
                                     "indicators": ["interest_during_construction"]},
    "working-capital": {"appendix_no": "附表3", "chapter_theme": "financial",
                        "indicators": ["working_capital"]},
    "financing": {"appendix_no": "附表4", "chapter_theme": "financial",
                  "indicators": ["equity_capital", "loan", "equity_ratio"]},
    "income-statement": {"appendix_no": "附表5", "chapter_theme": "financial",
                         "indicators": ["revenue", "net_profit"]},
    "total-cost": {"appendix_no": "附表6", "chapter_theme": "financial",
                   "indicators": ["operating_cost", "total_cost"]},
    "wage": {"appendix_no": "附表6-1", "chapter_theme": "financial",
             "indicators": ["wage"]},
    "depreciation": {"appendix_no": "附表6-2", "chapter_theme": "financial",
                     "indicators": ["depreciation"]},
    "amortization": {"appendix_no": "附表6-3", "chapter_theme": "financial",
                     "indicators": ["amortization"]},
    "profit-distribution": {"appendix_no": "附表7", "chapter_theme": "financial",
                            "indicators": ["total_profit", "net_profit"]},
    "debt-service": {"appendix_no": "附表8", "chapter_theme": "financial",
                     "indicators": ["dscr"]},
    "cashflow": {"appendix_no": "附表9", "chapter_theme": "financial",
                 "indicators": ["project_irr", "npv", "payback_years"]},
    "capital-cashflow": {"appendix_no": "附表10", "chapter_theme": "financial",
                         "indicators": ["capital_irr"]},
    # 展示表（不占 13 张基础附表编号）
    "sensitivity": {"appendix_no": "", "chapter_theme": "risk",
                    "indicators": ["project_irr"]},
    "key-indicators": {"appendix_no": "", "chapter_theme": "conclusion",
                       "indicators": ["total_investment", "project_irr", "capital_irr", "payback_years"]},
    # 正文内表（非独立附表）：序号留空，仅登记归章主题
    "risk-matrix": {"appendix_no": "", "chapter_theme": "risk", "indicators": []},
    "regulatory-schedule": {"appendix_no": "", "chapter_theme": "conclusion", "indicators": []},
    "equipment-comparison": {"appendix_no": "", "chapter_theme": "scheme", "indicators": []},
    "scheme-comparison": {"appendix_no": "", "chapter_theme": "scheme", "indicators": []},
}

# 导入时把附表映射注入各模板（单一真源，避免逐表零散维护）。
for _tid, _meta in _APPENDIX_META.items():
    _tpl = TEMPLATES.get(_tid)
    if _tpl is not None:
        _tpl.setdefault("appendix_no", _meta["appendix_no"])
        _tpl.setdefault("chapter_theme", _meta["chapter_theme"])
        _tpl.setdefault("indicators", list(_meta["indicators"]))


def list_categories() -> list[str]:
    return sorted({tpl["category"] for tpl in TEMPLATES.values()})


def filter_by_category(category: str | None) -> list[dict[str, Any]]:
    if not category:
        return list(TEMPLATES.values())
    return [tpl for tpl in TEMPLATES.values() if tpl["category"] == category]


def appendix_catalog(chapter_theme: str | None = None) -> list[dict[str, Any]]:
    """返回制式附表登记（按附表序号排序）；可按章节主题过滤。

    每项：``{template_id, name, appendix_no, chapter_theme, indicators}``。
    正文内表（appendix_no 为空）排在末尾。供 report_artifacts 归章与发布页附表清单使用。
    """
    items = []
    for tpl in TEMPLATES.values():
        if chapter_theme and tpl.get("chapter_theme") != chapter_theme:
            continue
        items.append({
            "template_id": tpl["template_id"],
            "name": tpl["name"],
            "appendix_no": tpl.get("appendix_no", ""),
            "chapter_theme": tpl.get("chapter_theme", ""),
            "indicators": list(tpl.get("indicators", [])),
        })

    def _key(it: dict[str, Any]) -> tuple[int, int, int]:
        no = it["appendix_no"]
        if not no:
            return (1, 0, 0)  # 正文内表殿后
        # 解析「附表6-1」为 (主号 6, 子号 1)，避免拼接数字导致 6-1 排到 7 之后
        import re
        m = re.search(r"(\d+)(?:-(\d+))?", no)
        if not m:
            return (0, 999, 0)
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        return (0, major, minor)

    return sorted(items, key=_key)


# ══════════════════════════════════════════════════════════════════════════
# P0-f：标准附表字典 —— 晏批注 13 张交付编号 ↔ 甲方 Excel 模板 16-sheet 桥接。
# 用「业务名称」做主键联合识别，绝不按裸编号自动对映（否则甲方 6-2 燃动会被
# 错当交付 6-2 折旧、甲方 6-5 折旧会被错当 6-6 摊销）。同时覆盖甲方表头 6-4/6-5
# 编号漂移缺陷。证据：review_outputs_20260708/csv/excel_workbook_sheet_summary.csv、
#       review_outputs_20260708/csv/excel_data_quality_issues.csv（severity=高）。
# ══════════════════════════════════════════════════════════════════════════

# 每项字段：
#   delivery_no   晏批注交付附表编号（对外交付口径）
#   business      业务名称（主键，唯一识别）
#   template_id   系统内部 finance_model/catalog 的 template_id（None=系统未单列）
#   vendor_sheet  甲方 Excel 工作表名（None=甲方模板无此表 / 交付不取自甲方）
#   vendor_header 甲方 Excel 表头显示编号（体现 6-5/6-6 漂移；None=无）
#   delivered     是否属于 13 张交付附表
#   note          桥接/漂移说明
_STANDARD_APPENDIX_DICT: list[dict[str, Any]] = [
    {"delivery_no": "附表1", "business": "固定资产投资估算表", "template_id": "investment-estimation",
     "vendor_sheet": "附表1", "vendor_header": "附表1", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表2", "business": "建设期贷款利息表", "template_id": "interest-during-construction",
     "vendor_sheet": "附表2", "vendor_header": "附表2", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表3", "business": "流动资金估算表", "template_id": "working-capital",
     "vendor_sheet": "附表3", "vendor_header": "附表3", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表4", "business": "投资使用计划与资金筹措表", "template_id": "financing",
     "vendor_sheet": "附表4", "vendor_header": "附表4", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表5", "business": "营业收入、税金及附加和增值税估算表", "template_id": "income-statement",
     "vendor_sheet": "附表5", "vendor_header": "附表5", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表6", "business": "总成本费用估算表", "template_id": "total-cost",
     "vendor_sheet": "附表6", "vendor_header": "附表6", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表6-1", "business": "工资及附加估算表", "template_id": "wage",
     "vendor_sheet": "附表6-3", "vendor_header": "附表6-3", "delivered": True,
     "note": "⚠️编号错位：交付6-1(工资) ↔ 甲方模板6-3(工资福利费)"},
    {"delivery_no": "附表6-2", "business": "固定资产折旧费估算表", "template_id": "depreciation",
     "vendor_sheet": "附表6-5", "vendor_header": "附表6-4", "delivered": True,
     "note": "⚠️编号错位+漂移：交付6-2(折旧) ↔ 甲方sheet 6-5(表头却写 6-4)"},
    {"delivery_no": "附表6-3", "business": "无形资产和其他资产摊销估算表", "template_id": "amortization",
     "vendor_sheet": "附表6-6", "vendor_header": "附表6-5", "delivered": True,
     "note": "⚠️编号错位+漂移：交付6-3(摊销) ↔ 甲方sheet 6-6(表头却写 6-5)"},
    {"delivery_no": "附表7", "business": "利润与利润分配表", "template_id": "profit-distribution",
     "vendor_sheet": "附表7", "vendor_header": "附表7", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表8", "business": "还款付息测算表", "template_id": "debt-service",
     "vendor_sheet": "附表8", "vendor_header": "附表8", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表9", "business": "项目投资现金流量表", "template_id": "cashflow",
     "vendor_sheet": "附表9", "vendor_header": "附表9", "delivered": True, "note": "编号一致"},
    {"delivery_no": "附表10", "business": "项目资本金流量表", "template_id": "capital-cashflow",
     "vendor_sheet": "附表10", "vendor_header": "附表10", "delivered": True, "note": "编号一致"},
    # ── 甲方模板专有 / 不交付（仅作公式对照与内部复算层）──
    {"delivery_no": "", "business": "外购原材料费估算表", "template_id": None,
     "vendor_sheet": "附表6-1", "vendor_header": "附表6-1", "delivered": False,
     "note": "制造业专有；13 张交付口径不含，文旅/房地产项目并入总成本"},
    {"delivery_no": "", "business": "外购燃料和动力费估算表", "template_id": None,
     "vendor_sheet": "附表6-2", "vendor_header": "附表6-2", "delivered": False,
     "note": "制造业专有；13 张交付口径不含，文旅/房地产项目并入总成本"},
    {"delivery_no": "", "business": "投资估算复核表", "template_id": None,
     "vendor_sheet": "投资复核", "vendor_header": "", "delivered": False,
     "note": "仅作双轨复算的内部对照层，不交付"},
    {"delivery_no": "", "business": "主要技术经济指标汇总表", "template_id": "key-indicators",
     "vendor_sheet": "主要经济指标汇总表", "vendor_header": "", "delivered": False,
     "note": "展示/复核表；指标必须反向勾稽到现金流与13张基础附表"},
    {"delivery_no": "", "business": "单因素敏感性分析表", "template_id": "sensitivity",
     "vendor_sheet": "敏感度分析", "vendor_header": "", "delivered": False,
     "note": "敏感度/敏感性及各因子子表统一映射为独立情景复核"},
    {"delivery_no": "", "business": "建筑/技术方案比选表", "template_id": "scheme-comparison",
     "vendor_sheet": "Sheet2", "vendor_header": "", "delivered": False,
     "note": "房地产模板的结构方案与技术指标页；非财务输入但不得静默忽略"},
]


def standard_appendix_dict() -> list[dict[str, Any]]:
    """返回标准附表字典（晏批注 13 张交付编号 ↔ 甲方 16-sheet 桥接）。

    Excel 导入映射时按 ``business``（业务名称）主键识别，不按裸编号；
    ``vendor_sheet`` 与 ``vendor_header`` 不一致的项即甲方 6-5/6-6 编号漂移。
    """
    return [dict(e) for e in _STANDARD_APPENDIX_DICT]


def map_vendor_sheet(sheet_name: str = "", header_no: str = "", business: str = "") -> dict[str, Any] | None:
    """把甲方 Excel 的一张工作表映射到交付附表口径。

    优先用 ``business``（业务名称）匹配；否则用 ``sheet_name`` 匹配 ``vendor_sheet``。
    返回该表在字典中的登记项（含 delivery_no / template_id / delivered），无匹配返回 None。
    正确处理漂移：甲方 sheet「附表6-5」(表头 6-4) 会被映射到交付「附表6-2 折旧」，
    不会因表头写 6-4 而错配。
    """
    biz = str(business or "").strip()
    sh = str(sheet_name or "").strip()
    for e in _STANDARD_APPENDIX_DICT:
        if biz and biz == e["business"]:
            return dict(e)
    for e in _STANDARD_APPENDIX_DICT:
        if sh and e["vendor_sheet"] and sh == e["vendor_sheet"]:
            return dict(e)
    return None


def delivery_appendix_list() -> list[dict[str, Any]]:
    """返回 13 张交付附表（按交付编号排序），供验收 B-02 与前端附表目录使用。"""
    delivered = [dict(e) for e in _STANDARD_APPENDIX_DICT if e["delivered"]]

    def _k(it: dict[str, Any]) -> tuple[int, int]:
        import re
        m = re.search(r"(\d+)(?:-(\d+))?", it["delivery_no"])
        if not m:
            return (999, 0)
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else 0)

    return sorted(delivered, key=_k)
