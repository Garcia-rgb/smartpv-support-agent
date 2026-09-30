"""一次性建立管理员账号。运行：python scripts/create_admin.py --generate。"""

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from support_agent.db import SessionFactory, create_schema  # noqa: E402
from support_agent.models import UserAccount  # noqa: E402
from support_agent.services.auth import hash_password  # noqa: E402


async def create_admin(password: str) -> bool:
    await create_schema()
    async with SessionFactory() as db:
        existing = await db.scalar(select(UserAccount).where(UserAccount.username == "admin"))
        if existing:
            return False
        db.add(UserAccount(username="admin", password_hash=hash_password(password), role="admin"))
        await db.commit()
        return True


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="创建初始 admin 账号")
    parser.add_argument("--generate", action="store_true", help="生成强密码并仅显示一次")
    args = parser.parse_args()
    if args.generate:
        password = secrets.token_urlsafe(18)
    else:
        password = getpass.getpass("管理员密码（至少 8 个字符）：")
        if password != getpass.getpass("再次输入密码："):
            raise SystemExit("两次输入不一致")
    if asyncio.run(create_admin(password)):
        print("管理员账号：admin")
        if args.generate:
            print(f"首次登录密码：{password}")
    else:
        print("admin 已存在，未修改密码")


if __name__ == "__main__":
    main()
