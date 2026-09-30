"""资料分流：默认内部；只有明确公开的资料和安全问题才可交给远程模型。"""

import re
from dataclasses import dataclass

import httpx

from ..config import Settings
from .rag import RAGService, SearchHit

# 这些是保守的拦截条件。自动识别不可能证明一段任意文字绝对公开，
# 因而还必须要求它能被人工标记的公开资料支持。
SENSITIVE = re.compile(
    r"SN[-_ ]?\d|序列号|工单|客户|业主|我司|我们公司|公司内部|内部平台|"
    r"托管平台|项目|现场|电站地址|站点|地址|合同|账号|密码|密钥|令牌|截图|"
    r"\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    re.I,
)
PRODUCT = re.compile(
    r"(?<![A-Za-z0-9_-])(?:SUN2000(?:-\d{1,3}KTL(?:-[A-Z0-9]{1,4})?)?|"
    r"LUNA2000|SmartLogger(?:\d+[A-Z]?)?|"
    r"NetEco\d*[A-Z]?|FusionSolar|DTSU666(?:-[A-Z])?|MBUS|AFCI|RS485)"
    r"(?![A-Za-z0-9_-])",
    re.I,
)
SAFE_TOPICS = (
    "绝缘阻抗低", "绝缘阻抗", "长组串", "优化器", "通信故障", "并网", "离网",
    "告警", "接线", "安装", "配置", "电表", "电池", "功率", "故障代码",
)


@dataclass(frozen=True)
class PrivacyDecision:
    scope: str
    public_hits: list[SearchHit]
    reason: str


async def classify_question(
    question: str, rag: RAGService, settings: Settings
) -> PrivacyDecision:
    """先在本地判定；未知、敏感或缺少公开证据时一律按内部问题处理。"""
    if SENSITIVE.search(question):
        return PrivacyDecision("private", [], "包含现场或身份信息")
    public_hits = await rag.search(
        question,
        top_k=settings.retrieval_top_k,
        corpus_id=settings.retrieval_corpus_id,
        visibility="public",
    )
    if not public_hits or public_hits[0].score < 0.5:
        return PrivacyDecision("private", [], "没有足够相关的公开资料")
    return PrivacyDecision("public", public_hits, "命中已审核的公开资料")


def safe_web_query(question: str) -> str | None:
    """仅从白名单产品名和通用技术词重建查询，不发送原问题或自由文本。"""
    products = [match.group(0) for match in PRODUCT.finditer(question)]
    topics = [term for term in SAFE_TOPICS if term in question]
    tokens = list(dict.fromkeys(products + topics))[:6]
    return " ".join(tokens) if products and topics else None


async def search_public_web(query: str, settings: Settings) -> list[dict[str, str]]:
    """可选的公开搜索工具。只接受 safe_web_query 产生的受限词串。"""
    if not settings.public_search_api_key:
        return []
    if not query or len(query) > 120 or SENSITIVE.search(query):
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5, "search_lang": "zh"},
            headers={"X-Subscription-Token": settings.public_search_api_key},
        )
        response.raise_for_status()
    items = response.json().get("web", {}).get("results", [])
    return [
        {
            "title": str(item.get("title") or "")[:160],
            "url": str(item.get("url") or "")[:500],
            "description": str(item.get("description") or "")[:500],
        }
        for item in items[:5]
        if str(item.get("url") or "").startswith("https://")
    ]
