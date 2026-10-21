import csv
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from support_agent.config import Settings, get_settings
from support_agent.main import app
from support_agent.models import UserAccount
from support_agent.services.auth import hash_password
from support_agent.services.data_check import read_table
from support_agent.services.pointtable import (
    NORTH_COLUMNS,
    SOUTH_COLUMNS,
    PointTableError,
    generate_csv,
    parse_protocol,
    validate_points,
)
from support_agent.services.pointtable_workbook import generate_review_workbook

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(devices, points):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="设备" sheetId="1" r:id="rId1"/>
<sheet name="信息点表" sheetId="2" r:id="rId2"/>
</sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships>
<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
</Relationships>""",
        )
        for index, records in enumerate([devices, points], 1):
            headers = list(records[0])
            root = ET.Element("worksheet", xmlns=NS)
            data = ET.SubElement(root, "sheetData")
            for row_index, row in enumerate([headers, *[list(r.values()) for r in records]], 1):
                element = ET.SubElement(data, "row", r=str(row_index))
                for column_index, value in enumerate(row):
                    cell = ET.SubElement(
                        element, "c", r=f"{chr(65 + column_index)}{row_index}", t="inlineStr"
                    )
                    ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(value)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(root))
    return output.getvalue()


def device(code="INV01", protocol="MODBUS-RTU", slave="1"):
    return {
        "设备类型": "组串式逆变器",
        "设备型号": code,
        "协议类型": protocol,
        "厂商名称": "测试厂商",
        "通道编号": "CH1",
        "设备地址": slave,
        "业务编号": code,
        "电站名称": "测试电站",
    }


def point(name="电网频率", address="100", function="遥测"):
    return {
        "信号名称": name,
        "功能类型": function,
        "数据类型": "INT16",
        "单位": "Hz",
        "增益": "100",
        "地址": address,
        "寄存器个数": "1",
        "是否持久化": "0",
        "偏移量": "-2.5",
        "BIT": "N/A",
        "寄存器类型": "输入寄存器",
    }


def rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_multiple_sheets_join_devices_and_preserve_scaling_offsets_and_zero():
    parsed = parse_protocol(
        "modbus.xlsx",
        workbook([device(), device("INV02", slave="2")], [point(), point("电流", "101")]),
        "south",
    )
    assert parsed["protocol"] == "MODBUS-RTU"
    result = validate_points("south", {}, parsed["points"], parsed["devices"])
    assert result["valid"] and result["output_points"] == 4
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    output = rows(data)
    assert tuple(output[0]) == SOUTH_COLUMNS
    assert [r["设备编码"] for r in output] == ["INV01", "INV01", "INV02", "INV02"]
    assert [r["MODBUS设备地址"] for r in output] == ["1", "1", "2", "2"]
    assert output[0]["增益"] == "100"  # No silent reciprocal conversion.
    assert output[0]["偏移量"] == "-2.5"
    assert output[0]["是否持久化"] == "0"
    assert [r["信号点ID"] for r in output] == ["1", "2", "3", "4"]


@pytest.mark.parametrize("protocol", ["MODBUS-RTU", "104"])
def test_confirmed_template_fields_follow_source_device_and_protocol(protocol, monkeypatch):
    """Lock fields confirmed against the manual reference, without customer data."""
    from support_agent.services import pointtable_excel

    monkeypatch.setattr(pointtable_excel, "_dictionary", lambda: {"测试归一化": [{
        "信号点编码": "TEST_POWER_YC", "中文名称": "测试归一化", "层级": "DEV",
        "设备类型": "INV", "设备实例": "STRING", "信号点类型": "YC",
        "是否计算点": "否", "状态": "1",
    }]})
    source = {
        **point("测试功率", "3004"),
        "数据类型": "FLOAT" if protocol == "104" else "INT32",
        "单位": "kW",
        "寄存器个数": "1" if protocol == "104" else "2",
        "归一化名称": "测试归一化",
        "业务编号": "INV01",
    }
    parsed = parse_protocol(
        "contract.xlsx", workbook([device(protocol=protocol)], [source]), "south"
    )
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    actual = rows(data)[0]
    confirmed = {
        "通道编号": "CH1",
        "设备类型": "组串式逆变器",
        "设备型号": "INV01",
        "厂商名称": "测试厂商",
        "设备编码": "INV01",
        "协议类型": protocol,
        "信号点名称": "测试功率",
        "信号地址": "3004",
        "信号点功能类型": "遥测",
        "信号点数据类型": source["数据类型"],
        "单位": "kW",
        "MODBUS设备地址": "" if protocol == "104" else "1",
        "MODBUS信号类型": "" if protocol == "104" else "输入寄存器",
        "MODBUS读写类型": "" if protocol == "104" else "RO",
        "MODBUS寄存器个数": source["寄存器个数"],
        "是否持久化": "0",
        "字典表编码": "TEST_POWER_YC",
        "BIT位": "",
        "父设备": "",
        "电站编号": "测试电站",
    }
    assert tuple(actual) == SOUTH_COLUMNS
    assert {key: actual[key] for key in confirmed} == confirmed


def test_104_is_a_separate_southbound_protocol_without_modbus_constraints():
    points = [
        {**point("开关", "70001", "遥控"), "数据类型": "FLOAT", "业务编号": "DEV01"},
        {**point("电压", "70002"), "数据类型": "FLOAT", "业务编号": "DEV02"},
    ]
    parsed = parse_protocol(
        "104.xlsx",
        workbook([device("DEV01", "104", ""), device("DEV02", "104", "")], points),
        "south",
    )
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    output = rows(data)
    assert len(output) == 2 and tuple(output[0]) == SOUTH_COLUMNS
    assert all(r["协议类型"] == "104" for r in output)
    for row in output:
        assert row["MODBUS设备地址"] == row["MODBUS信号类型"] == row["MODBUS读写类型"] == ""
        assert row["信号点数据类型"] == "FLOAT"
    assert output[0]["信号地址"] == "70001"
    assert all(row["MODBUS采集周期"] == "" for row in output)


def test_batch_keeps_protocol_scopes_and_unique_ids_with_source_offsets():
    from support_agent.services.pointtable_excel import parse_protocol_batch

    sources = [
        ("modbus.xlsx", workbook([device("INV01"), device("INV02", slave="2")], [point()])),
        ("104.xlsx", workbook([device("DEV01", "104", "")], [
            {**point("遥信", "100", "遥信"), "数据类型": "FLOAT", "业务编号": "DEV01",
             "偏移量": "0"},
        ])),
    ]
    parsed = parse_protocol_batch(sources, "south")
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    output = rows(data)
    assert len(output) == 3
    assert [row["信号点ID"] for row in output] == ["1", "2", "3"]
    assert [row["设备编码"] for row in output] == ["INV01", "INV02", "DEV01"]
    assert [row["协议类型"] for row in output] == ["MODBUS-RTU", "MODBUS-RTU", "104"]
    assert output[-1]["MODBUS采集周期"] == ""
    assert output[-1]["偏移量"] == "0"
    assert [row["增益"] for row in output] == ["100", "100", "100"]
    assert output[-1]["设备型号"] == "DEV01"


def test_dictionary_requires_device_scope_unique_name_and_signal_type():
    from support_agent.services.pointtable_excel import _resolve_dictionary

    row = {"信号点编码": "TEST_INV_YC", "层级": "DEV", "设备类型": "INV",
           "设备实例": "STRING", "信号点类型": "YC", "状态": "1", "是否计算点": "否"}
    refs = {"功率": [row]}
    source = {"dictionary_name": "功率", "function": "遥测"}
    assert _resolve_dictionary(source, [device()], refs) == "TEST_INV_YC"
    assert _resolve_dictionary(source, [{**device(), "设备类型": "通用设备"}], refs) == ""
    assert _resolve_dictionary({**source, "function": "遥控"}, [device()], refs) == ""
    ambiguous = {"功率": [row, {**row, "信号点编码": "OTHER_YC"}]}
    assert _resolve_dictionary(source, [device()], ambiguous) == ""
    assert _resolve_dictionary({**source, "dictionary_name": "不在字典中"}, [device()], refs) == ""


def test_merged_csv_over_500_rows_can_be_uploaded_and_exported_without_data_loss():
    from support_agent.services.pointtable_excel import parse_protocol_batch

    parsed = parse_protocol_batch([
        ("modbus.xlsx", workbook(
            [device(f"INV{i:02}", slave=str(i)) for i in range(1, 47)],
            [point(f"测点{i}", str(100 + i)) for i in range(16)],
        )),
        ("104.xlsx", workbook([device("DEV01", "104", "")], [
            {**point("104测点", "70001"), "数据类型": "FLOAT", "业务编号": "DEV01"},
        ])),
    ], "south")
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    assert len(rows(data)) == 737
    uploaded = parse_protocol("merged.csv", data, "south")
    assert uploaded["merged"] and uploaded["protocol"] == "多协议"
    assert uploaded["common_fields"] == []
    regenerated, _ = generate_csv("south", {}, uploaded["points"], uploaded["devices"])
    assert regenerated == data


def test_duplicate_source_identity_blocks_csv_and_review_does_not_guess_device_model():
    devices = [device("DEV01", "104", ""), {**device("DEV01", "104", ""), "设备型号": "DEV02"}]
    points = [{**point(), "数据类型": "FLOAT", "业务编号": "DEV01"}]
    parsed = parse_protocol("104.xlsx", workbook(devices, points), "south")
    assert any("重复" in item for item in parsed["warnings"])
    with pytest.raises(PointTableError, match="重复"):
        generate_csv("south", {}, parsed["points"], parsed["devices"])
    data, filename = generate_review_workbook("south", {}, parsed["points"], parsed["devices"])
    assert "待确认" in filename
    imported = read_table("review.xlsx", data, "导入点表")
    assert imported["columns"] == list(SOUTH_COLUMNS)
    assert imported["rows"][0]["设备型号"] == ""
    assert "未通过" in str(read_table("review.xlsx", data, "制作说明")["rows"])


def test_modbus_and_104_cannot_be_implicitly_combined():
    with pytest.raises(PointTableError, match="多种协议"):
        parse_protocol(
            "mixed.xlsx", workbook([device(), device("OTHER", "104")], [point()]), "south"
        )


def test_large_device_expansion_is_complete_and_overlaps_are_scoped_to_device():
    devices = [device(f"INV{i:02}", slave=str(i)) for i in range(1, 47)]
    points = [point(f"信号{i}", str(100 + i)) for i in range(16)]
    parsed = parse_protocol("large.xlsx", workbook(devices, points), "south")
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    assert len(rows(data)) == 736
    parsed["points"].append({**parsed["points"][0], "name": "真实冲突"})
    assert not validate_points("south", {}, parsed["points"], parsed["devices"])["valid"]


def test_northbound_template_does_not_reuse_modbus_source_addresses():
    parsed = parse_protocol("source.xlsx", workbook([device()], [point()]), "north")
    assert parsed["points"][0]["north_address"] == ""
    with pytest.raises(PointTableError, match="地址"):
        generate_csv("north", {}, parsed["points"])
    parsed["points"][0]["north_address"] = "16385"
    data, _ = generate_csv("north", {}, parsed["points"])
    assert tuple(rows(data)[0]) == NORTH_COLUMNS
    assert rows(data)[0]["信号点地址"] == "16385"


def test_bit_zero_and_distinct_bits_at_same_register_are_preserved():
    points = [
        {**point("故障0"), "数据类型": "BIT", "BIT": "0"},
        {**point("故障1"), "数据类型": "BIT", "BIT": "1"},
    ]
    parsed = parse_protocol("bits.xlsx", workbook([device()], points), "south")
    data, _ = generate_csv("south", {}, parsed["points"], parsed["devices"])
    assert [r["BIT位"] for r in rows(data)] == ["0", "1"]


async def test_authenticated_excel_upload_validation_and_both_exports(client, db_session):
    app.dependency_overrides[get_settings] = lambda: Settings(auth_enabled=True)
    db_session.add(
        UserAccount(
            username="exceluser", role="user", password_hash=hash_password("correct-password")
        )
    )
    await db_session.commit()
    login = await client.post(
        "/auth/login", json={"username": "exceluser", "password": "correct-password"}
    )
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    parsed = await client.post(
        "/point-tables/parse",
        headers=headers,
        data={"direction": "south"},
        files={"file": ("modbus.xlsx", workbook([device()], [point()]))},
    )
    assert parsed.status_code == 200
    payload = {
        "direction": "south",
        "fields": {},
        "points": parsed.json()["points"],
        "devices": parsed.json()["devices"],
    }
    checked = await client.post("/point-tables/validate", headers=headers, json=payload)
    assert checked.json()["valid"]
    csv_response = await client.post("/point-tables/generate", headers=headers, json=payload)
    assert csv_response.status_code == 200
    assert tuple(rows(csv_response.content)[0]) == SOUTH_COLUMNS
    xlsx_response = await client.post(
        "/point-tables/generate", headers=headers, json={**payload, "output_format": "xlsx"}
    )
    assert xlsx_response.status_code == 200 and xlsx_response.content.startswith(b"PK")
    assert xlsx_response.headers["cache-control"] == "no-store"
    assert read_table("review.xlsx", xlsx_response.content, "导入点表")["columns"] == list(
        SOUTH_COLUMNS
    )
    merged = await client.post(
        "/point-tables/parse", headers=headers, data={"direction": "south"},
        files=[
            ("files", ("modbus.xlsx", workbook([device()], [point()]))),
            ("files", ("104.xlsx", workbook([device("DEV01", "104", "")], [
                {**point("电压", "70001"), "数据类型": "FLOAT", "业务编号": "DEV01"},
            ]))),
        ],
    )
    assert merged.status_code == 200 and merged.json()["merged"]
    merged_payload = {"direction": "south", "fields": {}, "devices": [],
                      "points": merged.json()["points"]}
    response = await client.post("/point-tables/generate", headers=headers, json=merged_payload)
    assert response.status_code == 200
    exported = rows(response.content)
    assert len(exported) == 2 and len({row["信号点ID"] for row in exported}) == 2
    assert {row["协议类型"] for row in exported} == {"104", "MODBUS-RTU"}
