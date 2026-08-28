#!/usr/bin/env python3
"""CLI for split release preflight gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvke_mcp.runtime.build_metadata import build_metadata  # noqa: E402
from lvke_mcp.runtime.release_preflight import run_release_preflight  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts",
        nargs="*",
        default=[],
        help="Required artifact paths",
    )
    parser.add_argument("--zip", default="", help="Internal release ZIP path")
    parser.add_argument("--word-manifest", default="", help="Word conversion manifest JSON")
    parser.add_argument("--word-root", default="", help="Word output root directory")
    parser.add_argument(
        "--evd-json",
        default="",
        help='EVD distribution JSON, e.g. {"EVD-0":0,"EVD-1":0,"EVD-2":5}',
    )
    parser.add_argument("--sim-a", action="store_true", help="Unpromoted SIM-A materials present")
    parser.add_argument(
        "--sim-a-formal",
        action="store_true",
        help="Promoted sim_a_formal materials count as formal EVD-2",
    )
    parser.add_argument(
        "--required-evd2",
        type=int,
        default=None,
        help="Required EVD-2 count; default is P0 total from --evd-json",
    )
    parser.add_argument("--formal-evidence", default="", help="Formal evidence summary")
    parser.add_argument(
        "--calculation-ok",
        action="store_true",
        help="显式声明独立计算校验已通过；不再用构建元数据冒充 calculation_gate",
    )
    parser.add_argument(
        "--formal-candidate",
        action="store_true",
        help="正式候选模式：必须提供 artifact gate 检查项",
    )
    args = parser.parse_args()

    meta = build_metadata()

    def calculation_checks() -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        if args.calculation_ok:
            passed.append("independent calculation checks supplied")
        else:
            failed.append("independent calculation checks not supplied")
        return passed, failed

    evd = json.loads(args.evd_json) if args.evd_json else None
    report = run_release_preflight(
        calculation_checks=calculation_checks,
        required_artifacts=[Path(item) for item in args.artifacts],
        zip_path=Path(args.zip) if args.zip else None,
        word_manifest=Path(args.word_manifest) if args.word_manifest else None,
        word_root=Path(args.word_root) if args.word_root else None,
        evd_distribution=evd,
        required_evd2_count=args.required_evd2,
        sim_a_present=args.sim_a,
        sim_a_formal=args.sim_a_formal,
        build_metadata_complete=meta.complete,
        metadata_matches_commit=meta.complete,
        formal_evidence=args.formal_evidence,
        require_artifact_checks=args.formal_candidate,
    )
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("release_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
