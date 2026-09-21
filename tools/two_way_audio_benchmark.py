#!/usr/bin/env python3
"""Benchmark native and direct-camera RTSP two-way audio."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import queue
from pathlib import Path
import statistics
import struct
import sys
import threading
import time
import urllib.parse
import requests
from requests.auth import HTTPBasicAuth

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "videolink_doorbell"))
sys.path.insert(0, str(ROOT / "tools"))

CAMERA_HOST = "192.168.1.40"
CAMERA_PORT = 554
CAMERA_USERNAME = os.environ.get("VIDEOLINK_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("VIDEOLINK_PASSWORD", "")
CAMERA_CHANNEL = 0
CAMERA_STREAM = "main"
GO2RTC_URL = "http://ha.rsanzr.com:11984"
GO2RTC_STREAM = "videolink_doorbell_141484745042827_channel_0"
GO2RTC_USERNAME = os.environ.get("VIDEOLINK_GO2RTC_USERNAME", "admin")
GO2RTC_PASSWORD = os.environ.get("VIDEOLINK_GO2RTC_PASSWORD", "")
AUDIO_URL = "https://ha.rsanzr.com/local/tone_440hz_16khz.wav"
HA_URL = "https://ha.rsanzr.com"
HA_USERNAME = os.environ.get("VIDEOLINK_HA_USERNAME", "")
HA_PASSWORD = os.environ.get("VIDEOLINK_HA_PASSWORD", "")
HA_ENTITY_ID = "camera.porton"
TONE_SECONDS = 2.0
OBSERVE_SECONDS = 5.0
PTT_SECONDS = 5.0

GO2RTC_BASE_URL = GO2RTC_URL
GO2RTC_SESSION = requests.Session()
GO2RTC_SESSION.auth = HTTPBasicAuth(GO2RTC_USERNAME, GO2RTC_PASSWORD)


def get_stream() -> dict:
    response = GO2RTC_SESSION.get(
        f"{GO2RTC_BASE_URL}/api/streams", timeout=10
    )
    response.raise_for_status()
    streams = response.json()
    if GO2RTC_STREAM not in streams:
        raise RuntimeError(f"Stream not found: {GO2RTC_STREAM}")
    return streams[GO2RTC_STREAM]


def test_audio() -> None:
    response = requests.get(AUDIO_URL, timeout=10)
    response.raise_for_status()


def play() -> None:
    source = (
        f"ffmpeg:{AUDIO_URL}"
        "#audio=pcmu"
        "#input=file"
    )
    response = GO2RTC_SESSION.post(
        f"{GO2RTC_BASE_URL}/api/streams",
        params={"dst": GO2RTC_STREAM, "src": source},
        timeout=20,
    )
    response.raise_for_status()


def stop() -> None:
    response = GO2RTC_SESSION.post(
        f"{GO2RTC_BASE_URL}/api/streams",
        params={"dst": GO2RTC_STREAM, "src": ""},
        timeout=10,
    )
    response.raise_for_status()


def wait_for_audio_producer(timeout: float = 10.0) -> float:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stream = get_stream()
        for producer in stream.get("producers", []):
            if producer.get("url") == AUDIO_URL and producer.get("receivers"):
                return time.monotonic()
        time.sleep(0.05)
    raise RuntimeError("go2rtc audio producer did not become active")

from native_talk import NativeTalkSession  # noqa: E402
from api import VideolinkClient  # noqa: E402
from native_talk_rtsp_probe import (  # noqa: E402
    RtspAudioCapture,
    TONE_FREQUENCY,
    _tone,
)
from rtsp_backchannel import RtspBackchannel  # noqa: E402


class HomeAssistantNativeTalk:
    """The card's Home Assistant WebSocket bridge, for PTT testing."""

    def __init__(self, base_url: str, username: str, password: str, entity_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.entity_id = entity_id
        self.websocket = None
        self._command_id = 0

    def _login_token(self) -> str:
        if not self.username or not self.password:
            raise RuntimeError(
                "set VIDEOLINK_HA_USERNAME and VIDEOLINK_HA_PASSWORD for native PTT testing"
            )
        client_id = self.base_url + "/"
        response = requests.post(
            f"{self.base_url}/auth/login_flow",
            json={
                "handler": ["homeassistant", None],
                "client_id": client_id,
                "redirect_uri": client_id,
            },
            timeout=15,
        )
        response.raise_for_status()
        flow = response.json()
        response = requests.post(
            f"{self.base_url}/auth/login_flow/{flow['flow_id']}",
            json={
                "client_id": client_id,
                "username": self.username,
                "password": self.password,
            },
            timeout=15,
        )
        response.raise_for_status()
        code = response.json()["result"]
        response = requests.post(
            f"{self.base_url}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as err:
            raise RuntimeError("websockets is required for native PTT testing") from err
        token = await asyncio.to_thread(self._login_token)
        websocket_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.websocket = await websockets.connect(f"{websocket_url}/api/websocket")
        await self.websocket.recv()  # auth_required
        await self.websocket.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await self.websocket.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant WebSocket authentication failed: {auth}")

    async def command(self, action: str, pcm: bytes | None = None) -> None:
        if self.websocket is None:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        self._command_id += 1
        message = {
            "id": self._command_id,
            "type": "videolink_doorbell/native_talk",
            "action": action,
            "entity_id": self.entity_id,
        }
        if pcm is not None:
            message["pcm"] = base64.b64encode(pcm).decode()
        await self.websocket.send(json.dumps(message))
        while True:
            response = json.loads(await self.websocket.recv())
            if response.get("id") != self._command_id:
                continue
            if not response.get("success"):
                raise RuntimeError(response.get("error", response))
            return

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None


class MicrophonePcmCapture:
    """Capture 16 kHz mono PCM frames like the card's AudioContext path."""

    def __init__(self) -> None:
        self.pipeline = None
        self.loop = None
        self.loop_thread = None
        self.samples = queue.Queue(maxsize=20)
        self.buffer = bytearray()

    def start(self) -> None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import GLib, Gst
        except (ImportError, ValueError) as err:
            raise RuntimeError("GStreamer with PyGObject is required for native PTT testing") from err
        Gst.init(None)
        self._Gst = Gst
        self.pipeline = Gst.parse_launch(
            "autoaudiosrc ! audioconvert ! audioresample ! "
            "audio/x-raw,format=S16LE,rate=16000,channels=1 ! "
            "appsink name=microphone emit-signals=true sync=false max-buffers=20 drop=true"
        )
        sink = self.pipeline.get_by_name("microphone")
        if sink is None:
            raise RuntimeError("failed to create microphone appsink")

        def on_sample(appsink):
            sample = appsink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.ERROR
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if ok:
                try:
                    try:
                        self.samples.put_nowait(bytes(mapped.data))
                    except queue.Full:
                        pass
                finally:
                    buffer.unmap(mapped)
            return Gst.FlowReturn.OK

        sink.connect("new-sample", on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.loop = GLib.MainLoop()
        self.loop_thread = threading.Thread(
            target=self.loop.run,
            name="videolink-microphone-gst-loop",
            daemon=True,
        )
        self.loop_thread.start()

    async def frame(self, timeout: float = 1.0) -> bytes:
        while len(self.buffer) < 2048:
            try:
                self.buffer.extend(await asyncio.to_thread(self.samples.get, True, timeout))
            except queue.Empty as err:
                raise RuntimeError("microphone did not produce audio") from err
        frame = bytes(self.buffer[:2048])
        del self.buffer[:2048]
        return frame

    def close(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(self._Gst.State.NULL)
            if self.loop is not None:
                self.loop.quit()
            if self.loop_thread is not None:
                self.loop_thread.join(timeout=2)
            self.pipeline = None
            self.loop = None
            self.loop_thread = None


async def _send_native_tone(
    session: NativeTalkSession,
    seconds: float,
    sample_rate: int,
    block_size: int,
    on_first_sent=None,
) -> float:
    samples = _tone(seconds, sample_rate)
    if len(samples) % block_size:
        samples += [0] * (block_size - len(samples) % block_size)
    deadline = time.perf_counter()
    first_sent: float | None = None
    for offset in range(0, len(samples), block_size):
        block = struct.pack(f"<{block_size}h", *samples[offset : offset + block_size])
        await asyncio.sleep(max(0, deadline - time.perf_counter()))
        await session.send_pcm(block)
        if first_sent is None:
            first_sent = time.monotonic()
            if on_first_sent is not None:
                on_first_sent(first_sent)
        deadline += block_size / sample_rate
    return first_sent or time.monotonic()


async def _run_native_ptt(runs: int, seconds: float) -> None:
    """Exercise the complete card path: mic -> HA WebSocket -> camera."""
    microphone = MicrophonePcmCapture()
    bridge = HomeAssistantNativeTalk(HA_URL, HA_USERNAME, HA_PASSWORD, HA_ENTITY_ID)
    microphone.start()
    await bridge.connect()
    try:
        reports: list[tuple[int, float, float]] = []
        for run in range(1, runs + 1):
            await bridge.command("start")
            started = time.monotonic()
            frame_times: list[float] = []
            try:
                while time.monotonic() - started < seconds:
                    pcm = await microphone.frame()
                    sent = time.monotonic()
                    await bridge.command("audio", pcm)
                    frame_times.append(sent)
            finally:
                # Match the card's delayed native playback drain before stop.
                await asyncio.sleep(2.0)
                await bridge.command("stop")
            intervals = [right - left for left, right in zip(frame_times, frame_times[1:])]
            cadence = statistics.mean(intervals) * 1000 if intervals else 0.0
            duration = (frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0.0
            reports.append((len(frame_times), cadence, duration))
            print(
                f"native-ptt run {run}: frames={len(frame_times)} "
                f"cadence={cadence:.1f} ms send-duration={duration:.2f} s"
            )
        if reports:
            print(
                "native-ptt: "
                f"frames-mean={statistics.mean(item[0] for item in reports):.1f} "
                f"cadence-mean={statistics.mean(item[1] for item in reports):.1f} ms"
            )
    finally:
        await bridge.close()
        microphone.close()


def _post_go2rtc_tone(
    base_url: str, stream: str, audio_url: str,
    username: str | None, password: str | None,
) -> None:
    # Keep this sequence identical to test.py: one authenticated session,
    # stream existence check, public WAV check, then the stream API POST.
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    streams = session.get(f"{base_url.rstrip('/')}/api/streams", timeout=10)
    streams.raise_for_status()
    if stream not in streams.json():
        raise RuntimeError(f"stream not found: {stream}")
    audio = requests.get(audio_url, timeout=10, allow_redirects=True)
    audio.raise_for_status()
    if not audio.content:
        raise RuntimeError("audio file is empty")
    source = f"ffmpeg:{audio_url}#audio=pcmu"
    response = session.post(
        f"{base_url.rstrip('/')}/api/streams",
        params={"dst": stream, "src": source},
        timeout=20,
    )
    response.raise_for_status()


def _stop_go2rtc_tone(
    base_url: str, stream: str, username: str | None, password: str | None
) -> None:
    auth = HTTPBasicAuth(username, password) if username and password else None
    response = requests.post(
        f"{base_url.rstrip('/')}/api/streams",
        params={"dst": stream, "src": ""},
        auth=auth,
        timeout=20,
    )
    response.raise_for_status()


async def _send_go2rtc_tone() -> float:
    started = time.monotonic()
    await asyncio.to_thread(
        _post_go2rtc_tone,
        GO2RTC_URL, GO2RTC_STREAM, AUDIO_URL,
        GO2RTC_USERNAME, GO2RTC_PASSWORD,
    )
    return started


async def _send_copied_test_tone() -> float:
    def send() -> float:
        test_audio()
        get_stream()
        play()
        return wait_for_audio_producer()

    return await asyncio.to_thread(send)


async def _run_case(
    name: str,
    runs: int,
    capture_url: str,
    observe: float,
    send_tone,
    play: bool,
    before_run=None,
    after_run=None,
    listener_kind: str = "rtsp",
) -> list[float]:
    capture = RtspAudioCapture(capture_url, play=play, source_kind=listener_kind)
    stop = asyncio.Event()
    await capture.start()
    capture_task = asyncio.create_task(capture.run(stop))
    results: list[float] = []
    try:
        for run in range(1, runs + 1):
            if before_run is not None:
                await before_run()
            capture.tone_sent_at = None
            capture.tone_detected_at = None
            capture._tone_candidate_frames = 0
            sent_at = await send_tone(lambda timestamp: setattr(capture, "tone_sent_at", timestamp))
            await asyncio.sleep(observe)
            latency = (
                (capture.tone_detected_at - sent_at) * 1000
                if capture.tone_detected_at is not None
                else None
            )
            if latency is None:
                print(f"{name} run {run}: not detected")
            else:
                results.append(latency)
                print(f"{name} run {run}: {latency:.1f} ms")
            if after_run is not None:
                await after_run()
            await asyncio.sleep(0.25)
        return results
    finally:
        stop.set()
        await capture_task
        await capture.close()


async def _run_rtsp_capture_case(
    runs: int, capture_url: str, observe: float, play: bool, listener_kind: str
) -> None:
    """Verify direct camera RTSP audio without exercising a send path."""
    received = 0
    for run in range(1, runs + 1):
        capture = RtspAudioCapture(capture_url, play=play, source_kind=listener_kind)
        stop = asyncio.Event()
        await capture.start()
        task = asyncio.create_task(capture.run(stop))
        try:
            await asyncio.sleep(observe)
            if capture.first_audio_at is None:
                print(f"direct-rtsp run {run}: no audio received")
            else:
                received += 1
                print(
                    f"direct-rtsp run {run}: audio received, "
                    f"{capture.samples_seen / 16_000:.2f}s decoded"
                )
        finally:
            stop.set()
            await task
            await capture.close()
    print(f"direct-rtsp: n={received}/{runs} runs received audio")


async def _run_send_only_case(runs: int, observe: float, send_tone, cleanup) -> None:
    for run in range(1, runs + 1):
        await send_tone(lambda _timestamp: None)
        print(f"go2rtc run {run}: tone injection accepted; listener disabled")
        await asyncio.sleep(observe)
        await cleanup()


async def _flv_url() -> str:
    """Build an authenticated camera FLV preview URL for audio observation."""
    try:
        from aiohttp import ClientSession
    except ImportError as err:
        raise RuntimeError("aiohttp is required for FLV listening") from err
    async with ClientSession() as session:
        client = VideolinkClient(
            session,
            CAMERA_HOST,
            CAMERA_USERNAME,
            CAMERA_PASSWORD,
            port=443,
            verify_ssl=False,
        )
        return await client.flv_url(CAMERA_CHANNEL, CAMERA_STREAM)


async def _run_copied_test(runs: int) -> None:
    for run in range(1, runs + 1):
        test_audio()
        get_stream()
        try:
            play()
            await asyncio.sleep(3)
        finally:
            stop()
            await asyncio.sleep(1)
        print(f"go2rtc run {run}: test sequence completed successfully")


async def benchmark(args: argparse.Namespace) -> int:
    username = CAMERA_USERNAME
    password = CAMERA_PASSWORD
    user = urllib.parse.quote(username, safe="")
    secret = urllib.parse.quote(password, safe="")
    path = f"h264Preview_{CAMERA_CHANNEL + 1:02d}_{CAMERA_STREAM}"
    capture_url = f"rtsp://{user}:{secret}@{CAMERA_HOST}:{CAMERA_PORT}/{path}"
    listener_url = await _flv_url() if args.listener == "flv" else capture_url
    native_session: NativeTalkSession | None = None
    try:
        if args.native_ptt_runs:
            await _run_native_ptt(args.native_ptt_runs, args.ptt_seconds)

        if args.capture_runs:
            await _run_rtsp_capture_case(
                args.capture_runs, listener_url, OBSERVE_SECONDS, args.play,
                args.listener,
            )

        if args.native_runs:
            native_session = NativeTalkSession(
                CAMERA_HOST, username, password, channel=CAMERA_CHANNEL,
                trace=None, max_encryption=0xDC12,
            )
            config = None

            async def send_native(callback):
                nonlocal config
                if config is None:
                    await native_session.login()
                    ability = await native_session.talk_ability()
                    config = ability.to_config(CAMERA_CHANNEL)
                    await native_session.open_talk(config)
                    print(
                        f"Native profile: {config.sample_rate} Hz, "
                        f"{config.length_per_encoder} samples, mode={config.audio_stream_mode}"
                    )
                sent = await _send_native_tone(
                    native_session, TONE_SECONDS,
                    config.sample_rate, config.length_per_encoder,
                    on_first_sent=callback,
                )
                return sent

            native_results = await _run_case(
                "native", args.native_runs, listener_url, OBSERVE_SECONDS,
                send_native, args.play, listener_kind=args.listener,
            )
            await native_session.stop_talk()
            _print_summary("native", native_results)

        if args.direct_rtsp_runs:
            direct_sender: RtspBackchannel | None = None

            async def send_direct_rtsp(callback):
                nonlocal direct_sender

                def send() -> tuple[RtspBackchannel, float]:
                    sender = direct_sender
                    if sender is None:
                        sender = RtspBackchannel(capture_url, username, password)
                        sender.open()
                    samples = _tone(TONE_SECONDS, sender.sample_rate)
                    first_sent: float | None = None
                    for offset in range(0, len(samples), sender.samples_per_packet):
                        sent = sender.send_pcm16(
                            samples[offset : offset + sender.samples_per_packet],
                            sender.sample_rate,
                        )
                        if first_sent is None:
                            first_sent = sent
                            # Arm RTSP tone detection at the first packet,
                            # not after the complete two-second tone ends.
                            callback(sent)
                        time.sleep(sender.samples_per_packet / sender.sample_rate)
                    return sender, first_sent or time.monotonic()

                direct_sender, sent = await asyncio.to_thread(send)
                return sent

            async def close_direct_rtsp():
                nonlocal direct_sender
                if direct_sender is not None:
                    sender, direct_sender = direct_sender, None
                    await asyncio.to_thread(sender.close)

            try:
                direct_results = await _run_case(
                    "direct-rtsp", args.direct_rtsp_runs, listener_url,
                    OBSERVE_SECONDS, send_direct_rtsp, args.play,
                    listener_kind=args.listener,
                )
            finally:
                await close_direct_rtsp()
            _print_summary("direct-rtsp", direct_results)

        if args.go2rtc_runs:
            async def send_go2rtc(callback):
                sent = await _send_copied_test_tone()
                callback(sent)
                return sent

            async def reset_go2rtc():
                await asyncio.to_thread(stop)

            if args.no_listener:
                await _run_copied_test(args.go2rtc_runs)
            else:
                go2rtc_results = await _run_case(
                    "go2rtc", args.go2rtc_runs,
                    listener_url,
                    OBSERVE_SECONDS, send_go2rtc, args.play,
                    listener_kind=args.listener,
                    after_run=reset_go2rtc,
                )
                _print_summary("go2rtc", go2rtc_results)

        return 0
    finally:
        if native_session is not None:
            await native_session.close()


def _print_summary(name: str, values: list[float]) -> None:
    if not values:
        print(f"{name}: no successful detections")
        return
    ordered = sorted(values)
    report = (
        f"{name}: n={len(values)} mean={statistics.mean(values):.1f} ms "
        f"median={statistics.median(values):.1f} ms "
        f"min={ordered[0]:.1f} ms max={ordered[-1]:.1f} ms"
    )
    if len(values) > 1:
        report += f" cold={values[0]:.1f} ms warm-mean={statistics.mean(values[1:]):.1f} ms"
    print(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-runs", type=int, default=0)
    parser.add_argument(
        "--native-ptt-runs", type=int, default=0,
        help="emulate card PTT through Home Assistant WebSocket and local microphone",
    )
    parser.add_argument(
        "--ptt-seconds", type=float, default=PTT_SECONDS,
        help="microphone capture duration per native PTT run",
    )
    parser.add_argument(
        "--capture-runs", type=int, default=0,
        help="direct RTSP capture-only runs; validates camera audio reception",
    )
    parser.add_argument(
        "--direct-rtsp-runs", type=int, default=0,
        help="send directly via camera RTSP backchannel and listen via RTSP",
    )
    parser.add_argument("--rtsp-runs", dest="direct_rtsp_runs", type=int, default=0)
    parser.add_argument("--play", action="store_true")
    parser.add_argument(
        "--listener", choices=("rtsp", "flv"), default="rtsp",
        help="audio observation source (default: rtsp)",
    )
    parser.add_argument("--go2rtc-runs", type=int, default=0)
    parser.add_argument(
        "--no-listener", action="store_true",
        help="send go2rtc audio without opening an RTSP listener",
    )
    args = parser.parse_args()
    if any(value < 0 for value in (
        args.native_runs, args.native_ptt_runs, args.capture_runs,
        args.direct_rtsp_runs,
        args.go2rtc_runs,
    )):
        parser.error("run counts cannot be negative")
    try:
        return asyncio.run(benchmark(args))
    except Exception as err:
        print(f"benchmark failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
