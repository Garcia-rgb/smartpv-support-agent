"""管理员登录：密码哈希、数据库会话和请求身份验证。"""

import base64
import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_db
from ..models import LoginSession, UserAccount

COOKIE_NAME = "smartpv_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("管理员密码至少需要 8 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode()
    digest_text = base64.urlsafe_b64encode(digest).decode()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt_text}${digest_text}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256" or int(rounds) != PASSWORD_ITERATIONS:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(digest, base64.urlsafe_b64decode(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserAccount | None:
    """未启用时仅供旧测试使用；真实服务默认必须登录。"""
    if not settings.auth_enabled:
        return None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "请先登录")
    login = await db.get(LoginSession, token_hash(token))
    if not login or login.expires_at <= int(time.time()):
        raise HTTPException(401, "登录已过期，请重新登录")
    user = await db.get(UserAccount, login.user_id)
    if not user or not user.active:
        raise HTTPException(401, "账号不可用")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, csrf_token(token, settings.confirmation_secret)):
            raise HTTPException(403, "请求校验失败，请刷新页面后重试")
    return user


async def admin_user(
    user: Annotated[UserAccount | None, Depends(current_user)],
) -> UserAccount | None:
    if user is not None and user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
