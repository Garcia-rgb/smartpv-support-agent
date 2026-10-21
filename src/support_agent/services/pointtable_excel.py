"""Map local workbook columns to the fixed point-table fields without model calls."""

import csv
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .data_check import read_table

DEVICE_ALIASES = {
    "通道编号": ("通道编号", "通道编码"),
    "设备类型": ("设备类型",),
    "设备型号": ("设备型号",),
    "厂商名称": ("厂商名称", "厂家"),
    "设备编码": ("设备编码", "业务编号"),
    "协议类型": ("协议类型", "协议"),
    "MODBUS设备地址": ("MODBUS设备地址", "设备地址", "从站地址"),
    "电站编号": ("电站编号", "电站名称"),
    "父设备": ("父设备",),
    "通管机IP": ("通管机IP", "IP地址"),
    "端口": ("端口",),
}
POINT_ALIASES = {
    "name": ("信号点名称", "信号名称", "点位名称"),
    "address": ("信号地址", "地址", "寄存器地址", "信息体地址"),
    "north_address": ("信号点地址", "北向点号", "北向地址"),
    "function": ("信号点功能类型", "功能类型"),
    "data_type": ("信号点数据类型", "数据类型"),
    "unit": ("单位",),
    "gain": ("增益", "倍率"),
    "offset": ("偏移量",),
    "register_type": ("MODBUS信号类型", "寄存器类型"),
    "access": ("MODBUS读写类型", "读写类型", "读写"),
    "count": ("MODBUS寄存器个数", "寄存器个数"),
    "persistence": ("是否持久化",),
    "bit": ("BIT位", "BIT"),
    "dictionary": ("字典表编码",),
    "dictionary_name": ("归一化名称",),
    "device_code": ("设备编码", "业务编号"),
    "parent": ("父设备",),
    "cycle": ("MODBUS采集周期", "采集周期"),
}
NA = {"N/A", "NA", "NULL", "NONE"}


def _pick(row, aliases):
    for name in aliases:
        value = str(row.get(name, "")).strip()
        if value and value.upper() not in NA:
            return value
    return ""


def _protocol(value):
    value = value.strip().upper().replace("_", "-")
    if value in {"104", "IEC104", "IEC-104", "IEC60870-5-104"}:
        return "104"
    if value in {"MODBUS-RTU", "MODBUS-TCP"}:
        return value
    return value


def _dictionary():
    # This is company reference data, deliberately read from the local library,
    # never embedded in public program sources or sent to an external model.
    from ..config import get_settings

    path = Path(get_settings().knowledge_base_dir) / "点表规则" / "DictModel.csv"
    result = {}
    if path.is_file() and path.stat().st_size <= 2_000_000:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = path.read_text(encoding)
                break
            except UnicodeError:
                continue
        else:
            return result
        for row in csv.DictReader(io.StringIO(text)):
            name = row.get("中文名称", "").strip()
            if name:
                result.setdefault(name, []).append(row)
    return result


def _resolve_dictionary(point, devices, reference):
    """Exact name/code plus explicit device scope; ambiguity remains empty."""
    explicit = point.get("dictionary", "")
    candidates = reference.get(point.get("dictionary_name", ""), [])
    if explicit:
        candidates = [row for group in reference.values() for row in group
                      if row.get("信号点编码") == explicit]
    selected = [d for d in devices if d.get("设备编码") == point.get("device_code")]
    if not point.get("device_code"):
        selected = devices
    if not selected and point.get("_device_设备类型"):
        selected = [{"设备类型": point["_device_设备类型"]}]
    # Company device codes are accepted literally. Human aliases are explicit,
    # never inferred from a model string, signal name, or another device's points.
    aliases = {"组串式逆变器": ("INV", "STRING")}
    scopes = {aliases.get(d.get("设备类型"), (d.get("设备类型", ""), "")) for d in selected}
    if len(scopes) != 1:
        return ""
    device_type, instance = next(iter(scopes))
    functions = {"遥测": "YC", "遥信": "YX", "双点遥信": "YX", "遥控": "YK",
                 "双点遥控": "YK", "遥调": "YT", "遥脉": "YM"}
    matches = {
        row.get("信号点编码", "") for row in candidates
        if row.get("层级") == "DEV" and row.get("设备类型") == device_type
        and (not instance or row.get("设备实例") == instance)
        and row.get("信号点类型") == functions.get(point.get("function"))
        and row.get("是否计算点", "否") != "是" and row.get("状态", "1") == "1"
    }
    return next(iter(matches)) if len(matches) == 1 else ""


def parse_excel(filename, data, direction):
    from .pointtable import MAX_BYTES, MAX_OUTPUT_POINTS, MAX_POINTS, SOUTH_FIELDS, PointTableError

    if not data or len(data) > MAX_BYTES:
        raise PointTableError("请上传10MB以内的非空点表文件")
    suffix = Path(filename).suffix.lower()
    tables, warnings = [], []
    try:
        if suffix == ".csv":
            tables.append(read_table(filename, data))
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValueError("工作簿解压后超过50MB")
                root = ET.fromstring(archive.read("xl/workbook.xml"))
                names = [node.attrib["name"] for node in root.findall("{*}sheets/{*}sheet")]
                if len(names) > 30:
                    raise ValueError("工作表超过30个，请拆分文件")
            for name in names:
                try:
                    tables.append(read_table("source.xlsx", data, sheet=name))
                except ValueError as exc:
                    warnings.append(f"工作表{name}未解析：{exc}")
    except (ValueError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise PointTableError(f"无法读取Excel点表：{exc}") from exc
    devices, points, sheets = [], [], []
    dictionary = _dictionary()
    for table in tables:
        warnings.extend(table["warnings"])
        columns = set(table["columns"])
        is_points = bool(columns & set(POINT_ALIASES["name"])) and bool(
            columns & set(POINT_ALIASES["address"] + POINT_ALIASES["north_address"])
        )
        is_devices = not is_points and bool(columns & {"设备型号", "协议类型"})
        if not (is_points or is_devices):
            warnings.append(f"工作表{table['sheet'] or 'CSV'}没有识别到设备或点位表头")
            continue
        sheets.append(table["sheet"] or "CSV")
        for index, row in enumerate(table["rows"], 2):
            source = f"{table['sheet'] or 'CSV'} 第{index}行"
            if is_devices:
                device = {key: _pick(row, aliases) for key, aliases in DEVICE_ALIASES.items()}
                device["协议类型"] = _protocol(device["协议类型"])
                device["source"] = source
                devices.append(device)
                continue
            point = {key: _pick(row, aliases) for key, aliases in POINT_ALIASES.items()}
            if not point["name"]:
                raise PointTableError(f"{source}缺少信号名称，请补充后重试")
            # Keep device data when importing a previously generated flat table.
            for key, aliases in DEVICE_ALIASES.items():
                if key not in {"设备编码", "父设备"}:
                    value = _pick(row, aliases)
                    if value:
                        point["_device_" + key] = _protocol(value) if key == "协议类型" else value
            point["source"] = source
            if "设备编码" in columns:
                point["_device_设备编码"] = point["device_code"]
            if point["parent"]:
                point["_device_父设备"] = point["parent"]
            point["access"] = {"只读": "RO", "只写": "WO"}.get(
                point["access"], point["access"].upper()
            )
            if not point["access"] and point["function"]:
                point["access"] = (
                    "WO" if point["function"] in {"遥控", "双点遥控", "遥调"} else "RO"
                )
            points.append(point)
    if not points:
        raise PointTableError("未找到包含信号名称和地址的工作表，请检查表头")
    flat = direction == "south" and not devices and all(
        point.get("_device_协议类型") and point.get("_device_设备编码") for point in points
    )
    if len(points) > (MAX_OUTPUT_POINTS if flat else MAX_POINTS) or len(devices) > 200:
        raise PointTableError("单次支持500个原始点位、200台设备，请拆分上传")
    protocols = {device["协议类型"] for device in devices if device["协议类型"]}
    protocols.update(
        point.get("_device_协议类型", "") for point in points if point.get("_device_协议类型")
    )
    if len(protocols) > 1 and not flat:
        raise PointTableError("一个文件含多种协议，请将104与Modbus等不同协议分别上传")
    protocol = "多协议" if len(protocols) > 1 else next(iter(protocols), "")
    for point in points:
        point["dictionary"] = _resolve_dictionary(point, devices, dictionary)
        if point.get("_device_协议类型", protocol) == "104":
            point["count"] = point["count"] or "1"
            point["register_type"] = ""
        if direction == "north":
            # Generic source addresses are never silently repurposed as northbound numbers.
            point["north_address"] = point["north_address"] or ""
    if devices:
        codes = [device["设备编码"] for device in devices]
        if len(codes) != len(set(codes)):
            warnings.append("设备表的业务编号重复，须在设备信息中核对；未确认前阻止导出")
        missing = sorted(
            {point["device_code"] for point in points if point["device_code"]} - set(codes)
        )
        if missing:
            warnings.append("点位业务编号找不到设备信息：" + "、".join(missing))
    warnings.append("增益、偏移量、持久化与BIT位按原表保留；空白或冲突项须核对后补充")
    warnings.append("字典编码仅按本机DictModel.csv中明确的设备范围和归一化名称填写；未匹配项留空")
    if direction == "north":
        warnings.append("来源协议地址不等于北向点号，请填写北向地址；不会自动沿用南向地址")
    return {
        "direction": direction,
        "source_name": Path(filename).name,
        "points": points,
        "devices": devices if direction == "south" else [],
        "common_fields": []
        if (devices and direction == "south") or flat
        else (
            [field for field in SOUTH_FIELDS if protocol != "104" or field != "MODBUS设备地址"]
            if direction == "south"
            else []
        ),
        "suggested_fields": {"协议类型": protocol} if protocol else {},
        "protocol": protocol,
        "sheets": sheets,
        "warnings": list(dict.fromkeys(warnings)),
        "merged": flat,
    }


def parse_protocol_batch(sources, direction):
    """Expand each source in its own protocol scope, then concatenate the rows."""
    from .pointtable import MAX_OUTPUT_POINTS, PointTableError, _expand_points, parse_protocol

    if not 1 <= len(sources) <= 10:
        raise PointTableError("每次请选择1～10个来源文件")
    parsed = [parse_protocol(name, data, direction) for name, data in sources]
    if len(parsed) == 1:
        return parsed[0]
    if direction != "south":
        raise PointTableError("多文件合并目前用于南向点表，北向请单独上传")
    points, warnings = [], []
    for source in parsed:
        expanded = _expand_points(direction, source["points"], source.get("devices", []))
        for point in expanded:
            if not point.get("_device_协议类型"):
                raise PointTableError(
                    f"{source['source_name']}缺少设备协议信息，请单独解析补全后再合并"
                )
            points.append({
                **point, "source": source["source_name"] + " · " + point.get("source", "")
            })
        warnings.extend(source["warnings"])
        if len(points) > MAX_OUTPUT_POINTS:
            raise PointTableError("合并后超过20000个点位，请拆分制作")
    protocols = sorted({point["_device_协议类型"] for point in points})
    return {
        "direction": direction, "source_name": "、".join(name for name, _ in sources),
        "protocol": protocols[0] if len(protocols) == 1 else "多协议",
        "points": points, "devices": [], "common_fields": [], "suggested_fields": {},
        "warnings": list(dict.fromkeys(warnings)), "sheets": [], "merged": True,
    }
