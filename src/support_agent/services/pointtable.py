"""从本地已核对的点表模板生成 CSV，不推断缺失的协议点位。"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "knowledge_base" / "点表规则"

SOUTH_COLUMNS = (
    "通道编号", "设备类型", "设备型号", "厂商名称", "设备编码", "协议类型",
    "信号点名称", "信号地址", "信号点功能类型", "信号点ID", "信号点数据类型",
    "单位", "增益", "偏移量", "MODBUS设备地址", "MODBUS信号类型",
    "MODBUS读写类型", "MODBUS寄存器个数", "MODBUS采集周期", "是否持久化",
    "字典表编码", "BIT位", "父设备", "电站编号",
)
NORTH_COLUMNS = ("信号点名称", "信号点地址", "功能类型", "单位")


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    filename: str
    description: str
    columns: tuple[str, ...]
    required_fields: tuple[str, ...] = ()
    caution: str = ""


TEMPLATES = (
    Template(
        id="smart_breaker_south",
        name="智能空开南向 · 27 点",
        filename="智能空开南向点表_27点模板.csv",
        description="按智能空开协议 V2.0 整理的 24 列南向点表；填写现场设备信息后导出。",
        columns=SOUTH_COLUMNS,
        required_fields=(
            "通道编号", "设备型号", "厂商名称", "设备编码", "MODBUS设备地址", "电站编号",
        ),
        caution="累计电量点在旧实验中未完成现场对点；导入前还需核对该点的字序与实时值。",
    ),
    Template(
        id="smart_breaker_north",
        name="智能空开北向 · 实验室 19 点",
        filename="智能空开北向点表_实验室可导入.csv",
        description="实验室已导入的 4 列北向点表，地址按原样导出。",
        columns=NORTH_COLUMNS,
        caution="北向地址与现场调度点号可能不同，正式使用前需核对点号和对点结果。",
    ),
)

_NAME_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{1,64}\Z")


class PointTableError(ValueError):
    pass


def get_template(template_id: str) -> Template:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    raise PointTableError("没有这个点表模板")


def read_template(template: Template) -> list[dict[str, str]]:
    path = TEMPLATE_DIR / template.filename
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != template.columns:
                raise PointTableError("模板列名与预期格式不一致，已停止生成")
            rows = list(reader)
    except FileNotFoundError as exc:
        raise PointTableError("本机资料库中找不到这个点表模板") from exc
    if not rows or any(None in row for row in rows):
        raise PointTableError("点表模板有空行或列数不一致")
    key_columns = ("信号点名称", "信号地址") if template.id.endswith("south") else (
        "信号点名称", "信号点地址",
    )
    if any(not all((row[column] or "").strip() for column in key_columns) for row in rows):
        raise PointTableError("模板有缺少名称或地址的点位")
    return rows


def list_templates() -> list[dict]:
    result = []
    for template in TEMPLATES:
        try:
            rows = read_template(template)
        except PointTableError:
            continue
        result.append({
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "row_count": len(rows),
            "required_fields": list(template.required_fields),
            "caution": template.caution,
        })
    return result


def generate_csv(template_id: str, fields: dict[str, str]) -> tuple[bytes, str]:
    template = get_template(template_id)
    if set(fields) - set(template.required_fields):
        raise PointTableError("提交了模板不支持的字段")
    values = {}
    for field in template.required_fields:
        value = fields.get(field, "").strip()
        if not value:
            raise PointTableError(f"请填写{field}")
        if field == "MODBUS设备地址":
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 247:
                raise PointTableError("MODBUS设备地址应为 1～247 的整数")
        elif not _NAME_RE.fullmatch(value):
            raise PointTableError(f"{field}只能使用中英文、数字或下划线，最多 64 个字符")
        values[field] = value
    rows = read_template(template)
    for row in rows:
        row.update(values)
    if template.id == "smart_breaker_south":
        critical = (
            "信号点名称", "信号地址", "信号点功能类型", "信号点数据类型",
            "MODBUS信号类型", "MODBUS读写类型", "MODBUS寄存器个数",
        )
        if any(not all((row[column] or "").strip() for column in critical) for row in rows):
            raise PointTableError("模板缺少点位的关键协议参数，已停止生成")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=template.columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig"), template.name.replace(" · ", "_") + ".csv"
