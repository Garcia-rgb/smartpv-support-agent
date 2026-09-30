"""Read uploaded device protocols and build reviewed north/south point tables.

This parser only proposes points that carry an address in the source. It never
uses a device's Modbus register address as an IEC-104 northbound point number.
"""

import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

SOUTH_COLUMNS = (
    "通道编号", "设备类型", "设备型号", "厂商名称", "设备编码", "协议类型",
    "信号点名称", "信号地址", "信号点功能类型", "信号点ID", "信号点数据类型",
    "单位", "增益", "偏移量", "MODBUS设备地址", "MODBUS信号类型",
    "MODBUS读写类型", "MODBUS寄存器个数", "MODBUS采集周期", "是否持久化",
    "字典表编码", "BIT位", "父设备", "电站编号",
)
NORTH_COLUMNS = ("信号点名称", "信号点地址", "功能类型", "单位")
SOUTH_FIELDS = (
    "通道编号", "设备类型", "设备型号", "厂商名称", "设备编码", "协议类型",
    "MODBUS设备地址", "电站编号",
)
FUNCTIONS = {"遥信", "双点遥信", "遥测", "遥控", "双点遥控", "遥调", "遥脉"}
REGISTER_TYPES = {"线圈", "输入离散量", "保持寄存器", "输入寄存器"}
DATA_TYPES = {"BIT", "UINT16", "INT16", "UINT32", "INT32", "FLOAT", "DOUBLE"}
MAX_BYTES = 10 * 1024 * 1024
MAX_POINTS = 500
_ROW_RE = re.compile(
    r"(?<!\d)(0[xX][0-9A-Fa-f]+|\d{1,5})\x07"
    r"(输出线圈|线圈|输入离散量|保持寄存器|输入寄存器)\x07"
    r"([^\x07]{1,120})\x07([^\x07]{0,300})\x07([^\x07]{0,300})\x07"
)
_SITE_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{1,64}\Z")


class PointTableError(ValueError):
    pass


def _word_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 12 * 1024 * 1024:
                raise PointTableError("Word 正文过大")
            root = ElementTree.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise PointTableError("无法读取 Word 正文") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    for table in root.findall(".//w:tbl", ns):
        for row in table.findall("./w:tr", ns):
            cells = ["".join(node.text or "" for node in cell.findall(".//w:t", ns))
                     for cell in row.findall("./w:tc", ns)]
            if len(cells) == 4:
                cells.append("")
            lines.append("\x07".join(cells) + "\x07")
    for paragraph in root.findall(".//w:p", ns):
        line = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if line:
            lines.append(line)
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 80:
            raise PointTableError("PDF 页数超过 80 页，请上传相关协议章节")
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text(extraction_mode="layout") or "")
            except TypeError:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)
    except PointTableError:
        raise
    except Exception as exc:
        raise PointTableError("无法读取 PDF 正文") from exc


def extract_protocol_text(filename: str, data: bytes) -> str:
    if not data or len(data) > MAX_BYTES:
        raise PointTableError("协议文件应为 10 MB 以内的非空文件")
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        text = _word_docx(data)
    elif suffix == ".doc":
        # Legacy Word often stores its table text as UTF-16LE runs. Extraction
        # is best-effort; unsupported compressed .doc files fail explicitly.
        text = data.decode("utf-16le", errors="ignore")
    elif suffix == ".pdf":
        text = _pdf_text(data)
    else:
        raise PointTableError("请上传 Word（.doc/.docx）或 PDF 协议")
    if len(text.strip()) < 30:
        raise PointTableError("未提取到协议文字；扫描版 PDF 请先做 OCR")
    return text[:500_000]


def _raw_rows(text: str) -> list[dict[str, str]]:
    rows = []
    for match in _ROW_RE.finditer(text):
        address, register_type, name, detail, note = match.groups()
        register_type = "线圈" if register_type == "输出线圈" else register_type
        name = re.sub(r"\s+", "", name).strip()
        if not name:
            continue
        # The original Word table has separate read and write coil sections.
        previous = text[:match.start()]
        last_read = max(previous.rfind("读线圈操作"), previous.rfind("功能码: 读"))
        last_write = max(previous.rfind("写线圈操作"), previous.rfind("功能码: 写"))
        access = "WO" if register_type == "线圈" and last_write > last_read else "RO"
        rows.append({
            "address": address,
            "register_type": register_type,
            "name": name,
            "detail": detail.strip(),
            "note": note.strip(),
            "access": access,
        })
    if rows:
        return rows[:MAX_POINTS]
    # Text PDFs often have aligned columns instead of Word cell markers.
    pattern = re.compile(
        r"^\s*(0[xX][0-9A-Fa-f]+|\d{1,5})\s+"
        r"(线圈|输出线圈|输入离散量|保持寄存器|输入寄存器)\s+"
        r"([^\s]{2,60})(?:\s{2,}(.+))?$"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            address, register_type, name, detail = match.groups()
            rows.append({
                "address": address,
                "register_type": "线圈" if register_type == "输出线圈" else register_type,
                "name": name,
                "detail": detail or "",
                "note": "",
                "access": "",
            })
    return rows[:MAX_POINTS]


def _address(value: str, *, north: bool = False) -> str:
    try:
        number = int(value, 16) if value.lower().startswith("0x") else int(value)
    except (TypeError, ValueError) as exc:
        raise PointTableError("点位地址必须是十进制或 0x 开头的十六进制整数") from exc
    if not (1 if north else 0) <= number <= 65535:
        raise PointTableError("点位地址超出允许范围")
    return str(number)


def _unit_gain(detail: str) -> tuple[str, str]:
    match = re.search(r"单位\s*[:：]\s*(0\.\d+|1)\s*(kWh|mA|[A-Za-z%°]+|度)", detail)
    if match:
        step, unit = match.groups()
        gain = str(round(1 / float(step))) if step.startswith("0.") else "1"
        return ("kWh" if unit == "度" else unit), gain
    match = re.search(r"[（(]\s*(mA|kWh|[AVW%])\s*[）)]", detail)
    return (match.group(1), "1") if match else ("", "1")


def _merge_pairs(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    index = 0
    while index < len(rows):
        row = rows[index].copy()
        if index + 1 < len(rows):
            following = rows[index + 1]
            base = row["name"][:-1]
            if (row["name"].endswith("H") and following["name"] == base + "L"
                    and row["register_type"] == following["register_type"]
                    and int(_address(following["address"])) == int(_address(row["address"])) + 1):
                row["name"] = {"客户": "客户唯一号", "电度计量": "累计电量"}.get(base, base)
                row["detail"] += " " + following["detail"]
                row["count"] = "2"
                result.append(row)
                index += 2
                continue
        row["count"] = "1"
        result.append(row)
        index += 1
    return result


def _candidate(row: dict[str, str], *, unsigned16: bool) -> dict[str, str]:
    kind = row["register_type"]
    access = row["access"]
    name = row["name"]
    if kind in {"线圈", "输入离散量"}:
        function = "遥控" if access == "WO" else "遥信"
        data_type = "BIT"
    else:
        function = "遥调" if access == "WO" else (
            "遥信" if "状态" in name or "故障" in name else "遥测"
        )
        data_type = "UINT32" if row["count"] == "2" else (
            "UINT16" if unsigned16 else ""
        )
    unit, gain = _unit_gain(row["detail"])
    if access == "WO":
        name += "命令" if function == "遥控" else "下发"
    elif kind == "线圈" and not name.endswith("状态"):
        name += "状态"
    return {
        "name": name,
        "address": _address(row["address"]),
        "north_address": "",
        "function": function,
        "register_type": kind,
        "access": access,
        "data_type": data_type,
        "count": row["count"],
        "unit": unit,
        "gain": gain,
        "source": (row["name"] + " " + row["detail"]).strip()[:180],
    }


def parse_protocol(filename: str, data: bytes, direction: str) -> dict:
    if direction not in {"south", "north"}:
        raise PointTableError("请选择南向或北向")
    text = extract_protocol_text(filename, data)
    rows = _raw_rows(text)
    if not rows:
        raise PointTableError("未识别到带地址的寄存器表；请上传文字版协议或提供可编辑的点位表")
    rows = _merge_pairs(rows)
    candidates = []
    for row in rows:
        candidates.append(_candidate(row, unsigned16="16位无符" in text))
        if (row["register_type"] == "保持寄存器" and "读写" in row["note"]
                and any(word in row["name"] for word in ("过压", "欠压", "过流", "漏电", "定值"))):
            write_row = row.copy()
            write_row["access"] = "WO"
            candidates.append(_candidate(write_row, unsigned16="16位无符" in text))
    warnings = ["请核对地址基数、数据类型、字序、倍率和控制点；未确认前不要直接投入运行。"]
    if direction == "north":
        warnings.append("设备协议中的 Modbus 地址不是北向点号；请逐点填写北向地址。")
    if any("累计电量" in point["name"] for point in candidates):
        warnings.append("累计电量由两个寄存器合成，字序与实际值须现场对点。")
    suggested_fields = {}
    if "智能空开" in filename or "智能空开" in text[:3000]:
        suggested_fields["设备类型"] = "智能空开"
    if re.search(r"MODBUS[-‐‑–— ]?TCP", text, re.I):
        suggested_fields["协议类型"] = "MODBUS-TCP"
    elif re.search(r"MODBUS[-‐‑–— ]?RTU|RS\s*485", text, re.I):
        suggested_fields["协议类型"] = "MODBUS-RTU"
    return {
        "direction": direction,
        "source_name": Path(filename).name,
        "points": candidates[:MAX_POINTS],
        "common_fields": list(SOUTH_FIELDS) if direction == "south" else [],
        "suggested_fields": suggested_fields,
        "warnings": warnings,
    }


def _safe_text(value: str, label: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise PointTableError(f"{label}格式不正确")
    value = value.strip()
    if required and not value:
        raise PointTableError(f"请填写{label}")
    if len(value) > 100 or value.startswith(("=", "+", "-", "@")) or "\n" in value or "\r" in value:
        raise PointTableError(f"{label}包含不安全内容")
    return value


def generate_csv(
    direction: str, fields: dict[str, str], points: list[dict[str, str]]
) -> tuple[bytes, str]:
    if direction not in {"south", "north"}:
        raise PointTableError("请选择南向或北向")
    if not points or len(points) > MAX_POINTS:
        raise PointTableError("点位数量应为 1～500 个")
    common = {}
    if direction == "south":
        for field in SOUTH_FIELDS:
            value = _safe_text(fields.get(field, ""), field)
            if field == "MODBUS设备地址":
                if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 247:
                    raise PointTableError("MODBUS设备地址应为 1～247 的整数")
            elif field == "协议类型":
                if value not in {"MODBUS-RTU", "MODBUS-TCP"}:
                    raise PointTableError("协议类型应为 MODBUS-RTU 或 MODBUS-TCP")
            elif not _SITE_RE.fullmatch(value):
                raise PointTableError(f"{field}只能使用中英文、数字或下划线")
            common[field] = value
    elif fields:
        raise PointTableError("北向点表不需要南向设备字段")
    output_rows = []
    for index, point in enumerate(points, 1):
        name = _safe_text(point.get("name", ""), f"第{index}个信号名称")
        function = point.get("function", "")
        if function not in FUNCTIONS:
            raise PointTableError(f"第{index}个点的功能类型未确认")
        unit = _safe_text(point.get("unit", ""), "单位", required=False)
        if direction == "north":
            output_rows.append(dict(zip(NORTH_COLUMNS, (
                name, _address(point.get("north_address", ""), north=True), function, unit
            ), strict=True)))
            continue
        register_type = point.get("register_type", "")
        data_type = point.get("data_type", "")
        access = point.get("access", "")
        if (register_type not in REGISTER_TYPES or data_type not in DATA_TYPES
                or access not in {"RO", "WO"}):
            raise PointTableError(f"第{index}个点的寄存器类型、数据类型或读写属性未确认")
        if function in {"遥控", "双点遥控", "遥调"} and access != "WO":
            raise PointTableError(f"第{index}个控制点应为 WO")
        try:
            count = int(point.get("count", ""))
            gain = float(point.get("gain", ""))
        except (TypeError, ValueError) as exc:
            raise PointTableError(f"第{index}个点的寄存器个数或增益未确认") from exc
        if not 1 <= count <= 4 or not 0 < gain <= 1_000_000:
            raise PointTableError(f"第{index}个点的寄存器个数或增益超出范围")
        row = dict.fromkeys(SOUTH_COLUMNS, "")
        row.update(common)
        row.update({
            "信号点名称": name, "信号地址": _address(point.get("address", "")),
            "信号点功能类型": function, "信号点ID": str(index),
            "信号点数据类型": data_type, "单位": unit,
            "增益": str(point["gain"]), "偏移量": "0",
            "MODBUS信号类型": register_type, "MODBUS读写类型": access,
            "MODBUS寄存器个数": str(count), "MODBUS采集周期": "0",
            "是否持久化": "1" if function in {"遥测", "遥脉"} else "0",
        })
        output_rows.append(row)
    columns = SOUTH_COLUMNS if direction == "south" else NORTH_COLUMNS
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(output_rows)
    filename = "点表_南向.csv" if direction == "south" else "点表_北向.csv"
    return output.getvalue().encode("utf-8-sig"), filename
