#!/usr/bin/env python3
"""Exercise native Baichuan login/configuration without Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import math
import os
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "videolink_doorbell"))

from native_talk import NativeTalkSession  # noqa: E402


async def probe(args: argparse.Namespace) -> int:
    username = args.username or os.environ.get("VIDEOLINK_USERNAME") or input("Username: ")
    password = args.password or os.environ.get("VIDEOLINK_PASSWORD") or getpass.getpass("Password: ")
    session = NativeTalkSession(
        args.host,
        username,
        password,
        channel=args.channel,
        trace=print,
        max_encryption=args.max_encryption,
    )
    try:
        print(f"Connecting to {args.host}:9000, channel {args.channel}")
        await session.login()
        print("Native login: OK")
        ability = await session.talk_ability()
        config = ability.to_config(args.channel)
        print(
            "Talk ability: "
            f"{ability.audio_type} {ability.sample_rate} Hz, "
            f"{ability.length_per_encoder} samples, "
            f"duplex={ability.duplex}, mode={ability.audio_stream_mode}"
        )
        await session.configure_talk(config)
        print("Talk configuration: acknowledged")
        if args.wav or args.tone_seconds:
            samples = _read_audio(args.wav, config.sample_rate) if args.wav else _tone(
                args.tone_seconds, config.sample_rate
            )
            block_size = config.length_per_encoder
            if len(samples) % block_size:
                samples += [0] * (block_size - (len(samples) % block_size))
            for offset in range(0, len(samples), block_size):
                block = struct.pack(f"<{block_size}h", *samples[offset : offset + block_size])
                await session.send_pcm(block)
                await asyncio.sleep(block_size / config.sample_rate)
            print(f"Audio sent: {len(samples)} samples")
        return 0
    except Exception as err:
        print(f"Native probe failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    finally:
        await session.close()


def _read_audio(path: str, sample_rate: int) -> list[int]:
    with wave.open(path, "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("WAV must be mono 16-bit PCM")
        frames = source.readframes(source.getnframes())
        values = list(struct.unpack(f"<{len(frames) // 2}h", frames))
        if source.getframerate() == sample_rate:
            return values
        ratio = source.getframerate() / sample_rate
        count = int(len(values) / ratio)
        return [values[min(int(index * ratio), len(values) - 1)] for index in range(count)]


def _tone(seconds: float, sample_rate: int) -> list[int]:
    return [
        int(10_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(max(0, int(seconds * sample_rate)))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VIDEOLINK_HOST", "192.168.1.40"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument(
        "--max-encryption",
        type=lambda value: int(value, 0),
        default=0xDC12,
        help="legacy negotiation code, e.g. 0xdc12 (AES) or 0xdc01 (BCEncrypt)",
    )
    parser.add_argument("--configure", action="store_true", help="send TalkConfig after login")
    parser.add_argument("--wav", help="mono 16-bit PCM WAV to play through the camera")
    parser.add_argument("--tone-seconds", type=float, help="generate a 440 Hz test tone")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
