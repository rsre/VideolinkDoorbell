"""Tests for the experimental native-talk framing layer."""

from __future__ import annotations

import asyncio
import importlib.util
import math
from pathlib import Path
import random
import struct
import sys
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_mix_diagnostics_count_raw_headers_and_forwarded_frames() -> None:
    frames = []
    raw_frames = []
    session = native_talk.NativeTalkSession(
        "camera", "user", "password", mix_frame_callback=frames.append,
    )

    class Client:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def receive(self):
            return await self.messages.get()

    session.client = Client()
    session.raw_frame_callback = lambda header, extension, payload: raw_frames.append(
        (header.message_id, header.response_code, extension, payload)
    )
    await session.client.messages.put((SimpleNamespace(message_id=202, response_code=200), b"", b"reply"))
    await session.client.messages.put((SimpleNamespace(message_id=202, response_code=0), b"", b"\x00\x00" * 2))
    task = asyncio.create_task(session._read_mix_frames())
    while session._mix_raw_messages < 2:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert session.mix_diagnostics() == {
        "raw_messages": 2, "headers": {"202/200": 1, "202/0": 1},
        "talk_candidates": 2, "parsed_frames": 0,
        "forwarded_frames": 0, "aes_extension_xml_frames": 0,
        "aes_payload_media_magic_frames": 0, "rejected_frames": 0,
        "pcm_verified": False, "reader_error": None,
    }
    assert raw_frames == [
        (202, 200, b"", b"reply"),
        (202, 0, b"", b"\x00\x00" * 2),
    ]


@pytest.mark.asyncio
async def test_mix_reader_does_not_decode_unverified_payload() -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")

    class Client:
        calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls > 1:
                raise ConnectionError("closed")
            return SimpleNamespace(message_id=202, response_code=0), b"", b"bad"

    session.client = Client()
    await session._read_mix_frames()
    diagnostics = session.mix_diagnostics()
    assert diagnostics["raw_messages"] == 1
    assert diagnostics["talk_candidates"] == 1
    assert diagnostics["parsed_frames"] == 0
    assert "ConnectionError" in diagnostics["reader_error"]


@pytest.mark.asyncio
async def test_mix_reader_only_delivers_matching_control_response() -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")

    class Client:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def receive(self):
            return await self.messages.get()

    session.client = Client()
    reader = asyncio.create_task(session._read_mix_frames())
    session._mix_reader_task = reader
    await session.client.messages.put((SimpleNamespace(message_id=202, response_code=200), b"", b""))
    await session.client.messages.put((SimpleNamespace(message_id=79, response_code=200), b"", b"old"))
    await session.client.messages.put((SimpleNamespace(message_id=11, response_code=200), b"", b"stop"))
    header, _, payload = await asyncio.wait_for(session._receive_response(11), 1)
    assert header.message_id == 11
    assert payload == b"stop"
    assert session._response_queue.empty()
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_mix_reader_failure_wakes_waiting_response() -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")

    class Client:
        async def receive(self):
            await asyncio.sleep(0)
            raise ConnectionError("socket closed")

    session.client = Client()
    session._mix_reader_task = asyncio.create_task(session._read_mix_frames())
    with pytest.raises(RuntimeError, match="socket closed"):
        await asyncio.wait_for(session._receive_response(11), 1)


@pytest.mark.asyncio
async def test_control_response_timeout_is_bounded() -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")
    session.RESPONSE_TIMEOUT_SECONDS = 0.01

    class Client:
        async def receive(self):
            await asyncio.Future()

    session.client = Client()
    with pytest.raises(TimeoutError):
        await session._receive_response(11)


@pytest.mark.asyncio
async def test_mix_wire_probe_only_reports_recognized_decryption(monkeypatch) -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")
    session._aes_key = b"x" * 16
    monkeypatch.setattr(native_talk, "aes_cfb_decrypt", lambda _key, data, **_kw: data)

    class Client:
        def __init__(self):
            self.calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls > 1:
                raise ConnectionError("closed")
            return SimpleNamespace(message_id=202, response_code=200), b"<?xml test", b"0\x31wb" + b"\x00" * 75

    session.client = Client()
    await session._read_mix_frames()
    diagnostics = session.mix_diagnostics()
    assert diagnostics["aes_extension_xml_frames"] == 1
    assert diagnostics["aes_payload_media_magic_frames"] == 1
    assert diagnostics["parsed_frames"] == 0


def test_observed_frame_round_trips() -> None:
    payload = bytes(range(128))
    encoded = native_talk.NativeTalkFrame(payload).encode()
    assert len(encoded) == native_talk.OBSERVED_HEADER_SIZE + len(payload)
    assert native_talk.split_native_talk_frame(encoded).payload == payload


def test_sdk_mix_parser_rejects_wire_transport_body() -> None:
    with pytest.raises(ValueError, match="2048-byte PCM"):
        native_talk.parse_native_mix_frame(b"\x00" * 4160)
    frame = native_talk.parse_native_mix_frame(b"\x01" * 4096)
    assert len(frame.far_end) == len(frame.near_end) == 2048


WIRE_MIX_HEADER = bytes.fromhex(
    "30316463483236340010000028000000a57caefd0e000000"
    "f923000204004e4f4e450304000000000004040000080000"
    "05040000080000060400000800000000"
)


def test_wire_mix_parser_uses_declared_pcm_regions() -> None:
    assert len(WIRE_MIX_HEADER) == 64
    frame = native_talk.parse_wire_mix_frame(WIRE_MIX_HEADER + b"\x01" * 2048 + b"\x02" * 2048)
    assert frame.far_end == b"\x01" * 2048
    assert frame.near_end == b"\x02" * 2048
    with pytest.raises(ValueError, match="regions"):
        changed = bytearray(WIRE_MIX_HEADER)
        struct.pack_into("<I", changed, 51, 1024)
        native_talk.parse_wire_mix_frame(bytes(changed) + b"\x00" * 4096)
    with pytest.raises(ValueError, match="container"):
        native_talk.parse_wire_mix_frame(b"\x00" * 4160)


@pytest.mark.asyncio
async def test_mix_reader_decrypts_verified_pcm_and_forwards_it() -> None:
    key = b"0123456789abcdef"
    samples = [round(4000 * math.sin(2 * math.pi * 440 * index / 16000)) for index in range(1024)]
    pcm = struct.pack("<1024h", *samples)
    encrypted = native_talk.aes_cfb_encrypt(key, WIRE_MIX_HEADER + pcm + bytes(2048))
    frames = []
    session = native_talk.NativeTalkSession("camera", "user", "password", mix_frame_callback=frames.append)
    session._aes_key = key

    class Client:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def receive(self):
            return await self.messages.get()

    session.client = Client()
    task = asyncio.create_task(session._read_mix_frames())
    for _ in range(2):
        await session.client.messages.put((SimpleNamespace(message_id=202, response_code=200), b"", encrypted))
    await asyncio.wait_for(_wait_for_forwarded(session), 2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert session.mix_diagnostics()["parsed_frames"] == 2
    assert session.mix_diagnostics()["pcm_verified"] is True
    assert len(frames) == 1
    assert frames[0].far_end == pcm
    assert frames[0].near_end == bytes(2048)
    assert frames[0].incoming_pcm == pcm
    assert frames[0].cleaned_near_end is None


async def _wait_for_forwarded(session) -> None:
    while session.mix_diagnostics()["forwarded_frames"] < 1:
        await asyncio.sleep(0)


def test_pcm_plausibility_rejects_random_ciphertext() -> None:
    rng = random.Random(5)
    assert not native_talk._plausible_pcm16(rng.randbytes(2048))
    assert native_talk._plausible_pcm16(b"\x00" * 2048)
    tone = struct.pack(
        "<1024h",
        *(round(4000 * math.sin(2 * math.pi * 440 * index / 16000)) for index in range(1024)),
    )
    assert native_talk._plausible_pcm16(tone)


@pytest.mark.asyncio
async def test_mix_reader_rejects_random_data_even_with_valid_container() -> None:
    key = b"0123456789abcdef"
    rng = random.Random(5)
    encrypted = native_talk.aes_cfb_encrypt(key, WIRE_MIX_HEADER + rng.randbytes(4096))
    frames = []
    session = native_talk.NativeTalkSession("camera", "user", "password", mix_frame_callback=frames.append)
    session._aes_key = key

    class Client:
        calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(message_id=202, response_code=200), b"", encrypted
            raise ConnectionError("closed")

    session.client = Client()
    await session._read_mix_frames()
    assert session.mix_diagnostics()["parsed_frames"] == 1
    assert session.mix_diagnostics()["rejected_frames"] == 1
    assert session.mix_diagnostics()["forwarded_frames"] == 0
    assert frames == []


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


def test_stateful_dvi4_encoder_carries_predictor_between_blocks() -> None:
    first = b"\x10\x27" * 1024
    second = b"\xff\x7f" * 1024
    encoder = native_talk.Dvi4Encoder()
    first_block = encoder.encode_pcm16le(first)
    second_block = encoder.encode_pcm16le(second)
    assert len(first_block) == 516
    assert len(second_block) == 516
    assert int.from_bytes(second_block[:2], "little", signed=True) != 0


def test_stateful_dvi4_block_header_matches_encoder_state() -> None:
    encoder = native_talk.Dvi4Encoder()
    first_pcm = b"\x00\x20" * 1024
    second_pcm = b"\x00\xe0" * 1024
    first = encoder.encode_pcm16le(first_pcm)
    predictor, index = encoder.predictor, encoder.index
    second = encoder.encode_pcm16le(second_pcm)
    assert int.from_bytes(second[:2], "little", signed=True) == predictor
    assert second[2] == index
    assert len(first) == len(second) == 516


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


def test_talk_audio_extension_can_be_encrypted() -> None:
    message = native_talk.serialize_talk_audio_message(
        b"\x00" * 516,
        msg_num=8,
        encrypt_xml=lambda _channel, payload: b"X" * len(payload),
    )
    assert message[24:149] == b"X" * 125


def test_default_talk_profile_matches_doorbell_fallback() -> None:
    profile = native_talk.TalkAbility()
    assert profile.sample_rate == 16_000
    assert profile.sample_precision == 16
    assert profile.length_per_encoder == 1_024
    assert profile.sound_track == "mono"


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


def test_official_app_talk_header_fixture() -> None:
    # Safe prefix retained from the official app's 683-byte write. It does
    # not contain microphone payload or credentials.
    header = native_talk.parse_baichuan_header(
        bytes.fromhex("f0debc0aca00000093020000000000000000146483000000")
    )
    assert header.message_id == 202
    assert header.body_length == 659
    assert header.message_number == 0
    assert header.payload_offset == 131
    assert header.header_length + header.body_length == 683


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


def test_wire_prefix_decryption_is_bounded() -> None:
    session = native_talk.NativeTalkSession("camera", "user", "password")
    session._aes_key = b"0123456789abcdef"
    plaintext = bytes(range(100))
    encrypted = native_talk.aes_cfb_encrypt(session._aes_key, plaintext)
    assert session.decrypt_wire_prefix(encrypted) == plaintext[:64]
    with pytest.raises(ValueError):
        session.decrypt_wire_prefix(encrypted, limit=65)


def test_parser_rejects_control_or_truncated_data() -> None:
    with pytest.raises(ValueError, match="shorter"):
        native_talk.split_native_talk_frame(b"not audio")

    frame = native_talk.NativeTalkFrame(b"payload").encode()
    with pytest.raises(ValueError, match="length mismatch"):
        native_talk.split_native_talk_frame(frame[:-1])


@pytest.mark.asyncio
async def test_native_talk_channel_owns_session_lifecycle(monkeypatch) -> None:
    class FakeTransport:
        instances = []

        def __init__(self, *args, **kwargs):
            self.mix_frame_callback = kwargs.get("mix_frame_callback")
            self.closed = False
            self.sent = []
            self.config = native_talk.TalkAbility().to_config(kwargs["channel"])
            self.__class__.instances.append(self)

        async def login(self):
            return None

        async def talk_ability(self):
            return native_talk.TalkAbility()

        async def open_talk(self, config):
            self.config = config

        async def send_pcm(self, pcm):
            self.sent.append(pcm)

        async def stop_talk(self):
            return None

        async def close(self):
            self.closed = True

    monkeypatch.setattr(native_talk, "NativeTalkSession", FakeTransport)
    channel = native_talk.NativeTalkChannel("camera", "user", "secret", channel=0)

    await channel.start()
    assert channel.active is True
    assert channel.talk_config.sample_rate == 16_000
    observer = lambda *_args: None
    channel.set_raw_callback(observer)
    assert channel.transport.raw_frame_callback is observer
    await channel.send_pcm(b"frame")
    await asyncio.sleep(0)
    await channel.stop()

    assert FakeTransport.instances[0].sent == [b"frame"]
    assert FakeTransport.instances[0].closed is True
    assert channel.active is False
    await channel.start()
    assert FakeTransport.instances[1].raw_frame_callback is observer
    await channel.stop()


@pytest.mark.asyncio
async def test_native_audio_queue_keeps_recent_frames(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowTransport:
        def __init__(self, *args, **kwargs):
            self.sent = []

        async def login(self):
            pass

        async def talk_ability(self):
            return native_talk.TalkAbility()

        async def open_talk(self, config):
            pass

        async def send_pcm(self, pcm):
            started.set()
            await release.wait()
            self.sent.append(pcm)

        async def stop_talk(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(native_talk, "NativeTalkSession", SlowTransport)
    channel = native_talk.NativeTalkChannel("camera", "user", "secret")
    channel.MAX_AUDIO_AGE_SECONDS = 5
    await channel.start()
    transport = channel.transport
    completions = [await channel.enqueue_pcm(b"0")]
    await started.wait()
    for index in range(1, 6):
        completions.append(await channel.enqueue_pcm(str(index).encode()))
    release.set()
    await asyncio.gather(*completions)
    await channel.stop()
    assert transport.sent == [b"0", b"2", b"3", b"4", b"5"]


@pytest.mark.asyncio
async def test_native_audio_queue_discards_stale_frame(monkeypatch) -> None:
    class Transport:
        def __init__(self, *args, **kwargs):
            self.sent = []

        async def login(self):
            pass

        async def talk_ability(self):
            return native_talk.TalkAbility()

        async def open_talk(self, config):
            pass

        async def send_pcm(self, pcm):
            self.sent.append(pcm)

        async def stop_talk(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(native_talk, "NativeTalkSession", Transport)
    channel = native_talk.NativeTalkChannel("camera", "user", "secret")
    await channel.start()
    transport = channel.transport
    channel.MAX_AUDIO_AGE_SECONDS = -1
    await channel.send_pcm(b"old")
    await channel.stop()
    assert transport.sent == []
