"""Legacy benchmark protocol regressions without device/network I/O."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("two_way_benchmark_under_test", TOOLS / "two_way_audio_benchmark.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.asyncio
async def test_native_benchmark_sends_ownership_token_for_audio_and_stop():
    client = benchmark.HomeAssistantNativeTalk("https://ha.example", "user", "password", "camera.front")
    client.websocket = AsyncMock()
    client.websocket.recv.side_effect = [json.dumps(response) for response in (
        {"id": 1, "success": True, "result": {"token": "session-owner"}},
        {"id": 2, "success": True, "result": {"ok": True}},
        {"id": 3, "success": True, "result": {"ok": True}},
    )]
    await client.command("start")
    await client.command("audio", b"\x00\x00")
    await client.command("stop")
    sent = [json.loads(call.args[0]) for call in client.websocket.send.await_args_list]
    assert "token" not in sent[0]
    assert sent[1]["token"] == sent[2]["token"] == "session-owner"
    assert client._session_token is None


@pytest.mark.asyncio
async def test_native_benchmark_rejects_missing_start_token():
    client = benchmark.HomeAssistantNativeTalk("https://ha.example", "user", "password", "camera.front")
    client.websocket = AsyncMock()
    client.websocket.recv.return_value = json.dumps({"id": 1, "success": True, "result": {}})
    with pytest.raises(RuntimeError, match="ownership token"):
        await client.command("start")
    with pytest.raises(RuntimeError, match="Start a native talk session"):
        await client.command("audio", b"\x00\x00")
    assert client.websocket.send.await_count == 1
