"""Doorbell health and auth recovery with real HA event entities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from custom_components.videolink_doorbell import event
from custom_components.videolink_doorbell.api import VideolinkAuthError


@pytest.mark.asyncio
async def test_swallowed_subscription_failure_checks_credentials_and_starts_reauth(monkeypatch):
    host = SimpleNamespace(
        get_host_data=AsyncMock(),
        baichuan=SimpleNamespace(events_active=False, subscribe_events=AsyncMock(), check_subscribe_events=AsyncMock()),
    )
    monkeypatch.setattr(event, "Host", Mock(return_value=host))
    monkeypatch.setattr(event, "_CHECK_SECONDS", 0)
    client = SimpleNamespace(host="camera.local", port=443, validate_credentials=AsyncMock(side_effect=VideolinkAuthError()))
    entry = SimpleNamespace(
        data={"username": "admin", "password": "test"}, entry_id="entry",
        unique_id="serial_channel_0", runtime_data=SimpleNamespace(client=client), async_start_reauth=Mock(),
    )
    ring = event.VideolinkDoorbellRing(entry, Mock())
    ring.hass = Mock()
    ring.async_write_ha_state = Mock()
    await ring._listen()
    assert not ring.available
    client.validate_credentials.assert_awaited_once()
    entry.async_start_reauth.assert_called_once_with(ring.hass)


def test_availability_transitions_log_once(monkeypatch, caplog):
    host = SimpleNamespace(visitor_detected=Mock(return_value=False))
    monkeypatch.setattr(event, "Host", Mock(return_value=host))
    entry = SimpleNamespace(data={"username": "admin", "password": "test"}, entry_id="entry", unique_id="serial_channel_0",
                            runtime_data=SimpleNamespace(client=SimpleNamespace(host="camera.local", port=443)))
    ring = event.VideolinkDoorbellRing(entry, Mock())
    ring.async_write_ha_state = Mock()
    ring._set_subscription_available(False)
    ring._set_subscription_available(False)
    assert len(caplog.records) == 1
    ring._handle_push()
    assert ring.available
