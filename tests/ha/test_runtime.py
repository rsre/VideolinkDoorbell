"""Entry-owned resource lifecycle tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from custom_components.videolink_doorbell.api import VideolinkConnectionError
from custom_components.videolink_doorbell.runtime import VideolinkRuntime


@pytest.fixture
def runtime():
    client = SimpleNamespace(native_talk_start=AsyncMock(), native_talk_stop=AsyncMock())
    hass = SimpleNamespace(bus=SimpleNamespace(async_listen_once=Mock(return_value=Mock())))
    entry = SimpleNamespace(async_create_background_task=lambda hass, coro, name: asyncio.create_task(coro))
    return VideolinkRuntime(client, None, hass, entry)


async def test_runtime_cancels_native_acquisition_and_closes_once(runtime):
    entered = asyncio.Event()
    async def start(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    runtime.client.native_talk_start.side_effect = start
    runtime.async_initialize()
    task = asyncio.create_task(runtime.async_start_native(0, owner="owner"))
    await entered.wait()
    await asyncio.gather(runtime.async_close(), runtime.async_close())
    assert task.cancelled()
    assert runtime.closed
    runtime.client.native_talk_stop.assert_awaited_once()
    runtime.hass.bus.async_listen_once.return_value.assert_called_once()
    with pytest.raises(VideolinkConnectionError, match="unloading"):
        await runtime.async_start_native(0, owner="owner")


async def test_runtime_settles_tasks_and_continues_independent_cleanup(runtime):
    settled = asyncio.Event()
    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
    runtime.async_create_task(background(), "test cleanup")
    await asyncio.sleep(0)
    first = AsyncMock()
    failed = AsyncMock(side_effect=TimeoutError)
    runtime.async_add_cleanup(first)
    runtime.async_add_cleanup(failed)
    await runtime.async_close()
    assert settled.is_set()
    first.assert_awaited_once()
    failed.assert_awaited_once()
    runtime.client.native_talk_stop.assert_awaited_once()


async def test_failed_platform_unload_keeps_runtime_usable(runtime):
    from custom_components.videolink_doorbell import async_unload_entry

    entry = SimpleNamespace(runtime_data=runtime)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=False)))
    assert not await async_unload_entry(hass, entry)
    assert not runtime.closing
    runtime.client.native_talk_stop.assert_not_awaited()
