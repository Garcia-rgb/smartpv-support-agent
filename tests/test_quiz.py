from types import SimpleNamespace

import pytest

from support_agent.services.quiz import _altitude_rule, _bank_match, parse_question, recognize_image


def test_parse_ocr_lines_into_judgment_question():
    kind, question, options = parse_question(
        "判断\n当操作高度达到或超过2.0米时，必须佩戴\n全身安全带。\nA正确\nB错误"
    )
    assert kind == "判断"
    assert question == "当操作高度达到或超过2.0米时，必须佩戴全身安全带。"
    assert options == {"A": "正确", "B": "错误"}
    assert _altitude_rule(question, options) == "A"


def test_altitude_rule_does_not_answer_negated_statement():
    assert _altitude_rule("2米以上不必佩戴安全带", {"A": "正确", "B": "错误"}) is None


def test_bank_answer_needs_question_match_and_unique_option():
    rows = [
        (
            SimpleNamespace(
                content="## 第2题\n题目：低压并网的上限容量是多少？占配变容量的建议比例是多少？\n"
                "参考答案：低压上限 400kW；占配变 25~30%。"
            ),
            SimpleNamespace(filename="HCSA-V2-模拟题逐题解析.md"),
        )
    ]
    _, question, options = parse_question("选择\n低压并网的上限容量是多少？\nA 400kW\nB 500kW")
    assert _bank_match(question, options, rows)[0] == ["A"]
    assert _bank_match("逆变器的额定功率是多少？", {"A": "400kW", "B": "500kW"}, rows) is None


def test_bank_supports_multi_answer_when_multi_choice():
    rows = [
        (
            SimpleNamespace(
                content="## 补2\n题目：下列符合NetEco1000S系统配置要求的选项是（）？\n"
                "参考答案：全选（普通PC机、内存≥4G、硬盘≥500G、WinDows7操作系统）。"
            ),
            SimpleNamespace(filename="HCSA-V2-补充题解析.md"),
        )
    ]
    options = {"A": "普通PC机", "B": "内存≥4G"}
    matched = _bank_match("下列符合NetEco1000S系统配置要求的选项是（）", options, rows, multi=True)
    assert matched[0] == ["A", "B"]
    # 同样的多个命中在单选题里必须拒绝，防止「以上都不是」类答案误判。
    assert _bank_match("下列符合NetEco1000S系统配置要求的选项是（）", options, rows) is None


def test_parse_merges_letter_only_ocr_lines():
    # OCR 把选项字母单独切成一行（"C" 一行、"25kW" 一行）。
    kind, question, options = parse_question(
        "单选\n通过SmartLogger MBUS端口接入的光伏系统数量或功率应大于多少？\n"
        "A 75kW\nB 68kW\nC\n25kW\nD\n50kW"
    )
    assert kind == "单选"
    assert question == "通过SmartLogger MBUS端口接入的光伏系统数量或功率应大于多少？"
    assert options == {"A": "75kW", "B": "68kW", "C": "25kW", "D": "50kW"}


def test_parse_multi_choice_infers_missing_option_letters():
    # 多选题图 OCR 丢掉后面的选项字母时按顺序顺延；单选题不冒这个险。
    kind, _, options = parse_question(
        "多选\n下列符合NetEco1000S系统配置要求的选项是（）\n"
        "A普通PC机\nB 内存≥4G\n硬盘≥500G\nWinDows7操作系统"
    )
    assert kind == "多选"
    assert options == {
        "A": "普通PC机",
        "B": "内存≥4G",
        "C": "硬盘≥500G",
        "D": "WinDows7操作系统",
    }
    kind, _, options = parse_question("选择\n题干？\nA 选项一\n选项一补充\nB 选项二")
    assert kind == "单选"
    assert options == {"A": "选项一选项一补充", "B": "选项二"}


def test_reject_non_image_upload():
    with pytest.raises(ValueError, match="PNG"):
        recognize_image(b"not an image")
