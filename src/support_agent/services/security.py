import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any


def create_confirmation_token(payload: dict[str, Any], secret: str, ttl_seconds: int = 600) -> str:
    """为待确认操作生成带有效期和签名的令牌。"""
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    # 签名用于发现内容是否被篡改；Base64 只是编码，并不负责加密。
    return f"{encoded}.{signature}"


def verify_confirmation_token(token: str, secret: str) -> dict[str, Any]:
    """校验令牌签名和有效期，成功后返回其中的操作数据。

    三种拒绝原因必须分得开：格式错、签名被改、放太久。它们对调用方的含义不同——
    前两种是有人动手脚，第三种只是慢了一步，排查时看的是不同东西。
    所以签名比对放在自己的 try 之外：写进同一个 try 里会被自己的 except 接住，
    重包成笼统的「无效」，那个分支就成了永远走不到的死代码。
    """
    try:
        encoded, supplied_signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("确认令牌格式无效") from exc

    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    # 签名覆盖的是编码后的正文，所以改内容不重签一定对不上。
    if not hmac.compare_digest(supplied_signature, expected):
        raise ValueError("确认令牌签名无效")

    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("确认令牌内容无效") from exc

    # 签名对不代表内容可用：这里要挡住「正文不是对象」和「exp 不是数字」，
    # 否则 `payload.get` 会抛 AttributeError、`int()` 会抛 TypeError，
    # 都不是 ValueError，调用方接不住就变成 500。
    if not isinstance(payload, dict):
        raise ValueError("确认令牌内容无效")
    try:
        expires_at = int(payload.get("exp", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("确认令牌内容无效") from exc
    if expires_at < int(time.time()):
        raise ValueError("确认令牌已过期")
    return payload


SUSPICIOUS_PATTERNS = (
    "忽略之前的指令",
    "ignore previous instructions",
    "system prompt",
    "泄露密钥",
    "api key",
)


def looks_like_prompt_injection(text: str) -> bool:
    """用简单关键词识别明显的提示词注入；生产环境需要更完整的防护。"""
    lowered = text.lower()
    return any(pattern in lowered for pattern in SUSPICIOUS_PATTERNS) or bool(
        re.search(r"忽略.{0,8}(?:指令|规则)|(?:输出|泄露|告诉我).{0,8}(?:系统提示词|密钥)", text)
    )
