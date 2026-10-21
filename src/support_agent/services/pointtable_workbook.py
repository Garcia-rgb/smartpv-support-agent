"""Export the fixed import columns as a local, formula-free review workbook."""

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .pointtable import (
    MAX_OUTPUT_POINTS,
    NORTH_COLUMNS,
    SOUTH_COLUMNS,
    SOUTH_FIELDS,
    generate_csv,
    validate_points,
)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _draft_rows(direction, fields, points, devices):
    """Unresolved device identities remain blank; never pick one of conflicting rows."""
    if not devices:
        expanded = points
    elif all(not point.get("device_code") for point in points):
        if len(devices) * len(points) > MAX_OUTPUT_POINTS:
            from .pointtable import PointTableError

            raise PointTableError("核对版展开超过20000个点位，请拆分制作")
        expanded = [
            {**point, **{"_device_" + key: value for key, value in device.items()}}
            for device in devices
            for point in points
        ]
    else:
        expanded = []
        for point in points:
            code = point.get("device_code", "")
            matches = [device for device in devices if device.get("设备编码") == code]
            common = {}
            for key in (*SOUTH_FIELDS, "父设备"):
                values = {device.get(key, "") for device in matches}
                common[key] = next(iter(values)) if len(values) == 1 else ""
            common["设备编码"] = code
            expanded.append({**point, **{"_device_" + key: value for key, value in common.items()}})
    mappings = {
        "信号点名称": "name",
        "信号地址": "address",
        "信号点功能类型": "function",
        "信号点数据类型": "data_type",
        "单位": "unit",
        "增益": "gain",
        "偏移量": "offset",
        "MODBUS信号类型": "register_type",
        "MODBUS读写类型": "access",
        "MODBUS寄存器个数": "count",
        "是否持久化": "persistence",
        "BIT位": "bit",
        "字典表编码": "dictionary",
        "MODBUS采集周期": "cycle",
    }
    result = []
    for index, point in enumerate(expanded, 1):
        if direction == "north":
            result.append(
                {
                    "信号点名称": point.get("name", ""),
                    "信号点地址": point.get("north_address", ""),
                    "功能类型": point.get("function", ""),
                    "单位": point.get("unit", ""),
                }
            )
            continue
        row = dict.fromkeys(SOUTH_COLUMNS, "")
        row.update({key: point.get("_device_" + key, "") for key in (*SOUTH_FIELDS, "父设备")})
        row.update({key: value for key, value in fields.items() if value and key in row})
        row.update({key: point.get(value, "") for key, value in mappings.items()})
        row["信号点ID"] = str(index)
        if row["协议类型"] == "104":
            for key in ("MODBUS设备地址", "MODBUS信号类型", "MODBUS读写类型", "MODBUS采集周期"):
                row[key] = ""
        result.append(row)
    return result


def _column(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet(matrix, widths, *, freeze=False):
    root = ET.Element("worksheet", xmlns=NS)
    views = ET.SubElement(root, "sheetViews")
    view = ET.SubElement(views, "sheetView", workbookViewId="0")
    if freeze:
        ET.SubElement(view, "pane", ySplit="1", topLeftCell="A2", state="frozen")
    columns = ET.SubElement(root, "cols")
    for index, width in enumerate(widths, 1):
        ET.SubElement(
            columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1"
        )
    data = ET.SubElement(root, "sheetData")
    for row_index, values in enumerate(matrix, 1):
        row = ET.SubElement(
            data,
            "row",
            r=str(row_index),
            ht="48" if len(widths) == 2 and row_index > 1 else "24",
            customHeight="1",
        )
        for column_index, value in enumerate(values, 1):
            numeric = isinstance(value, (int, float))
            cell = ET.SubElement(
                row,
                "c",
                r=f"{_column(column_index)}{row_index}",
                t="n" if numeric else "inlineStr",
                s="1" if row_index == 1 else "0",
            )
            if numeric:
                ET.SubElement(cell, "v").text = str(value)
            else:
                text = ET.SubElement(ET.SubElement(cell, "is"), "t")
                text.text = str(value) if value is not None else ""
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def generate_review_workbook(direction, fields, points, devices=None):
    devices = devices or []
    validation = validate_points(direction, fields, points, devices)
    if validation["valid"]:
        csv_data, _ = generate_csv(direction, fields, points, devices)
        rows = list(csv.DictReader(io.StringIO(csv_data.decode("utf-8-sig"))))
    else:
        rows = _draft_rows(direction, fields, points, devices)
    columns = SOUTH_COLUMNS if direction == "south" else NORTH_COLUMNS
    numeric_columns = {
        "信号地址",
        "信号点地址",
        "信号点ID",
        "增益",
        "偏移量",
        "MODBUS设备地址",
        "MODBUS寄存器个数",
        "MODBUS采集周期",
        "是否持久化",
        "BIT位",
    }
    matrix = []
    for row in rows:
        values = []
        for key in columns:
            value = row.get(key, "")
            if key in numeric_columns and re.fullmatch(r"-?\d+(?:\.\d+)?", str(value)):
                value = float(value) if "." in str(value) else int(value)
            values.append(value)
        matrix.append(values)
    notes = [
        ["核对项", "说明"],
        [
            "状态",
            "检查通过，仍需现场对点"
            if validation["valid"]
            else "未通过检查：仅供核对，不可直接导入现场",
        ],
        [
            "固定格式",
            f"{'南向24列' if direction == 'south' else '北向4列'}；CSV用于软件导入，Excel用于核对",
        ],
        ["记录数量", str(len(rows))],
        ["协议", "104与Modbus按各自设备关联后可合并；104的Modbus从站、寄存器类型和采集周期留空"],
        ["缺失字段", "留空表示来源中缺失或存在冲突，请核实后在点表制作界面补充"],
        ["地址与倍率", "保留来源地址和增益；不自动转换地址基数或取倍率倒数"],
    ]
    notes.extend([[f"错误 第{item['row']}行", item["message"]] for item in validation["errors"]])
    notes.extend([[f"提示 第{item['row']}行", item["message"]] for item in validation["warnings"]])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels"
ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml"
ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml"
ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml"
ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml"
ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml"
ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="导入点表" sheetId="1" r:id="rId1"/>
<sheet name="制作说明" sheetId="2" r:id="rId2"/>
</sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
Target="styles.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/styles.xml",
            """<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font>
<sz val="11"/>
<color rgb="FF000000"/>
<name val="Arial"/>
</font>
<font>
<b/>
<sz val="11"/>
<color rgb="FF000000"/>
<name val="Arial"/>
</font>
</fonts>
<fills count="2">
<fill>
<patternFill patternType="none"/>
</fill>
<fill>
<patternFill patternType="gray125"/>
</fill>
</fills>
<borders count="1">
<border/>
</borders>
<cellStyleXfs count="1">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
</cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>
</cellXfs>
</styleSheet>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            _sheet(
                [columns, *matrix],
                [max(16, min(30, len(key) * 2 + 2)) for key in columns],
                freeze=True,
            ),
        )
        archive.writestr("xl/worksheets/sheet2.xml", _sheet(notes, [24, 110]))
    suffix = "核对版" if validation["valid"] else "待确认核对版"
    return output.getvalue(), f"点表_{'南向' if direction == 'south' else '北向'}_{suffix}.xlsx"
