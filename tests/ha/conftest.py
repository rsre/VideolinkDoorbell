"""Shared real-manager fixtures; device transports are the mocked boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
async def entry_runtime(hass, enable_custom_integrations, monkeypatch):
    from homeassistant.loader import async_get_integration
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components import videolink_doorbell as integration
    from custom_components.videolink_doorbell import backend, subscription
    from custom_components.videolink_doorbell.api import DeviceInfo, VideolinkClient
    from custom_components.videolink_doorbell.const import DOMAIN

    loaded = await async_get_integration(hass, DOMAIN)
    # Frontend/go2rtc services are separate integrations; preserve actual platform
    # forwarding, registries, entry states and entity teardown in these tests.
    monkeypatch.setattr(loaded, "dependencies", [])
    monkeypatch.setattr(integration, "async_setup_frontend", AsyncMock())
    client = AsyncMock(spec=VideolinkClient)
    client.host = "camera.local"
    client.port = 443
    client.base_url = "https://camera.local"
    client.device_info.return_value = DeviceInfo("Front", "Model", "serial", "FW")
    monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
    host = SimpleNamespace(
        get_host_data=AsyncMock(), logout=AsyncMock(), visitor_detected=Mock(return_value=False),
        baichuan=SimpleNamespace(events_active=True, register_callback=Mock(), unregister_callback=Mock(),
                                subscribe_events=AsyncMock(), check_subscribe_events=AsyncMock(), unsubscribe_events=AsyncMock()),
    )
    monkeypatch.setattr(subscription, "Host", Mock(return_value=host))
    entry = MockConfigEntry(
        domain=DOMAIN, title="Front", version=4, unique_id="serial_channel_0",
        data={"host": "camera.local", "port": 443, "username": "admin", "password": "test", "verify_ssl": True},
    )
    entry.add_to_hass(hass)
    return entry, client, host

