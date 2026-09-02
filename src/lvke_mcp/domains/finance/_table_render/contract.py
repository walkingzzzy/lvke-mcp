"""Shared finance table delivery contract constants.

Kept in the table-render implementation package so render specifications do
not need to import the run-service facade (which would create an import cycle).
The run service re-exports these names for backwards compatibility.
"""

from __future__ import annotations


# 正式交付编号（附表6-1/6-2/6-3不是简单的第7/8/9张表）。
DELIVERY_TABLE_META: tuple[tuple[str, str, str], ...] = (
    ("investment", "附表1", "固定资产投资估算表"),
    ("interest-during-construction", "附表2", "建设期贷款利息表"),
    ("working-capital", "附表3", "流动资金估算表"),
    ("funding", "附表4", "投资使用计划与资金筹措表"),
    ("income-statement", "附表5", "营业收入、税金及附加和增值税估算表"),
    ("total-cost", "附表6", "总成本费用估算表"),
    ("wage", "附表6-1", "工资及附加估算表"),
    ("depreciation", "附表6-2", "固定资产折旧费估算表"),
    ("amortization", "附表6-3", "无形资产及其他资产摊销估算表"),
    ("profit-distribution", "附表7", "利润与利润分配表"),
    ("debt-service", "附表8", "还款付息测算表"),
    ("cashflow", "附表9", "项目投资现金流量表"),
    ("capital-cashflow", "附表10", "项目资本金流量表"),
    ("financial-plan", "附表11", "财务计划现金流量表"),
)

# 唯一交付成员和顺序由 DELIVERY_TABLE_META 派生；参考来源 sheet 不得进入此集合。
DELIVERY_TABLE_KEYS: tuple[str, ...] = tuple(item[0] for item in DELIVERY_TABLE_META)

