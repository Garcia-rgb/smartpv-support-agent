"""Bounded, offline Modbus inspection and register decoding. Never sends a packet."""

import math
import re
import struct

from .tools import ToolError

FUNCTIONS = {
    1: "读线圈",
    2: "读离散输入",
    3: "读保持寄存器",
    4: "读输入寄存器",
    5: "写单线圈",
    6: "写单寄存器",
    15: "写多线圈",
    16: "写多寄存器",
}
EXCEPTIONS = {
    1: "非法功能",
    2: "非法数据地址",
    3: "非法数据值",
    4: "服务器设备故障",
    5: "确认/处理中",
    6: "服务器设备忙",
    8: "存储奇偶校验错误",
    10: "网关路径不可用",
    11: "网关目标设备无响应",
}
FORMATS = {
    "UINT16": (1, "H"),
    "INT16": (1, "h"),
    "UINT32": (2, "I"),
    "INT32": (2, "i"),
    "FLOAT32": (2, "f"),
    "FLOAT64": (4, "d"),
    "UINT64": (4, "Q"),
    "INT64": (4, "q"),
}


def hex_bytes(text: str) -> bytes:
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ToolError("请输入一条完整的十六进制报文，最多260字节")
    cleaned = re.sub(r"0[xX]", "", text.strip())
    pieces = re.split(r"[\s,:;，：；\[\]-]+", cleaned)
    if any(len(p) % 2 for p in pieces if p):
        raise ToolError("每段十六进制数据必须包含完整字节，不能把单个半字节拼成报文")
    cleaned = re.sub(r"[\s,:;，：；\[\]-]", "", cleaned)
    if not re.fullmatch(r"[0-9a-fA-F]+", cleaned) or len(cleaned) % 2:
        raise ToolError("报文只能包含成对的十六进制字节，请勿混入日志时间或文字")
    data = bytes.fromhex(cleaned)
    if len(data) > 260:
        raise ToolError("报文超过260字节；请逐条提交，不能拼接多个报文")
    return data


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def parse_modbus_frame(frame: str, transport: str = "auto", direction: str = "auto") -> dict:
    data = hex_bytes(frame)
    if transport not in {"auto", "rtu", "tcp"} or direction not in {"auto", "request", "response"}:
        raise ToolError("传输类型请选择RTU/TCP，方向请选择请求/响应/自动")
    crc_ok = len(data) >= 4 and crc16(data[:-2]) == int.from_bytes(data[-2:], "little")
    if transport == "auto":
        # A CRC-valid RTU request can coincidentally look like an MBAP header.
        transport = (
            "rtu"
            if crc_ok
            else "tcp"
            if (
                len(data) >= 8
                and data[2:4] == b"\x00\x00"
                and int.from_bytes(data[4:6], "big") == len(data) - 6
            )
            else "rtu"
        )
    errors = []
    result = {"transport": transport, "frame": data.hex(" ").upper(), "errors": errors}
    if transport == "rtu":
        if not 4 <= len(data) <= 256:
            raise ToolError("RTU单帧长度应为4至256字节（包含CRC）")
        unit, pdu = data[0], data[1:-2]
        result.update(
            crc_valid=crc_ok,
            crc_received=data[-2:].hex(" ").upper(),
            crc_expected=crc16(data[:-2]).to_bytes(2, "little").hex(" ").upper(),
        )
        if not crc_ok:
            errors.append("CRC校验不通过；可能抄录错误、报文不完整或通信干扰，不能据此判断设备损坏")
        if unit > 247:
            errors.append("RTU设备地址超过247")
    else:
        if not 8 <= len(data) <= 260:
            raise ToolError("TCP单帧长度应为8至260字节")
        unit, pdu = data[6], data[7:]
        length = int.from_bytes(data[4:6], "big")
        result.update(transaction_id=int.from_bytes(data[:2], "big"), mbap_length=length)
        if data[2:4] != b"\x00\x00":
            errors.append("MBAP协议标识应为0")
        if length != len(data) - 6:
            errors.append("MBAP长度与实际字节数不一致；请确认完整单帧")
    function = pdu[0]
    base_function = function & 0x7F
    result.update(
        unit_id=unit,
        function_code=f"0x{function:02X}",
        function_name=FUNCTIONS.get(base_function, "本工具未展开的功能码"),
    )
    if function & 0x80:
        result["direction"] = "response"
        if direction == "request":
            errors.append("异常功能码只能出现在响应中")
        if len(pdu) != 2:
            errors.append("异常响应应仅含功能码和一个异常码")
        else:
            result.update(
                exception_code=f"0x{pdu[1]:02X}",
                exception_name=EXCEPTIONS.get(pdu[1], "未定义/厂家扩展异常码"),
            )
    elif base_function in {1, 2, 3, 4}:
        if direction == "auto":
            if len(pdu) == 5 and pdu[1] == len(pdu) - 2:
                raise ToolError("这条读报文的方向存在歧义，请明确选择请求或响应")
            direction = "request" if len(pdu) == 5 else "response"
        result["direction"] = direction
        if direction == "request":
            if len(pdu) != 5:
                errors.append("读请求应含起始地址和读取数量，各2字节")
            else:
                address, quantity = struct.unpack(">HH", pdu[1:])
                result.update(address_offset=address, quantity=quantity)
                maximum = 2000 if base_function in {1, 2} else 125
                if not 1 <= quantity <= maximum or address + quantity > 65536:
                    errors.append("读取数量超出协议范围或地址范围溢出")
        else:
            if len(pdu) < 2 or pdu[1] != len(pdu) - 2:
                errors.append("响应字节数与数据长度不一致")
            else:
                result["byte_count"] = pdu[1]
                maximum = 250
                if not 1 <= pdu[1] <= maximum:
                    errors.append("读取响应数据长度超出协议范围")
                if base_function in {3, 4}:
                    if pdu[1] % 2:
                        errors.append("寄存器响应数据必须是偶数字节")
                    else:
                        result["registers"] = [
                            int.from_bytes(pdu[i : i + 2], "big") for i in range(2, len(pdu), 2)
                        ]
                else:
                    result["packed_bits_hex"] = pdu[2:].hex(" ").upper()
    elif base_function in {5, 6}:
        result["direction"] = "request_or_echo_response" if direction == "auto" else direction
        if len(pdu) != 5:
            errors.append("单点写入/回显应含地址和值，各2字节")
        else:
            address, value = struct.unpack(">HH", pdu[1:])
            result.update(address_offset=address, value=value)
            if base_function == 5 and value not in {0x0000, 0xFF00}:
                errors.append("写单线圈的值只能为0x0000或0xFF00")
    elif base_function in {15, 16}:
        if direction == "auto":
            direction = "response" if len(pdu) == 5 else "request"
        result["direction"] = direction
        if len(pdu) < 5:
            errors.append("多点写入缺少地址或数量")
        else:
            address, quantity = struct.unpack(">HH", pdu[1:5])
            result.update(address_offset=address, quantity=quantity)
            maximum = 1968 if base_function == 15 else 123
            if not 1 <= quantity <= maximum or address + quantity > 65536:
                errors.append("写入数量超出协议范围或地址范围溢出")
            if direction == "response":
                if len(pdu) != 5:
                    errors.append("多点写入响应长度应为固定回显地址和数量")
            else:
                expected = (quantity + 7) // 8 if base_function == 15 else quantity * 2
                if len(pdu) < 6 or pdu[5] != expected or len(pdu) != 6 + expected:
                    errors.append("写入字节数、数量与数据长度不一致")
                else:
                    result["data_hex"] = pdu[6:].hex(" ").upper()
    else:
        result.update(direction=direction, data_hex=pdu[1:].hex(" ").upper(), supported=False)
        errors.append("该功能码仅显示原始字段，未校验其功能数据格式")
    if transport == "rtu" and unit == 0:
        if base_function in {1, 2, 3, 4} or result.get("direction") == "response":
            errors.append("RTU地址0为广播地址，不能用于读请求或设备响应")
        result["broadcast"] = True
    result["valid"] = not errors
    result["address_note"] = "报文字段为0起算的协议偏移地址；不能直接等同于资料中的40001编号"
    return result


def decode_registers(
    registers: list[int],
    data_type: str,
    byte_order: str = "big",
    word_order: str = "unknown",
    multiplier: float = 1,
    offset: float = 0,
) -> dict:
    data_type = {"FLOAT": "FLOAT32", "DOUBLE": "FLOAT64"}.get(data_type.upper(), data_type.upper())
    if data_type not in FORMATS:
        raise ToolError("类型支持UINT/INT16、32、64以及FLOAT32、FLOAT64")
    if byte_order not in {"big", "little", "unknown"} or word_order not in {
        "high_first",
        "low_first",
        "unknown",
    }:
        raise ToolError("字节序/字序取值不正确")
    count, fmt = FORMATS[data_type]
    if (
        not isinstance(registers, list)
        or len(registers) != count
        or any(
            isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 65535 for v in registers
        )
    ):
        raise ToolError(f"{data_type}需要恰好{count}个0至65535的16位寄存器值")
    if any(
        isinstance(v, bool)
        or not isinstance(v, int | float)
        or not math.isfinite(v)
        or abs(v) > 1e15
        for v in (multiplier, offset)
    ):
        raise ToolError("乘数与偏移必须为有限数值，绝对值不超过1e15")
    orders = (
        ["high_first"]
        if count == 1
        else (["high_first", "low_first"] if word_order == "unknown" else [word_order])
    )
    byte_orders = ["big", "little"] if byte_order == "unknown" else [byte_order]
    candidates = []
    for order in orders:
        for byte in byte_orders:
            words = registers if order == "high_first" else list(reversed(registers))
            raw_bytes = b"".join(v.to_bytes(2, byte) for v in words)
            value = struct.unpack(">" + fmt, raw_bytes)[0]
            scaled = value if multiplier == 1 and offset == 0 else value * multiplier + offset
            finite = math.isfinite(value) and math.isfinite(scaled)
            candidates.append(
                {
                    "word_order": order,
                    "byte_order": byte,
                    "raw_value": value if finite else None,
                    "value": scaled if finite else None,
                    "valid": finite,
                    "warning": "非有限浮点数，不能作为测量值" if not finite else "",
                }
            )
    return {
        "data_type": data_type,
        "registers": registers,
        "candidates": candidates,
        "needs_confirmation": len(candidates) > 1,
        "scale_formula": "原值 × 乘数 + 偏移",
        "multiplier": multiplier,
        "offset": offset,
        "note": "跨寄存器字序须以厂家协议为准；点表增益若为除数，不能直接当乘数使用",
    }


def render_modbus(result: dict) -> str:
    lines = [
        f"Modbus报文解析：{result['transport'].upper()}，设备地址 {result['unit_id']}，"
        f"功能码 {result['function_code']}（{result['function_name']}）。"
    ]
    if "crc_valid" in result:
        lines.append(
            "CRC："
            + ("通过。" if result["crc_valid"] else f"不通过，计算值 {result['crc_expected']}。")
        )
    if "exception_name" in result:
        lines.append(
            f"异常码 {result['exception_code']}：{result['exception_name']}。"
            "异常码不能单独确定现场根因。"
        )
    if "address_offset" in result:
        lines.append(
            f"协议偏移地址：{result['address_offset']}（0x{result['address_offset']:04X}）。"
        )
    if "quantity" in result:
        lines.append(f"数量：{result['quantity']}。")
    if "value" in result:
        lines.append(f"写入/回显值：0x{result['value']:04X}。这里只解析，没有发送写入指令。")
    if "registers" in result:
        values = result["registers"]
        lines.append(
            "寄存器值："
            + ", ".join(f"0x{v:04X}" for v in values[:16])
            + (f"……共{len(values)}个，界面仅展示前16个。" if len(values) > 16 else "。")
        )
    if result.get("direction") == "request_or_echo_response":
        lines.append("单点写入请求与正常回显格式相同，需结合收发方向确认。")
    lines.extend("注意：" + e for e in result["errors"])
    lines.append(result["address_note"])
    return "\n".join(lines)


def render_registers(result: dict) -> str:
    lines = [f"寄存器解码：{result['data_type']}。"]
    if result["needs_confirmation"]:
        lines.append("字序/字节序尚未确认，以下为候选结果，不能自动选取看起来合理的一项：")
    for candidate in result["candidates"]:
        order = "高字在前" if candidate["word_order"] == "high_first" else "低字在前"
        byte = "高字节在前" if candidate["byte_order"] == "big" else "低字节在前"
        lines.append(
            f"- {order}，{byte}：原值 {candidate['raw_value']}，换算后 {candidate['value']}。"
            if candidate["valid"]
            else f"- {order}，{byte}：{candidate['warning']}。"
        )
    lines.append(f"换算：原值 × {result['multiplier']} + {result['offset']}。")
    lines.append(result["note"])
    return "\n".join(lines)
