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
from xml.etree import ElementTree
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
MSG_ID_TALK_ABILITY = 10
BCMEDIA_ADPCM_MAGIC = 0x62773130
BCMEDIA_ADPCM_DATA_MAGIC = 0x0100
XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>'


def _xml_child_text(node: ElementTree.Element, name: str, default: str) -> str:
    """Read a child value while tolerating namespace-qualified XML."""
    for child in node.iter():
        if child is not node and child.tag.rsplit("}", 1)[-1] == name and child.text:
            return child.text
    return default

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


def encode_dvi4_block(samples: Iterable[int], *, byte_order: str = "<") -> bytes:
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


def encode_dvi4_pcm16le(pcm: bytes, *, byte_order: str = "<") -> bytes:
    """Encode signed 16-bit little-endian mono PCM as one DVI-4 block."""
    if len(pcm) % 2:
        raise ValueError("PCM16 data must contain complete samples")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return encode_dvi4_block(samples, byte_order=byte_order)


class Dvi4Encoder:
    """Stateful IMA/DVI-4 encoder used by the camera talk stream."""

    def __init__(self) -> None:
        self.predictor = 0
        self.index = 0

    def encode_pcm16le(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("PCM16 data must contain complete samples")
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        if len(samples) < 2 or len(samples) % 2:
            raise ValueError("DVI-4 blocks require an even number of samples >= 2")

        encoded = bytearray(struct.pack("<hBB", self.predictor, self.index, 0))
        for offset in range(0, len(samples), 2):
            first, self.predictor, self.index = _ima_encode_nibble(
                samples[offset], self.predictor, self.index
            )
            second, self.predictor, self.index = _ima_encode_nibble(
                samples[offset + 1], self.predictor, self.index
            )
            encoded.append((first << 4) | second)
        return bytes(encoded)


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
    version: str = "1.1"

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
        return XML_DECLARATION + f'<body>{values}</body>'.encode()


@dataclass(frozen=True, slots=True)
class TalkAbility:
    """Talk profile advertised by the camera."""

    version: str = "1.1"
    duplex: str = "FDX"
    audio_stream_mode: str = "followVideoStream"
    audio_type: str = "adpcm"
    sample_rate: int = SAMPLE_RATE
    sample_precision: int = 16
    length_per_encoder: int = SAMPLES_PER_FRAME
    sound_track: str = "mono"

    def to_config(self, channel_id: int) -> TalkConfig:
        return TalkConfig(
            channel_id=channel_id,
            version=self.version or "1.1",
            duplex=self.duplex,
            audio_stream_mode=self.audio_stream_mode,
            sample_rate=self.sample_rate,
            sample_precision=self.sample_precision,
            length_per_encoder=self.length_per_encoder,
            sound_track=self.sound_track,
        )


def serialize_adpcm_media(data: bytes) -> bytes:
    """Serialize one Baichuan ADPCM media block, including 8-byte padding."""
    if len(data) < 4:
        raise ValueError("ADPCM data must include its 4-byte predictor header")
    if (len(data) - 4) % 2:
        raise ValueError("ADPCM block payload must have an even byte count")
    return serialize_adpcm_media_with_sequence(data, sequence=0)


def serialize_adpcm_media_with_sequence(data: bytes, *, sequence: int) -> bytes:
    """Serialize one ADPCM block with the camera-required sequence field."""
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("ADPCM sequence must fit in uint16")
    payload_size = len(data) + 4
    media = struct.pack(
        "<IHHHH",
        BCMEDIA_ADPCM_MAGIC,
        payload_size,
        payload_size,
        BCMEDIA_ADPCM_DATA_MAGIC,
        sequence,
    ) + data
    return media + bytes((-payload_size) % 8)


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
    extension = XML_DECLARATION + (
        f'<Extension version="1.1"><channelId>{config.channel_id}</channelId></Extension>'.encode()
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
    adpcm_data: bytes,
    *,
    msg_num: int,
    channel_id: int = 0,
    sequence: int = 0,
    encrypt_xml: Callable[[int, bytes], bytes] | None = None,
) -> bytes:
    """Build one binary MSG_ID_TALK message from a DVI-4 ADPCM block."""
    extension = XML_DECLARATION + (
        f'<Extension version="1.1"><channelId>{channel_id}</channelId><binaryData>1</binaryData></Extension>'.encode()
    )
    if encrypt_xml is not None:
        extension = encrypt_xml(channel_id, extension)
    return serialize_talk_message(
        msg_id=MSG_ID_TALK,
        msg_num=msg_num,
        channel_id=channel_id,
        extension=extension,
        payload=serialize_adpcm_media_with_sequence(adpcm_data, sequence=sequence),
    )


def md5_hex(value: str) -> str:
    """Return Baichuan's uppercase 31-character modern MD5 representation."""
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest().upper()[:31]


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
        return 24 if self.message_class in (BC_CLASS_MODERN_24, 0) else 20


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
    payload_offset = None
    if message_class in (BC_CLASS_MODERN_24, 0) and len(data) >= 24:
        payload_offset = struct.unpack_from("<I", data, 20)[0]
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

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        channel: int = 0,
        trace: Callable[[str], None] | None = None,
        mix_frame_callback: Callable[[bytes], None] | None = None,
        max_encryption: int = 0xDC12,
    ) -> None:
        self.client = BaichuanTcpClient(host)
        self.username = username
        self.password = password
        self.channel = channel
        self._aes_key: bytes | None = None
        self._encryption_mode = 0
        self._logged_in = False
        self._audio_sequence = 0
        self._audio_encoder = Dvi4Encoder()
        self.trace = trace
        self.mix_frame_callback = mix_frame_callback
        self._response_queue: asyncio.Queue[tuple[BaichuanHeader, bytes, bytes]] = asyncio.Queue()
        self._mix_reader_task: asyncio.Task[None] | None = None
        self.max_encryption = max_encryption

    def _trace(self, message: str) -> None:
        if self.trace is not None:
            self.trace(message)

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
        await self.client.send(
            serialize_login_upgrade(
                message_number=number,
                encryption_code=self.max_encryption,
            )
        )
        reply_header, _, reply_payload = await self.client.receive()
        self._trace(
            f"login negotiation: id={reply_header.message_id} class=0x{reply_header.message_class:04x} "
            f"response={reply_header.response_code} body={reply_header.body_length} payload={len(reply_payload)}"
        )
        encryption_xml = self._xml_bytes(reply_payload)
        self._trace(f"login negotiation XML: {encryption_xml.decode(errors='replace')}")
        nonce_match = re.search(rb"<nonce>([^<]+)</nonce>", encryption_xml)
        if nonce_match is None:
            raise ValueError("camera login reply did not contain a nonce")
        nonce = nonce_match.group(1).decode()
        self._encryption_mode = reply_header.response_code & 0xFF
        if self._encryption_mode not in (0x00, 0x01, 0x02, 0x12):
            raise ValueError(f"camera selected unsupported encryption: 0x{self._encryption_mode:02x}")
        if self._encryption_mode in (0x02, 0x12):
            self._aes_key = make_aes_key(self.password, nonce)
        username_digest, password_digest = modern_login_digests(
            self.username, self.password, nonce
        )
        login_xml = XML_DECLARATION + (
            '<body><LoginUser version="1.1">'
            f"<userName>{escape(username_digest)}</userName>"
            f"<password>{escape(password_digest)}</password>"
            "<userVer>1</userVer></LoginUser>"
            '<LoginNet version="1.1"><type>LAN</type><udpPort>0</udpPort></LoginNet>'
            "</body>"
        ).encode()
        modern = serialize_talk_message(
            msg_id=1,
            msg_num=number,
            channel_id=self.channel,
            # The modern login itself is always BCEncrypt.  The negotiated
            # AES/FullAES mode becomes active only after its 200 response.
            extension=b"",
            payload=bc_encrypt(self.channel, login_xml),
        )
        self._trace(
            f"login request: id=1 class=0x{BC_CLASS_MODERN_24:04x} "
            f"extension=0 payload={len(login_xml)} encrypted=bc"
        )
        await self.client.send(modern)
        result_header, _, result_payload = await self.client.receive()
        self._trace(
            f"login result: id={result_header.message_id} class=0x{result_header.message_class:04x} "
            f"response={result_header.response_code} body={result_header.body_length} payload={len(result_payload)}"
        )
        if result_header.response_code != 200:
            raise PermissionError(f"camera rejected native login: {result_header.response_code}")
        self._xml_bytes(result_payload)
        self._logged_in = True

    async def close(self) -> None:
        if self._mix_reader_task is not None:
            self._mix_reader_task.cancel()
            await asyncio.gather(self._mix_reader_task, return_exceptions=True)
            self._mix_reader_task = None
        try:
            await self.client.close()
        except (ConnectionResetError, BrokenPipeError):
            # The camera may reset the socket after rejecting a talk packet.
            # Preserve the original protocol error instead of masking it while
            # cleaning up the probe/session.
            pass
        finally:
            self._logged_in = False

    def _encrypt_xml(self, offset: int, payload: bytes) -> bytes:
        if self._encryption_mode in (0x02, 0x12):
            if self._aes_key is None:
                raise RuntimeError("native talk AES key is not available")
            return aes_cfb_encrypt(self._aes_key, payload)
        if self._encryption_mode == 0x01:
            return bc_encrypt(offset, payload)
        return payload

    async def configure_talk(self, config: TalkConfig) -> None:
        """Send the negotiated talk configuration."""
        if not self._logged_in:
            raise RuntimeError("native talk session is not authenticated")
        message = serialize_talk_config_message(
            config,
            msg_num=self.client.next_message_number(),
            encrypt_xml=self._encrypt_xml,
        )
        await self.client.send(message)
        header, _, _ = await self.client.receive()
        if header.response_code == 422:
            await self.stop_talk()
            await self.client.send(
                serialize_talk_config_message(
                    config,
                    msg_num=self.client.next_message_number(),
                    encrypt_xml=self._encrypt_xml,
                )
            )
            header, _, _ = await self._receive_response()
        if header.response_code != 200:
            raise PermissionError(f"camera rejected talk configuration: {header.response_code}")
        if config.audio_stream_mode == "mixAudioStream":
            self._mix_reader_task = asyncio.create_task(self._read_mix_frames())

    async def _read_mix_frames(self) -> None:
        """Drain unsolicited mix frames and preserve control responses."""
        while True:
            header, extension, payload = await self.client.receive()
            if header.message_id == MSG_ID_TALK:
                if self.mix_frame_callback is not None:
                    self.mix_frame_callback(payload)
                continue
            await self._response_queue.put((header, extension, payload))

    async def _receive_response(self) -> tuple[BaichuanHeader, bytes, bytes]:
        if self._mix_reader_task is None:
            return await self.client.receive()
        return await self._response_queue.get()

    async def talk_ability(self, *, native_request: bool = False) -> TalkAbility:
        """Read and select the camera's advertised ADPCM talk profile."""
        if not self._logged_in:
            raise RuntimeError("native talk session is not authenticated")
        extension = b"" if native_request else XML_DECLARATION + (
            f'<Extension version="1.1"><channelId>{self.channel}</channelId></Extension>'.encode()
        )
        await self.client.send(
            serialize_talk_message(
                msg_id=MSG_ID_TALK_ABILITY,
                msg_num=self.client.next_message_number(),
                channel_id=self.channel,
                extension=self._encrypt_xml(self.channel, extension),
            )
        )
        header, _, payload = await self.client.receive()
        if header.response_code != 200:
            raise PermissionError(f"camera rejected talk ability request: {header.response_code}")
        xml = self._xml_bytes(payload, key=self._aes_key)
        root = ElementTree.fromstring(xml)
        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def children_named(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
            return [child for child in node.iter() if local_name(child.tag) == name]

        talk = next((node for node in root.iter() if local_name(node.tag) == "TalkAbility"), None)
        if talk is None:
            tags = ",".join(local_name(node.tag) for node in root.iter())
            snippet = xml[:240].decode(errors="replace")
            # This doorbell's firmware routes the generic XML command 10 to
            # VideoInput instead of exposing the native BCSDK talk ability.
            # The APK's BCSDK path uses command 2157 and supplies the same
            # profile below to its talk opener.  The profile has been verified
            # by the accepted config/audio path on this model.
            if "VideoInput" in tags and not native_request:
                self._trace(
                    "talk ability: camera returned VideoInput for generic XML "
                    "query; using verified native doorbell profile"
                )
                return TalkAbility()
            raise ValueError(
                f"camera talk ability response did not contain TalkAbility "
                f"(root={local_name(root.tag)}, tags={tags}, xml={snippet!r})"
            )
        duplexes = [node.text for node in children_named(talk, "duplex") if node.text]
        modes = [node.text for node in children_named(talk, "audioStreamMode") if node.text]
        configs = [node for node in children_named(talk, "audioConfig")]
        selected = next(
            (
                config
                for config in configs
                if next(
                    (node.text for node in children_named(config, "audioType") if node.text),
                    "",
                )
                == "adpcm"
            ),
            None,
        )
        if selected is None:
            raise ValueError("camera did not advertise an ADPCM talk profile")
        return TalkAbility(
            version=talk.attrib.get("version", "1.1"),
            duplex="fullDuplex" if "fullDuplex" in duplexes else (duplexes[0] if duplexes else "FDX"),
            audio_stream_mode=(
                "mixAudioStream" if "mixAudioStream" in modes
                else ("speaker" if "speaker" in modes else (modes[0] if modes else "followVideoStream"))
            ),
            audio_type="adpcm",
            sample_rate=int(_xml_child_text(selected, "sampleRate", str(SAMPLE_RATE))),
            sample_precision=int(_xml_child_text(selected, "samplePrecision", "16")),
            length_per_encoder=int(
                _xml_child_text(selected, "lengthPerEncoder", str(SAMPLES_PER_FRAME))
            ),
            sound_track=_xml_child_text(selected, "soundTrack", "mono"),
        )

    async def send_audio(self, adpcm_data: bytes) -> None:
        """Send one already-encoded ADPCM block."""
        if not self._logged_in:
            raise RuntimeError("native talk session is not authenticated")
        self._audio_sequence = (self._audio_sequence + 1) & 0xFFFF
        await self.client.send(
            serialize_talk_audio_message(
                adpcm_data,
                msg_num=self.client.next_message_number(),
                channel_id=self.channel,
                sequence=self._audio_sequence,
                encrypt_xml=self._encrypt_xml,
            )
        )

    async def send_pcm(self, pcm16le: bytes) -> None:
        """Encode and send one PCM talk frame using continuous DVI-4 state."""
        await self.send_audio(self._audio_encoder.encode_pcm16le(pcm16le))

    async def stop_talk(self) -> None:
        """Send the native talk reset command."""
        if not self._logged_in:
            return
        extension = XML_DECLARATION + (
            f'<Extension version="1.1"><channelId>{self.channel}</channelId></Extension>'.encode()
        )
        await self.client.send(
            serialize_talk_message(
                msg_id=MSG_ID_TALK_STOP,
                msg_num=self.client.next_message_number(),
                channel_id=self.channel,
                extension=self._encrypt_xml(self.channel, extension),
            )
        )
        header, _, _ = await self._receive_response()
        if header.response_code not in (200, 421, 422):
            raise PermissionError(f"camera rejected talk stop: {header.response_code}")
