"""Doorbell push-event behavior against Home Assistant's event entity API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from homeassistant.components.event import DoorbellEventType

from custom_components.videolink_doorbell import event
from custom_components.videolink_doorbell.subscription import DoorbellSubscription


def test_visitor_rising_edges_produce_rings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated status pushes do not ring, but a second press does."""

    host = SimpleNamespace(pressed=False)
    host.visitor_detected = lambda channel: host.pressed
    runtime = SimpleNamespace(entry=SimpleNamespace(data={}, entry_id="entry"))
    subscription = DoorbellSubscription(runtime)
    subscription._host = host
    entry = SimpleNamespace(data={}, unique_id="serial_channel_0", runtime_data=SimpleNamespace(doorbell=subscription))
    ring = event.VideolinkDoorbellRing(entry)
    subscription._listeners.add(ring._handle_event)
    trigger = Mock()
    monkeypatch.setattr(ring, "_trigger_event", trigger)
    monkeypatch.setattr(ring, "async_write_ha_state", Mock())

    for pressed in (False, True, True, False, True):
        host.pressed = pressed
        subscription._handle_push()

    assert trigger.call_count == 2
    trigger.assert_any_call(DoorbellEventType.RING)
    assert ring.async_write_ha_state.call_count == 3
