"""HACS-only dashboard asset installation and Lovelace resource management."""

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant

CARD_URL = "/videolink_doorbell/videolink-doorbell.js"
LEGACY_CARD_URL = "/videolink_doorbell/videolink-doorbell-camera-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "videolink-doorbell.js"
CARD_VERSION = "0.12.56"


async def async_setup(hass: HomeAssistant) -> None:
    """Install the bundled card for HACS; the backend never calls this hook."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    await _async_register_card(hass)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Persist the card resource, with an extra-module fallback for YAML."""
    versioned_url = f"{CARD_URL}?v={CARD_VERSION}"
    lovelace = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        add_extra_js_url(hass, versioned_url)
        return

    try:
        await resources.async_get_info()
        existing = next(
            (
                item
                for item in resources.async_items()
                if item.get("url", "").split("?", 1)[0]
                in {CARD_URL, LEGACY_CARD_URL}
            ),
            None,
        )
        resource = {"res_type": "module", "url": versioned_url}
        if existing is None:
            await resources.async_create_item(resource)
        elif (
            existing.get("url") != versioned_url
            or existing.get("type", existing.get("res_type")) != "module"
        ):
            await resources.async_update_item(existing["id"], resource)
    except (AttributeError, KeyError, TypeError, ValueError):
        add_extra_js_url(hass, versioned_url)
