"""Bounded, entry-owned diagnostic capture storage."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .videolink_client.native_talk import BaichuanHeader

RAW_CAPTURE_MAX_BYTES = 16 * 1024 * 1024


@dataclass(slots=True)
class CapturedFrame:
    """One opted-in raw receive record; never persisted by the integration."""

    received_ns: int
    header: BaichuanHeader
    extension: bytes
    payload: bytes
    decrypted_prefix: bytes | None

    def as_dict(self) -> dict:
        header = self.header
        return {
            "received_at_utc": datetime.fromtimestamp(self.received_ns / 1_000_000_000, timezone.utc).isoformat(),
            "message_id": header.message_id,
            "response_code": header.response_code,
            "message_class": header.message_class,
            "channel_id": header.channel_id,
            "stream_type": header.stream_type,
            "message_number": header.message_number,
            "body_length": header.body_length,
            "payload_offset": header.payload_offset,
            "extension_b64": base64.b64encode(self.extension).decode(),
            "payload_b64": base64.b64encode(self.payload).decode(),
            **({"decrypted_payload_prefix_b64": base64.b64encode(self.decrypted_prefix).decode()}
               if self.decrypted_prefix is not None else {}),
        }


@dataclass(slots=True)
class _Capture:
    frames: list[CapturedFrame] = field(default_factory=list)
    size: int = 0
    dropped: int = 0


class NativeCaptureStore:
    """Own one active session's buffers behind an explicit capture interface."""

    def __init__(self, *, max_bytes: int = RAW_CAPTURE_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._captures: dict[str, _Capture] = {}

    def start(self, owner: str) -> None:
        self.clear()
        self._captures[owner] = _Capture()

    def record(self, owner: str, header: BaichuanHeader, extension: bytes, payload: bytes,
               decrypted_prefix: bytes | None = None) -> None:
        capture = self._captures.get(owner)
        if capture is None:
            return
        size = len(extension) + len(payload) + 64 + len(decrypted_prefix or b"")
        if capture.size + size > self._max_bytes:
            capture.dropped += 1
            return
        capture.frames.append(CapturedFrame(time.time_ns(), header, extension, payload, decrypted_prefix))
        capture.size += size

    def chunk(self, owner: str, cursor: int) -> dict:
        if cursor < 0:
            raise ValueError("Raw capture cursor must be non-negative")
        capture = self._captures.get(owner)
        if capture is None:
            raise ValueError("Raw capture was not enabled for this session")
        frames = capture.frames[cursor:cursor + 32]
        return {
            "frames": [frame.as_dict() for frame in frames],
            "next_cursor": cursor + len(frames),
            "done": cursor + len(frames) >= len(capture.frames),
            "dropped": capture.dropped,
        }

    def discard(self, owner: str) -> None:
        self._captures.pop(owner, None)

    def retain(self, owner: str) -> None:
        for other in tuple(self._captures):
            if other != owner:
                self.discard(other)

    def clear(self) -> None:
        self._captures.clear()
