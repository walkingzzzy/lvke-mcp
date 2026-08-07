"""lvke-deep-research 工具的 ToolAnnotations 实例。"""

from __future__ import annotations

from mcp import types

# annotations 如实声明副作用：
# - 只读：dr_prepare（纯计算）、dr_status/dr_get_report/dr_get_evidence（只读任务产物）
# - 写入：dr_start（建立 Agent 研究会话）、dr_submit/dr_get_bundle（固化研究包）
# - 破坏性：dr_cancel（中止后原任务不可恢复运行）
_read_only = types.ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_agent_write = types.ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_cancel = types.ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
_bundle_write = types.ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
