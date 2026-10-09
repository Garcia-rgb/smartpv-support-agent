from types import SimpleNamespace
from unittest.mock import AsyncMock

from support_agent.config import Settings
from support_agent.services.agent import SupportAgent
from support_agent.services.issue_input import prepare_screenshot_question
from support_agent.services.llm import ANSWER_SYSTEM_PROMPT, QUIZ_SYSTEM_PROMPT
from support_agent.services.telemetry_input import spatial_telemetry_rows


def test_chat_platform_values_survive_and_names_do_not():
    raw = ("运维示例人员\n设备详情\n功率因数\n0\n有功功率\n61.31\n"
           "运维示例人员\n这个电流是不是不对\n看下\n"
           "截图数值行（从左到右，列归属待核对）：功率因数 | 0 | 有功功率(kW) | 61.31")
    prepared = prepare_screenshot_question(raw)
    assert "运维示例人员" not in prepared
    assert "61.31" in prepared and "功率因数 | 0" in prepared


def test_spatial_rows_keep_measurements_without_inventing_columns():
    result = SimpleNamespace(txts=["设备详情", "A相电压(V)", "419", "功率因数", "0"],
                             boxes=[[[0, 0], [80, 0], [80, 12], [0, 12]],
                                    [[0, 30], [80, 30], [80, 42], [0, 42]],
                                    [[100, 30], [125, 30], [125, 42], [100, 42]],
                                    [[170, 30], [230, 30], [230, 42], [170, 42]],
                                    [[270, 30], [280, 30], [280, 42], [270, 42]]])
    rows = spatial_telemetry_rows(result)
    assert len(rows) == 1
    assert "A相电压(V)=419 | 功率因数=0" in rows[0]
    assert "OCR需核对" in rows[0]


async def test_platform_miss_calls_model_for_general_checks_without_fake_evidence(db_session):
    settings = Settings(privacy_routing_enabled=True, allow_remote_llm=True,
                        llm_api_key="test", llm_model="test-model",
                        llm_base_url="https://example.invalid/v1")
    agent = SupportAgent(db_session, settings)
    agent.llm.answer = AsyncMock(return_value="通用核查：先比较本机与平台的同一时刻读数")
    result = await agent.run_turn("平台电流电压值不正常，怎么排查？", [])
    assert result.status == "completed"
    assert result.citations == []
    assert "通用核查" in result.answer
    assert agent.llm.answer.await_args.args[1] == []


async def test_platform_miss_without_model_still_offers_checks(db_session):
    agent = SupportAgent(db_session, Settings(privacy_routing_enabled=True))
    result = await agent.run_turn("平台电流值异常", [])
    assert "通用排查" in result.answer and "本机读数" in result.answer
    assert "倍率" in result.answer and "列归属" not in result.answer


def test_field_reasoning_does_not_relax_exam_answering():
    assert "通用工程知识" in ANSWER_SYSTEM_PROMPT
    assert "不得使用任何外部知识" in QUIZ_SYSTEM_PROMPT


async def test_new_empty_conversation_persists_before_any_message(client):
    created = await client.post("/sessions", headers={"X-User-Id": "alice"})
    assert created.status_code == 201
    session = created.json()
    listed = await client.get("/sessions", headers={"X-User-Id": "alice"})
    assert session["id"] in [s["id"] for s in listed.json()["items"]]
    loaded = await client.get("/sessions/" + session["id"], headers={"X-User-Id": "alice"})
    assert loaded.json()["messages"] == []
    denied = await client.get("/sessions/" + session["id"], headers={"X-User-Id": "bob"})
    assert denied.status_code == 404
