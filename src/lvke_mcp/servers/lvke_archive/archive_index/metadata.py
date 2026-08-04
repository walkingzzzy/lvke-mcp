"""Stage 2 · 元数据抽取（规则优先，不调 LLM）。

实测 1847 份文件，超过 90% 通过文件名 + 目录名 + MD 头部即可识别行业、年份、
报告类型。Auxiliary-LLM 兜底属 Phase A.1 的 P1 任务，当前 stage 留 hook 不强依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# 顶层行业关键词 → 标准行业标签
INDUSTRY_KEYWORDS: list[tuple[str, str]] = [
    ("光伏", "新能源-光伏"),
    ("风电", "新能源-风电"),
    ("储能", "新能源-储能"),
    ("氢能", "新能源-氢能"),
    ("锂电", "化工新材料-锂电"),
    ("电池", "化工新材料-锂电"),
    ("半导体", "电子-半导体"),
    ("芯片", "电子-半导体"),
    ("集成电路", "电子-半导体"),
    ("农村", "农林牧渔"),
    ("农业", "农林牧渔"),
    ("农产品", "农林牧渔"),
    ("种植", "农林牧渔"),
    ("养殖", "农林牧渔"),
    ("水利", "水利工程"),
    ("污水", "市政-环保"),
    ("供水", "市政-供水"),
    ("管网", "市政-管网"),
    ("道路", "路桥工程"),
    ("公路", "路桥工程"),
    ("市政", "市政-综合"),
    ("桥梁", "路桥工程"),
    ("医院", "医疗卫生"),
    ("康养", "医疗卫生"),
    ("中医院", "医疗卫生"),
    ("学校", "教育"),
    ("学院", "教育"),
    ("职业技术", "教育"),
    ("学生宿舍", "教育"),
    ("幼儿园", "教育"),
    ("旅游", "文旅"),
    ("文旅", "文旅"),
    ("产业园", "园区-综合"),
    ("产业基地", "园区-综合"),
    ("产教融合", "教育"),
    ("低空经济", "新兴产业-低空"),
    ("仓储", "物流仓储"),
    ("冷链", "物流仓储"),
    ("物流", "物流仓储"),
    ("农资", "物流仓储"),
    ("酒店", "文旅"),
    ("住宅", "房产建筑"),
    ("小区", "房产建筑"),
    ("安置", "房产建筑"),
    ("保障房", "房产建筑"),
    ("食品", "食品烟酒"),
    ("饮料", "食品烟酒"),
    ("烟酒", "食品烟酒"),
    ("化工", "化工新材料"),
    ("新材料", "化工新材料"),
    ("机械", "机械装备"),
    ("设备", "机械装备"),
    ("装备", "机械装备"),
    ("汽车", "汽车制造"),
    ("数码", "电子-消费"),
    ("家电", "电子-消费"),
    ("通讯", "通信"),
    ("IT", "通信"),
    ("广告", "传媒"),
    ("电子商务", "互联网"),
]

REPORT_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("可行性研究报告", "可研"),
    ("可研报告", "可研"),
    ("可研", "可研"),
    ("申请报告", "申请报告"),
    ("核准报告", "申请报告"),
    ("资金申请", "资金申请"),
    ("实施方案", "实施方案"),
    ("建议书", "建议书"),
]

# 项目性质
PROJECT_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("新建", "新建"),
    ("改扩建", "改扩建"),
    ("改造", "改建"),
    ("扩建", "扩建"),
    ("续建", "续建"),
]

# 区域识别（湖北重点）
REGION_KEYWORDS: list[tuple[str, str]] = [
    ("湖北省", "湖北省"),
    ("武汉", "湖北省武汉市"),
    ("黄石", "湖北省黄石市"),
    ("十堰", "湖北省十堰市"),
    ("宜昌", "湖北省宜昌市"),
    ("襄阳", "湖北省襄阳市"),
    ("鄂州", "湖北省鄂州市"),
    ("荆门", "湖北省荆门市"),
    ("孝感", "湖北省孝感市"),
    ("荆州", "湖北省荆州市"),
    ("黄冈", "湖北省黄冈市"),
    ("咸宁", "湖北省咸宁市"),
    ("随州", "湖北省随州市"),
    ("恩施", "湖北省恩施州"),
    ("利川", "湖北省利川市"),
    ("赤壁", "湖北省赤壁市"),
    ("崇阳", "湖北省崇阳县"),
    ("通山", "湖北省通山县"),
    ("仙桃", "湖北省仙桃市"),
    ("潜江", "湖北省潜江市"),
    ("天门", "湖北省天门市"),
    ("神农架", "湖北省神农架林区"),
]

YEAR_RE = re.compile(r"(?:^|[^0-9])(20\d{2})(?:[^0-9]|$)")


@dataclass(slots=True)
class ReportMeta:
    report_id: str
    title: str
    corpus_origin: str
    industry: str
    project_type: str
    report_type: str
    year: int | None
    region: str
    source_path: str
    char_len: int
    quality_flag: str
    brief: str


def _first_match(text: str, table: Iterable[tuple[str, str]]) -> str:
    for kw, label in table:
        if kw in text:
            return label
    return ""


def _read_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return fallback


def extract(
    *,
    report_id: str,
    corpus_origin: str,
    relative_path: str,
    full_text: str,
    haystack_hint: str,
) -> ReportMeta:
    """Run heuristic metadata extraction over a single MD file.

    ``haystack_hint`` 通常是相对路径 + 文件名 + 文档前 2000 字的拼接，给关键词匹配。
    """
    char_len = len(full_text)
    title = _read_title(full_text, fallback=Path(relative_path).stem)

    hay = haystack_hint

    industry = _first_match(hay, INDUSTRY_KEYWORDS) or "其他"
    report_type = _first_match(hay, REPORT_TYPE_KEYWORDS) or "可研"
    project_type = _first_match(hay, PROJECT_TYPE_KEYWORDS) or "新建"
    region = _first_match(hay, REGION_KEYWORDS) or ""

    year: int | None = None
    m = YEAR_RE.search(hay)
    if m:
        try:
            y = int(m.group(1))
            if 2000 <= y <= 2030:
                year = y
        except ValueError:
            pass

    quality_flag = "ok"
    if char_len < 800:
        quality_flag = "low_text"

    brief = title
    body_lines = [
        ln.strip()
        for ln in full_text.splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    if body_lines:
        brief = (title + " · " + body_lines[0])[:300]

    return ReportMeta(
        report_id=report_id,
        title=title,
        corpus_origin=corpus_origin,
        industry=industry,
        project_type=project_type,
        report_type=report_type,
        year=year,
        region=region,
        source_path=relative_path,
        char_len=char_len,
        quality_flag=quality_flag,
        brief=brief,
    )
