import importlib

from sqlalchemy import func, select

from support_agent.config import Settings, get_settings
from support_agent.main import app
from support_agent.models import DocumentChunk, ReviewedCase, SourceDocument, UserAccount
from support_agent.schemas import ChatResponse
from support_agent.services.auth import hash_password

routes = importlib.import_module("support_agent.service_routes")


async def login(client, db, name, role="user"):
    db.add(UserAccount(username=name, role=role, password_hash=hash_password("password123")))
    await db.commit()
    res = await client.post("/auth/login", json={"username": name, "password": "password123"})
    assert res.status_code == 200
    return {"X-CSRF-Token": res.json()["csrf_token"]}


def authenticated_settings(tmp_path):
    return Settings(
        auth_enabled=True,
        confirmation_secret="test-secret",
        knowledge_base_dir=str(tmp_path / "knowledge"),
    )


async def test_continuous_case_tracks_feedback_and_exports_verified_result(client, monkeypatch):
    seen = []

    async def respond(self, message, session_id, user_id):
        seen.append(message)
        return ChatResponse(session_id="s", message_id="m", answer="请核对485通讯地址")

    monkeypatch.setattr(routes.SupportAgent, "respond", respond)
    created = await client.post(
        "/service/cases", json={"symptom": "逆变器断联", "device": "测试设备"}
    )
    assert created.status_code == 201
    case = created.json()
    path = "/service/cases/" + case["id"]
    assert (await client.post(path + "/next")).status_code == 200
    observed = await client.post(
        path + "/observations",
        json={"revision": 0, "result": "采集器在线，供电正常，485接线待核查"},
    )
    assert observed.status_code == 200
    assert (await client.post(path + "/next")).status_code == 200
    assert "采集器在线，供电正常" in seen[-1]
    assert "请核对485通讯地址" not in seen[-1]  # Previous guesses are not facts.
    stale = await client.post(path + "/observations", json={"revision": 0, "result": "重复结果"})
    assert stale.status_code == 409
    draft = await client.get(path + "/export?kind=report")
    assert "尚未确认" in draft.text and "不等于验证结论" in draft.text
    assert (await client.post(path + "/submit")).status_code == 409
    resolved = await client.post(
        path + "/resolve",
        json={
            "revision": 1,
            "cause": "通讯地址冲突",
            "solution": "按厂家步骤修正地址",
            "verification": "数据恢复，观察30分钟正常",
        },
    )
    assert resolved.status_code == 200
    assert (await client.post(path + "/next")).status_code == 409
    assert (
        await client.post(path + "/observations", json={"revision": 2, "result": "再检查"})
    ).status_code == 409
    reply = await client.get(path + "/export?kind=reply")
    assert "数据恢复，观察30分钟正常" in reply.text
    assert "请核对485通讯地址" not in reply.text
    assert "attachment" in reply.headers["content-disposition"]
    assert (await client.post(path + "/submit")).status_code == 201
    assert (await client.post(path + "/submit")).status_code == 409


async def test_owner_boundaries_csrf_and_admin_publication(client, db_session, tmp_path):
    app.dependency_overrides[get_settings] = lambda: authenticated_settings(tmp_path)
    assert (await client.get("/service/cases")).status_code == 401
    staff = await login(client, db_session, "staff")
    assert (await client.post("/service/cases", json={"symptom": "断联问题"})).status_code == 403
    created = await client.post("/service/cases", headers=staff, json={"symptom": "断联问题"})
    case_id = created.json()["id"]
    path = "/service/cases/" + case_id
    assert (await client.get("/service/reviews")).status_code == 403
    assert (
        await client.post(
            path + "/resolve",
            headers=staff,
            json={
                "revision": 0,
                "cause": "地址重复",
                "solution": "修正重复地址",
                "verification": "现场验证通信恢复",
            },
        )
    ).status_code == 200
    submitted = await client.post(path + "/submit", headers=staff)
    review_id = submitted.json()["id"]
    assert await db_session.scalar(select(func.count()).select_from(SourceDocument)) == 0
    client.cookies.clear()
    other = await login(client, db_session, "other")
    assert (await client.get(path)).status_code == 404
    assert (await client.get(path + "/export")).status_code == 404
    assert (await client.post(path + "/next", headers=other)).status_code == 404
    assert not (await client.get("/service/cases")).json()["items"]
    assert (
        await client.post("/service/reviews/" + review_id, headers=other, json={"approve": True})
    ).status_code == 403
    client.cookies.clear()
    admin = await login(client, db_session, "admin", "admin")
    assert len((await client.get("/service/reviews")).json()["items"]) == 1
    published = await client.post(
        "/service/reviews/" + review_id,
        headers=admin,
        json={"approve": True, "comment": "核实通过"},
    )
    assert published.status_code == 200
    assert published.json()["document_id"]
    assert await db_session.scalar(select(func.count()).select_from(SourceDocument)) == 1
    chunk = await db_session.scalar(select(DocumentChunk))
    assert chunk.chunk_metadata["visibility"] == "private"
    assert chunk.chunk_metadata["case_status"] == "verified"
    assert "通讯地址" not in chunk.content or "地址重复" in chunk.content
    assert (
        await client.post("/service/reviews/" + review_id, headers=admin, json={"approve": True})
    ).status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(SourceDocument)) == 1


async def test_rejected_case_never_enters_knowledge(client, db_session, tmp_path):
    app.dependency_overrides[get_settings] = lambda: authenticated_settings(tmp_path)
    headers = await login(client, db_session, "admin", "admin")
    created = await client.post("/service/cases", headers=headers, json={"symptom": "数据异常"})
    path = "/service/cases/" + created.json()["id"]
    await client.post(
        path + "/resolve",
        headers=headers,
        json={
            "revision": 0,
            "cause": "推测原因",
            "solution": "待确认方法",
            "verification": "人员填写验证",
        },
    )
    submitted = await client.post(path + "/submit", headers=headers)
    reviewed = await client.post(
        "/service/reviews/" + submitted.json()["id"],
        headers=headers,
        json={"approve": False, "comment": "验证依据不足"},
    )
    assert reviewed.status_code == 200
    assert not reviewed.json()["document_id"]
    assert await db_session.scalar(select(func.count()).select_from(SourceDocument)) == 0
    assert (await db_session.get(ReviewedCase, submitted.json()["id"])).status == "rejected"
