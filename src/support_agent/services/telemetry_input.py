"""Keep spatial OCR rows, without guessing which column belongs to a PV channel."""

import re
from statistics import median

TELEMETRY = re.compile(
    r"(?:[ABC]相|输入|电网|直流|交流).{0,6}(?:电压|电流)|"
    r"有功功率|无功功率|输入总功率|功率因数|电网频率|组串容量|机内温度", re.I
)
PLATFORM = re.compile(r"设备详情|实时信息|实时数据|组串容量|遥测|PV\d+", re.I)


def spatial_telemetry_rows(result) -> list[str]:
    texts = list(result.txts or ())
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) != len(texts) or not PLATFORM.search("\n".join(texts)):
        return []
    cells = []
    for text, box in zip(texts, boxes, strict=True):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        cells.append((min(xs), (min(ys) + max(ys)) / 2, max(ys) - min(ys), text))
    tolerance = max(3, median(c[2] for c in cells) * .55)
    rows = []
    for cell in sorted(cells, key=lambda c: (c[1], c[0])):
        if rows and abs(cell[1] - rows[-1][0][1]) <= tolerance:
            rows[-1].append(cell)
        else:
            rows.append([cell])
    output = []
    for row in rows:
        ordered = sorted(row, key=lambda c: c[0])
        if any(TELEMETRY.search(c[3]) for c in ordered):
            first_field = next(i for i, c in enumerate(ordered) if TELEMETRY.search(c[3]))
            values = [c[3] for c in ordered[first_field:]
                      if c[3] not in {"重置", "导出", "有功调节", "无功调节", "电网"}
                      and not c[3].startswith("NB")]
            numeric = r"[-−]?\d+(?:\.\d+)?|[-—]"
            # Only alternating scalar label/value rows have clear adjacency.
            # Multi-column matrix rows remain explicitly unmapped.
            scalar = len(values) % 2 == 0 and all(
                not re.fullmatch(numeric, values[i])
                and re.fullmatch(numeric, values[i + 1])
                for i in range(0, len(values), 2)
            )
            if scalar:
                values = [values[i] + "=" + values[i + 1] for i in range(0, len(values), 2)]
            output.append(
                ("截图数值行（相邻标量，OCR需核对）：" if scalar
                 else "截图数值行（从左到右，列归属待核对）：") + " | ".join(values)
            )
    return output


def platform_issue(text: str) -> bool:
    return bool(
        PLATFORM.search(text)
        or re.search(r"平台|点表|采集数据", text)
    ) and bool(re.search(r"问题|不对|异常|不正常|没数据|无数据|暂无数据|电压|电流|告警", text))


def general_checks() -> str:
    return (
        "当前判断：暂不能确定根因；以下是通用排查建议，不是已确认的处理结论。\n"
        "1. 先看最后采集时间，将同一时刻的设备本机读数与平台值对照，"
        "区分现场异常、数据陈旧和显示异常。\n"
        "2. 本机正常而平台异常时，核对设备关联、寄存器地址、数据类型、倍率和字序；先只读核对。\n"
        "3. 多台设备同时异常优先检查公共采集链路；仅部分字段异常优先检查点位映射。\n"
        "请补充一组异常字段的本机值、平台值和采集时间；空白不等于零，不能仅凭截图判定硬件损坏。"
    )
