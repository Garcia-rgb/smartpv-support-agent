import math
import struct

import pytest

from support_agent.services.modbus_tools import crc16, decode_registers, parse_modbus_frame
from support_agent.services.tools import ToolError


def rtu(payload):
    data = bytes.fromhex(payload)
    return (data + crc16(data).to_bytes(2, "little")).hex(" ")


def test_known_request_crc_and_zero_based_address():
    result = parse_modbus_frame("01 03 00 00 00 02 C4 0B")
    assert result["valid"] and result["transport"] == "rtu"
    assert result["direction"] == "request" and result["quantity"] == 2
    assert result["address_offset"] == 0
    assert result["crc_expected"] == "C4 0B"


def test_crc_failure_is_explicit_not_device_diagnosis():
    result = parse_modbus_frame("01 03 00 00 00 02 C4 FF", "rtu")
    assert not result["valid"] and not result["crc_valid"]
    assert "不能据此判断设备损坏" in result["errors"][0]


def test_read_register_response_and_exception():
    response = parse_modbus_frame(rtu("01 03 04 43 C8 00 00"), direction="response")
    assert response["valid"] and response["registers"] == [0x43C8, 0]
    error = parse_modbus_frame(rtu("01 83 02"))
    assert error["valid"] and error["exception_name"] == "非法数据地址"
    assert not parse_modbus_frame(rtu("01 83 02 00"))["valid"]


def test_tcp_length_and_read_request():
    result = parse_modbus_frame("00 01 00 00 00 06 01 03 00 10 00 02", "tcp")
    assert result["valid"] and result["address_offset"] == 16 and result["transaction_id"] == 1
    bad = parse_modbus_frame("00 01 00 00 00 07 01 03 00 10 00 02", "tcp")
    assert not bad["valid"]


def test_echo_cannot_prove_a_write_was_executed():
    result = parse_modbus_frame(rtu("01 06 00 01 00 02"))
    assert result["valid"] and result["direction"] == "request_or_echo_response"
    assert not parse_modbus_frame(rtu("01 05 00 01 12 34"))["valid"]


def test_read_quantity_and_broadcast_validation():
    assert not parse_modbus_frame(rtu("01 03 00 00 00 7e"), direction="request")["valid"]
    assert not parse_modbus_frame(rtu("00 03 00 00 00 02"), direction="request")["valid"]
    assert not parse_modbus_frame(rtu("01 03 03 00 00 00"), direction="response")["valid"]
    with pytest.raises(ToolError, match="歧义"):
        parse_modbus_frame(rtu("01 01 03 00 00 01"))


def test_multiple_write_lengths():
    assert parse_modbus_frame(rtu("01 10 00 00 00 02 04 00 01 00 02"))["valid"]
    assert not parse_modbus_frame(rtu("01 10 00 00 00 02 02 00 01"))["valid"]
    assert parse_modbus_frame(rtu("01 0f 00 00 00 08 01 ff"))["valid"]


@pytest.mark.parametrize("frame", ["01 03 GG 00", "01 0", "01" * 261, "2026-10-09 收到01 03"])
def test_invalid_hex_is_rejected(frame):
    with pytest.raises(ToolError):
        parse_modbus_frame(frame)


@pytest.mark.parametrize(
    "registers,byte,word",
    [
        ([0x43C8, 0], "big", "high_first"),
        ([0xC843, 0], "little", "high_first"),
        ([0, 0x43C8], "big", "low_first"),
        ([0, 0xC843], "little", "low_first"),
    ],
)
def test_known_float_all_four_orders(registers, byte, word):
    result = decode_registers(registers, "FLOAT32", byte, word, 0.1, 2)
    assert result["candidates"][0]["raw_value"] == 400
    assert result["candidates"][0]["value"] == 42


def test_unknown_order_returns_candidates_not_guessed_value():
    result = decode_registers([0x43C8, 0], "FLOAT32")
    assert result["needs_confirmation"] and len(result["candidates"]) == 2
    assert "value" not in result


def test_integer_signedness_and_nonfinite_float():
    assert decode_registers([65535], "INT16")["candidates"][0]["value"] == -1
    assert decode_registers([65535], "UINT16")["candidates"][0]["value"] == 65535
    result = decode_registers([0x7F80, 0], "FLOAT32", word_order="high_first")
    assert result["candidates"][0]["value"] is None
    assert not result["candidates"][0]["valid"]
    words = list(struct.unpack(">4H", struct.pack(">d", 123.5)))
    assert (
        decode_registers(words, "FLOAT64", word_order="high_first")["candidates"][0]["value"]
        == 123.5
    )


@pytest.mark.parametrize(
    "values,kind",
    [([True], "UINT16"), ([-1], "INT16"), ([65536], "UINT16"), ([1], "FLOAT32"), ([1, 2], "BAD")],
)
def test_bad_register_input(values, kind):
    with pytest.raises(ToolError):
        decode_registers(values, kind)


def test_nonfinite_multiplier_rejected():
    with pytest.raises(ToolError):
        decode_registers([1], "UINT16", multiplier=math.inf)
