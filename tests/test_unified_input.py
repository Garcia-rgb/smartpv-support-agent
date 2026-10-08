import importlib

from support_agent.schemas import ChatResponse, QuizResponse
from support_agent.services.input_routing import is_quiz

main = importlib.import_module("support_agent.main")


def test_customer_screenshot_is_not_forced_into_quiz():
    assert not is_quiz("客户发来的聊天记录\n逆变器无直流数值\nA站正常\nB站异常")
    assert not is_quiz("现场告警截图\nA 设备离线\nB 通讯中断")
    assert is_quiz("判断\n高空作业需要系安全带\nA 正确\nB 错误")
    assert is_quiz("通过 SmartLogger 接入的额定功率是多少？\nA 75kW\nB 65kW\nC 25kW")


async def test_image_issue_uses_chat_after_local_ocr(client, monkeypatch):
    seen = {}

    def fake_ocr(data):
        seen["image"] = data
        return "客户说逆变器无直流数值，现场应该怎样排查？"

    async def fake_respond(self, message, session_id, user_id):
        seen["message"] = message
        return ChatResponse(session_id="s1", message_id="m1", answer="先核对告警")

    monkeypatch.setattr(main, "recognize_image", fake_ocr)
    monkeypatch.setattr(main.SupportAgent, "respond", fake_respond)
    result = await client.post("/input", files={"file": ("customer.png", b"fake", "image/png")})
    assert result.status_code == 200
    assert result.json()["kind"] == "chat"
    assert "逆变器无直流数值" in seen["message"]
    assert seen["image"] == b"fake"


async def test_text_exam_uses_quiz_pipeline(client, monkeypatch):
    async def fake_answer(*args, **kwargs):
        return QuizResponse(
            status="answered", question_type="判断", question="高空作业需要系安全带",
            options={"A": "正确", "B": "错误"}, selected_options=["A"],
            explanation="依据教材", citations=[],
            recognized_text="判断\n高空作业需要系安全带\nA 正确\nB 错误",
        )

    monkeypatch.setattr(main, "answer_question", fake_answer)
    result = await client.post("/input", data={
        "message": "判断\n高空作业需要系安全带\nA 正确\nB 错误",
    })
    assert result.status_code == 200
    assert result.json()["kind"] == "quiz"
    assert result.json()["quiz"]["selected_options"] == ["A"]
    session = result.json()["quiz"]["session_id"]
    saved = (await client.get("/sessions/" + session)).json()
    assert len(saved["messages"]) == 2
    assert "依据教材" in saved["messages"][-1]["content"]
