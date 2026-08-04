# -*- coding: utf-8 -*-
"""C 层：受限执行沙箱（BC 方案 §6，仅 spec 表达不了的定制计算才用）。

定位（务必读方案 §1.3 / §6）：
- **默认关闭**：须显式 ``FINANCE_SANDBOX=1`` 且 ``spec.custom`` 非空才启用。评审级
  交付默认只走 B 层（LLM 定规范 + 引擎执行），C 层是"spec 表达不了的定制项"的补充。
- **算术护栏不变**：C 层结果必须回灌引擎并过与 B 相同的 6 条勾稽；任一勾稽不过或
  sandbox 抛错，则**丢弃该定制项、回退 B 层结果**（由调用方 finance_model 钩子执行）。
- LLM 生成的定制片段永远碰不到 IRR/NPV 的求解本身（那仍在 finance_calc 纯函数）。

安全设计（多层）：
1. **静态禁字**：import / open / exec / eval / __ / os. / sys. / subprocess / socket 等
   先扫描，命中即拒。
2. **白名单命名空间**：``__builtins__`` 只注入安全数学函数，无 import / open / eval。
3. **子进程 + 超时**：用 multiprocessing 隔离执行，超时 terminate（防死循环/资源耗尽）。
   worker 为模块级函数（Windows spawn 需可 pickle），通过 Queue 回传结果。

用法：片段须定义 ``def compute(inputs) -> result``，result 须为可 JSON 序列化的
基础类型（数值/列表/字典），供引擎回灌与勾稽。
"""

from __future__ import annotations

import multiprocessing
import os
from typing import Any

# 白名单：只暴露安全数学函数，无 import / open / eval / __import__ / 文件网络 IO。
_SAFE_BUILTINS = {
    "min": min, "max": max, "abs": abs, "round": round, "sum": sum, "len": len,
    "range": range, "float": float, "int": int, "bool": bool, "list": list,
    "dict": dict, "tuple": tuple, "enumerate": enumerate, "zip": zip,
    "sorted": sorted, "map": map, "filter": filter, "pow": pow, "divmod": divmod,
}

# 静态禁字：命中任一即拒执行（先于 compile，防危险符号进受限命名空间）。
_BANNED = (
    "import", "open(", "exec(", "eval(", "compile(", "__", "os.", "sys.",
    "subprocess", "socket", "globals(", "locals(", "getattr(", "setattr(",
    "delattr(", "vars(", "input(", "breakpoint(",
)


class SandboxError(Exception):
    """沙箱拒绝执行或执行失败（静态禁字/未定义 compute/超时/运行异常）。"""


def enabled() -> bool:
    """C 层默认关闭；须显式 ``FINANCE_SANDBOX=1`` 打开（评审级默认只走 B 层）。"""
    return os.environ.get("FINANCE_SANDBOX", "").strip().lower() in ("1", "true", "yes")


def _static_scan(code: str) -> None:
    """静态禁字扫描；命中即抛 SandboxError（在编译/执行之前）。"""
    low = str(code or "").lower()
    for b in _BANNED:
        if b in low:
            raise SandboxError(f"片段含禁用符号 {b!r}")


def _worker(code: str, inputs: dict[str, Any], q: "multiprocessing.Queue") -> None:
    """子进程执行体（模块级，Windows spawn 可 pickle）。结果/错误经 Queue 回传。

    受限命名空间：__builtins__ 只含白名单；片段须定义 compute(inputs)->result。
    """
    try:
        ns: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
        exec(compile(code, "<finance_custom>", "exec"), ns)  # noqa: S102 - 受限命名空间
        fn = ns.get("compute")
        if not callable(fn):
            q.put(("err", "片段未定义 compute(inputs)"))
            return
        result = fn(dict(inputs))
        q.put(("ok", result))
    except Exception as exc:  # noqa: BLE001 - 任何执行异常经 Queue 回传，不崩子进程
        q.put(("err", str(exc)[:200]))


def run_restricted(code: str, inputs: dict[str, Any], *, timeout_s: float = 2.0) -> Any:
    """在受限子进程执行 LLM 定制片段。片段须定义 ``def compute(inputs) -> result``。

    护栏：静态禁字 → 白名单命名空间 → 子进程 + 超时 kill。
    超时/未定义 compute/执行异常/禁字命中均抛 SandboxError，供调用方回退 B 层。
    """
    if not enabled():
        raise SandboxError("沙箱未启用（须 FINANCE_SANDBOX=1）")
    _static_scan(code)

    ctx = multiprocessing.get_context("spawn")  # 跨平台一致（Windows 仅支持 spawn）
    q: "multiprocessing.Queue" = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(code, dict(inputs), q), daemon=True)
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(1.0)
        raise SandboxError(f"片段执行超时（>{timeout_s}s），已终止")
    try:
        status, payload = q.get_nowait()
    except Exception as exc:  # noqa: BLE001 - 队列空=子进程异常退出
        raise SandboxError(f"片段无返回（子进程异常退出，exitcode={proc.exitcode}）") from exc
    if status == "ok":
        return payload
    raise SandboxError(str(payload))


def apply_custom_calcs(spec: dict[str, Any], base_inputs: dict[str, Any]) -> dict[str, Any]:
    """对 ``spec.custom`` 逐条跑受限执行，返回 {target: result}（默认关时返回空）。

    仅当 ``enabled()`` 且 ``spec.custom`` 非空才执行；任一片段失败静默跳过该项
    （不阻断，调用方对成功项再过勾稽后决定是否采纳）。
    """
    out: dict[str, Any] = {}
    if not enabled() or not isinstance(spec, dict):
        return out
    for c in (spec.get("custom") or []):
        if not isinstance(c, dict):
            continue
        target = str(c.get("target") or "").strip()
        code = c.get("code") or ""
        if not target or not code:
            continue
        try:
            out[target] = {
                "value": run_restricted(code, base_inputs),
                "reason": str(c.get("reason") or ""),
            }
        except SandboxError as exc:
            out[target] = {"error": str(exc), "reason": str(c.get("reason") or "")}
    return out
