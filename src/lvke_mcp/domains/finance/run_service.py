"""工作区级财务模型运行服务（P0/P1 编排真源）。

把原先 ``workspace_finance_model`` 中的“读输入 → 可选 LLM 定 spec → 确定性算数 →
回显输入”拆成可单独调用、可审计、可幂等复用的阶段：

1. ``prepare_workspace_finance_spec`` —— 可读项目资料 / 可走 LLM 生成 FinanceSpec
2. ``run_workspace_finance_model`` —— **禁止内部再调 LLM**，只消费已固化输入 + spec
3. ``render_workspace_finance_tables`` —— 只从指定 run 渲染 13 表
4. ``get_workspace_finance_run`` —— 纯查询，不重算、不写库
5. ``generate_workspace_finance_package`` —— 宿主固定编排：prepare → run → render

设计原则（对齐《财务模型与13表上下级关系及AI调用流程方案_20260712》）：
- 模型是数值真源，run 是版本真源，13 表是交付视图
- 相同 input/spec/model/template 幂等复用 run
- GET 路径不得隐式写审计库；写副作用只在显式 run 命令里

Wave 3.3 门面：实现搬到 ``_run_service/`` 子模块 —— ``base``（版本常量、十三表
交付键与元信息、hash/幂等键原语、manifest 解析）、``spec_prepare``（联动成本项
注入与 spec 准备）、``run_model``（确定性运行事务）、``render``（十三表渲染）、
``query``（run 读取）与 ``package``（整包编排）。

上面五个阶段函数的调用关系与事务边界不变：``package`` 仍按 prepare → run →
render 顺序编排，幂等键仍由 ``base.compute_idempotency_key`` 单点计算。

注意 ``tables_application`` 在**顶层** ``from ... import`` 绑定了
``get_workspace_finance_run`` 与 ``render_workspace_finance_tables``，因此测试里
``patch("...run_service.render_workspace_finance_tables")`` 影响不到那份绑定。
这是拆分前既有行为，此处照原样保留，未做“顺手修正”。
"""

from __future__ import annotations

import copy  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
from datetime import date  # noqa: F401
from typing import Any, Optional  # noqa: F401

from lvke_mcp.domains.finance.industry_registry import select_industry_profile  # noqa: F401
from lvke_mcp.domains.finance.model_manifest import (  # noqa: F401
    ModelManifest,
    build_manifest,
    manifest_from_dict,
)
from lvke_mcp.domains.finance.policy_registry import select_policy_profile  # noqa: F401

from ._run_service.base import (  # noqa: F401
    DELIVERY_TABLE_KEYS,
    DELIVERY_TABLE_META,
    DELIVERY_TABLE_SCHEMA_VERSION,
    ENGINE_DELIVERY_COUNT,
    REFERENCE_SOURCE_SHEET_COUNT,
    REVIEW_WORKBOOK_SHEET_COUNT,
    MODEL_VERSION,
    TEMPLATE_VERSION,
    _ensure_workspace,
    _markdown_table_row_count,
    _project_brief,
    _read_workspace_req,
    _resolve_valuation_date_for_mode,
    _sha256_hex,
    _stable_json,
    _table_manifest,
    compute_idempotency_key,
    compute_input_hash,
    compute_spec_hash,
    compute_table_bundle_hash,
    delivery_count_semantics,
    delivery_table_contract,
    delivery_table_contract_hash,
    resolve_model_manifest,
)
from ._run_service.package import generate_workspace_finance_package  # noqa: F401
from ._run_service.query import get_workspace_finance_run  # noqa: F401
from ._run_service.render import render_workspace_finance_tables  # noqa: F401
from ._run_service.run_model import run_workspace_finance_model  # noqa: F401
from ._run_service.spec_prepare import (  # noqa: F401
    _inject_linked_cost_items,
    _nonnegative_cost_issues,
    prepare_workspace_finance_spec,
)
