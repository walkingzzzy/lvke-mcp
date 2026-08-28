"""Isolated LibreOffice (soffice) worker for headless conversions.

The GUI ``LibreOffice.app`` on macOS aborts with SIGABRT when Python inside
Cursor launches ``--headless --convert-to`` against the default
UserInstallation, especially when another soffice is already running. Every
caller must use a private profile and serialize conversions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from filelock import FileLock, Timeout as FileLockTimeout

HEADLESS_FLAGS: tuple[str, ...] = (
    "--headless",
    "--nologo",
    "--norestore",
    "--nodefault",
    "--nofirststartwizard",
)

_DEFAULT_ENV_NAMES: tuple[str, ...] = (
    "LVKE_REVIEW_SOFFICE",
    "SOFFICE",
    "LIBREOFFICE",
)


def resolve_soffice_binary(*env_names: str) -> str | None:
    """Return the first configured or PATH-visible soffice binary."""
    names = env_names or _DEFAULT_ENV_NAMES
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return shutil.which("soffice") or shutil.which("libreoffice")


def _lock_path() -> Path:
    return Path(tempfile.gettempdir()) / "lvke-soffice.convert.lock"


def _worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OOO_DISABLE_RECOVERY"] = "1"
    env["SAL_NO_UPDATE_CHECK"] = "1"
    # svp is the Linux headless VCL plugin. Forcing it on the macOS .app build
    # is a common cause of abort(), so only set it off Darwin.
    if sys.platform != "darwin":
        env.setdefault("SAL_USE_VCLPLUGIN", "svp")
    return env


def _with_lock(timeout: float) -> FileLock:
    return FileLock(str(_lock_path()), timeout=max(1.0, timeout))


def soffice_version(binary: str, *, timeout: float = 15) -> str:
    """Return ``soffice --version`` text, serialized with other workers."""
    cmd = [binary, "--version"]
    try:
        with _with_lock(timeout):
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_worker_env(),
            )
    except FileLockTimeout as exc:
        raise subprocess.TimeoutExpired(cmd, timeout) from exc
    return (result.stdout or result.stderr or "").strip()


def run_soffice_convert(
    *,
    source: Path | str,
    convert_to: str,
    outdir: Path | str,
    binary: str | None = None,
    extra_args: Sequence[str] = (),
    timeout: float = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Convert ``source`` with an isolated UserInstallation.

    Concurrent callers wait on a process lock so two MCP tools cannot start
    soffice against overlapping macOS named pipes at the same time.
    """
    worker = binary or resolve_soffice_binary()
    if not worker:
        raise FileNotFoundError("soffice/LibreOffice 不可用")
    source_path = Path(source)
    output_dir = Path(outdir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = []
    try:
        with _with_lock(timeout), tempfile.TemporaryDirectory(prefix="lvke-soffice-profile-") as profile:
            profile_uri = Path(profile).resolve().as_uri()
            cmd = [
                worker,
                *HEADLESS_FLAGS,
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                convert_to,
                "--outdir",
                str(output_dir),
                str(source_path),
                *extra_args,
            ]
            return subprocess.run(
                cmd,
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_worker_env(),
            )
    except FileLockTimeout as exc:
        raise subprocess.TimeoutExpired(cmd or [worker, "--convert-to", convert_to], timeout) from exc
