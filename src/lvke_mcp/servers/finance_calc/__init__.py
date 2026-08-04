"""finance-calc MCP server: IRR / NPV / 敏感性 / 投资回收期 精确计算。

提供 4 个工具:

- ``calc_irr(cashflows)``                  内部收益率
- ``calc_npv(cashflows, rate)``            净现值
- ``payback_period(cashflows, rate=0)``    静态/动态投资回收期
- ``sensitivity_analysis(model, factors, ranges)``
                                           单因素敏感性扫描
"""
