"""MCP Server / Client 集成测试。

这些用例会真的起一个 Python 子进程当 Server，走 stdio 传 JSON-RPC——
不共享任何进程内对象。这一点很重要：只有跨进程测得通，
才能说明「工具执行权在 Server 那边」不是一句口号。
"""

import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from support_agent.mcp_server import DEVICE_CATALOG_URI
from support_agent.services.tools import MOCK_DEVICES

SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "support_agent.mcp_server"],
    env=os.environ.copy(),
)


async def test_server_exposes_tools_resources_and_prompts() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            resources = {str(item.uri) for item in (await session.list_resources()).resources}
            prompts = {item.name for item in (await session.list_prompts()).prompts}

    assert info.server_info.name == "smartpv-device"
    assert "query_device" in tools
    assert DEVICE_CATALOG_URI in resources
    assert "fault_report" in prompts


async def test_query_device_returns_the_device_record() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("query_device", {"sn": "SN-2024-000123"})

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["sn"] == "SN-2024-000123"
    assert payload["model"] == "SUN2000-100KTL-M1"
    assert payload["grid_connected"] is True


async def test_business_failure_is_reported_as_a_protocol_error() -> None:
    """查不到设备属于业务失败，必须让 Host 能从协议层看出来，而不是靠读文本猜。"""
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("query_device", {"sn": "SN-2024-000999"})

    assert result.is_error is True
    assert "未找到设备" in result.content[0].text


async def test_catalog_resource_is_read_only_data() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            contents = (await session.read_resource(DEVICE_CATALOG_URI)).contents

    catalog = json.loads(contents[0].text)
    assert {item["sn"] for item in catalog} == set(MOCK_DEVICES)


async def test_prompt_template_is_rendered_by_the_server() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            rendered = await session.get_prompt("fault_report", {"device_sn": "SN-2024-000123"})

    text = rendered.messages[0].content.text
    assert "SN-2024-000123" in text
    assert "现象" in text
