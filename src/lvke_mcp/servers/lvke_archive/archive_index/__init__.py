"""统一模板库索引化管线 · 各 stage 拆分模块。

stage 1 scan       —— scan.py
stage 2 metadata   —— metadata.py
stage 3 chunk      —— chunker.py
stage 4 indicators —— indicators.py (本次仅留接口，需 aux LLM 落地)
stage 5 bm25       —— bm25_build.py
schema             —— schema.py
"""
