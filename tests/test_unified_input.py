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
            status="answered",
            question_type="判断",
            question="高空作业需要系安全带",
            options={"A": "正确", "B": "错误"},
            selected_options=["A"],
            explanation="依据教材",
            citations=[],
            recognized_text="判断\n高空作业需要系安全带\nA 正确\nB 错误",
        )

    monkeypatch.setattr(main, "answer_question", fake_answer)
    result = await client.post(
        "/input",
        data={
            "message": "判断\n高空作业需要系安全带\nA 正确\nB 错误",
        },
    )
    assert result.status_code == 200
    assert result.json()["kind"] == "quiz"
    assert result.json()["quiz"]["selected_options"] == ["A"]
    session = result.json()["quiz"]["session_id"]
    saved = (await client.get("/sessions/" + session)).json()
    assert len(saved["messages"]) == 2
    assert "依据教材" in saved["messages"][-1]["content"]


async def test_wechat_names_and_ui_noise_do_not_reach_agent(client, monkeypatch):
    raw = (
        "若水\n10.41\n设备管理\nBRE\nARDON WHE\n若水\n"
        "@明明很安静帮忙看一下这个断联是怎么回事，正好现场\n现在有人可以查看"
    )
    seen = {}
    monkeypatch.setattr(main, "recognize_image", lambda data: raw)

    async def fake_respond(self, message, session_id, user_id):
        seen["message"] = message
        return ChatResponse(session_id="s1", message_id="m1", answer="需确认断联设备")

    monkeypatch.setattr(main.SupportAgent, "respond", fake_respond)
    result = await client.post("/input", files={"file": ("wechat.png", b"fake", "image/png")})
    assert result.status_code == 200
    assert "断联" in seen["message"] and "现在有人可以查看" in seen["message"]
    for noise in ("若水", "明明很安静", "ARDON", "10.41", "设备管理"):
        assert noise not in seen["message"]
    assert result.json()["recognized_text"] == raw


async def test_multiple_images_are_prepared_and_sent_to_agent_once(client, monkeypatch):
    seen = []
    texts = {
        b"chat": "若水\n设备管理\n若水\n@明明很安静 帮忙看下这个断联\n现在有人可以查看",
        b"plate": "华为\nModel: SUN2000-100KTL-M2",
    }
    monkeypatch.setattr(main, "recognize_image", lambda data: texts[data])

    async def fake_respond(self, message, session_id, user_id):
        seen.append(message)
        return ChatResponse(session_id="s1", message_id="m1", answer="先确认断联设备")

    monkeypatch.setattr(main.SupportAgent, "respond", fake_respond)
    result = await client.post(
        "/input",
        data={"message": "一起看下，现场供电正常"},
        files=[
            ("files", ("chat.png", b"chat", "image/png")),
            ("files", ("plate.jpg", b"plate", "image/jpeg")),
        ],
    )
    assert result.status_code == 200
    assert result.json()["kind"] == "chat"
    assert len(seen) == 1
    assert "现场供电正常" in seen[0] and "断联" in seen[0]
    assert "SUN2000-100KTL-M2" in seen[0]
    assert "若水" not in seen[0] and "明明很安静" not in seen[0]
    assert "【图片1】" in seen[0] and "【图片2】" in seen[0]
    assert "【图片2】" in result.json()["recognized_text"]


async def test_batch_validation_happens_before_ocr_or_model(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "recognize_image", lambda data: calls.append(data) or "现场断联")
    too_many = [("files", (f"{i}.png", b"test", "image/png")) for i in range(7)]
    assert (await client.post("/input", files=too_many)).status_code == 400
    invalid = [
        ("files", ("a.png", b"test", "image/png")),
        ("files", ("b.txt", b"bad", "text/plain")),
    ]
    assert (await client.post("/input", files=invalid)).status_code == 400
    assert not calls


async def test_multiquiz_images_are_not_collapsed_to_a_single_quiz(client, monkeypatch):
    monkeypatch.setattr(
        main, "recognize_image", lambda data: "判断\n问题是否正确？\nA 正确\nB 错误"
    )
    seen = []

    async def fake_respond(self, message, session_id, user_id):
        seen.append(message)
        assert not is_quiz(message)
        return ChatResponse(session_id="s1", message_id="m1", answer="联合问题回答")

    monkeypatch.setattr(main.SupportAgent, "respond", fake_respond)
    result = await client.post(
        "/input",
        files=[
            ("files", ("a.png", b"a", "image/png")),
            ("files", ("b.png", b"b", "image/png")),
        ],
    )
    assert result.status_code == 200 and result.json()["kind"] == "chat"
    assert len(seen) == 1
