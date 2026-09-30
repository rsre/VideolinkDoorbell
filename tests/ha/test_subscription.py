"""Doorbell health and auth recovery with real HA event entities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from custom_components.videolink_doorbell import subscription
from custom_components.videolink_doorbell.api import VideolinkAuthError


@pytest.mark.asyncio
async def test_swallowed_subscription_failure_checks_credentials_and_starts_reauth(monkeypatch):
    host = SimpleNamespace(
        get_host_data=AsyncMock(),
        baichuan=SimpleNamespace(events_active=False, subscribe_events=AsyncMock(), check_subscribe_events=AsyncMock()),
    )
    monkeypatch.setattr(subscription, "_CHECK_SECONDS", 0)
    client = SimpleNamespace(host="camera.local", port=443, validate_credentials=AsyncMock(side_effect=VideolinkAuthError()))
    entry = SimpleNamespace(
        data={"username": "admin", "password": "test"}, entry_id="entry",
        unique_id="serial_channel_0", runtime_data=SimpleNamespace(client=client), async_start_reauth=Mock(),
    )
    runtime = SimpleNamespace(entry=entry, client=client, hass=Mock())
    service = subscription.DoorbellSubscription(runtime)
    await service._listen(host)
    assert not service.available
    client.validate_credentials.assert_awaited_once()
    entry.async_start_reauth.assert_called_once_with(runtime.hass)


def test_availability_transitions_log_once(monkeypatch, caplog):
    host = SimpleNamespace(visitor_detected=Mock(return_value=False))
    entry = SimpleNamespace(data={"username": "admin", "password": "test"}, entry_id="entry", unique_id="serial_channel_0",
                            runtime_data=SimpleNamespace(client=SimpleNamespace(host="camera.local", port=443)))
    service = subscription.DoorbellSubscription(SimpleNamespace(entry=entry))
    service._host = host
    service._set_available(False)
    service._set_available(False)
    assert len(caplog.records) == 1
    service._handle_push()
    assert service.available


@pytest.mark.asyncio
async def test_subscription_is_lazy_shared_and_reconnects_after_last_consumer(monkeypatch):
    import asyncio

    from custom_components.videolink_doorbell.runtime import VideolinkRuntime

    host = SimpleNamespace(
        get_host_data=AsyncMock(), logout=AsyncMock(),
        baichuan=SimpleNamespace(events_active=True, register_callback=Mock(), unregister_callback=Mock(),
                                subscribe_events=AsyncMock(), check_subscribe_events=AsyncMock(), unsubscribe_events=AsyncMock()),
    )
    factory = Mock(return_value=host)
    monkeypatch.setattr(subscription, "Host", factory)
    entry = SimpleNamespace(data={"username": "admin", "password": "test"}, entry_id="entry",
                            async_create_background_task=lambda hass, coro, name: asyncio.create_task(coro))
    client = SimpleNamespace(host="camera.local", port=443, native_talk_stop=AsyncMock())
    runtime = VideolinkRuntime(client, None, Mock(), entry)
    service = runtime.doorbell
    factory.assert_not_called()
    first, second = Mock(), Mock()
    await service.async_subscribe(first)
    await service.async_subscribe(second)
    await asyncio.sleep(0)
    assert service.available
    assert factory.call_count == host.baichuan.register_callback.call_count == 1
    await service.async_unsubscribe(first)
    host.logout.assert_not_awaited()
    await service.async_unsubscribe(second)
    assert not service.available
    host.logout.assert_awaited_once()
    await service.async_subscribe(first)
    await asyncio.sleep(0)
    assert factory.call_count == 2
    await runtime.async_close()
    assert not service.available
    assert host.logout.await_count == host.baichuan.unregister_callback.call_count == 2
    assert service._task is None
