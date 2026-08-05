"""lvke-templates MCP server: 制式表格库。

提供 3 个工具:

- ``list_templates(category)``        按分类列出所有制式表格
- ``get_template(template_id)``       拿单张表的结构与字段定义
- ``fill_template(template_id, data)``  把业务数据填入模板,返回填充后的 markdown 表

内置模板分类:
- ``investment-estimation``   投资估算汇总(对应 5.4 总投资构成)
- ``financing``               资金筹措与资金使用计划(5.5 / 5.6)
- ``income-statement``        营业收入与成本费用(6.2 / 6.3)
- ``cashflow``                现金流量表(6.5)
- ``sensitivity``             敏感性分析表(6.6)
- ``key-indicators``          主要技术经济指标汇总表(9.2)
- ``risk-matrix``             风险评级矩阵(7.X)
- ``regulatory-schedule``     行政手续进度安排(9.4)
"""
