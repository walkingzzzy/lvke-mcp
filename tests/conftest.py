"""Test-wide isolation of the Lvke data root.

`LVKE_MCP_DATA_DIR` 未设置时，``workspace_root()`` 落到 ``~/.lvke``——即用户
真实的工作区与交付库。曾有 24 个测试文件不自设该变量，跑一次 pytest 就会在
真实数据根里留下 ``ndrc-run-*``、``planning-gateless-*`` 等目录：既污染用户
交付物，也让测试之间能互相看见残留状态（上一个用例建的对象被下一个用例读到，
测试因此"意外通过"）。

这里在整个测试会话开始前把默认值指向一个临时目录，会话结束后删除。已经自设
``LVKE_MCP_DATA_DIR`` 的测试不受影响：它们在 setUp 里覆盖本值，tearDown 再恢复
成本模块设的临时目录，而不是恢复成"未设置"。

刻意**只**设 ``LVKE_MCP_DATA_DIR``，不设 ``LVKE_DELIVERABLE_DIR``：
``deliverable_root()`` 的既有设计是"未显式指定交付根时，若数据根已被改到非默认
目录，交付根就挂到该数据根下的 ``lvke产出/``"。这条联动正是测试隔离依赖的行为——
测试在 setUp 里改数据根，交付物就跟着走。若在这里把交付根也钉死，
联动被切断，测试自己设的数据根与交付输出会落到两棵不相干的树上。
"""

from __future__ import annotations

import os
import tempfile

import pytest


_ISOLATED_ROOTS = ("LVKE_MCP_DATA_DIR",)


@pytest.fixture(scope="session", autouse=True)
def isolate_lvke_roots() -> object:
    """Point every unset Lvke storage root at a throwaway directory."""

    directories: list[tempfile.TemporaryDirectory[str]] = []
    previous: dict[str, str | None] = {}
    for name in _ISOLATED_ROOTS:
        previous[name] = os.environ.get(name)
        if previous[name]:
            # 调用方（例如 CI 或开发者）显式指定了根目录就尊重它，
            # 不要偷偷改到临时目录——那会让"我指定的目录里为什么没东西"变成谜。
            continue
        holder = tempfile.TemporaryDirectory(prefix=f"lvke-tests-{name.lower()}-")
        directories.append(holder)
        os.environ[name] = holder.name
    try:
        yield None
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for holder in directories:
            holder.cleanup()
