"""Extract fault descriptions from OCR noise without replacing the stored original."""

import re

_MODEL = re.compile(
    r"(?<![A-Z0-9])(?:SUN2000-[A-Z0-9-]+|GW\d+[A-Z0-9-]+|"
    r"SmartLogger\d*[A-Z0-9-]*|LUNA2000-[A-Z0-9-]+)(?![A-Z0-9])", re.I
)
_ALIAS = re.compile(r"(?<![A-Z0-9])NB\d+(?![A-Z0-9])", re.I)
_BRAND = re.compile(r"华为|HUAWEI|固德威|GOODWE", re.I)
_ISSUE = re.compile(
    r"挂不上|挂载不上|下挂不到|不刷新|不更新|数据.{0,4}不对|没(?:有)?数据|"
    r"无数据|无直流|离线|掉线|断联|通讯失败|通信失败|请求超时|告警|报错|"
    r"故障|异常|扫.{0,12}地址|地址.{0,6}\d|offline|error", re.I
)
_CONSTRAINT = re.compile(r"不要|不能|禁止|不允许|只读|已尝试|已经尝试")
_BACKGROUND = re.compile(
    r"合格证|QUALIFICATION|Manufactured|生产日期|质检|最大直流|MPPT|"
    r"额定|防护等级|验证码|扫码|菜单切换|设备控制|组串容量|补充图片识别文字", re.I
)
_FIELD = re.compile(r"^(?:功率因数|有功功率|输入总功率|电网频率|输入电压|输入电流)")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def normalize_issue_input(text: str, *, include_fields: bool = True) -> str:
    """Only condense a multiline issue with identifiable screenshot background.

    Keep the customer's symptoms, addresses, explicit restrictions and exact device
    identities. Numeric table values are retained only when immediately adjacent to
    a field; OCR order is not used to reconstruct a whole table. The caller retains
    the original input for history and the OCR correction UI.
    """
    if text.startswith("现场问题：") and "\n已检查结果（" in text and "\n请结合" in text:
        # Service workflow instructions belong to generation, not evidence search.
        return text if include_fields else text.split("\n请结合", 1)[0]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4 or not _BACKGROUND.search(text):
        return text
    symptoms = list(dict.fromkeys(
        line for line in lines
        if line not in {"告警信息", "暂无数据", "告警", "设备故障信息"}
        and (_ISSUE.search(line) or _CONSTRAINT.search(line))
    ))
    if not symptoms:
        return text
    identities = list(dict.fromkeys(
        "华为" if match.group(0).upper() == "HUAWEI" else
        "固德威" if match.group(0).upper() == "GOODWE" else match.group(0)
        for pattern in (_BRAND, _MODEL) for match in pattern.finditer(text)
    ))
    fields = []
    for index, line in enumerate(lines):
        if _FIELD.match(line):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            fields.append(line + (" " + following if _NUMBER.fullmatch(following) else ""))
    parts = ["客户现场问题：" + "；".join(symptoms)]
    if identities:
        parts.append("设备：" + "、".join(identities))
    if include_fields and (aliases := list(dict.fromkeys(_ALIAS.findall(text)))):
        parts.append("平台设备编号：" + "、".join(aliases))
    if fields and include_fields:
        parts.append("截图字段（顺序未核实）：" + "；".join(dict.fromkeys(fields)))
    if re.search(r"菜单切换|实时信息|设备详情|PV\d+", text):
        parts.append("平台采集数据、组串与点表如何核对？")
    else:
        parts.append("如何排查？")
    return "\n".join(parts)


_CHAT_QUESTION = re.compile(r"怎么|如何|为什么|帮忙|帮我|请问|看一下|看下")
_CHAT_CONTEXT = re.compile(r"现场|现在有人|已经|已尝试|之前|一直|地址|通讯|通信|连接")
_MENTION = re.compile(r"@[^@\s]*?(?=帮忙|帮我|请|看一下|看下|\s|$)")


def prepare_screenshot_question(text: str) -> str:
    """Locally extract chat content; never send detected chat names to generation.

    Raw OCR is returned separately for local correction. Ambiguous chat screenshots
    without a recognizable question are rejected rather than forwarded verbatim.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    repeated = {line for line in lines if lines.count(line) > 1
                and len(line) <= 30 and not (_ISSUE.search(line) or _CHAT_QUESTION.search(line))}
    is_chat = bool(re.search(r"@", text) or repeated)
    if not is_chat:
        return normalize_issue_input(text)
    content = []
    for line in lines:
        cleaned = _MENTION.sub("", line).strip()
        for name in repeated:
            if cleaned.startswith(name + "：") or cleaned.startswith(name + ":"):
                cleaned = cleaned[len(name) + 1:].strip()
        if not cleaned or cleaned in repeated:
            continue
        if _ISSUE.search(cleaned) or _CONSTRAINT.search(cleaned) or _CHAT_QUESTION.search(cleaned):
            content.append(cleaned)
        elif content and _CHAT_CONTEXT.search(cleaned):
            content.append(cleaned)
    if not content:
        raise ValueError("未能可靠识别聊天截图中的问题，请补充问题文字或裁剪聊天内容")
    # Only explicit equipment identities are admitted from surrounding UI/nameplates.
    identities = list(dict.fromkeys(match.group(0) for pattern in (_BRAND, _MODEL)
                                   for match in pattern.finditer(text)))
    parts = ["客户现场问题：" + "；".join(dict.fromkeys(content))]
    if identities:
        parts.append("设备：" + "、".join(identities))
    return "\n".join(parts)
