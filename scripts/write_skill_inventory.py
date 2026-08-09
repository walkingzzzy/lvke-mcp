#!/usr/bin/env python3
"""Write the published Skill inventory consumed at runtime for route gating.

在构建或插件安装时运行。写出的 ``skill_inventory.json`` 声明"与这份代码一同发布的
Skill 集合"，`planning_resolve_industry_skill` 据此判定路由命中的 Skill 是否真的
可加载。

不写这份清单会怎样：门禁只剩环境变量一条路，而没有任何宿主会去手写它，于是该工具
对所有真实调用恒定返回 ``skill_loadability_unverified``——把一个假阳性换成了恒定
降级。因此清单由构建器生成，与实际发布的 Skill 目录同源，不靠人工维护。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvke_mcp.runtime.build_metadata import utc_now_iso  # noqa: E402
from lvke_mcp.runtime.skill_inventory import SKILL_INVENTORY_FILENAME  # noqa: E402

# 只写包内一份。build_metadata 写两份是因为它两份都不入库，需要仓库根那份支撑
# 源码树运行；而本清单入库且包内路径在 wheel 与源码树布局下都可读，再写一份仓库
# 根副本只会成为多余回退——它会在包内那份缺失时悄悄顶上，让守门测试失去意义。
TARGETS = (ROOT / "src" / "lvke_mcp" / "runtime" / SKILL_INVENTORY_FILENAME,)


def published_skill_names(skills_root: Path) -> list[str]:
    """Return sorted names of directories that expose a readable SKILL.md.

    没有 SKILL.md 的目录无法被加载，即使名字存在也不计入——否则清单会声明一个
    加载不了的名字，正是它要防的那种幻影。
    """

    try:
        entries = sorted(skills_root.iterdir())
    except OSError:
        return []
    return sorted(
        entry.name for entry in entries if (entry / "SKILL.md").is_file()
    )


def write_skill_inventory(skills_root: Path | None = None) -> dict[str, object]:
    """Resolve and persist the published Skill inventory; return the payload."""

    root = skills_root or (ROOT / "plugins" / "lvke-mcp" / "skills")
    names = published_skill_names(root)
    if not names:
        raise RuntimeError(
            f"skill_inventory_empty: {root} 下没有可加载的 Skill；"
            "不写出空清单，否则运行时会把一切路由判成 Skill 缺失"
        )
    payload: dict[str, object] = {
        "schema_version": "skill-inventory.v1",
        "generated_at": utc_now_iso(),
        "source_root": str(root.relative_to(ROOT)),
        "skills": names,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-root",
        default="",
        help="Skill 目录根（默认 plugins/lvke-mcp/skills）",
    )
    args = parser.parse_args()

    try:
        payload = write_skill_inventory(
            Path(args.skills_root).resolve() if args.skills_root else None
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    skills = payload["skills"]
    assert isinstance(skills, list)
    print(f"skills={len(skills)}: {', '.join(skills)}")
    print("written: " + ", ".join(str(path.relative_to(ROOT)) for path in TARGETS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
