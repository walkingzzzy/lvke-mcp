"""lvke_mcp.servers —— MCP server 入口点。

每个 server 一个包,内含 ``server.py``(``build_server`` + ``main``)与
``__init__.py``。参考实现:``lvke_mcp.servers.scaffold``。

批次 8(MCP_INDEPENDENCE_PLAN §29.4)已将全部顶层领域 server 迁入本包:
``lvke_source_files`` 之外,environmental_data / excel_bridge / finance_calc /
industry_research / lvke_archive / lvke_asset_acquisition / lvke_clients /
lvke_data_acquisition / lvke_data_analysis / lvke_deep_research /
lvke_deliverable_review / lvke_experts / lvke_finance_model /
lvke_finance_tables / lvke_knowledge_governance / lvke_project_planning /
lvke_report_generation / lvke_templates / lvke_zero_material_delivery /
map_geo / policy_search / statistics_cn 各占一个子包。

顶层 ``mcp_servers.<name>`` 保留兼容垫片(§696 过渡期)。
"""