"""Historical app frame envelope retained for parser research tests only."""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

SAMPLES_PER_FRAME = 1024
OBSERVED_HEADER = bytes.fromhex(
    "f0debc0aca00000093020000000000000000146483000000"
)
OBSERVED_HEADER_SIZE = len(OBSERVED_HEADER)


@dataclass(frozen=True, slots=True)
class NativeTalkFrame:
    """A captured app media frame, separate from the current wire serializer."""

    payload: bytes

    def encode(self) -> bytes:
        if not self.payload:
            raise ValueError("native talk payload must not be empty")
        header = bytearray(OBSERVED_HEADER)
        struct.pack_into("<I", header, 8, len(self.payload))
        return bytes(header) + self.payload


def split_native_talk_frame(data: bytes) -> NativeTalkFrame:
    """Validate and split one complete captured media frame."""
    if len(data) < OBSERVED_HEADER_SIZE:
        raise ValueError("native talk frame is shorter than its header")
    if data[:8] != OBSERVED_HEADER[:8]:
        raise ValueError("unexpected native talk frame marker")
    payload_length = struct.unpack_from("<I", data, 8)[0]
    expected = OBSERVED_HEADER_SIZE + payload_length
    if len(data) != expected:
        raise ValueError(
            f"native talk frame length mismatch: got {len(data)}, expected {expected}"
        )
    return NativeTalkFrame(data[OBSERVED_HEADER_SIZE:])


class NativeTalkPacketizer:
    """Model the abandoned 64 ms packetizer for recorded-frame comparisons."""

    def __init__(self, encode_frame: Callable[[bytes], bytes]) -> None:
        self._encode_frame = encode_frame

    def packetize(self, pcm_frame: bytes) -> bytes:
        if len(pcm_frame) != SAMPLES_PER_FRAME * 2:
            raise ValueError("expected 1024 signed 16-bit mono samples")
        payload = self._encode_frame(pcm_frame)
        if not isinstance(payload, bytes):
            raise TypeError("native talk encoder must return bytes")
        return NativeTalkFrame(payload).encode()
