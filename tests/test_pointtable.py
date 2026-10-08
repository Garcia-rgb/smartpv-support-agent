import csv
import io
import zipfile

from support_agent.config import Settings, get_settings
from support_agent.main import app
from support_agent.models import UserAccount
from support_agent.services.auth import hash_password
from support_agent.services.pointtable import (
    PointTableError,
    generate_csv,
    parse_protocol,
    validate_points,
)


def sample_doc() -> bytes:
    text = (
        "输出线圈操作 功能码: 读(0X01)\x07\x07"
        "0\x07输出线圈\x07远程复位\x07\x07成功后自动为0\x07\x07"
        "保持寄存器 功能码: 写(0x06) 读(0x03)\x07\x07"
        "2\x07保持寄存器\x07过压值\x07250~300（V）\x07读写\x07\x07"
        "输入寄存器 功能码: 读(0x04)\x07\x07"
        "5\x07输入寄存器\x07实时电流A\x070~0xFFFF(单位：0.01A)\x07只读\x07\x07"
        "7\x07输入寄存器\x07电度计量H\x070~0xFFFF\x07只读\x07\x07"
        "8\x07输入寄存器\x07电度计量L\x070~0xFFFF(单位：0.001度)\x07只读\x07\x07"
        "注：标注类型均为16位无符整型。RS485。"
    )
    return text.encode("utf-16le")


def sample_docx() -> bytes:
    cells = ["5", "输入寄存器", "实时电流A", "单位：0.01A", "只读"]
    row = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>"
                  for cell in cells)
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:tbl><w:tr>'
        + row + "</w:tr></w:tbl></w:body></w:document>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def south_fields() -> dict[str, str]:
    return {
        "通道编号": "CH_1", "设备类型": "智能空开", "设备型号": "ZNKK_V2",
        "厂商名称": "测试厂商", "设备编码": "BRK_01", "协议类型": "MODBUS-RTU",
        "MODBUS设备地址": "2", "电站编号": "测试电站",
    }


def test_validation_blocks_overlap_width_and_duplicate_north_numbers():
    point = {"name": "电流", "address": "10", "function": "遥测", "register_type": "输入寄存器",
             "access": "RO", "data_type": "UINT32", "count": "2", "gain": "1", "unit": "A"}
    overlapping = {**point, "name": "功率", "address": "11"}
    result = validate_points("south", south_fields(), [point, overlapping])
    assert not result["valid"]
    assert any("重叠" in error["message"] for error in result["errors"])
    wrong = validate_points("south", south_fields(), [{**point, "count": "1"}])
    assert any("应占2" in error["message"] for error in wrong["errors"])
    north = [{"name": "电流", "north_address": "1", "function": "遥测", "unit": "A"},
             {"name": "电压", "north_address": "1", "function": "遥测", "unit": "V"}]
    assert not validate_points("north", {}, north)["valid"]
    try:
        generate_csv("north", {}, north)
    except PointTableError:
        pass
    else:
        raise AssertionError("重复北向点号不能导出")


def test_protocol_drives_candidates_and_missing_north_numbers():
    south = parse_protocol("智能空开协议.doc", sample_doc(), "south")
    points = south["points"]
    assert south["suggested_fields"]["设备类型"] == "智能空开"
    assert len(points) == 5  # 读线圈、保持寄存器读写、输入电流、合并电量
    current = next(point for point in points if point["name"] == "实时电流A")
    assert (current["address"], current["register_type"], current["gain"]) == (
        "5", "输入寄存器", "100",
    )
    energy = next(point for point in points if point["name"] == "累计电量")
    assert (energy["data_type"], energy["count"], energy["gain"]) == (
        "UINT32", "2", "1000",
    )
    north = parse_protocol("智能空开协议.doc", sample_doc(), "north")
    assert all(point["north_address"] == "" for point in north["points"])
    try:
        generate_csv("north", {}, north["points"])
    except PointTableError as exc:
        assert "地址" in str(exc)
    else:
        raise AssertionError("北向地址缺失时不应生成")


def test_word_table_can_supply_points():
    parsed = parse_protocol("设备协议.docx", sample_docx(), "south")
    assert parsed["points"][0]["address"] == "5"
    assert parsed["points"][0]["gain"] == "100"


def test_south_csv_requires_site_fields_and_preserves_protocol_values():
    points = parse_protocol("智能空开协议.doc", sample_doc(), "south")["points"]
    try:
        generate_csv("south", {}, points)
    except PointTableError as exc:
        assert "通道编号" in str(exc)
    else:
        raise AssertionError("现场信息缺失时不应生成")
    data, _ = generate_csv("south", south_fields(), points)
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    current = next(row for row in rows if row["信号点名称"] == "实时电流A")
    assert (current["信号地址"], current["增益"], current["MODBUS设备地址"]) == (
        "5", "100", "2",
    )
    assert len(rows[0]) == 24


async def test_upload_requires_login_and_user_can_generate(client, db_session):
    app.dependency_overrides[get_settings] = lambda: Settings(
        auth_enabled=True, confirmation_secret="test-secret"
    )
    upload = {"file": ("device.doc", sample_doc(), "application/msword")}
    assert (await client.post("/point-tables/parse", data={"direction": "south"},
                              files=upload)).status_code == 401
    db_session.add(UserAccount(
        username="staff", password_hash=hash_password("correct-password-123"), role="user"
    ))
    await db_session.commit()
    login = await client.post("/auth/login", json={
        "username": "staff", "password": "correct-password-123",
    })
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    parsed = await client.post("/point-tables/parse", data={"direction": "south"},
                               files=upload, headers=headers)
    assert parsed.status_code == 200
    assert parsed.json()["points"]
    generated = await client.post("/point-tables/generate", headers=headers, json={
        "direction": "south", "fields": south_fields(), "points": parsed.json()["points"],
    })
    assert generated.status_code == 200
    assert generated.content.startswith(b"\xef\xbb\xbf")
    assert generated.headers["cache-control"] == "no-store"
    assert (await client.post("/point-tables/parse", data={"direction": "south"},
                              files={"file": ("bad.txt", b"bad")},
                              headers=headers)).status_code == 422
