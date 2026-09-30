"""Bounded raw capture storage without Home Assistant or a camera."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("videolink_capture_under_test", Path(__file__).parents[1] / "custom_components/videolink_doorbell/capture.py")
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)

HEADER = SimpleNamespace(message_id=202, response_code=200, message_class=0x6414, channel_id=0,
                         stream_type=0, message_number=1, body_length=2, payload_offset=0)


def test_capture_budget_bounds_storage_and_reports_drops():
    store = capture.NativeCaptureStore(max_bytes=80)
    store.start("owner")
    for _ in range(3):
        store.record("owner", HEADER, b"", b"ab")
    result = store.chunk("owner", 0)
    assert len(result["frames"]) == 1
    assert result["dropped"] == 2
    assert result["frames"][0]["payload_b64"] == "YWI="
    assert result["done"]
    assert "decrypted_payload_prefix_b64" not in result["frames"][0]


def test_capture_pagination_takeover_and_discard():
    store = capture.NativeCaptureStore()
    store.start("first")
    for _ in range(40):
        store.record("first", HEADER, b"", b"ab", b"prefix")
    first = store.chunk("first", 0)
    assert len(first["frames"]) == first["next_cursor"] == 32
    assert not first["done"]
    assert len(store.chunk("first", 32)["frames"]) == 8
    store.start("second")
    store.record("first", HEADER, b"", b"ignored")
    with pytest.raises(ValueError, match="not enabled"):
        store.chunk("first", 0)
    assert store.chunk("second", 0)["frames"] == []
    store.discard("second")
    with pytest.raises(ValueError):
        store.chunk("second", 0)
