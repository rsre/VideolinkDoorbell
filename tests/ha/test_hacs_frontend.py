"""Keep HACS dashboard resource management outside device backend setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import (
    ResourceStorageCollection,
    ResourceYAMLCollection,
)
from homeassistant.components.websocket_api.const import DOMAIN as WEBSOCKET_DOMAIN

from custom_components import videolink_doorbell as integration
from custom_components.videolink_doorbell import backend, hacs_frontend
from custom_components.videolink_doorbell.websocket import COMMAND


async def test_backend_setup_needs_no_dashboard_or_static_http_server(hass, monkeypatch):
    install = AsyncMock(side_effect=AssertionError("Backend invoked card installation"))
    monkeypatch.setattr(integration, "async_setup_frontend", install)
    assert LOVELACE_DATA not in hass.data
    assert await backend.async_setup(hass, {})
    assert COMMAND in hass.data[WEBSOCKET_DOMAIN]
    install.assert_not_awaited()
    assert LOVELACE_DATA not in hass.data


@pytest.mark.parametrize("existing_url", [None, hacs_frontend.LEGACY_CARD_URL + "?v=old"])
async def test_hacs_setup_installs_or_migrates_one_card_resource(
    hass, hass_storage, monkeypatch, existing_url,
):
    resources = ResourceStorageCollection(hass, SimpleNamespace(async_load=AsyncMock(return_value={})))
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    static_paths = AsyncMock()
    hass.http = SimpleNamespace(async_register_static_paths=static_paths)
    fallback = Mock()
    monkeypatch.setattr(hacs_frontend, "add_extra_js_url", fallback)
    if existing_url:
        original = await resources.async_create_item({"res_type": "js", "url": existing_url})

    assert await integration.async_setup(hass, {})
    # Resource management remains idempotent if invoked again.
    await hacs_frontend._async_register_card(hass)
    items = resources.async_items()
    assert len(items) == 1
    assert items[0]["url"] == f"{hacs_frontend.CARD_URL}?v={hacs_frontend.CARD_VERSION}"
    assert items[0]["type"] == "module"
    if existing_url:
        assert items[0]["id"] == original["id"]
    static_paths.assert_awaited_once()
    path = static_paths.call_args.args[0][0]
    assert path.url_path == hacs_frontend.CARD_URL
    assert path.path == str(hacs_frontend.CARD_PATH)
    fallback.assert_not_called()
    assert COMMAND in hass.data[WEBSOCKET_DOMAIN]


async def test_yaml_resources_use_extra_module_without_modifying_yaml(hass, monkeypatch):
    resources = ResourceYAMLCollection([])
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    fallback = Mock()
    monkeypatch.setattr(hacs_frontend, "add_extra_js_url", fallback)
    await hacs_frontend.async_setup(hass)
    fallback.assert_called_once_with(hass, f"{hacs_frontend.CARD_URL}?v={hacs_frontend.CARD_VERSION}")
    assert resources.async_items() == []
