"""Baichuan native-talk codec and framing primitives.

The Baichuan session and message serializer are intentionally separate from
this module.  The codec behavior follows the public Neolink/bairelay talk
implementation: 16-bit mono PCM is encoded as DVI-4 (IMA) ADPCM in small,
paced blocks before being placed in a Baichuan media message.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import hashlib
import re
import struct
from typing import Callable, Iterable
from xml.sax.saxutils import escape


NATIVE_TALK_PORT = 9000
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLES_PER_FRAME = 1_024
FRAME_DURATION_MS = 64
OBSERVED_HEADER_SIZE = 24
OBSERVED_FRAME_SIZE = 683
BC_MAGIC = 0x0ABCDEF0
BC_CLASS_MODERN_24 = 0x6414
MSG_ID_TALK_CONFIG = 201
MSG_ID_TALK = 202
MSG_ID_TALK_STOP = 11
BCMEDIA_ADPCM_MAGIC = 0x62773130
BCMEDIA_ADPCM_DATA_MAGIC = 0x0100

_IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8) * 2
_IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878,
    2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894,
    6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818,
    18500, 20350, 22385, 24623, 27086, 29794, 32767,
)

# The first native talk frame captured from the official client.  This is kept
# only as a parser fixture; the actual Baichuan message envelope is still a
# separate layer and must not be assumed from this media prefix alone.
_OBSERVED_HEADER = bytes.fromhex(
    "f0debc0aca00000093020000000000000000146483000000"
)


def _ima_encode_nibble(sample: int, predictor: int, index: int) -> tuple[int, int, int]:
    step = _IMA_STEP_TABLE[index]
    difference = sample - predictor
    nibble = 0
    if difference < 0:
        nibble = 8
        difference = -difference
    estimate = step >> 3
    if difference >= step:
        nibble |= 4
        difference -= step
        estimate += step
    if difference >= step >> 1:
        nibble |= 2
        difference -= step >> 1
        estimate += step >> 1
    if difference >= step >> 2:
        nibble |= 1
        estimate += step >> 2
    predictor += -estimate if nibble & 8 else estimate
    predictor = max(-32768, min(32767, predictor))
    index += _IMA_INDEX_TABLE[nibble]
    index = max(0, min(88, index))
    return nibble, predictor, index


def encode_dvi4_block(samples: Iterable[int], *, byte_order: str = ">") -> bytes:
    """Encode one mono DVI-4/IMA ADPCM block.

    DVI-4 stores the initial 16-bit predictor, an index, and a reserved byte,
    followed by two 4-bit samples per byte.  The first sample is represented
    by the predictor, so a block containing ``N`` PCM samples contains
    ``4 + ceil((N - 1) / 2)`` bytes.
    """
    pcm = tuple(max(-32768, min(32767, int(sample))) for sample in samples)
    if not pcm:
        raise ValueError("DVI-4 block requires at least one sample")
    if byte_order not in ("<", ">"):
        raise ValueError("byte_order must be '<' or '>'")
    predictor = pcm[0]
    index = 0
    encoded = bytearray(struct.pack(f"{byte_order}hBB", predictor, index, 0))
    for offset in range(1, len(pcm), 2):
        high, predictor, index = _ima_encode_nibble(pcm[offset], predictor, index)
        low = 0
        if offset + 1 < len(pcm):
            low, predictor, index = _ima_encode_nibble(pcm[offset + 1], predictor, index)
        encoded.append((high << 4) | low)
    return bytes(encoded)


def encode_dvi4_pcm16le(pcm: bytes, *, byte_order: str = ">") -> bytes:
    """Encode signed 16-bit little-endian mono PCM as one DVI-4 block."""
    if len(pcm) % 2:
        raise ValueError("PCM16 data must contain complete samples")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return encode_dvi4_block(samples, byte_order=byte_order)


@dataclass(frozen=True, slots=True)
class NativeTalkFrame:
    """One captured native-media frame used for parser fixtures."""

    payload: bytes

    def encode(self) -> bytes:
        """Return the observed media prefix around an encoded payload."""
        if not self.payload:
            raise ValueError("native talk payload must not be empty")
        header = bytearray(_OBSERVED_HEADER)
        struct.pack_into("<I", header, 8, len(self.payload))
        return bytes(header) + self.payload


def split_native_talk_frame(data: bytes) -> NativeTalkFrame:
    """Validate and split one complete captured media frame."""
    if len(data) < OBSERVED_HEADER_SIZE:
        raise ValueError("native talk frame is shorter than its header")
    if data[:8] != _OBSERVED_HEADER[:8]:
        raise ValueError("unexpected native talk frame marker")
    payload_length = struct.unpack_from("<I", data, 8)[0]
    expected = OBSERVED_HEADER_SIZE + payload_length
    if len(data) != expected:
        raise ValueError(
            f"native talk frame length mismatch: got {len(data)}, expected {expected}"
        )
    return NativeTalkFrame(data[OBSERVED_HEADER_SIZE:])


class NativeTalkPacketizer:
    """Turn one 64 ms PCM frame into encoded audio.

    ``encode_frame`` is injected so the Baichuan message layer can later use
    the negotiated TalkAbility framing without coupling it to this codec.
    """

    def __init__(self, encode_frame: Callable[[bytes], bytes]) -> None:
        self._encode_frame = encode_frame

    def packetize(self, pcm_frame: bytes) -> bytes:
        """Encode one 1024-sample mono PCM frame."""
        if len(pcm_frame) != SAMPLES_PER_FRAME * 2:
            raise ValueError("expected 1024 signed 16-bit mono samples")
        payload = self._encode_frame(pcm_frame)
        if not isinstance(payload, bytes):
            raise TypeError("native talk encoder must return bytes")
        return NativeTalkFrame(payload).encode()


@dataclass(frozen=True, slots=True)
class TalkConfig:
    """The negotiated camera-facing talk configuration."""

    channel_id: int = 0
    sample_rate: int = SAMPLE_RATE
    sample_precision: int = 16
    length_per_encoder: int = 1024
    duplex: str = "FDX"
    audio_stream_mode: str = "followVideoStream"
    sound_track: str = "mono"
    version: str = "1.0"

    def to_xml(self) -> bytes:
        """Serialize the TalkConfig body used by MSG_ID_TALK_CONFIG."""
        values = (
            f'<TalkConfig version="{escape(self.version)}">'
            f"<channelId>{self.channel_id}</channelId>"
            f"<duplex>{escape(self.duplex)}</duplex>"
            f"<audioStreamMode>{escape(self.audio_stream_mode)}</audioStreamMode>"
            "<audioConfig>"
            "<audioType>adpcm</audioType>"
            f"<sampleRate>{self.sample_rate}</sampleRate>"
            f"<samplePrecision>{self.sample_precision}</samplePrecision>"
            f"<lengthPerEncoder>{self.length_per_encoder}</lengthPerEncoder>"
            f"<soundTrack>{escape(self.sound_track)}</soundTrack>"
            "</audioConfig>"
            "</TalkConfig>"
        )
        return f'<body version="{escape(self.version)}">{values}</body>'.encode()


def serialize_adpcm_media(data: bytes) -> bytes:
    """Serialize one Baichuan ADPCM media block, including 8-byte padding."""
    if len(data) < 4:
        raise ValueError("ADPCM data must include its 4-byte predictor header")
    if (len(data) - 4) % 2:
        raise ValueError("ADPCM block payload must have an even byte count")
    media = struct.pack(
        "<IHHHH",
        BCMEDIA_ADPCM_MAGIC,
        len(data) + 4,
        len(data) + 4,
        BCMEDIA_ADPCM_DATA_MAGIC,
        (len(data) - 4) // 2,
    ) + data
    return media + bytes((-len(data)) % 8)


def serialize_talk_message(
    *,
    msg_id: int,
    msg_num: int,
    channel_id: int = 0,
    payload: bytes = b"",
    extension: bytes = b"",
    stream_type: int = 0,
    response_code: int = 0,
    message_class: int = BC_CLASS_MODERN_24,
) -> bytes:
    """Serialize a modern Baichuan message without XML encryption.

    Authentication negotiates XML encryption separately.  This low-level
    serializer therefore refuses to pretend that encrypted sessions are
    supported; callers must supply the negotiated cipher layer before using
    it against a camera.
    """
    if not 0 <= channel_id <= 255 or not 0 <= stream_type <= 255:
        raise ValueError("channel_id and stream_type must fit in one byte")
    if not 0 <= msg_num <= 0xFFFF or not 0 <= response_code <= 0xFFFF:
        raise ValueError("message number and response code must fit in uint16")
    if message_class != BC_CLASS_MODERN_24:
        raise ValueError("only the modern 24-byte header is supported")
    body = extension + payload
    header = struct.pack(
        "<III BBHHH I",
        BC_MAGIC,
        msg_id,
        len(body),
        channel_id,
        stream_type,
        msg_num,
        response_code,
        message_class,
        len(extension) if extension else 0,
    )
    return header + body


def serialize_talk_config_message(
    config: TalkConfig,
    *,
    msg_num: int,
    encrypt_xml: Callable[[int, bytes], bytes] | None = None,
) -> bytes:
    """Build the talk configuration command."""
    extension = (
        f'<Extension><channelId>{config.channel_id}</channelId></Extension>'.encode()
    )
    payload = config.to_xml()
    if encrypt_xml is not None:
        extension = encrypt_xml(config.channel_id, extension)
        payload = encrypt_xml(config.channel_id, payload)
    return serialize_talk_message(
        msg_id=MSG_ID_TALK_CONFIG,
        msg_num=msg_num,
        channel_id=config.channel_id,
        extension=extension,
        payload=payload,
    )


def serialize_talk_audio_message(
    adpcm_data: bytes, *, msg_num: int, channel_id: int = 0
) -> bytes:
    """Build one binary MSG_ID_TALK message from a DVI-4 ADPCM block."""
    extension = f"<Extension><channelId>{channel_id}</channelId><binaryData>1</binaryData></Extension>".encode()
    return serialize_talk_message(
        msg_id=MSG_ID_TALK,
        msg_num=msg_num,
        channel_id=channel_id,
        extension=extension,
        payload=serialize_adpcm_media(adpcm_data),
    )


def md5_hex(value: str) -> str:
    """Return the lowercase MD5 representation used by modern login."""
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


def bc_encrypt(offset: int, data: bytes) -> bytes:
    """Apply the legacy Baichuan XML XOR transform."""
    key = (0x1F, 0x2D, 0x3C, 0x4B, 0x5A, 0x69, 0x78, 0xFF)
    return bytes(value ^ key[(offset + index) % 8] ^ (offset & 0xFF) for index, value in enumerate(data))


def make_aes_key(password: str, nonce: str) -> bytes:
    """Derive the 128-bit AES key used by modern Baichuan sessions."""
    phrase_digest = hashlib.md5(
        f"{nonce}-{password}".encode(), usedforsecurity=False
    ).hexdigest().upper()
    return phrase_digest.encode()[:16]


def aes_cfb_encrypt(key: bytes, data: bytes, iv: bytes = b"0123456789abcdef") -> bytes:
    """Encrypt XML using AES-128-CFB, matching the public reference client."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
        try:
            from cryptography.hazmat.decrepit.ciphers import modes
        except ImportError:  # pragma: no cover - older cryptography
            from cryptography.hazmat.primitives.ciphers import modes
    except ImportError as err:  # pragma: no cover - depends on HA runtime
        raise RuntimeError("cryptography is required for AES Baichuan sessions") from err
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("AES-CFB requires 16-byte key and IV")
    return Cipher(algorithms.AES(key), modes.CFB(iv)).encryptor().update(data)


def aes_cfb_decrypt(key: bytes, data: bytes, iv: bytes = b"0123456789abcdef") -> bytes:
    """Decrypt XML using AES-128-CFB, matching the public reference client."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
        try:
            from cryptography.hazmat.decrepit.ciphers import modes
        except ImportError:  # pragma: no cover - older cryptography
            from cryptography.hazmat.primitives.ciphers import modes
    except ImportError as err:  # pragma: no cover - depends on HA runtime
        raise RuntimeError("cryptography is required for AES Baichuan sessions") from err
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("AES-CFB requires 16-byte key and IV")
    return Cipher(algorithms.AES(key), modes.CFB(iv)).decryptor().update(data)


def modern_login_digests(username: str, password: str, nonce: str) -> tuple[str, str]:
    """Build the username/password digests for the modern login message."""
    return md5_hex(username + nonce), md5_hex(password + nonce)


@dataclass(frozen=True, slots=True)
class BaichuanHeader:
    """Parsed Baichuan message header."""

    message_id: int
    body_length: int
    channel_id: int
    stream_type: int
    message_number: int
    response_code: int
    message_class: int
    payload_offset: int | None

    @property
    def header_length(self) -> int:
        return 24 if self.payload_offset is not None else 20


def parse_baichuan_header(data: bytes) -> BaichuanHeader:
    """Parse one 20- or 24-byte Baichuan header."""
    if len(data) < 20:
        raise ValueError("Baichuan header is truncated")
    magic = struct.unpack_from("<I", data)[0]
    if magic not in (BC_MAGIC, 0x0FEDCBA0):
        raise ValueError("unexpected Baichuan magic")
    message_id, body_length = struct.unpack_from("<II", data, 4)
    channel_id, stream_type, message_number, response_code, message_class = struct.unpack_from(
        "<BBHHH", data, 12
    )
    payload_offset = (
        struct.unpack_from("<I", data, 20)[0]
        if message_class in (BC_CLASS_MODERN_24, 0)
        else None
    )
    return BaichuanHeader(
        message_id,
        body_length,
        channel_id,
        stream_type,
        message_number,
        response_code,
        message_class,
        payload_offset,
    )


def split_baichuan_message(data: bytes) -> tuple[BaichuanHeader, bytes, bytes]:
    """Split a complete message into header, extension, and payload bytes."""
    header = parse_baichuan_header(data)
    end = header.header_length + header.body_length
    if len(data) != end:
        raise ValueError(f"Baichuan message length mismatch: got {len(data)}, expected {end}")
    body = data[header.header_length:end]
    offset = header.payload_offset or 0
    if offset > len(body):
        raise ValueError("Baichuan payload offset exceeds body length")
    return header, body[:offset], body[offset:]


def serialize_login_upgrade(*, message_number: int, encryption_code: int = 0xDC12) -> bytes:
    """Build the legacy header-only login negotiation message."""
    return struct.pack(
        "<III BBHHH",
        BC_MAGIC,
        1,
        0,
        0,
        0,
        message_number,
        encryption_code,
        0x6514,
    )


class BaichuanTcpClient:
    """Minimal framed TCP transport for a single Baichuan session."""

    def __init__(self, host: str, *, port: int = NATIVE_TALK_PORT) -> None:
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._message_number = 0

    async def connect(self) -> None:
        """Open the camera's proprietary media/control TCP service."""
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def close(self) -> None:
        """Close the session transport."""
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        self.reader = None
        self.writer = None

    def next_message_number(self) -> int:
        """Return the next 16-bit Baichuan message number."""
        self._message_number = (self._message_number + 1) & 0xFFFF
        return self._message_number

    async def send(self, message: bytes) -> None:
        """Write one complete Baichuan message and flush it."""
        if self.writer is None:
            raise RuntimeError("Baichuan TCP client is not connected")
        self.writer.write(message)
        await self.writer.drain()

    async def receive(self) -> tuple[BaichuanHeader, bytes, bytes]:
        """Read one complete framed Baichuan message."""
        if self.reader is None:
            raise RuntimeError("Baichuan TCP client is not connected")
        prefix = await self.reader.readexactly(20)
        header = parse_baichuan_header(prefix)
        extra = await self.reader.readexactly(4) if header.header_length == 24 else b""
        body = await self.reader.readexactly(header.body_length)
        return split_baichuan_message(prefix + extra + body)


class NativeTalkSession:
    """Authenticated native-talk session for a local Reolink camera."""

    def __init__(self, host: str, username: str, password: str, *, channel: int = 0) -> None:
        self.client = BaichuanTcpClient(host)
        self.username = username
        self.password = password
        self.channel = channel
        self._aes_key: bytes | None = None
        self._logged_in = False

    @staticmethod
    def _xml_bytes(raw: bytes, *, key: bytes | None = None) -> bytes:
        candidates = [raw]
        if key is not None:
            candidates.insert(0, aes_cfb_decrypt(key, raw))
        candidates.append(bc_encrypt(0, raw))
        for candidate in candidates:
            if b"<" in candidate and b">" in candidate:
                return candidate[candidate.index(b"<") :]
        raise ValueError("Baichuan payload is not recognizable XML")

    async def login(self) -> None:
        """Perform the local legacy/modern login sequence."""
        await self.client.connect()
        number = self.client.next_message_number()
        await self.client.send(serialize_login_upgrade(message_number=number))
        reply_header, _, reply_payload = await self.client.receive()
        encryption_xml = self._xml_bytes(reply_payload)
        nonce_match = re.search(rb"<nonce>([^<]+)</nonce>", encryption_xml)
        if nonce_match is None:
            raise ValueError("camera login reply did not contain a nonce")
        nonce = nonce_match.group(1).decode()
        username_digest, password_digest = modern_login_digests(
            self.username, self.password, nonce
        )
        login_xml = (
            '<body><LoginUser version="1.0">'
            f"<userName>{escape(username_digest)}</userName>"
            f"<password>{escape(password_digest)}</password>"
            "<userVer>1</userVer></LoginUser>"
            '<LoginNet version="1.0"><type>LAN</type><udpPort>0</udpPort></LoginNet>'
            "</body>"
        ).encode()
        extension = f"<Extension><channelId>{self.channel}</channelId></Extension>".encode()
        modern = serialize_talk_message(
            msg_id=1,
            msg_num=number,
            channel_id=self.channel,
            extension=bc_encrypt(self.channel, extension),
            payload=bc_encrypt(self.channel, login_xml),
        )
        await self.client.send(modern)
        result_header, _, result_payload = await self.client.receive()
        if result_header.response_code != 200:
            raise PermissionError(f"camera rejected native login: {result_header.response_code}")
        self._aes_key = make_aes_key(self.password, nonce)
        self._xml_bytes(result_payload, key=self._aes_key)
        self._logged_in = True

    async def close(self) -> None:
        await self.client.close()
        self._logged_in = False

    def _encrypt_xml(self, offset: int, payload: bytes) -> bytes:
        if self._aes_key is None:
            raise RuntimeError("native talk session is not authenticated")
        return aes_cfb_encrypt(self._aes_key, payload)

    async def configure_talk(self, config: TalkConfig) -> None:
        """Send the negotiated talk configuration."""
        if not self._logged_in:
            raise RuntimeError("native talk session is not authenticated")
        await self.client.send(
            serialize_talk_config_message(
                config,
                msg_num=self.client.next_message_number(),
                encrypt_xml=self._encrypt_xml,
            )
        )

    async def send_audio(self, adpcm_data: bytes) -> None:
        """Send one already-encoded ADPCM block."""
        if not self._logged_in:
            raise RuntimeError("native talk session is not authenticated")
        await self.client.send(
            serialize_talk_audio_message(
                adpcm_data,
                msg_num=self.client.next_message_number(),
                channel_id=self.channel,
            )
        )

    async def stop_talk(self) -> None:
        """Send the native talk reset command."""
        if not self._logged_in:
            return
        extension = f"<Extension><channelId>{self.channel}</channelId></Extension>".encode()
        await self.client.send(
            serialize_talk_message(
                msg_id=MSG_ID_TALK_STOP,
                msg_num=self.client.next_message_number(),
                channel_id=self.channel,
                extension=self._encrypt_xml(self.channel, extension),
            )
        )
