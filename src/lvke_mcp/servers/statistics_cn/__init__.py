"""statistics-cn MCP server: 国家统计局 + 湖北省统计局公开指标查询。

提供 2 个工具:

- ``query_indicator(name, region, year)``  查询某指标多年序列
- ``list_dictionaries()``                  列出可用指标 / 地区字典

数据策略:
- 仓库自带 ``seed/indicators.json``,内置 GDP / 常住人口 / 工业增加值 / 固定资产投资 等 6 个常用指标的湖北全省 + 武汉等地市近 5 年数据。
- 业务方可在 ``data/indicators.json`` 提供更全面的数据(全国 / 长江中游 / 其他省市)。
- 远期演进:接国家统计局 EasyQuery API,但需要 token 与限速。
"""
