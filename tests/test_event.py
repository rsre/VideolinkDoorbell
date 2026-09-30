"""Doorbell push-event behavior against Home Assistant's event entity API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

try:
    pytest.importorskip("homeassistant")
    pytest.importorskip("reolink_aio")
    from homeassistant.components.event import DoorbellEventType
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

    from custom_components.videolink_doorbell import event
except (ImportError, AttributeError) as err:
    pytest.skip(f"Home Assistant test dependencies are unavailable: {err}", allow_module_level=True)


def test_visitor_rising_edges_produce_rings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated status pushes do not ring, but a second press does."""

    class Host:
        pressed = False

        def __init__(self, *args, **kwargs) -> None:
            pass

        def visitor_detected(self, channel: int) -> bool:
            assert channel == 0
            return self.pressed

    monkeypatch.setattr(event, "Host", Host)
    entry = SimpleNamespace(
        data={CONF_USERNAME: "admin", CONF_PASSWORD: "test"},
        entry_id="entry-1",
        unique_id="serial_channel_0",
        runtime_data=SimpleNamespace(client=SimpleNamespace(host="camera.local", port=443)),
    )
    ring = event.VideolinkDoorbellRing(entry, SimpleNamespace())
    trigger = Mock()
    monkeypatch.setattr(ring, "_trigger_event", trigger)
    monkeypatch.setattr(ring, "async_write_ha_state", Mock())

    for pressed in (False, True, True, False, True):
        ring._host.pressed = pressed
        ring._handle_push()

    assert trigger.call_count == 2
    trigger.assert_any_call(DoorbellEventType.RING)
    assert ring.async_write_ha_state.call_count == 2
