"""Bounded local CSV/XLSX reading and explicit field/value reconciliation."""

import csv
import io
import re
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_ROWS = 2000
MAX_BYTES = 10 * 1024 * 1024
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _table(rows: list[list[str]]) -> dict:
    if len(rows) < 2:
        raise ValueError("文件至少需要表头和一行数据")
    headers = [str(value).strip() for value in rows[0]]
    if len(headers) > 100 or not all(headers) or len(set(headers)) != len(headers):
        raise ValueError("表头最多100列，不能为空或重复")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError("数据超过2000行，请按设备或时间拆分上传")
    items = []
    for row in rows[1:]:
        if len(row) > len(headers):
            raise ValueError("存在超出表头的列，请检查文件格式")
        if any(len(str(cell)) > 1000 for cell in row):
            raise ValueError("单元格文字过长，请仅上传需要核对的数据")
        if not any(str(cell).strip() for cell in row):
            continue
        items.append(dict(zip(headers, [*row, *([""] * (len(headers) - len(row)))], strict=True)))
    return {"columns": headers, "rows": items}


def read_table(filename: str, data: bytes, sheet: str = "") -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("请上传10MB以内的非空CSV或XLSX文件")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("gb18030")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV编码无法识别，请另存为UTF-8") from exc
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for row in csv.reader(io.StringIO(text), dialect):
            rows.append(row)
            if len(rows) > MAX_ROWS + 1:
                raise ValueError("数据超过2000行，请拆分上传")
        return {**_table(rows), "sheets": [], "sheet": "", "warnings": []}
    if suffix != ".xlsx":
        raise ValueError("支持CSV和XLSX；旧版XLS请另存为XLSX")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError("工作簿解压后过大，请拆分文件")
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                node.attrib["Id"]: node.attrib["Target"]
                for node in rels
                if node.attrib.get("TargetMode") != "External"
            }
            sheets = workbook.findall("s:sheets/s:sheet", NS)
            names = [node.attrib["name"] for node in sheets]
            if not names:
                raise ValueError("工作簿没有工作表")
            selected = sheet or names[0]
            if selected not in names:
                raise ValueError("指定的工作表不存在")
            node = sheets[names.index(selected)]
            rel_id = node.attrib[
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            ]
            target = targets[rel_id].lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            if ".." in path.split("/"):
                raise ValueError("工作表路径不合法")
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = [
                    "".join(item.itertext())
                    for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(
                        "s:si", NS
                    )
                ]
            root = ET.fromstring(archive.read(path))
            rows, formulas = [], 0
            for row in root.findall("s:sheetData/s:row", NS):
                values = {}
                for cell in row.findall("s:c", NS):
                    letters = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
                    if not letters:
                        raise ValueError("单元格缺少坐标")
                    index = 0
                    for letter in letters.group():
                        index = index * 26 + ord(letter) - ord("A") + 1
                    if index > 100:
                        raise ValueError("工作表超过100列，请只上传需要核对的列")
                    kind = cell.attrib.get("t", "")
                    value = cell.findtext("s:v", "", NS)
                    if cell.find("s:f", NS) is not None:
                        formulas += 1
                        # Cached formula values can be stale: require exported values.
                        value = ""
                    elif kind == "s":
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(cell.find("s:is", NS).itertext())
                    elif kind == "e":
                        value = ""
                    values[index - 1] = value
                if values:
                    rows.append([values.get(index, "") for index in range(max(values) + 1)])
                if len(rows) > MAX_ROWS + 1:
                    raise ValueError("数据超过2000行，请拆分上传")
            warnings = ["公式单元格不参与计算，请粘贴为值后核对"] if formulas else []
            return {**_table(rows), "sheets": names, "sheet": selected, "warnings": warnings}
    except ValueError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("无法读取工作簿，请重新导出为XLSX或CSV") from exc


def number(value: str) -> Decimal:
    try:
        result = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("数值格式不正确") from exc
    if not result.is_finite() or abs(result) > Decimal("1e15"):
        raise ValueError("数值必须有限且绝对值不超过10的15次方")
    return result


def compare_tables(left: dict, right: dict, tolerance: str) -> dict:
    allowed = number(tolerance)
    if allowed < 0:
        raise ValueError("允许误差不能小于0")
    issues, counts = [], Counter()

    def issue(kind, key, detail):
        counts[kind] += 1
        if len(issues) < 200:
            issues.append({"kind": kind, "key": key, "detail": detail})

    def index_table(table, label):
        index, duplicates = {}, set()
        for row in table["rows"]:
            for column in (
                table["key_column"],
                table["value_column"],
                table.get("unit_column"),
                table.get("gain_column"),
            ):
                if column and column not in row:
                    raise ValueError(f"{label}不存在列：{column}")
            key = row.get(table["key_column"], "").strip()
            if not key:
                issue("缺少匹配字段", label, "匹配字段为空")
            elif key in index:
                duplicates.add(key)
                issue("重复匹配字段", key, label + "重复，未自动选择其中一条")
            else:
                index[key] = row
        return index, duplicates

    a, duplicate_a = index_table(left, "参考文件")
    b, duplicate_b = index_table(right, "待核对文件")
    for key in sorted(a.keys() - b.keys()):
        issue("缺失数据", key, "待核对文件中没有此项")
    for key in sorted(b.keys() - a.keys()):
        issue("多余数据", key, "参考文件中没有此项")
    compared = 0
    for key in sorted(a.keys() & b.keys() - duplicate_a - duplicate_b):
        lrow, rrow = a[key], b[key]
        unit_l = lrow.get(left.get("unit_column", ""), "").strip()
        unit_r = rrow.get(right.get("unit_column", ""), "").strip()
        if unit_l != unit_r:
            issue(
                "单位不一致",
                key,
                f"参考：{unit_l or '未填写'}；待核对：{unit_r or '未填写'}，未自动换算",
            )
            continue
        try:
            lraw = number(lrow[left["value_column"]])
            rraw = number(rrow[right["value_column"]])
            lgain = number(lrow[left["gain_column"]]) if left.get("gain_column") else Decimal(1)
            rgain = number(rrow[right["gain_column"]]) if right.get("gain_column") else Decimal(1)
            if lgain <= 0 or rgain <= 0:
                raise ValueError("倍率应大于0")
            lv, rv = lraw * lgain, rraw * rgain
        except ValueError as exc:
            issue("无效数值或倍率", key, str(exc))
            continue
        compared += 1
        if lgain != rgain:
            issue("倍率不一致", key, f"参考：{lgain}；待核对：{rgain}")
        if abs(lv - rv) > allowed:
            issue("数值差异", key, f"乘倍率后参考：{lv}；待核对：{rv}；差值：{rv - lv}")
    return {
        "compared": compared,
        "counts": dict(counts),
        "issues": issues,
        "total_issues": sum(counts.values()),
        "truncated": sum(counts.values()) > 200,
        "sources": [
            {"name": table["name"], "data_time": table.get("data_time") or "未提供"}
            for table in (left, right)
        ],
        "note": (
            "仅核对所选工作表和字段；数据来自上传文件，并非平台实时数据。倍率按原始值×倍率计算。"
        ),
    }
