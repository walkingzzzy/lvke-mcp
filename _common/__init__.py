"""绿科 MCP 服务集合的共用工具。

模块：
- ``responses``：统一响应包装（ok / err）。
- ``logging``：stderr 日志（避免污染 stdio 协议）。
- ``official_server``：官方 SDK 之上的严格 stdio runtime（七个正式 Server 共用）。
- ``errors``：标准 JSON-RPC 错误码与脱敏错误构造。
- ``schemas``：方案 5.4 公共输出 envelope 的 JSON Schema 构造器。
- ``resources``：``lvke://`` Resource URI 构造/解析与描述 helper。
- ``protocol_testkit``：七 Server 协议合规测试的子进程驱动 helper。
- ``stdio_server``：可选 fallback 实现，当 ``mcp`` SDK 不可用时也能提供
  最小化 JSON-RPC over stdio 能力以便单测与本地调试。
"""

from .responses import err, ok  # noqa: F401
from .logging import get_logger  # noqa: F401
