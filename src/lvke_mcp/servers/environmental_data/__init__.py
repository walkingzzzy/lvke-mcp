"""environmental-data MCP server: 环境质量基线数据查询。

提供 3 个工具:

- ``query_air_quality(city, year, month)``    查询大气质量(AQI / PM2.5 / PM10 等)
- ``query_water_quality(basin_or_section)``    查询水环境质量(类别 / 主要污染物)
- ``list_monitored_locations()``              列出可查询的城市 / 流域 / 断面字典
"""
