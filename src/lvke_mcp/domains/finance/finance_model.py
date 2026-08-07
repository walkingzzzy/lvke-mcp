"""可研财务测算模型（PT-1：专业化第一支柱）。

从 ``requirement.finance`` 的用户输入参数，用 finance_calc 的确定性纯函数
（npv/irr/payback）产出**真实**财务指标与制式附表 Markdown，供生成链路把
"详见第六章测算"占位替换为真实数字，并保证正文/技经指标表/附表三处勾稽一致。

设计原则：
- 只做"可研深度"的简化联动模型（±15% 合理区间），不追求审计级精度。
- 缺输入时按行业经验默认估算，并标 ``assumptions`` 以便前端/审查提示。
- 所有衍生数字来自本模块（单一真源），杜绝 LLM 心算导致的不一致。

Wave 3.4 门面：实现搬到 ``_finance_model/`` 子模块 —— ``base``（基准利率、参数
缓存、IRR/NPV/回收期计算底座与格式化原语，含两个可选依赖兜底 try 块）、
``tax``（所得税表、亏损弥补与增值税留抵）、``profiles``（项目性质政策与行业
参考）、``investment``（投资明细三段式与范围分类）、``engine``（核心计算）、
``annual``（年度投影与财务计划）、``render``（表格/摘要渲染与 markdown）与
``checks``（勾稽一致性与估算依据）。

``engine`` 把 ``compute_financials`` 与 ``_apply_custom_calcs`` /
``_rerun_scaled`` / ``_build_scenarios`` / ``_build_sensitivity`` 放在一起：
后四者会重入主计算（缩放重算与自定义目标求解都要重跑 ``compute_financials``），
四者互相递归，属同一事务边界。按方案 §4，不为降低行数切开函数体，也不把递归
拆成跨模块调用——那会破坏数值口径与 hash 顺序。因此 ``engine.py`` 与
``annual.py`` 仍超过 600 行，这是有意保留。
"""

from __future__ import annotations

import copy  # noqa: F401
import math  # noqa: F401
from typing import Any, Optional  # noqa: F401

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import assets as _fin_assets  # noqa: F401
from lvke_mcp.domains.finance import capitalization as _fin_cap  # noqa: F401
from lvke_mcp.domains.finance import checks as _fin_checks  # noqa: F401
from lvke_mcp.domains.finance import debt as _fin_debt  # noqa: F401
from lvke_mcp.domains.finance import normalize as _fin_normalize  # noqa: F401
from lvke_mcp.domains.finance import scenarios as _fin_scenarios  # noqa: F401
from lvke_mcp.domains.finance import statements as _fin_statements  # noqa: F401
from lvke_mcp.domains.finance import taxes as _fin_taxes  # noqa: F401
from lvke_mcp.domains.finance import timeline as _fin_timeline  # noqa: F401
from lvke_mcp.domains.finance import working_capital as _fin_wc  # noqa: F401
from lvke_mcp.domains.finance.contracts import FINANCE_SCHEMA_VERSION  # noqa: F401

from ._finance_model.annual import (  # noqa: F401
    _build_annual,
    _build_financial_plan,
    _construction_interest,
    _equal_principal_debt,
)
from ._finance_model.base import (  # noqa: F401
    BENCHMARK_RATE,
    DEFAULT_LOAN_RATE,
    _COST_FALLBACK,
    _HAS_INVESTMENT_BREAKDOWN,
    _PARAMS_CACHE,
    _cost_param,
    _f,
    _fmt,
    _fmt_rate_display,
    _irr,
    _load_finance_params,
    _npv,
    _payback,
    _resolve_benchmark,
)
from ._finance_model.checks import (  # noqa: F401
    basis_of_estimate_md,
    check_consistency,
)
from ._finance_model.engine import (  # noqa: F401
    _CUSTOM_TARGET_INPUTS,
    _apply_custom_calcs,
    _build_scenarios,
    _build_sensitivity,
    _custom_fallback,
    _rerun_scaled,
    compute_financials,
)
from ._finance_model.investment import (  # noqa: F401
    _FLAT_LIFT_NOTE,
    _INVEST_CONTINGENCY,
    _INVEST_ENGINEERING,
    _INVEST_OTHER,
    _SCOPE_TOL,
    _classify_investment_scope,
    _lift_flat_invest_breakdown,
    _parse_invest_detail,
)
from ._finance_model.profiles import (  # noqa: F401
    industry_reference,
    project_nature_policy,
)
from ._finance_model.render import (  # noqa: F401
    _render_annual_tables,
    _render_summary,
    _render_tables,
    _required_markers,
    finance_tables_markdown,
)
from ._finance_model.tax import (  # noqa: F401
    _DEFAULT_LOSS_CARRYFORWARD_YEARS,
    _compute_income_tax_with_loss_carryforward,
    _compute_vat_with_credit_carryover,
    _income_tax_schedule,
    _tax_spec_int,
)

# ``InvestmentBreakdown`` 属于原模块公开表面（api_snapshot 基线里有），但它只在
# ``base`` 的可选依赖 try 成功分支里绑定：缺 ``finance.spec`` 时原模块的行为是
# 该名字不存在，而不是 import 失败。门面照抄这个条件性，不把可选依赖变成硬依赖。
if _HAS_INVESTMENT_BREAKDOWN:  # pragma: no branch - 依赖存在时的常规路径
    from ._finance_model.base import InvestmentBreakdown  # noqa: F401
