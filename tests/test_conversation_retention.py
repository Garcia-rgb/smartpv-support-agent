import importlib

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from support_agent.config import Settings, get_settings
from support_agent.db import get_db
from support_agent.main import app
from support_agent.models import Conversation, Message, MessageImage, UserAccount
from support_agent.services.auth import hash_password

main = importlib.import_module("support_agent.main")


async def test_text_and_images_survive_database_connection_reopen(client, db_session, monkeypatch):
    monkeypatch.setattr(main, "recognize_image", lambda data: "客户现场逆变器断联，供电正常")
    result = await client.post(
        "/input",
        files=[
            ("files", ("one.png", b"first image", "image/png")),
            ("files", ("two.jpg", b"second image", "image/jpeg")),
        ],
    )
    assert result.status_code == 200
    session_id = result.json()["chat"]["session_id"]
    # A new engine and DB connection read only persisted state, not ORM memory.
    url = db_session.bind.url.render_as_string(hide_password=False)
    reopened = create_async_engine(url)
    factory = async_sessionmaker(reopened, expire_on_commit=False)

    async def new_db():
        async with factory() as db:
            yield db

    previous = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = new_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as browser:
            listed = await browser.get("/sessions")
            assert listed.json()["items"][0]["id"] == session_id
            assert "断联" in listed.json()["items"][0]["title"]
            history = await browser.get("/sessions/" + session_id)
            messages = history.json()["messages"]
            assert len(messages) == 2
            assert "断联" in messages[0]["content"]
            assert len(messages[0]["images"]) == 2
            for image, original in zip(
                messages[0]["images"], [b"first image", b"second image"], strict=True
            ):
                response = await browser.get("/message-images/" + image["id"])
                assert response.status_code == 200 and response.content == original
                assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides[get_db] = previous
        await reopened.dispose()


async def test_history_image_access_is_bound_to_account(client, db_session):
    users = [
        UserAccount(username=name, role="user", password_hash=hash_password("password123"))
        for name in ("owner", "other")
    ]
    db_session.add_all(users)
    await db_session.flush()
    session = Conversation(user_id=users[0].id)
    db_session.add(session)
    await db_session.flush()
    message = Message(session_id=session.id, role="user", content="现场问题")
    db_session.add(message)
    await db_session.flush()
    image = MessageImage(
        message_id=message.id, position=0, content_type="image/png", data=b"private"
    )
    db_session.add(image)
    await db_session.commit()
    app.dependency_overrides[get_settings] = lambda: Settings(
        auth_enabled=True, confirmation_secret="test-secret"
    )
    path = "/message-images/" + image.id
    assert (await client.get(path)).status_code == 401
    await client.post("/auth/login", json={"username": "other", "password": "password123"})
    assert (await client.get(path)).status_code == 404
    assert (await client.get("/sessions/" + session.id)).status_code == 404
    client.cookies.clear()
    await client.post("/auth/login", json={"username": "owner", "password": "password123"})
    assert (await client.get(path)).content == b"private"
