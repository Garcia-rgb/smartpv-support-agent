import csv
import io

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from support_agent.config import Settings, get_settings
from support_agent.main import app
from support_agent.models import UserAccount
from support_agent.services import pointtable
from support_agent.services.auth import hash_password


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pointtable, "TEMPLATE_DIR", tmp_path)
    with (tmp_path / "智能空开南向点表_27点模板.csv").open(
        "w", encoding="utf-8", newline=""
    ) as target:
        writer = csv.DictWriter(target, fieldnames=pointtable.SOUTH_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "设备类型": "智能空开", "协议类型": "MODBUS-RTU",
            "信号点名称": "实时电流A", "信号地址": "5", "信号点功能类型": "遥测",
            "信号点ID": "1", "信号点数据类型": "UINT16", "单位": "A", "增益": "100",
            "偏移量": "0", "MODBUS信号类型": "输入寄存器", "MODBUS读写类型": "RO",
            "MODBUS寄存器个数": "1", "MODBUS采集周期": "0", "是否持久化": "1",
        })
    with (tmp_path / "智能空开北向点表_实验室可导入.csv").open(
        "w", encoding="utf-8", newline=""
    ) as target:
        writer = csv.DictWriter(target, fieldnames=pointtable.NORTH_COLUMNS)
        writer.writeheader()
        writer.writerow({"信号点名称": "智能空开_实时电流", "信号点地址": "16389",
                         "功能类型": "遥测", "单位": "A"})
    return tmp_path


def test_generation_requires_site_fields_and_preserves_protocol_point(template_dir):
    fields = {
        "通道编号": "CH_1", "设备型号": "ZNKK_V2", "厂商名称": "测试厂商",
        "设备编码": "BRK_01", "MODBUS设备地址": "2", "电站编号": "测试电站",
    }
    with pytest.raises(pointtable.PointTableError, match="请填写通道编号"):
        pointtable.generate_csv("smart_breaker_south", {})
    with pytest.raises(pointtable.PointTableError, match="只能使用"):
        pointtable.generate_csv("smart_breaker_south", fields | {"设备型号": "=1+1"})
    content, _ = pointtable.generate_csv("smart_breaker_south", fields)
    row = next(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert list(row) == list(pointtable.SOUTH_COLUMNS)
    assert row["通道编号"] == "CH_1"
    assert row["MODBUS设备地址"] == "2"
    assert (row["信号地址"], row["MODBUS信号类型"], row["增益"]) == (
        "5", "输入寄存器", "100",
    )
    assert "测试电站" in content.decode("utf-8-sig")


async def test_user_can_download_template_but_auth_and_bad_fields_are_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, template_dir,
):
    app.dependency_overrides[get_settings] = lambda: Settings(
        auth_enabled=True, confirmation_secret="test-secret"
    )
    assert (await client.get("/point-tables/templates")).status_code == 401
    db_session.add(UserAccount(
        username="staff", password_hash=hash_password("correct-password-123"), role="user"
    ))
    await db_session.commit()
    login = await client.post("/auth/login", json={
        "username": "staff", "password": "correct-password-123"
    })
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    templates = (await client.get("/point-tables/templates")).json()["items"]
    assert {item["id"] for item in templates} == {"smart_breaker_south", "smart_breaker_north"}
    assert (await client.post("/point-tables/generate", headers=headers, json={
        "template_id": "smart_breaker_south", "fields": {},
    })).status_code == 422
    assert (await client.post("/point-tables/generate", headers=headers, json={
        "template_id": "../../secrets", "fields": {},
    })).status_code == 422
    generated = await client.post("/point-tables/generate", headers=headers, json={
        "template_id": "smart_breaker_north", "fields": {},
    })
    assert generated.status_code == 200
    assert generated.content.startswith(b"\xef\xbb\xbf")
    assert "16389" in generated.content.decode("utf-8-sig")
    assert "no-store" == generated.headers["cache-control"]
