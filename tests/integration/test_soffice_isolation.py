from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from lvke_mcp.runtime.soffice import HEADLESS_FLAGS, resolve_soffice_binary, run_soffice_convert


def test_resolve_soffice_binary_prefers_named_env(monkeypatch, tmp_path: Path) -> None:
    binary = tmp_path / "soffice"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("SOFFICE", str(binary))
    monkeypatch.delenv("LIBREOFFICE", raising=False)
    monkeypatch.delenv("LVKE_REVIEW_SOFFICE", raising=False)
    assert resolve_soffice_binary("SOFFICE", "LIBREOFFICE") == str(binary)


def test_run_soffice_convert_uses_isolated_profile_and_serializes(
    monkeypatch, tmp_path: Path,
) -> None:
    source = tmp_path / "input.xlsx"
    source.write_bytes(b"xlsx")
    outdir = tmp_path / "out"
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("lvke_mcp.runtime.soffice.subprocess.run", fake_run)

    result = run_soffice_convert(
        source=source,
        convert_to="xlsx",
        outdir=outdir,
        binary="/Applications/LibreOffice.app/Contents/MacOS/soffice",
        timeout=12,
        check=True,
    )

    assert result.returncode == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0].endswith("soffice")
    for flag in HEADLESS_FLAGS:
        assert flag in cmd
    env_flag = next(item for item in cmd if item.startswith("-env:UserInstallation="))
    assert "lvke-soffice-profile-" in env_flag
    assert str(source) in cmd
    assert str(outdir) in cmd
    assert captured["env"]["OOO_DISABLE_RECOVERY"] == "1"
