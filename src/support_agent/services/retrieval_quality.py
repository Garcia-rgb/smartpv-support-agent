"""Local query planning and evidence ranking; no additional model calls."""

import re
from collections.abc import Mapping

MODEL = re.compile(
    r"(?<![A-Z0-9])(?:SUN2000(?:-[A-Z0-9]+)*|GW\d+[A-Z0-9]*(?:-[A-Z0-9]+)*|"
    r"SmartLogger\d*[A-Z]*|DTSU\d+(?:-[A-Z])?|NetEco\d+[A-Z]*)(?![A-Z0-9_-])",
    re.I,
)
BRANDS = {
    "华为": re.compile(r"华为|Huawei|SUN2000|SmartLogger", re.I),
    "固德威": re.compile(r"固德威|GoodWe|\bGW\d+[A-Z0-9-]*", re.I),
}
PV_CHANNEL = re.compile(r"(?<![A-Z0-9])(?:PV|NB)\d+(?![A-Z0-9])", re.I)
TOPICS = (
    (
        re.compile(r"挂不上|挂载不上|扫描|扫.{0,8}地址|地址.{0,8}[26]"),
        "逆变器 通讯地址设置 RS485",
        ("地址", "扫描", "通信", "通讯"),
    ),
    (
        re.compile(r"不刷新|不更新|旧时间|没有更新|数据.{0,5}不对|电压电流.*横线|断联|离线"),
        "逆变器 通信中断 数据不更新",
        ("数据", "通信", "通讯", "采集", "点表"),
    ),
)


def product_models(text: str) -> list[str]:
    return list(dict.fromkeys(m.group().upper() for m in MODEL.finditer(text)))


def fallback_query(query: str, unit_df: Mapping[str, int]) -> str:
    """Unknown known-family identities must not suppress general fault evidence."""

    def replace(match: re.Match[str]) -> str:
        value = match.group()
        tokens = re.findall(r"[a-z0-9_]+", value.lower())
        if tokens and unit_df.get(tokens[0], 0):
            return value
        brand = (
            "固德威"
            if value.upper().startswith("GW")
            else "华为"
            if (value.upper().startswith(("SUN2000", "SMARTLOGGER")))
            else "逆变器"
        )
        return brand

    return PV_CHANNEL.sub("", MODEL.sub(replace, query))


def query_variants(query: str) -> list[str]:
    variants = [query]
    brands = [name for name, pattern in BRANDS.items() if pattern.search(query)]
    for pattern, canonical, _ in TOPICS:
        if pattern.search(query):
            variants.append(" ".join(brands + [canonical]))
    if re.search(r"平台|实时信息|实时数据|组串|电压|电流|数据.*不对|值有问题", query):
        variants = [query, "平台 实时数据 设备信息 数据查询 指标关联",
                    "平台 数据异常 点表 寄存器 倍率 字序"]
    return list(dict.fromkeys(variants))[:3]


def identity_adjustment(query: str, content: str) -> float:
    wanted, found = set(product_models(query)), set(product_models(content))
    score = 0.12 if wanted & found else 0.0
    if re.search(r"平台|实时信息|实时数据|值有问题|数据.*不对", query):
        if re.search(r"实时数据|设备信息|数据查询|指标关联", content):
            score += .14
    # A different exact model remains visible as a weaker, explicitly limited source.
    if wanted and found and not wanted & found:
        score -= 0.12
    qbrands = {name for name, p in BRANDS.items() if p.search(query)}
    cbrands = {name for name, p in BRANDS.items() if p.search(content)}
    if qbrands & cbrands:
        score += 0.04
    elif qbrands and cbrands:
        score -= 0.25
    for pattern, _, terms in TOPICS:
        if pattern.search(query):
            score += min(0.12, 0.04 * sum(t in content for t in terms))
    if TOPICS[0][0].search(query) and re.search(r"通[讯信]地址.{0,4}设置", content[:250]):
        score += 0.18
    if TOPICS[1][0].search(query) and re.search(r"逆变器.{0,8}通[讯信]中断", content):
        score += 0.12
    if not re.search(r"AGC|AVC|一次调频", query, re.I) and re.search(
        r"AGC|AVC|一次调频", content[:180], re.I
    ):
        score -= 0.15
    codes = re.findall(r"(?<!\d)\d{4,5}(?!\d)", query)
    if any(re.search(r"(?<!\d)" + code + r"(?!\d)", content) for code in codes):
        score += 0.08
    return score


def applicability_note(question: str, content: str) -> str:
    wanted, found = set(product_models(question)), set(product_models(content))
    if wanted and not wanted & found:
        return (
            "[适用性：本片段未明确覆盖所问完整型号，只能参考通用核查；"
            "不得认定型号专用参数、寄存器或操作步骤适用。]\n"
        )
    return ""
