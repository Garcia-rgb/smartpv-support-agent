"""Local extraction of explicit tool requests, with bounded same-chat follow-ups."""

import re
from dataclasses import dataclass, field


@dataclass
class EngineeringPlan:
    name: str
    arguments: dict = field(default_factory=dict)
    error: str | None = None


ORDERS = {
    "ABCD": ("big", "high_first"),
    "BADC": ("little", "high_first"),
    "CDAB": ("big", "low_first"),
    "DCBA": ("little", "low_first"),
}
_HEX = re.compile(r"(?:0x)?[0-9A-Fa-f]{2}(?:[\s,，]*(?:0x)?[0-9A-Fa-f]{2}){3,}")
_TYPE = re.compile(
    r"(?<![A-Za-z])(UINT(?:16|32|64)|INT(?:16|32|64)|FLOAT(?:32|64)?|DOUBLE)(?![A-Za-z])", re.I
)
_NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"


def _number(text: str, labels: str) -> float | None:
    match = re.search(r"(?:" + labels + r")\s*[:=：]?\s*" + _NUMBER, text, re.I)
    return float(match[1]) if match else None


def _decode(text: str, previous: EngineeringPlan | None = None) -> EngineeringPlan:
    args = dict(previous.arguments) if previous else {}
    bracket = re.search(r"\[([^\]]{1,150})\]", text)
    value = re.search(r"(?:寄存器值|数值|值)\s*[:=：]\s*([^；;\n]{1,150})", text)
    tokens = re.findall(r"0x[0-9a-f]+", text, re.I)
    if bracket or value:
        raw = (bracket or value)[1]
        pieces = re.split(r"[,，\s]+", raw.strip())
        try:
            tokens = [int(s, 16) if s.lower().startswith("0x") else int(s, 10) for s in pieces]
        except ValueError:
            return EngineeringPlan(
                "register_decode", error="寄存器值请填写十进制整数或带0x的十六进制整数"
            )
    else:
        tokens = [int(s, 16) for s in tokens]
    if tokens:
        args["registers"] = tokens
    kind = _TYPE.search(text)
    if kind:
        args["data_type"] = kind[1].upper()
    elif "浮点" in text:
        args["data_type"] = "FLOAT32"
    args.setdefault("byte_order", "big")
    args.setdefault("word_order", "unknown")
    for key, (byte, word) in ORDERS.items():
        if re.search(r"(?<![A-Za-z])" + key + r"(?![A-Za-z])", text, re.I):
            args.update(byte_order=byte, word_order=word)
    for key, pattern in (
        ("byte_order", r"字节序\s*[:=：]\s*(big|little|unknown)"),
        ("word_order", r"字序\s*[:=：]\s*(high_first|low_first|unknown)"),
    ):
        match = re.search(pattern, text, re.I)
        if match:
            args[key] = match[1].lower()
    if "低字在前" in text:
        args["word_order"] = "low_first"
    if "高字在前" in text:
        args["word_order"] = "high_first"
    if "低字节在前" in text:
        args["byte_order"] = "little"
    if "高字节在前" in text:
        args["byte_order"] = "big"
    multiplier = _number(text, "乘数|乘以|乘|multiplier")
    divisor = _number(text, "除数|除以")
    offset = _number(text, "偏移|offset")
    if multiplier is not None and divisor is not None:
        return EngineeringPlan("register_decode", error="请只选择乘数或除数，不能同时填写")
    if divisor == 0:
        return EngineeringPlan("register_decode", error="除数不能为0")
    if multiplier is not None:
        args["multiplier"] = multiplier
    if divisor is not None:
        args["multiplier"] = 1 / divisor
    if offset is not None:
        args["offset"] = offset
    if re.search(r"倍率|增益", text) and multiplier is None and divisor is None:
        return EngineeringPlan(
            "register_decode", error="请确认倍率/增益是乘数还是除数，例如“乘数=0.1”或“除数=10”"
        )
    if "registers" not in args or "data_type" not in args:
        return EngineeringPlan(
            "register_decode",
            error="请补充寄存器原值与数据类型，例如：[0x43C8,0x0000]，FLOAT32，字序ABCD",
        )
    return EngineeringPlan("register_decode", args)


def _frame(text: str, previous: EngineeringPlan | None = None) -> EngineeringPlan:
    args = dict(previous.arguments) if previous else {}
    matches = _HEX.findall(text)
    if len(matches) > 1:
        return EngineeringPlan(
            "modbus_parse", error="检测到多条报文，请分别提交并说明请求/响应方向"
        )
    if matches:
        args["frame"] = matches[0].strip()
    labelled = re.search(r"报文\s*[:：]\s*(.+)", text, re.S)
    if labelled:
        # Preserve malformed bytes too; never silently trim an invalid ASCII suffix.
        tail = re.split(r"[\u4e00-\u9fff]", labelled[1], maxsplit=1)[0].strip()
        if tail:
            args["frame"] = tail
    if re.search(r"TCP", text, re.I):
        args["transport"] = "tcp"
    elif re.search(r"RTU", text, re.I):
        args["transport"] = "rtu"
    args.setdefault("transport", "auto")
    request, response = (
        bool(re.search(r"请求|发送", text)),
        bool(re.search(r"响应|回复|接收", text)),
    )
    if request != response:
        args["direction"] = "request" if request else "response"
    args.setdefault("direction", "auto")
    if "frame" not in args:
        return EngineeringPlan(
            "modbus_parse", error="请粘贴完整十六进制单帧报文，并说明RTU/TCP和请求/响应方向"
        )
    return EngineeringPlan("modbus_parse", args)


def plan_engineering(text: str, history: list[dict] | None = None) -> EngineeringPlan | None:
    previous = None
    # Never reuse unrelated conversations; this history is already ownership checked.
    if history and not re.search(r"新问题|另一个问题|换个问题", text):
        for message in reversed(history[-8:]):
            if message["role"] == "user":
                old = plan_engineering(message["content"])
                if old and not old.error:
                    previous = old
                    break
    definition = re.search(r"什么是|是什么意思|有什么区别", text)
    decode = bool(re.search(r"寄存器解码|寄存器.{0,25}(?:转成|转换|换算|解码|浮点)", text))
    decode |= bool(re.search(r"0x[0-9a-f]+", text, re.I) and _TYPE.search(text))
    if (
        previous
        and previous.name == "register_decode"
        and re.search(r"ABCD|BADC|CDAB|DCBA|字序|字节序|乘数|除数|偏移|换成|改成", text, re.I)
    ):
        decode = True
    if decode and not definition:
        return _decode(text, previous if previous and previous.name == "register_decode" else None)
    frame = bool(
        re.search(
            r"(?:解析|分析|检查|校验).{0,15}(?:Modbus|报文)|报文.{0,15}(?:解析|CRC|不对)",
            text,
            re.I,
        )
    )
    frame |= bool(re.fullmatch(r"[0-9a-fA-FxX\s,，]+", text.strip()) and _HEX.search(text))
    if (
        previous
        and previous.name == "modbus_parse"
        and re.search(r"(?:是|改成|换成).{0,5}(?:响应|请求|TCP|RTU)", text, re.I)
    ):
        frame = True
    if frame and not definition:
        return _frame(text, previous if previous and previous.name == "modbus_parse" else None)
    return None
