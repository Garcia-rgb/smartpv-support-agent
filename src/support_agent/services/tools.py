import ast
import operator
import re
from dataclasses import dataclass


class ToolError(ValueError):
    pass


_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_calculate(expression: str) -> float:
    """只计算普通算术表达式，拒绝变量名、函数调用和属性访问。"""
    if len(expression) > 100:
        raise ToolError("表达式过长")

    def evaluate(node: ast.AST) -> float:
        # 只处理白名单中的语法树节点，避免直接执行用户输入的代码。
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ToolError("指数过大")
            return _OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](evaluate(node.operand))
        raise ToolError("表达式包含不允许的内容")

    try:
        result = evaluate(ast.parse(expression, mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ToolError("无法计算该表达式") from exc
    if abs(result) > 1e100:
        raise ToolError("计算结果过大")
    return result


@dataclass(frozen=True)
class Device:
    """一台逆变器的档案快照。

    真实项目里这份数据来自设备管理系统或监控平台，这里先用内存字典顶替，
    让「模型申请调工具取事实」这条链路在离线环境下也能完整跑通。
    """

    sn: str
    model: str
    rated_power_kw: float
    status: str
    firmware: str
    grid_connected: bool


MOCK_DEVICES = {
    "SN-2024-000123": Device(
        sn="SN-2024-000123",
        model="SUN2000-100KTL-M1",
        rated_power_kw=100.0,
        status="并网发电",
        firmware="V200R023C10SPC200",
        grid_connected=True,
    ),
    "SN-2024-000456": Device(
        sn="SN-2024-000456",
        model="SUN2000-50KTL-M0",
        rated_power_kw=50.0,
        status="待机",
        firmware="V200R023C10SPC200",
        grid_connected=True,
    ),
    "SN-2024-000789": Device(
        sn="SN-2024-000789",
        model="SUN2000-110KTL-M0",
        rated_power_kw=110.0,
        status="故障停机",
        firmware="V200R022C00SPC100",
        grid_connected=False,
    ),
}

# 设备序列号的书写形式，例如 SN-2024-000123。
DEVICE_SN_PATTERN = re.compile(r"\bSN-\d{4}-\d{6}\b")


def find_device_sn(text: str) -> str | None:
    match = DEVICE_SN_PATTERN.search(text.upper())
    return match.group(0) if match else None


# 算术表达式的书写形式：数字左右夹着明确的运算符，中英文写法都认。
# 刻意不把单独的「-」算进来——它与日期（2024-09-16）、型号区间、编号的写法混在一起，
# 误判成算式会让跑题问题也被放行；减法走「减去」这类中文说法即可。
ARITHMETIC_PATTERN = re.compile(r"\d\s*(?:[+*/×÷]|乘以|除以|加上|减去)\s*\d")

_CALC_INTENT = re.compile(
    r"(?:计算|算一下|求值|calc)\s*[:：]?\s*(?=[\d(（+-])|"
    r"\d\s*(?:乘以|除以|加上|减去)\s*\d", re.I
)
_PURE_EXPRESSION = re.compile(r"[\d\s.()+*/%×÷-]+")


def is_calculation_request(text: str) -> bool:
    """A parameter substring is not a request to calculate it."""
    if not ARITHMETIC_PATTERN.search(text):
        return False
    stripped = text.strip().rstrip("=？?。")
    return bool(_CALC_INTENT.search(text) or _PURE_EXPRESSION.fullmatch(stripped))


# 写操作意图。这些诉求由 create_ticket 承接，同样不依赖知识库。
TICKET_KEYWORDS = ("工单", "报修", "派单", "转人工", "投诉")


def has_structured_anchor(text: str) -> bool:
    """这句话是否带结构化锚点，也就是能不能绕开知识库、单独由工具回答。

    目前只服务于一个用途：语料范围判据的豁免。判据问的是「这个问题属于这份资料吗」，
    而带序列号、算式或工单诉求的问法根本不需要这份资料，拿同一把尺子量会量错。
    实测两种情况会被误判成跑题：

    - 「SN-2024-000123 这台设备现在什么状态」的词组缺失比例 0.62，
      「SN-2024-000123」单独问更是 0.67，于是被判成跑题去追问用户，
      `query_device` 永远调不到——而换个说法（「设备 SN-2024-000123 现在是什么状态」）
      缺失比例就掉到 0.50 能过。拦下它的不是「话题不相关」，而是「措辞没对上语料」。
    - 「帮我建个工单」缺失比例 0.67，等于写操作入口整个失效。

    判定要宽进严出：锚点只证明「这个问题有工具可答」，不证明「答得出来」，
    查不到的设备序列号仍会由工具自己报错。
    """
    if DEVICE_SN_PATTERN.search(text.upper()):
        return True
    if is_calculation_request(text):
        return True
    return any(keyword in text for keyword in TICKET_KEYWORDS)


def query_device(sn: str) -> Device:
    """按序列号查询设备档案；真实项目中这里应调用监控平台或设备管理接口。"""
    try:
        return MOCK_DEVICES[sn.upper()]
    except KeyError as exc:
        raise ToolError(f"未找到设备 {sn}") from exc
