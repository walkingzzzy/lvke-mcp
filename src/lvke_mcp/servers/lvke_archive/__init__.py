"""lvke-archive MCP server: 绿科历史可研报告档案库。

提供 3 个工具:

- ``search_archive(keyword, industry, year, limit)``
                                       关键词检索归档报告
- ``get_chapter(report_id, chapter)``  按 report_id 拿单章节正文
- ``find_similar_projects(brief, top_n)``
                                       按摘要找相似项目

数据存储:
- 仓库内 ``seed/`` 目录提供 5-10 份脱敏样例,可直接跑通链路
- 实际部署时把 ``data/`` 用业务方提供的脱敏报告库覆盖(已加 ``.gitignore``)
- ``manifest`` 通过环境变量 ``LVKE_ARCHIVE_DATA_DIR`` 指向真实数据
"""
