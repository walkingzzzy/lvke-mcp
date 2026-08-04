"""excel-bridge MCP server: Excel(.xlsx) 读写桥接。

提供 2 个工具:

- ``read_xlsx(path, sheet, range, max_rows, max_cols)``  读取 sheet 内容
- ``list_sheets(path)``                                   列出 sheet 名

依赖检测:
- 若已安装 ``openpyxl`` -> 使用全功能(支持公式 / 复杂格式)
- 否则 fallback 到内置的最小 zip + xml 解析(只能读取纯值,不解析公式)

为避免引入大依赖,默认仅暴露读取能力。
"""
