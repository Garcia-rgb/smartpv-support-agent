import io
import zipfile

import pytest

from support_agent.services.data_check import compare_tables, read_table


def table(rows, name="reference"):
    return {
        "name": name,
        "data_time": "",
        "key_column": "点名",
        "value_column": "值",
        "unit_column": "单位",
        "gain_column": "倍率",
        "rows": rows,
    }


def row(key, value, unit="V", gain="1"):
    return {"点名": key, "值": value, "单位": unit, "倍率": gain}


def test_compare_scaled_values_missing_duplicate_units_and_invalid_numbers():
    left = table(
        [
            row("a", "10", gain="2"),
            row("b", "1"),
            row("c", "1"),
            row("d", "1"),
            row("e", "1"),
            row("f", "1"),
            row("g", "1"),
        ]
    )
    right = table(
        [
            row("a", "20"),
            row("b", "2"),
            row("c", "1", "A"),
            row("d", "NaN"),
            row("e", "1"),
            row("e", "2"),
            row("g", "1", gain="0"),
            row("extra", "1"),
        ]
    )
    result = compare_tables(left, right, "0.01")
    assert result["compared"] == 2
    for kind in [
        "倍率不一致",
        "数值差异",
        "单位不一致",
        "重复匹配字段",
        "缺失数据",
        "多余数据",
        "无效数值或倍率",
    ]:
        assert result["counts"][kind] >= 1
    assert result["counts"]["数值差异"] == 1  # 10*2 == 20*1.
    assert result["sources"][0]["data_time"] == "未提供"


def test_csv_rejects_duplicate_headers_and_preserves_ids():
    result = read_table("data.csv", "编号,数值\n0002,5\n".encode())
    assert result["rows"][0]["编号"] == "0002"
    with pytest.raises(ValueError, match="重复"):
        read_table("data.csv", b"id,id\n1,2\n")
    with pytest.raises(ValueError, match="2000"):
        read_table("data.csv", ("id,value\n" + "a,1\n" * 2001).encode())
    with pytest.raises(ValueError):
        compare_tables(table([row("a", "1")]), table([row("a", "1")]), "NaN")


def xlsx():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="测点" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>编号</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>值</t></is></c></row><row r="2">'
            '<c r="A2" t="inlineStr"><is><t>PV1</t></is></c>'
            '<c r="B2"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>',
        )
    return out.getvalue()


def test_xlsx_formula_is_not_trusted_as_a_measured_value():
    result = read_table("readings.xlsx", xlsx())
    assert result["sheet"] == "测点"
    assert result["rows"][0]["值"] == ""
    assert "公式" in result["warnings"][0]
    with pytest.raises(ValueError):
        read_table("readings.xlsx", xlsx(), "不存在")


async def test_local_comparison_api(client):
    uploaded = await client.post(
        "/data-check/read", files={"file": ("a.csv", b"id,value\nA,1\n", "text/csv")}
    )
    assert uploaded.status_code == 200
    payload = {
        "name": "a.csv",
        "key_column": "id",
        "value_column": "value",
        "rows": uploaded.json()["rows"],
    }
    result = await client.post("/data-check/compare", json={"left": payload, "right": payload})
    assert result.status_code == 200
    assert result.json()["total_issues"] == 0
    assert "实时" in result.json()["note"]
