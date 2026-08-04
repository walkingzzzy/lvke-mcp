"""policy-search MCP server: 政策文件库检索。

提供 3 个工具:

- ``search_policy(keyword, year, region, level, limit)``  关键词检索政策
- ``get_policy_full(policy_id)``                            拿政策全文摘要
- ``verify_policy_active(citation)``                        校验引用的政策是否仍生效

数据源策略:
- 仓库自带 ``seed/policies.json`` 内置 12 份常用国家级 + 湖北省政策,覆盖能源、化工、农业、生态、双碳、招投标等。
- 业务方可覆盖 ``data/policies.json`` 接入北大法宝 / 中国政府网导出的扩展库。
- ``verify_policy_active`` 仅按 ``status`` 字段判断,不调用外网。
"""
