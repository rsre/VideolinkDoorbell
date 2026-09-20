#!/usr/bin/env python3
"""Measure native-talk send to RTSP-audio observation latency locally."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import threading
import time
import wave
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "videolink_doorbell"))

from native_talk import NativeTalkSession  # noqa: E402


SAMPLE_RATE = 16_000
TONE_FREQUENCY = 440.0
DETECT_FRAME_SAMPLES = 1024


class _GstAudioPlayer:
    """Play the camera RTSP audio using test.py's GStreamer pipeline."""

    def __init__(self, url: str) -> None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import GLib, Gst
        except (ImportError, ValueError) as err:
            raise RuntimeError(
                "GStreamer with PyGObject is required for --play"
            ) from err
        self.Gst = Gst
        self.GLib = GLib
        Gst.init(None)
        self.pipeline = Gst.Pipeline.new("rtsp-audio-player")
        self.source = Gst.ElementFactory.make("rtspsrc", "camera-player")
        if self.source is None:
            raise RuntimeError("failed to create GStreamer RTSP source")
        self.source.set_property("location", url)
        self.source.set_property("latency", 100)
        self.source.set_property("protocols", 4)  # TCP
        self.pipeline.add(self.source)
        self.source.connect("pad-added", self._on_pad_added)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.loop = GLib.MainLoop()
        self.loop_thread = threading.Thread(
            target=self.loop.run,
            name="videolink-gst-loop",
            daemon=True,
        )
        self.loop_thread.start()

    def _on_pad_added(self, _source, pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0)
        if structure.get_string("media") != "audio":
            return
        if structure.get_string("encoding-name") != "MPEG4-GENERIC":
            return
        elements = [
            self.Gst.ElementFactory.make("rtpmp4gdepay", None),
            self.Gst.ElementFactory.make("avdec_aac", None),
            self.Gst.ElementFactory.make("audioconvert", None),
            self.Gst.ElementFactory.make("audioresample", None),
            self.Gst.ElementFactory.make("autoaudiosink", None),
        ]
        if any(element is None for element in elements):
            return
        for element in elements:
            self.pipeline.add(element)
            element.sync_state_with_parent()
        for left, right in zip(elements, elements[1:]):
            if not left.link(right):
                return
        pad.link(elements[0].get_static_pad("sink"))

    def close(self) -> None:
        self.pipeline.set_state(self.Gst.State.NULL)
        self.loop.quit()
        self.loop_thread.join(timeout=2)


class RtspAudioCapture:
    """Decode one RTSP audio stream to local PCM16."""

    def __init__(self, url: str, *, record: str | None = None, play: bool = False) -> None:
        self.url = url
        self.record = record
        self.play = play
        self.process: asyncio.subprocess.Process | None = None
        self.player: _GstAudioPlayer | None = None
        self.writer: wave.Wave_write | None = None
        self.samples_seen = 0
        self.first_audio_at: float | None = None
        self.tone_sent_at: float | None = None
        self.tone_detected_at: float | None = None
        self._tone_candidate_frames = 0
        self._buffer = bytearray()

    async def start(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required; install it and retry")
        if self.record:
            self.writer = wave.open(self.record, "wb")
            self.writer.setnchannels(1)
            self.writer.setsampwidth(2)
            self.writer.setframerate(SAMPLE_RATE)
        if self.play:
            self.player = _GstAudioPlayer(self.url)
        self.process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-rtsp_transport", "tcp", "-i", self.url,
            "-vn", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def run(self, stop: asyncio.Event) -> None:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("RTSP capture is not started")
        while not stop.is_set():
            try:
                chunk = await asyncio.wait_for(self.process.stdout.read(4096), 0.5)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            self._buffer.extend(chunk)
            if self.writer:
                self.writer.writeframes(chunk)
            while len(self._buffer) >= DETECT_FRAME_SAMPLES * 2:
                frame = bytes(self._buffer[: DETECT_FRAME_SAMPLES * 2])
                del self._buffer[: DETECT_FRAME_SAMPLES * 2]
                now = time.monotonic()
                if self.first_audio_at is None:
                    self.first_audio_at = now
                    print("RTSP audio received")
                self.samples_seen += DETECT_FRAME_SAMPLES
                if self.tone_detected_at is None and self.tone_sent_at is not None:
                    if _detect_tone(frame):
                        self._tone_candidate_frames += 1
                    else:
                        self._tone_candidate_frames = 0
                    if self._tone_candidate_frames >= 2:
                        self.tone_detected_at = now
                        print(f"440 Hz tone detected at RTSP sample {self.samples_seen}")

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.player is not None:
            self.player.close()
        if self.writer:
            self.writer.close()


def _detect_tone(pcm: bytes) -> bool:
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    energy = sum(sample * sample for sample in samples)
    if energy < 1024 * 1024:
        return False
    omega = 2 * math.pi * TONE_FREQUENCY / SAMPLE_RATE
    sine = sum(sample * math.sin(omega * index) for index, sample in enumerate(samples))
    cosine = sum(sample * math.cos(omega * index) for index, sample in enumerate(samples))
    # For a full-scale sine wave, the two quadrature sums contain N²A²/4
    # power while the sample energy is NA²/2. Normalize by N * energy so a
    # clean tone scores close to 1 rather than 1/N.
    normalized_power = 4 * (sine * sine + cosine * cosine) / (len(samples) * energy)
    # The RTSP observation is normally the camera microphone hearing the
    # speaker, not a clean digital loopback.  Speaker/microphone distance,
    # AGC, echo cancellation, and AAC encoding reduce the correlation score
    # substantially, so use a tolerant threshold while retaining the energy
    # gate above to reject silence.
    return normalized_power > 0.02


def _tone(seconds: float, sample_rate: int) -> list[int]:
    return [
        int(10_000 * math.sin(2 * math.pi * TONE_FREQUENCY * index / sample_rate))
        for index in range(max(0, int(seconds * sample_rate)))
    ]


async def _send_tone(
    session: NativeTalkSession,
    seconds: float,
    sample_rate: int,
    block_size: int,
    on_first_sent=None,
) -> float:
    samples = _tone(seconds, sample_rate)
    if len(samples) % block_size:
        samples += [0] * (block_size - len(samples) % block_size)
    first_sent_at: float | None = None
    deadline = time.perf_counter()
    for offset in range(0, len(samples), block_size):
        block = struct.pack(f"<{block_size}h", *samples[offset : offset + block_size])
        await asyncio.sleep(max(0, deadline - time.perf_counter()))
        await session.send_pcm(block)
        if first_sent_at is None:
            first_sent_at = time.monotonic()
            if on_first_sent is not None:
                on_first_sent(first_sent_at)
        deadline += block_size / sample_rate
    return first_sent_at or time.monotonic()


async def probe(args: argparse.Namespace) -> int:
    username = args.username or os.environ.get("VIDEOLINK_USERNAME") or input("Username: ")
    password = args.password or os.environ.get("VIDEOLINK_PASSWORD") or getpass.getpass("Password: ")
    rtsp_user = quote(username, safe="")
    rtsp_password = quote(password, safe="")
    path = f"h264Preview_{args.channel + 1:02d}_{args.stream}"
    url = f"rtsp://{rtsp_user}:{rtsp_password}@{args.host}:{args.rtsp_port}/{path}"
    capture = RtspAudioCapture(url, record=args.record, play=args.play)
    stop = asyncio.Event()
    session: NativeTalkSession | None = None
    try:
        print(f"Opening RTSP audio: {url.rsplit('@', 1)[-1]}")
        await capture.start()
        capture_task = asyncio.create_task(capture.run(stop))
        if args.tone_seconds > 0:
            session = NativeTalkSession(
                args.host, username, password, channel=args.channel,
                trace=print, max_encryption=args.max_encryption,
            )
            await session.login()
            ability = await session.talk_ability()
            config = ability.to_config(args.channel)
            print(
                f"Talk ability: {config.sample_rate} Hz, "
                f"{config.length_per_encoder} samples, mode={config.audio_stream_mode}"
            )
            await session.open_talk(config)
            sent_at = await _send_tone(
                session,
                args.tone_seconds,
                config.sample_rate,
                config.length_per_encoder,
                on_first_sent=lambda timestamp: setattr(capture, "tone_sent_at", timestamp),
            )
            print(f"First native tone frame sent; observing RTSP for {args.observe:.1f}s")
            await asyncio.sleep(args.observe)
            if capture.tone_detected_at is not None:
                print(f"Estimated send-to-RTSP lag: {(capture.tone_detected_at - sent_at) * 1000:.1f} ms")
            else:
                print("440 Hz tone was not detected in the RTSP audio")
                print(
                    "Note: RTSP normally carries the camera microphone, not a "
                    "digital return of the speaker. Automatic send-to-speaker "
                    "latency requires acoustic pickup of the tone or another "
                    "audio observation point."
                )
        else:
            await asyncio.sleep(args.observe)
        stop.set()
        await capture_task
        return 0
    except Exception as err:
        print(f"RTSP probe failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    finally:
        stop.set()
        if session is not None:
            await session.close()
        await capture.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VIDEOLINK_HOST", "192.168.1.40"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--stream", choices=("main", "sub"), default="main")
    parser.add_argument("--rtsp-port", type=int, default=554)
    parser.add_argument("--record", help="write decoded mono 16 kHz PCM to a WAV file")
    parser.add_argument("--play", action="store_true", help="play the decoded RTSP audio with ffplay")
    parser.add_argument("--tone-seconds", type=float, default=0.0, help="send a 440 Hz native tone")
    parser.add_argument("--observe", type=float, default=5.0, help="seconds to observe after sending/capturing")
    parser.add_argument(
        "--max-encryption", type=lambda value: int(value, 0), default=0xDC12,
        help="legacy negotiation code, e.g. 0xdc12 (AES) or 0xdc01 (BCEncrypt)",
    )
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
