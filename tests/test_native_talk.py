"""Tests for the experimental native-talk framing layer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components/videolink_doorbell/native_talk.py"
)
SPEC = importlib.util.spec_from_file_location("videolink_native_talk", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
native_talk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native_talk
SPEC.loader.exec_module(native_talk)


def test_observed_frame_round_trips() -> None:
    payload = bytes(range(128))
    encoded = native_talk.NativeTalkFrame(payload).encode()
    assert len(encoded) == native_talk.OBSERVED_HEADER_SIZE + len(payload)
    assert native_talk.split_native_talk_frame(encoded).payload == payload


def test_packetizer_requires_one_native_frame() -> None:
    packetizer = native_talk.NativeTalkPacketizer(lambda pcm: b"encoded")
    packet = packetizer.packetize(bytes(native_talk.SAMPLES_PER_FRAME * 2))
    assert native_talk.split_native_talk_frame(packet).payload == b"encoded"

    with pytest.raises(ValueError):
        packetizer.packetize(b"short")


def test_dvi4_encoder_has_expected_block_shape() -> None:
    pcm = b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in range(1024))
    encoded = native_talk.encode_dvi4_pcm16le(pcm)
    assert len(encoded) == 4 + (1023 + 1) // 2
    assert encoded[:2] == b"\x00\x00"


def test_dvi4_encoder_rejects_invalid_pcm() -> None:
    with pytest.raises(ValueError):
        native_talk.encode_dvi4_pcm16le(b"\x00")


def test_adpcm_media_has_baichuan_header_and_alignment() -> None:
    media = native_talk.serialize_adpcm_media(b"\x00\x00\x00\x00" + b"\x55" * 508)
    assert media[:4] == b"0\x31wb"
    assert media[-4:] == b"\x00" * 4
    assert int.from_bytes(media[4:6], "little") == 516
    assert int.from_bytes(media[10:12], "little") == 0


def test_talk_config_and_message_layout() -> None:
    config = native_talk.TalkConfig()
    message = native_talk.serialize_talk_config_message(config, msg_num=7)
    assert message[:4] == b"\xf0\xde\xbc\x0a"
    assert int.from_bytes(message[4:8], "little") == native_talk.MSG_ID_TALK_CONFIG
    assert int.from_bytes(message[20:24], "little") > 0
    assert b"<audioType>adpcm</audioType>" in message


def test_talk_audio_message_contains_binary_media() -> None:
    data = b"\x00\x00\x00\x00" + b"\x00" * 512
    message = native_talk.serialize_talk_audio_message(data, msg_num=8)
    assert int.from_bytes(message[4:8], "little") == native_talk.MSG_ID_TALK
    assert b"<binaryData>1</binaryData>" in message
    assert b"0\x31wb" in message


def test_modern_login_digests_and_bc_encrypt() -> None:
    user_digest, password_digest = native_talk.modern_login_digests("admin", "secret", "nonce")
    assert user_digest == "B69E7AAD464DBE941A1AA4ABFB3CF89"
    assert password_digest == "8D7B6BEA1513F839F8861291F18AC1D"
    data = b"<body>test</body>"
    assert native_talk.bc_encrypt(0, native_talk.bc_encrypt(0, data)) == data


def test_baichuan_message_parser_handles_modern_header() -> None:
    message = native_talk.serialize_talk_config_message(native_talk.TalkConfig(), msg_num=7)
    header, extension, payload = native_talk.split_baichuan_message(message)
    assert header.message_id == native_talk.MSG_ID_TALK_CONFIG
    assert header.message_number == 7
    assert extension.startswith(b'<?xml version="1.0"')
    assert b'<Extension version="1.1">' in extension
    assert b"<body>" in payload


def test_modern_header_can_be_inspected_from_twenty_byte_prefix() -> None:
    message = native_talk.serialize_talk_message(
        msg_id=202,
        msg_num=1,
        extension=b"<Extension />",
        payload=b"payload",
    )
    header = native_talk.parse_baichuan_header(message[:20])
    assert header.message_class == native_talk.BC_CLASS_MODERN_24
    assert header.header_length == 24
    assert header.payload_offset is None


def test_login_upgrade_is_a_20_byte_header() -> None:
    message = native_talk.serialize_login_upgrade(message_number=3)
    assert len(message) == 20
    header = native_talk.parse_baichuan_header(message)
    assert header.message_id == 1
    assert header.response_code == 0xDC12


def test_aes_key_derivation_and_cfb_round_trip() -> None:
    key = native_talk.make_aes_key("password", "nonce-abc")
    assert len(key) == 16
    plaintext = b"<body><TalkConfig/></body>"
    encrypted = native_talk.aes_cfb_encrypt(key, plaintext)
    assert encrypted != plaintext
    assert native_talk.aes_cfb_decrypt(key, encrypted) == plaintext


def test_parser_rejects_control_or_truncated_data() -> None:
    with pytest.raises(ValueError, match="shorter"):
        native_talk.split_native_talk_frame(b"not audio")

    frame = native_talk.NativeTalkFrame(b"payload").encode()
    with pytest.raises(ValueError, match="length mismatch"):
        native_talk.split_native_talk_frame(frame[:-1])
