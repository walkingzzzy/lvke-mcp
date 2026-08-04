"""Safety scanning for untrusted research sources and generated artifacts."""

from __future__ import annotations

import os
import re
from typing import Any


_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(
        r"(?:disregard|override|bypass)\s+(?:all\s+)?(?:previous|prior|system|developer)?\s*"
        r"(?:instructions?|prompts?|rules?)",
        re.IGNORECASE,
    ),
    re.compile(r"system\s*(?:message|prompt)|developer\s*message", re.IGNORECASE),
    re.compile(r"call\s+(?:the\s+)?tool|execute\s+(?:this\s+)?command", re.IGNORECASE),
    re.compile(r"upload|exfiltrate|send\s+(?:the\s+)?(?:secret|file|credential)", re.IGNORECASE),
    re.compile(
        r"(?:reveal|show|print|return)\s+(?:the\s+)?(?:environment\s+variables?|secrets?|credentials?|api\s*keys?)",
        re.IGNORECASE,
    ),
    re.compile(r"忽略(?:此前|之前|以上|所有).{0,12}(?:指令|要求|规则)"),
    re.compile(r"(?:无视|绕过|跳过|覆盖).{0,12}(?:系统|开发者|之前|以上).{0,12}(?:指令|要求|规则|提示词)"),
    re.compile(r"系统(?:消息|提示词)|开发者(?:消息|指令)"),
    re.compile(r"调用.{0,12}工具|执行.{0,12}命令|上传.{0,12}(?:文件|密钥|数据)"),
    re.compile(r"(?:泄露|显示|输出|返回).{0,12}(?:密钥|凭据|口令|环境变量|API\s*密钥)", re.IGNORECASE),
)

_FINANCE_REDLINE_PATTERNS = (
    re.compile(r"\b(?:F?IRR|F?NPV|DSCR|ICR)\b", re.IGNORECASE),
    re.compile(
        r"财务内部收益率|内部收益率|财务净现值|净现值|"
        r"投资回收期|回收期测算|项目现金流|现金流量?测算|"
        r"折现现金流|项目总投资|投资估算|建设投资|"
        r"项目资本金收益率|资本金收益率|偿债备付率|"
        r"利息备付率|借款偿还期"
    ),
)

_SENSITIVE_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("api_key", re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b")),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
            re.IGNORECASE,
        ),
    ),
)

_AUTHORIZATION_PATTERN = re.compile(
    r"\bAuthorization\s*[:=]\s*(?:Bearer|Basic)?\s*[^\s,;]+",
    re.IGNORECASE,
)
_SECRET_NAME_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY", "_PASSWORD")


def scan_untrusted_text(value: str) -> list[dict[str, Any]]:
    text = str(value or "")
    findings: list[dict[str, Any]] = []
    for pattern in _INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                {
                    "code": "prompt_injection",
                    "severity": "high",
                    "start": match.start(),
                    "end": match.end(),
                    "excerpt": match.group(0)[:120],
                }
            )
    for code, pattern in _SENSITIVE_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                {
                    "code": code,
                    "severity": "critical",
                    "start": match.start(),
                    "end": match.end(),
                    "excerpt": "[REDACTED]",
                }
            )
    return sorted(findings, key=lambda item: (int(item["start"]), str(item["code"])))


def finance_redline_terms(value: str) -> list[str]:
    """Return deterministic project-finance terms that DR must not calculate.

    Public market prices and industry costs remain usable evidence.  These
    terms represent project-specific investment or financial-evaluation work
    that belongs to the governed finance workbench.
    """

    text = str(value or "")
    matches = [
        match.group(0)
        for pattern in _FINANCE_REDLINE_PATTERNS
        for match in pattern.finditer(text)
    ]
    return list(dict.fromkeys(matches))


def sanitize_untrusted_text(value: str) -> tuple[str, list[dict[str, Any]]]:
    """Remove instruction-like and credential-bearing lines before evidence use."""

    text = str(value or "")
    findings = scan_untrusted_text(text)
    if not findings:
        return text, []
    unsafe_lines: set[int] = set()
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        offsets.append((cursor, cursor + len(line)))
        cursor += len(line)
        if scan_untrusted_text(line):
            unsafe_lines.add(index)
    safe_lines = [
        line
        for index, line in enumerate(text.splitlines())
        if index not in unsafe_lines
    ]
    return "\n".join(safe_lines).strip(), findings


def redact_sensitive_text(value: str) -> tuple[str, list[dict[str, Any]]]:
    text = str(value or "")
    findings = scan_untrusted_text(text)
    sensitive = [item for item in findings if item["code"] != "prompt_injection"]
    for _, pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if _AUTHORIZATION_PATTERN.search(text):
        sensitive.append(
            {
                "code": "authorization_header",
                "severity": "critical",
                "start": 0,
                "end": 0,
                "excerpt": "[REDACTED]",
            }
        )
        text = _AUTHORIZATION_PATTERN.sub("Authorization: [REDACTED]", text)
    for name, secret in os.environ.items():
        if not name.upper().endswith(_SECRET_NAME_SUFFIXES):
            continue
        if len(secret) < 8 or secret not in text:
            continue
        text = text.replace(secret, "[REDACTED]")
        sensitive.append(
            {
                "code": "runtime_secret",
                "severity": "critical",
                "start": 0,
                "end": 0,
                "excerpt": "[REDACTED]",
            }
        )
    return text, sensitive


def redact_sensitive_value(value: Any) -> Any:
    """Recursively redact credentials before research state is persisted."""

    runtime_secrets = tuple(
        secret
        for name, secret in os.environ.items()
        if name.upper().endswith(_SECRET_NAME_SUFFIXES) and len(secret) >= 8
    )

    def redact_text(text: str) -> str:
        for _, pattern in _SENSITIVE_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        text = _AUTHORIZATION_PATTERN.sub("Authorization: [REDACTED]", text)
        for secret in runtime_secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return redact_text(item)
        if isinstance(item, dict):
            return {str(key): walk(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(nested) for nested in item]
        return item

    return walk(value)
