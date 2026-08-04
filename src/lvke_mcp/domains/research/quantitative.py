"""Deterministic quantitative semantics shared by evidence and report audits.

Numeric equality alone is not evidence support.  A report sentence about
Hunan vehicle sales must not be accepted merely because a cited Hubei parts
production source contains the same year and number.  This module therefore
keeps the lightweight token API used by the existing graph while also
extracting the semantic dimensions needed for claim merging and citation
support: region, period, measure/value, unit, indicator, subject and whether
the statement is an observed fact, plan target, forecast or company claim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_DIGIT = r"[0-9\uff10-\uff19]"
_ARABIC_NUMBER = rf"(?:{_DIGIT}{{1,3}}(?:[,\uff0c]{_DIGIT}{{3}})+|{_DIGIT}+)(?:[.\uff0e]{_DIGIT}+)?"
_CHINESE_NUMBER = r"[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07]+"
# Exact Chinese magnitude words before ``亿`` are also unambiguous.  The
# negative boundary rejects fuzzy compounds such as ``数万亿`` rather than
# silently converting them into one exact threshold.
_CHINESE_MAGNITUDE_NUMBER = (
    r"(?<![\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d"
    r"\u5341\u767e\u5343\u4e07\u6570])(?:\u767e|\u5343|\u4e07)(?=\s*\u4ebf)"
)
# Other Chinese numerals remain quantitative only for an unambiguous
# percentage-like construction such as “四成”.  Treating every “两个/三家” in
# report-method prose as a measured business claim creates false failures.
_NUMBER = (
    rf"(?:{_ARABIC_NUMBER}|{_CHINESE_MAGNITUDE_NUMBER}|"
    rf"{_CHINESE_NUMBER}(?=\s*\u6210))"
)
# Long energy units must precede, and be disjoint from, their shorter power
# counterparts. This prevents ``10GWh`` from degrading to ``10GW`` if the
# unit alternation is later reordered.
_ELECTRIC_UNIT = r"(?:kWh|MWh|GWh|kW(?!h)|MW(?!h)|GW(?!h))"
# Concrete business counters that commonly carry production, delivery, order,
# or transaction totals.  Keep document/evidence counters such as ``份`` out
# of this list: methodology prose like “三份来源” is not a business measure,
# and Chinese counter numerals remain conservatively excluded by ``_NUMBER``.
_BUSINESS_COUNT_UNIT = (
    r"(?:\u4e07\u4ef6|\u4ef6|\u4e07\u6279|\u6279|"
    r"\u4e07\u7b14|\u7b14|\u4e07\u5355(?!\u5143)|\u5355(?!\u5143))"
)
# ``9大重点领域`` is a count only when ``大`` is followed by a concrete noun.
# This avoids promoting standalone adjectives such as ``重大`` and ``强大``.
_EXPLICIT_COUNT_NOUN = (
    r"(?:\u9886\u57df|\u9879\u76ee|\u4efb\u52a1|\u5de5\u7a0b|\u884c\u52a8|"
    r"\u4ea7\u4e1a|\u96c6\u7fa4|\u677f\u5757|\u533a\u57df|\u57fa\u5730|"
    r"\u5e73\u53f0|\u4e3e\u63aa|\u65b9\u5411|\u4f53\u7cfb|\u573a\u666f)"
)
_COUNT_NOUN_QUALIFIER = r"(?:\u91cd\u70b9|\u4e13\u9879|\u4e3b\u8981|\u6838\u5fc3|\u5173\u952e)"
_EMPHATIC_COUNT_UNIT = (
    rf"(?:\u5927\s*(?:{_COUNT_NOUN_QUALIFIER}\s*)?{_EXPLICIT_COUNT_NOUN})"
)
_EMPHATIC_COUNT_NORMALIZE_RE = re.compile(
    rf"^\u5927(?:{_COUNT_NOUN_QUALIFIER})?(?P<noun>{_EXPLICIT_COUNT_NOUN})$"
)
_MEASURE_UNIT = (
    r"(?:%|\uff05|\u767e\u5206\u70b9|\u4e07\u4ebf\u5143\u7ea7|\u4e07\u4ebf\u7ea7|"
    r"\u4e07\u4ebf\u5143|\u4ebf\u5143\u7ea7|\u4ebf\u7ea7|\u4ebf\u5143|\u4e07\u5143|\u5143|"
    r"\u4ebf|\u4e07\u8f86|\u8f86|\u4e07\u53f0|\u53f0|\u4e07\u5957|\u5957|"
    rf"\u4e07\u5428|\u5428|\u4e07\u4eba\u6b21|\u4eba\u6b21|\u4e07\u4eba|\u4eba|\u4e07\u6839|\u6839|\u4e07\u5ea7|\u5ea7|\u4e07\u7ad9|\u7ad9|"
    rf"\u4e07\u6237|\u6237|\u4e07\u6761|\u6761|\u4e07\u6240|\u6240|{_BUSINESS_COUNT_UNIT}|{_EMPHATIC_COUNT_UNIT}|{_ELECTRIC_UNIT}|\u516c\u9877|ha|\u4ea9|\u5e73\u65b9\u7c73|\u5e73\u7c73|\u33a1|m\u00b2|m2|"
    r"\u5e73\u65b9\u516c\u91cc|\u516c\u91cc|\u5c0f\u65f6|\u5bb6|\u9879|\u4e2a|\u6210)"
)
# A calendar year is contextual scope, not by itself a measured result.  Keep
# it in the signature parser below, but do not let ``2025年`` alone turn a
# report sentence into a quantitative claim.
_UNIT = rf"(?:{_MEASURE_UNIT}|\u5e74)"
_CALENDAR_YEAR_NUMBER = rf"(?:(?:[1\uff11][9\uff19])|(?:[2\uff12][0\uff10])){_DIGIT}{{2}}"
_DURATION_YEAR = (
    rf"(?!(?:{_CALENDAR_YEAR_NUMBER})\s*\u5e74){_NUMBER}\s*\u5e74(?!\u4efd)"
)
QUANTITATIVE_RE = re.compile(
    rf"(?<!{_DIGIT})(?<!\u7b2c)(?:{_NUMBER}\s*{_MEASURE_UNIT}|{_DURATION_YEAR})",
    re.IGNORECASE,
)
_MEASURE_RE = re.compile(
    rf"(?<!\u7b2c)(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
    re.IGNORECASE,
)

_COMPARATOR_SUFFIXES = (
    (re.compile(r"^(?:\u53ca)?\u4ee5\u4e0a"), "gte"),
    (re.compile(r"^(?:\u53ca)?\u4ee5\u4e0b"), "lte"),
    (re.compile(r"^(?:\u5de6\u53f3|\u4e0a\u4e0b)"), "approx"),
)
_COMPARATOR_PREFIXES = (
    (
        re.compile(
            r"(?:\u4e0d\u4f4e\u4e8e|\u4e0d\u5c11\u4e8e|\u81f3\u5c11)"
            r"(?:\u5e94)?(?:\u8fbe\u5230|\u8fbe|\u4e3a|\u6709)?\s*$"
        ),
        "gte",
    ),
    (
        re.compile(
            r"(?:\u4e0d\u9ad8\u4e8e|\u4e0d\u8d85\u8fc7|\u81f3\u591a|\u6700\u591a)"
            r"(?:\u4e3a|\u6709)?\s*$"
        ),
        "lte",
    ),
    (re.compile(r"(?:\u672a\u8fbe\u5230|\u672a\u8fbe|\u4f4e\u4e8e|\u5c0f\u4e8e|\u4e0d\u8db3)\s*$"), "lt"),
    (re.compile(r"(?:\u8d85\u8fc7|\u9ad8\u4e8e|\u5927\u4e8e)\s*$"), "gt"),
    (re.compile(r"(?:\u7ea6|\u5927\u7ea6|\u7ea6\u4e3a|\u8fd1)\s*$"), "approx"),
)
_GROWTH_PREFIX_RE = re.compile(
    r"(?:\u540c\u6bd4|\u73af\u6bd4|\u8f83[^\s\uff0c\u3002\uff1b;]{1,8})?"
    r"(?:\u660e\u663e|\u5927\u5e45|\u5c0f\u5e45|\u5feb\u901f|\u6301\u7eed)?"
    r"(?:\u589e\u957f(?:\u7387|\u5e45)?|\u589e\u5e45|\u4e0a\u5347|\u4e0a\u6da8|\u589e\u52a0|\u63d0\u5347)"
    r"(?:\u4e3a|\u8fbe\u5230|\u8fbe|\u7ea6|\u8fd1|\u8d85\u8fc7|\u4e0d\u4f4e\u4e8e|\u81f3\u5c11|"
    r"\u4e0d\u8d85\u8fc7|\u4e0d\u9ad8\u4e8e|\u81f3\u591a|\u6700\u591a|\u4e86)?\s*$"
)
_DECLINE_PREFIX_RE = re.compile(
    r"(?:\u540c\u6bd4|\u73af\u6bd4|\u8f83[^\s\uff0c\u3002\uff1b;]{1,8})?"
    r"(?:\u660e\u663e|\u5927\u5e45|\u5c0f\u5e45|\u5feb\u901f|\u6301\u7eed)?"
    r"(?:\u4e0b\u964d(?:\u7387|\u5e45)?|\u964d\u5e45|\u4e0b\u6ed1|\u4e0b\u8dcc|\u51cf\u5c11|\u964d\u4f4e|\u56de\u843d|\u6536\u7f29|\u8d1f\u589e\u957f)"
    r"(?:\u4e3a|\u8fbe\u5230|\u8fbe|\u7ea6|\u8fd1|\u8d85\u8fc7|\u4e0d\u4f4e\u4e8e|\u81f3\u5c11|"
    r"\u4e0d\u8d85\u8fc7|\u4e0d\u9ad8\u4e8e|\u81f3\u591a|\u6700\u591a|\u4e86)?\s*$"
)
_GROWTH_SUFFIX_RE = re.compile(
    r"^\s*(?:\u7684)?(?:\u589e\u957f(?:\u7387|\u5e45)?|\u589e\u5e45|\u4e0a\u5347|\u4e0a\u6da8|\u589e\u52a0|\u63d0\u5347)"
)
_DECLINE_SUFFIX_RE = re.compile(
    r"^\s*(?:\u7684)?(?:\u4e0b\u964d(?:\u7387|\u5e45)?|\u964d\u5e45|\u4e0b\u6ed1|\u4e0b\u8dcc|\u51cf\u5c11|\u964d\u4f4e|\u56de\u843d|\u6536\u7f29)"
)

_REGION_ALIASES = {
    "北京市": "北京",
    "北京": "北京",
    "天津市": "天津",
    "天津": "天津",
    "上海市": "上海",
    "上海": "上海",
    "重庆市": "重庆",
    "重庆": "重庆",
    "河北省": "河北",
    "河北": "河北",
    "山西省": "山西",
    "山西": "山西",
    "辽宁省": "辽宁",
    "辽宁": "辽宁",
    "吉林省": "吉林",
    "吉林": "吉林",
    "黑龙江省": "黑龙江",
    "黑龙江": "黑龙江",
    "江苏省": "江苏",
    "江苏": "江苏",
    "浙江省": "浙江",
    "浙江": "浙江",
    "安徽省": "安徽",
    "安徽": "安徽",
    "福建省": "福建",
    "福建": "福建",
    "江西省": "江西",
    "江西": "江西",
    "山东省": "山东",
    "山东": "山东",
    "河南省": "河南",
    "河南": "河南",
    "湖北省": "湖北",
    "湖北": "湖北",
    "湖南省": "湖南",
    "湖南": "湖南",
    "广东省": "广东",
    "广东": "广东",
    "海南省": "海南",
    "海南": "海南",
    "四川省": "四川",
    "四川": "四川",
    "贵州省": "贵州",
    "贵州": "贵州",
    "云南省": "云南",
    "云南": "云南",
    "陕西省": "陕西",
    "陕西": "陕西",
    "甘肃省": "甘肃",
    "甘肃": "甘肃",
    "青海省": "青海",
    "青海": "青海",
    "内蒙古自治区": "内蒙古",
    "内蒙古": "内蒙古",
    "广西壮族自治区": "广西",
    "广西": "广西",
    "西藏自治区": "西藏",
    "西藏": "西藏",
    "宁夏回族自治区": "宁夏",
    "宁夏": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "新疆": "新疆",
    "香港特别行政区": "香港",
    "香港": "香港",
    "澳门特别行政区": "澳门",
    "澳门": "澳门",
    "台湾省": "台湾",
    "台湾": "台湾",
}
_ENGLISH_REGIONS = {
    "hubei": "湖北",
    "hunan": "湖南",
    "beijing": "北京",
    "shanghai": "上海",
    "guangdong": "广东",
    "zhejiang": "浙江",
    "jiangsu": "江苏",
    "sichuan": "四川",
}

_INDICATOR_ALIASES = (
    (r"\u672c\u5730\u914d\u5957\u7387|\u914d\u5957\u7387", "配套率"),
    (r"\u5e02\u573a\u4efd\u989d|\u533a\u57df\u4efd\u989d|\u4efd\u989d|\u5360\u6bd4", "份额"),
    (r"\u589e\u957f\u7387|\u589e\u901f|\u540c\u6bd4", "增长率"),
    (r"\u5e02\u573a\u89c4\u6a21|\u4ea7\u4e1a\u89c4\u6a21", "市场规模"),
    (r"\u9500\u91cf|\u9500\u552e\u91cf", "销量"),
    (r"\u4ea7\u91cf|\u4ea7\u51fa", "产量"),
    (r"\u4ea7\u80fd", "产能"),
    (r"\u4ea7\u503c", "产值"),
    (r"\u8425\u4e1a\u6536\u5165|\u8425\u6536|\u9500\u552e\u6536\u5165", "营业收入"),
    (r"\u5e73\u5747\u4ef7\u683c|\u5747\u4ef7|\u4ef7\u683c", "价格"),
    (r"\u6210\u672c", "成本"),
    (r"\u88c5\u673a\u5bb9\u91cf|\u88c5\u673a", "装机容量"),
    (r"\u53d1\u7535\u91cf", "发电量"),
    (r"\u7528\u7535\u91cf", "用电量"),
    (r"\u6295\u8d44\u989d|\u603b\u6295\u8d44", "投资额"),
    (r"\u51fa\u53e3\u91cf", "出口量"),
    (r"\u8fdb\u53e3\u91cf", "进口量"),
    (r"\u4f01\u4e1a\u6570\u91cf|\u4f01\u4e1a\u603b\u6570|\u4f01\u4e1a\u6570", "企业数量"),
    (r"\u9879\u76ee\u6570\u91cf|\u9879\u76ee\u603b\u6570|\u9879\u76ee\u6570", "项目数量"),
    (r"\u5efa\u7b51\u9762\u79ef|\u7528\u5730\u9762\u79ef|\u9762\u79ef", "面积"),
    (r"\u91cc\u7a0b", "里程"),
    (r"\u4eba\u6570|\u5c31\u4e1a\u4eba\u5458", "人数"),
)

# Most-specific subject labels are selected first.  These are intentionally
# conservative: an unknown subject does not fail merely for being unknown, but
# two explicit and different objects can never corroborate each other.
_SUBJECT_ALIASES = (
    ("新能源汽车零部件", "汽车零部件"),
    ("汽车零部件", "汽车零部件"),
    ("新能源汽车整车", "整车"),
    ("整车", "整车"),
    ("动力电池", "动力电池"),
    ("锂电池", "锂电池"),
    ("光伏组件", "光伏组件"),
    ("光伏", "光伏"),
    ("风电", "风电"),
    ("新能源汽车", "新能源汽车"),
    ("清洁能源", "清洁能源"),
    ("新能源产业", "新能源产业"),
    ("茶叶", "茶叶"),
    ("酒店", "酒店"),
    ("旅游", "旅游"),
    ("农产品", "农产品"),
)

_PLAN_RE = re.compile(
    r"(?:\u89c4\u5212|\u76ee\u6807|\u529b\u4e89|\u65b9\u6848.{0,10}(?:\u63d0\u51fa|\u8981\u6c42|\u660e\u786e)|"
    r"\u8981\u6c42.{0,20}(?:\u8fbe\u5230|\u5f62\u6210|\u5efa\u6210)|"
    # Government plan sentences often insert capacity / status clauses between
    # the horizon year and the measured target (e.g. 到2025年，…产能…，…配套率达到).
    r"\u523020\d{2}\u5e74.{0,80}(?:\u8fbe\u5230|\u8d85\u8fc7|\u63d0\u9ad8|\u5f62\u6210|\u5efa\u6210)|\u5e94\u8fbe\u5230|"
    r"\u4e0b\u4e00\u6b65.{0,16}(?:\u5c06|\u529b\u4e89|\u62df|\u8ba1\u5212).{0,32}"
    r"(?:\u63a8\u52a8|\u6253\u9020|\u5efa\u8bbe|\u57f9\u80b2|\u5f62\u6210|\u5b9e\u73b0|\u8fbe\u5230)|"
    r"(?:\u62df|\u8ba1\u5212).{0,12}(?:\u5c06|\u63a8\u52a8|\u5b9e\u65bd|\u5f00\u5c55|\u6253\u9020|\u5efa\u8bbe|\u57f9\u80b2|\u5f62\u6210|\u5b9e\u73b0|\u8fbe\u5230)|"
    r"(?<!\u5df2)(?<!\u5df2\u7ecf)(?<!\u6210\u529f)(?:\u6253\u9020|\u5efa\u8bbe|\u57f9\u80b2)"
    r".{0,36}(?:\u767e|\u5343|\u4e07)\s*\u4ebf(?:\u5143)?(?:\u7ea7)?)"
)
_FORECAST_RE = re.compile(
    r"(?:\u9884\u8ba1|\u9884\u6d4b|\u6709\u671b|"
    r"\u5c06(?:\u8fbe\u5230|\u8fbe|\u589e\u957f|\u589e|\u63d0\u9ad8|\u63d0|\u8d85\u8fc7|\u8d85|\u4e0b\u964d|\u964d))"
)
_COMPANY_RE = re.compile(r"(?:\u516c\u53f8|\u4f01\u4e1a).{0,8}(?:\u8868\u793a|\u62ab\u9732|\u516c\u544a)")


@dataclass(frozen=True, slots=True)
class QuantitativeMeasure:
    """One value with semantics that the legacy numeric token discards."""

    value: str
    unit: str
    comparator: str = "eq"
    direction: str = "neutral"


@dataclass(frozen=True, slots=True)
class QuantitativeSignature:
    regions: frozenset[str]
    periods: frozenset[str]
    measures: tuple[tuple[str, str], ...]
    indicators: frozenset[str]
    subject: str
    nature: str
    # Keep ``measures`` as value/unit pairs for backward compatibility.  This
    # parallel form is used only by semantic merge and support decisions.
    measure_semantics: tuple[QuantitativeMeasure, ...] = ()


def _normalize_text(value: str) -> str:
    """Normalize compatibility-width digits, letters and punctuation.

    Official PDF / OCR extracts often insert spaces inside plan horizons and
    measure phrases (``到 2025 年`` / ``40% 以上`` / ``3500 亿元``).  Collapse
    only those digit-unit / comparator seams so plan nature and comparator
    detection stay aligned with clean HTML wording, without wiping ordinary
    word spacing used for readability.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"(?<=到)\s+(?=(?:19|20)\d{2})", "", text)
    text = re.sub(r"(?<=(?:19|20)\d{2})\s+(?=年)", "", text)
    text = re.sub(
        r"(?<=\d)\s+(?=(?:%|％|亿|万|千|百|元|辆|台|套|吨|人|项|家|个|成|级))",
        "",
        text,
    )
    text = re.sub(
        r"(?<=\d)\s+(?=(?:亿元|万亿元|万人|万辆|万台|万套|万吨|千瓦|万千瓦))",
        "",
        text,
    )
    text = re.sub(r"(?<=[%％])\s+(?=(?:以上|以下|左右|上下))", "", text)
    text = re.sub(
        r"(?<=(?:亿|万|千|百|元|辆|台|套|吨|人|项|家|个|成|级))"
        r"\s+(?=(?:以上|以下|左右|上下))",
        "",
        text,
    )
    # Split multi-digit runs with internal spaces: ``3 500`` / ``40 %``.
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"(?<=\d)\s+(?=[%％])", "", text)
    return text


def _normalize_unit(value: str) -> str:
    unit = re.sub(r"\s+", "", _normalize_text(value)).replace("％", "%")
    emphatic_count = _EMPHATIC_COUNT_NORMALIZE_RE.fullmatch(unit)
    if emphatic_count:
        return emphatic_count.group("noun")
    aliases = {
        "平米": "平方米",
        "㎡": "平方米",
        "m²": "平方米",
        "m2": "平方米",
        "万亿元级": "万亿元",
        "万亿级": "万亿元",
        "亿元级": "亿元",
        "亿级": "亿",
        "kwh": "kWh",
        "mwh": "MWh",
        "gwh": "GWh",
        "kw": "kW",
        "mw": "MW",
        "gw": "GW",
    }
    return aliases.get(unit.lower(), unit)


def _normalize_number(value: str) -> str:
    number = _normalize_text(value).replace(",", "")
    if re.fullmatch(_CHINESE_NUMBER, number):
        digits = {
            "\u96f6": 0,
            "\u3007": 0,
            "\u4e00": 1,
            "\u4e8c": 2,
            "\u4e24": 2,
            "\u4e09": 3,
            "\u56db": 4,
            "\u4e94": 5,
            "\u516d": 6,
            "\u4e03": 7,
            "\u516b": 8,
            "\u4e5d": 9,
        }
        small_units = {"\u5341": 10, "\u767e": 100, "\u5343": 1000}
        total = 0
        section = 0
        current = 0
        for char in number:
            if char in digits:
                current = digits[char]
            elif char in small_units:
                section += (current or 1) * small_units[char]
                current = 0
            elif char == "\u4e07":
                total += (section + current or 1) * 10000
                section = 0
                current = 0
        return str(total + section + current)
    try:
        parsed = float(number)
    except ValueError:
        return number
    return str(int(parsed)) if parsed.is_integer() else format(parsed, "g")


def _is_calendar_year(number: str, unit: str) -> bool:
    return unit == "年" and re.fullmatch(r"(?:19|20)\d{2}", number) is not None


def _measure_comparator(text: str, match: re.Match[str]) -> str:
    suffix = text[match.end() : match.end() + 8]
    for pattern, comparator in _COMPARATOR_SUFFIXES:
        if pattern.search(suffix):
            return comparator
    prefix = text[max(0, match.start() - 48) : match.start()]
    for pattern, comparator in _COMPARATOR_PREFIXES:
        if pattern.search(prefix):
            return comparator
    raw_unit = re.sub(r"\s+", "", _normalize_text(match.group("unit")))
    if raw_unit.endswith("级"):
        return "approx"
    raw_value = _normalize_text(match.group("value")).strip()
    if raw_value in {"百", "千", "万"} and _normalize_unit(raw_unit) in {
        "亿",
        "亿元",
    }:
        # Bare ``百亿/千亿/万亿`` industry labels conventionally express a
        # lower-bound scale, unlike exact forms such as ``一百亿元``.
        return "gte"
    return "eq"


def _measure_direction(text: str, match: re.Match[str]) -> str:
    prefix = text[max(0, match.start() - 64) : match.start()]
    if _GROWTH_PREFIX_RE.search(prefix):
        return "growth"
    if _DECLINE_PREFIX_RE.search(prefix):
        return "decline"
    suffix = text[match.end() : match.end() + 16]
    if _GROWTH_SUFFIX_RE.search(suffix):
        return "growth"
    if _DECLINE_SUFFIX_RE.search(suffix):
        return "decline"
    return "neutral"


def quantitative_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for match in _MEASURE_RE.finditer(_normalize_text(value)):
        number = _normalize_number(match.group("value"))
        unit = _normalize_unit(match.group("unit"))
        if not _is_calendar_year(number, unit):
            tokens.add(f"{number}{unit}")
    return tokens


def quantitative_signature(value: str) -> QuantitativeSignature:
    text = _normalize_text(value)
    low = text.lower()
    regions = {
        canonical
        for alias, canonical in _REGION_ALIASES.items()
        if alias in text
    }
    regions.update(
        canonical
        for alias, canonical in _ENGLISH_REGIONS.items()
        if re.search(rf"\b{re.escape(alias)}\b", low)
    )
    periods: set[str] = set()
    measures: list[tuple[str, str]] = []
    measure_semantics: list[QuantitativeMeasure] = []
    for match in _MEASURE_RE.finditer(text):
        number = _normalize_number(match.group("value"))
        unit = _normalize_unit(match.group("unit"))
        if _is_calendar_year(number, unit):
            periods.add(number)
        else:
            measures.append((number, unit))
            measure_semantics.append(
                QuantitativeMeasure(
                    number,
                    unit,
                    comparator=_measure_comparator(text, match),
                    direction=_measure_direction(text, match),
                )
            )
    indicators = {
        canonical
        for pattern, canonical in _INDICATOR_ALIASES
        if re.search(pattern, text)
    }
    units = {unit for _, unit in measures}
    if not indicators:
        if units & {"家"}:
            indicators.add("企业数量")
        if units & {"项"}:
            indicators.add("项目数量")
        if units & {"kW", "MW", "GW"}:
            indicators.add("装机容量")
        if units & {"亩", "平方米", "平方公里"}:
            indicators.add("面积")
        if units & {"公里"}:
            indicators.add("里程")
        if units & {"人", "万人"}:
            indicators.add("人数")
    subject = next(
        (canonical for marker, canonical in _SUBJECT_ALIASES if marker in text),
        "",
    )
    if _PLAN_RE.search(text):
        nature = "plan_target"
    elif _FORECAST_RE.search(text):
        nature = "forecast"
    elif _COMPANY_RE.search(text):
        nature = "company_statement"
    else:
        nature = "fact"
    return QuantitativeSignature(
        regions=frozenset(regions),
        periods=frozenset(periods),
        measures=tuple(dict.fromkeys(measures)),
        indicators=frozenset(indicators),
        subject=subject,
        nature=nature,
        measure_semantics=tuple(dict.fromkeys(measure_semantics)),
    )


def quantitative_statement_supported(statement: str, evidence: str) -> bool:
    """Return whether one exact evidence sentence supports one report sentence.

    Missing specificity in the report is tolerated (for example omitting a
    region already established by the section), but every specificity asserted
    by the report must be present and equal in the evidence.  A fact may never
    be upgraded from a plan target, forecast or company statement.
    """

    reported = quantitative_signature(statement)
    cited = quantitative_signature(evidence)
    if not reported.measures or not set(reported.measures) <= set(cited.measures):
        return False
    if not set(reported.measure_semantics) <= set(cited.measure_semantics):
        return False
    if reported.periods and not reported.periods <= cited.periods:
        return False
    if reported.regions and not reported.regions <= cited.regions:
        return False
    if reported.indicators and not reported.indicators <= cited.indicators:
        return False
    if reported.subject and reported.subject != cited.subject:
        return False
    if reported.nature != cited.nature:
        return False
    return True


def quantitative_claims_compatible(left: str, right: str) -> bool:
    """Symmetric semantic guard used before merging source claims."""

    first = quantitative_signature(left)
    second = quantitative_signature(right)
    if not first.measures or set(first.measures) != set(second.measures):
        return False
    if set(first.measure_semantics) != set(second.measure_semantics):
        return False
    if first.periods != second.periods:
        return False
    if first.regions != second.regions:
        return False
    if first.indicators and second.indicators and first.indicators != second.indicators:
        return False
    if first.subject and second.subject and first.subject != second.subject:
        return False
    return first.nature == second.nature


def normalized_quote_family(value: str) -> str:
    return re.sub(
        r"[^a-z0-9\u4e00-\u9fff%\uff05]",
        "",
        _normalize_text(value).lower(),
    ).replace("\uff05", "%")
