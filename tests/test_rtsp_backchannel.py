"""Incremental RTSP parsing and finite handshake regression tests."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location("rtsp_backchannel_under_test", Path(__file__).parents[1] / "tools/rtsp_backchannel.py")
rtsp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rtsp)


def client():
    return rtsp.RtspBackchannel("rtsp://camera.local/video", "admin", "password")


def test_fragmented_short_response_keeps_body_and_following_response():
    channel = client()
    channel.sock = Mock()
    channel.sock.recv.side_effect = [
        b"RTSP/1.0 200 OK\r\nCont", b"ent-Length: 3\r\n\r\nabcRTSP/1.0 200 OK\r\n\r\n",
    ]
    status, _, body = channel._read_response()
    assert (status, body) == (200, b"abc")
    assert channel._read_response() == (200, {}, b"")
    assert channel.sock.recv.call_count == 2


def test_interleaved_frame_before_fragmented_response_is_skipped():
    channel = client()
    channel.sock = Mock()
    channel.sock.recv.side_effect = [b"$\x00\x00\x02abRTSP/1.0 200", b" OK\r\n\r\n"]
    assert channel._read_response() == (200, {}, b"")
    assert channel.sock.recv.call_count == 2


def test_handshake_preserves_connection_timeout_until_play(monkeypatch):
    channel = client()
    sock = Mock()
    connect = Mock(return_value=sock)
    monkeypatch.setattr(rtsp.socket, "create_connection", connect)
    def request(*args, **kwargs):
        sock.settimeout.assert_not_called()
        return 200, {}, b""
    channel._request = Mock(side_effect=request)
    channel.open()
    connect.assert_called_once_with(("camera.local", 554), timeout=5)
    sock.settimeout.assert_called_once_with(None)


def test_closed_peer_during_header_is_reported():
    channel = client()
    channel.sock = Mock()
    channel.sock.recv.side_effect = [b"RTSP/1.0 200", b""]
    with pytest.raises(ConnectionError, match="headers"):
        channel._read_response()
