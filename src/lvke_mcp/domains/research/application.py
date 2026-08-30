"""研究准备、续研（lineage 级）与不可变研究包投影。

注意 checkpoint 的真实落点：引擎把 checkpoint 写在
``task_dir/checkpoint.json``（见 ``research_engine.task_service`` 的
``_checkpoint_path``），而 ``artifacts/`` 落盘清单从不包含
``checkpoint.json``。因此 ``load_artifact('checkpoint')`` 恒为 None，
历史版本据此生成的 checkpoint Resource URI 是死链——本模块统一经
:func:`load_checkpoint` 回退读取真实落点，读不到就诚实省略。

实现已按职责拆入 ``_service/``：基座、Agent 生命周期、引擎控制、
计划读写、事件游标、断点续研、研究包投影。本模块只做 re-export。
"""

from __future__ import annotations

# 下列导入构成本模块被 api_snapshot 冻结的模块属性集合（``package_service``
# 经 ``import *`` 转发），因此保留原样，不得按“未使用”剪除。
import base64
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

from lvke_mcp.adapters.research_repository import (
    AGENT_SESSION_STORE,
    AGENT_TRANSITION_STORE,
    CHECKPOINT_STORE,
    EVENT_STORE,
    IDEMPOTENCY_STORE,
    PACKAGE_STORE,
    PLAN_PROPOSAL_STORE,
    PLAN_STORE,
    QUALITY_REVIEW_STORE,
)
from lvke_mcp.runtime.storage import canonical_json, sha256_json

from ._service.agent_lifecycle import (
    agent_status,
    cancel_agent,
    confirm_quality,
    start_agent,
    submit_agent,
)
from ._service.checkpoints import (
    create_checkpoint,
    load_checkpoint,
    resume_from_checkpoint,
)
from ._service.engine_control import continue_task, prepare
from ._service.events import list_events
from ._service.planning import (
    add_sources,
    apply_plan_revision,
    get_plan,
    propose_plan_revision,
    remove_sources,
)
from ._service.resources import bundle, list_resources, resolve_resource

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "AGENT_SESSION_STORE",
    "AGENT_TRANSITION_STORE",
    "Any",
    "CHECKPOINT_STORE",
    "EVENT_STORE",
    "EVIDENCE_STORE",
    "FileLock",
    "IDEMPOTENCY_STORE",
    "PACKAGE_STORE",
    "PLAN_PROPOSAL_STORE",
    "PLAN_STORE",
    "QUALITY_REVIEW_STORE",
    "add_sources",
    "agent_status",
    "apply_plan_revision",
    "base64",
    "bundle",
    "cancel_agent",
    "canonical_json",
    "confirm_quality",
    "contextmanager",
    "continue_task",
    "create_checkpoint",
    "datetime",
    "get_plan",
    "hashlib",
    "hmac",
    "json",
    "list_events",
    "list_resources",
    "load_checkpoint",
    "os",
    "prepare",
    "propose_plan_revision",
    "remove_sources",
    "resolve_resource",
    "resume_from_checkpoint",
    "sha256_json",
    "start_agent",
    "submit_agent",
    "timedelta",
    "timezone",
    "workspace_root",
]
