#!/usr/bin/env python3
"""Record repeatable HA native-tone to doorbell-microphone observations."""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit
import wave

from aiohttp import ClientSession, ClientTimeout, TCPConnector, ThreadedResolver

from native_talk_rtsp_probe import DETECT_FRAME_SAMPLES, SAMPLE_RATE, RtspAudioCapture


TONE_SECONDS = 2.0  # The integration's native tone command sends a two-second tone.


class HomeAssistantNativeTone:
    """Use the same native WebSocket commands as the dashboard card."""

    def __init__(
        self, base_url: str, access_token: str | None, entity_id: str,
        *, username: str | None = None, password: str | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("--ha-url must be an http(s) Home Assistant URL")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        self.base_url = base_url.rstrip("/")
        self.url = urlunsplit((scheme, parsed.netloc, parsed.path.rstrip("/") + "/api/websocket", "", ""))
        self.access_token = access_token
        self.username = username
        self.password = password
        self.refresh_token: str | None = None
        self.entity_id = entity_id
        self.socket = None
        self.command_id = 0
        self.session_token: str | None = None
        self._legacy_session_started = False
        self.subscription_id: int | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._reader_task: asyncio.Task | None = None
        self.mix_events = 0
        self.mix_pcm_bytes = 0
        self.mix_pcm_squared_sum = 0
        self.mix_pcm_samples = 0
        self.mix_pcm_non_silent_frames = 0
        self.mix_pcm_clipped_samples = 0
        self.capture_mix_pcm = False
        self.mix_pcm_frames: list[bytes] = []
        self.raw_dump_count = 0
        self.raw_dump_dropped = 0
        self._raw_dump_file = None

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as err:
            raise RuntimeError("Install websockets to run this benchmark") from err
        if not self.access_token:
            try:
                self.access_token, self.refresh_token = await self._login_with_password()
            finally:
                self.password = None
        self.socket = await websockets.connect(self.url, open_timeout=10)
        greeting = json.loads(await asyncio.wait_for(self.socket.recv(), 10))
        if greeting.get("type") != "auth_required":
            raise RuntimeError("Home Assistant did not request WebSocket authentication")
        await self.socket.send(json.dumps({"type": "auth", "access_token": self.access_token}))
        response = json.loads(await asyncio.wait_for(self.socket.recv(), 10))
        if response.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the access token")

    async def _login_with_password(self) -> tuple[str, str]:
        """Exchange local-account credentials through Home Assistant's auth flow."""
        if not self.username or not self.password:
            raise ValueError("Home Assistant username and password are required")
        client_id = self.base_url + "/"
        timeout = ClientTimeout(total=15)
        # The system resolver also supports local/mDNS Home Assistant hostnames.
        # It avoids optional aiodns/pycares version mismatches during login.
        async with ClientSession(
            timeout=timeout, connector=TCPConnector(resolver=ThreadedResolver())
        ) as session:
            async with session.post(
                f"{self.base_url}/auth/login_flow",
                json={
                    "handler": ["homeassistant", None],
                    "client_id": client_id,
                    "redirect_uri": client_id,
                },
            ) as response:
                response.raise_for_status()
                flow = await response.json()
            flow_id = flow.get("flow_id")
            if flow.get("type") != "form" or not isinstance(flow_id, str):
                raise RuntimeError("Home Assistant local-account login is unavailable")
            async with session.post(
                f"{self.base_url}/auth/login_flow/{flow_id}",
                json={
                    "client_id": client_id,
                    "username": self.username,
                    "password": self.password,
                },
            ) as response:
                response.raise_for_status()
                result = await response.json()
            code = result.get("result")
            if result.get("type") != "create_entry" or not isinstance(code, str):
                raise RuntimeError(
                    "Home Assistant login needs another step or rejected the credentials; "
                    "use VIDEOLINK_HA_TOKEN for MFA or another auth provider"
                )
            async with session.post(
                f"{self.base_url}/auth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                },
            ) as response:
                response.raise_for_status()
                tokens = await response.json()
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise RuntimeError("Home Assistant did not return usable login tokens")
        return access, refresh

    async def _revoke_refresh_token(self) -> None:
        if not self.refresh_token:
            return
        token, self.refresh_token = self.refresh_token, None
        try:
            async with ClientSession(
                timeout=ClientTimeout(total=10),
                connector=TCPConnector(resolver=ThreadedResolver()),
            ) as session:
                async with session.post(
                    f"{self.base_url}/auth/revoke", data={"token": token}
                ) as response:
                    response.raise_for_status()
        except Exception:
            print("Warning: could not revoke the temporary Home Assistant login", file=sys.stderr)

    async def _read_messages(self) -> None:
        """Drain native mix events continuously and route command replies."""
        try:
            while True:
                response = json.loads(await self.socket.recv())
                if response.get("type") == "event":
                    if response.get("id") == self.subscription_id:
                        event = response.get("event") or {}
                        if event.get("type") == "videolink_doorbell/native_talk_raw":
                            if self._raw_dump_file is not None:
                                self._raw_dump_file.write(json.dumps({
                                    "received_at_utc": datetime.now(timezone.utc).isoformat(),
                                    **event,
                                }) + "\n")
                                self.raw_dump_count += 1
                        else:
                            self.mix_events += 1
                            pcm = event.get("pcm")
                            if isinstance(pcm, str):
                                try:
                                    decoded = base64.b64decode(pcm, validate=True)
                                    self.mix_pcm_bytes += len(decoded)
                                    if len(decoded) == 2048:
                                        if self.capture_mix_pcm:
                                            self.mix_pcm_frames.append(decoded)
                                        samples = struct.unpack("<1024h", decoded)
                                        self.mix_pcm_samples += len(samples)
                                        self.mix_pcm_squared_sum += sum(sample * sample for sample in samples)
                                        self.mix_pcm_non_silent_frames += any(abs(sample) >= 64 for sample in samples)
                                        self.mix_pcm_clipped_samples += sum(abs(sample) >= 32760 for sample in samples)
                                except ValueError:
                                    pass
                    continue
                future = self._pending.pop(response.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(response)
        except Exception as err:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(err)
            self._pending.clear()

    async def command(
        self, action: str, *, timeout: float = 15, on_send=None,
        allow_missing_token: bool = False, dump_raw: bool = False,
        cursor: int = 0, decrypt_headers: bool = False,
    ) -> tuple[int, dict]:
        if self.socket is None:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        if action != "start" and not self.session_token and not allow_missing_token:
            raise RuntimeError("Native talk session is not started")
        if self._reader_task is not None and self._reader_task.done():
            raise RuntimeError("Home Assistant WebSocket reader has stopped")
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_messages())
        self.command_id += 1
        command_id = self.command_id
        if action == "subscribe":
            # Events can arrive before Home Assistant's subscribe acknowledgement.
            self.subscription_id = command_id
        response_future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = response_future
        message = {
            "id": command_id,
            "type": "videolink_doorbell/native_talk",
            "action": action,
            "entity_id": self.entity_id,
        }
        if action != "start":
            if self.session_token:
                message["token"] = self.session_token
        if action == "subscribe" and dump_raw:
            message["dump_raw"] = True
            if decrypt_headers:
                message["dump_decrypted_header"] = True
        if action == "raw_fetch":
            message["cursor"] = cursor
        try:
            if on_send is not None:
                on_send(time.monotonic())
            await self.socket.send(json.dumps(message))
            response = await asyncio.wait_for(response_future, timeout)
            if not response.get("success"):
                raise RuntimeError(f"Home Assistant {action} failed: {response.get('error')}")
            return command_id, response.get("result") or {}
        finally:
            self._pending.pop(command_id, None)

    async def start(self, *, raw_dump_path: Path | None = None,
                    decrypt_headers: bool = False) -> dict:
        _, result = await self.command("start")
        token = result.get("token")
        if not isinstance(token, str) or not token:
            self._legacy_session_started = True
            raise RuntimeError(
                "Home Assistant's installed Videolink integration returned a native-talk "
                "start response without a session token. It appears older than this "
                "benchmark; update custom_components/videolink_doorbell in Home Assistant "
                "and restart Home Assistant before retrying"
            )
        self.session_token = token
        if raw_dump_path is not None:
            # Raw frames may contain microphone audio or camera metadata.
            descriptor = os.open(raw_dump_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._raw_dump_file = os.fdopen(descriptor, "w", encoding="utf-8")
        self.subscription_id, _ = await self.command(
            "subscribe", dump_raw=raw_dump_path is not None,
            decrypt_headers=decrypt_headers,
        )
        return result

    async def mix_snapshot(self) -> dict:
        """Snapshot camera-side receive counters and CLI-delivered mix events."""
        try:
            _, backend = await self.command("diagnostics")
        except RuntimeError as err:
            raise RuntimeError(
                "Native mix diagnostics require the updated Videolink integration "
                "in Home Assistant; deploy it and restart Home Assistant"
            ) from err
        return {
            "backend": backend,
            "websocket_events": self.mix_events,
            "websocket_pcm_bytes": self.mix_pcm_bytes,
            "pcm_samples": self.mix_pcm_samples,
            "pcm_squared_sum": self.mix_pcm_squared_sum,
            "pcm_non_silent_frames": self.mix_pcm_non_silent_frames,
            "pcm_clipped_samples": self.mix_pcm_clipped_samples,
        }

    async def close(self) -> None:
        capture_error = None
        try:
            if self._raw_dump_file is not None and self.session_token:
                cursor = 0
                while True:
                    _, batch = await self.command("raw_fetch", timeout=30, cursor=cursor)
                    for frame in batch["frames"]:
                        self._raw_dump_file.write(json.dumps(frame) + "\n")
                        self.raw_dump_count += 1
                    self.raw_dump_dropped = batch["dropped"]
                    next_cursor = batch["next_cursor"]
                    if batch["done"] or next_cursor <= cursor:
                        break
                    cursor = next_cursor
        except Exception as err:
            capture_error = err
        finally:
            try:
                if self.socket is not None and (self.session_token or self._legacy_session_started):
                    await self.command("stop", timeout=8, allow_missing_token=self._legacy_session_started)
            except Exception:
                pass
            try:
                if self.socket is not None:
                    await self.socket.close()
            finally:
                if self._reader_task is not None:
                    self._reader_task.cancel()
                    await asyncio.gather(self._reader_task, return_exceptions=True)
                    self._reader_task = None
                if self._raw_dump_file is not None:
                    self._raw_dump_file.close()
                    self._raw_dump_file = None
                self.socket = None
                self.session_token = None
                self._legacy_session_started = False
                self.access_token = None
                await self._revoke_refresh_token()
        if capture_error is not None:
            raise RuntimeError(f"raw capture retrieval failed: {capture_error}") from capture_error


async def _wait_for_camera_audio(capture: RtspAudioCapture, task: asyncio.Task, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while capture.first_audio_at is None:
        if task.done():
            raise RuntimeError("RTSP audio ended before producing decoded samples")
        if time.monotonic() >= deadline:
            raise TimeoutError("No decoded RTSP audio received from the doorbell")
        await asyncio.sleep(0.05)


def summarize(runs: list[dict]) -> dict:
    latencies = [run["latency_ms"] for run in runs if run["latency_ms"] is not None]
    summary = {"detected": len(latencies), "runs": len(runs)}
    if latencies:
        summary.update({
            "mean_ms": round(statistics.mean(latencies), 1),
            "median_ms": round(statistics.median(latencies), 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "first_ms": runs[0]["latency_ms"],
            "subsequent_mean_ms": round(statistics.mean(
                run["latency_ms"] for run in runs[1:] if run["latency_ms"] is not None
            ), 1) if any(run["latency_ms"] is not None for run in runs[1:]) else None,
        })
    return summary


def mix_delta(before: dict, after: dict) -> dict:
    """Explain where camera mix data disappeared during one benchmark trial."""
    earlier, later = before["backend"], after["backend"]
    counters = (
        "raw_messages", "talk_candidates", "parsed_frames", "forwarded_frames",
        "rejected_frames", "aes_extension_xml_frames", "aes_payload_media_magic_frames",
    )
    result = {key: later.get(key, 0) - earlier.get(key, 0) for key in counters}
    result["headers"] = {
        key: count - earlier.get("headers", {}).get(key, 0)
        for key, count in later.get("headers", {}).items()
        if count > earlier.get("headers", {}).get(key, 0)
    }
    result["websocket_events"] = after["websocket_events"] - before["websocket_events"]
    result["websocket_pcm_bytes"] = after["websocket_pcm_bytes"] - before["websocket_pcm_bytes"]
    for key in ("pcm_samples", "pcm_squared_sum", "pcm_non_silent_frames", "pcm_clipped_samples"):
        result[key] = after.get(key, 0) - before.get(key, 0)
    result["pcm_rms"] = round(math.sqrt(result["pcm_squared_sum"] / result["pcm_samples"]), 1) if result["pcm_samples"] else None
    result["pcm_clipped_percent"] = round(100 * result["pcm_clipped_samples"] / result["pcm_samples"], 3) if result["pcm_samples"] else None
    result.pop("pcm_squared_sum")
    result.pop("pcm_samples")
    result.pop("pcm_clipped_samples")
    result["pcm_verified"] = later.get("pcm_verified", False)
    result["reader_error"] = later.get("reader_error")
    if result["raw_messages"] == 0:
        result["interpretation"] = "no Baichuan messages observed on the native talk socket during this run"
    elif result["talk_candidates"] == 0:
        result["interpretation"] = "camera sent messages, but no data-bearing talk ID 202 messages"
    elif result["parsed_frames"] == 0:
        result["interpretation"] = "data-bearing talk messages arrived, but no recognized mix container was decoded"
    elif result["forwarded_frames"] == 0:
        result["interpretation"] = "mix container parsed, but PCM validation or callback prevented forwarding"
    elif result["websocket_events"] == 0:
        result["interpretation"] = "PCM mix forwarded, but no mix events reached the CLI"
    else:
        result["interpretation"] = "native mix events reached the CLI"
    if result["reader_error"]:
        result["interpretation"] += f"; reader stopped: {result['reader_error']}"
    return result


def save_mix_wav(path: Path, frames: list[bytes]) -> None:
    """Save opted-in decoded talk mix privately for listening/quality checks."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file, wave.open(file, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"".join(frames))


def tone_continuity(flags: list[bool], tone_seconds: float) -> dict | None:
    """Estimate how continuously the recorded two-second tone survives."""
    first = next(
        (index for index in range(1, len(flags)) if flags[index - 1] and flags[index]),
        None,
    )
    if first is None:
        return None
    frame_ms = DETECT_FRAME_SAMPLES / SAMPLE_RATE * 1000
    expected = round(tone_seconds * 1000 / frame_ms)
    window = flags[first - 1:first - 1 + expected]
    longest_gap = gap = 0
    for present in window:
        gap = 0 if present else gap + 1
        longest_gap = max(longest_gap, gap)
    return {
        "tone_presence_percent": round(100 * sum(window) / len(window), 1),
        "longest_tone_gap_ms": round(longest_gap * frame_ms, 1),
        "evaluated_ms": round(len(window) * frame_ms, 1),
    }


async def _run_trial(
    client: HomeAssistantNativeTone, url: str, output_dir: Path, run_number: int,
    observe_seconds: float,
) -> dict:
    recording = output_dir / f"run-{run_number:02d}.wav"
    capture = RtspAudioCapture(url, record=str(recording))
    stop = asyncio.Event()
    task: asyncio.Task | None = None
    result = {
        "run": run_number,
        "recording": recording.name,
        "latency_ms": None,
        "status": "not_detected",
        "rtsp_audio_received": False,
    }
    try:
        await capture.start()
        task = asyncio.create_task(capture.run(stop))
        await _wait_for_camera_audio(capture, task, 10)
        result["rtsp_audio_received"] = True
        await asyncio.sleep(0.5)  # Check for an existing 440 Hz tone before sending.
        if capture.background_tone_detected_at is not None:
            result["status"] = "background_tone"
            return result
        await client.command("tone", timeout=TONE_SECONDS + 10,
                             on_send=lambda timestamp: setattr(capture, "tone_sent_at", timestamp))
        deadline = capture.tone_sent_at + TONE_SECONDS + observe_seconds
        while capture.tone_detected_at is None and time.monotonic() < deadline and not task.done():
            await asyncio.sleep(0.05)
        if capture.tone_detected_at is not None:
            result["latency_ms"] = round(
                (capture.tone_detected_at - capture.tone_sent_at) * 1000, 1
            )
            result.update(tone_continuity(capture.tone_presence_flags, TONE_SECONDS) or {})
            result["status"] = "detected"
        elif task.done():
            result["status"] = "rtsp_ended"
    except Exception as err:
        result["status"] = "error"
        result["error"] = f"{type(err).__name__}: {err}"
    finally:
        stop.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await capture.close()
    return result


async def benchmark(args: argparse.Namespace) -> int:
    access_token = os.environ.get("VIDEOLINK_HA_TOKEN")
    ha_username = args.ha_username or os.environ.get("VIDEOLINK_HA_USERNAME")
    ha_password = os.environ.get("VIDEOLINK_HA_PASSWORD")
    if not access_token:
        if not ha_username:
            raise RuntimeError("Set VIDEOLINK_HA_USERNAME, or set VIDEOLINK_HA_TOKEN")
        ha_password = ha_password or getpass.getpass("Home Assistant password: ")
    username = args.username or os.environ.get("VIDEOLINK_USERNAME", "admin")
    password = os.environ.get("VIDEOLINK_PASSWORD") or getpass.getpass("Doorbell password: ")
    user = quote(username, safe="")
    secret = quote(password, safe="")
    host = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
    stream_path = f"h264Preview_{args.channel + 1:02d}_{args.stream}"
    rtsp_url = f"rtsp://{user}:{secret}@{host}:{args.rtsp_port}/{stream_path}"
    if args.output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {args.output_dir}")
    client = HomeAssistantNativeTone(
        args.ha_url, access_token, args.entity_id,
        username=ha_username, password=ha_password,
    )
    client.capture_mix_pcm = getattr(args, "save_mix_wav", False)
    runs: list[dict] = []
    profile = None
    try:
        await client.connect()
        decrypt_headers = getattr(args, "dump_decrypted_headers", False)
        dump_raw = getattr(args, "dump_raw_received", False) or decrypt_headers
        if dump_raw:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            profile = await client.start(
                raw_dump_path=args.output_dir / "native-receives.jsonl",
                decrypt_headers=decrypt_headers,
            )
        else:
            profile = await client.start()
        await client.mix_snapshot()  # Fail early if Home Assistant has the older integration.
        if not dump_raw:
            args.output_dir.mkdir(parents=True, exist_ok=False)
        for run_number in range(1, args.runs + 1):
            mix_frame_start = len(client.mix_pcm_frames) if client.capture_mix_pcm else 0
            mix_before = await client.mix_snapshot()
            result = await _run_trial(client, rtsp_url, args.output_dir, run_number, args.observe)
            result["native_mix"] = mix_delta(mix_before, await client.mix_snapshot())
            if client.capture_mix_pcm:
                mix_wav = f"run-{run_number:02d}-native-mix.wav"
                save_mix_wav(args.output_dir / mix_wav, client.mix_pcm_frames[mix_frame_start:])
                result["native_mix"]["recording"] = mix_wav
            runs.append(result)
            latency = result["latency_ms"]
            print(f"run {run_number}: {result['status']}" + (f" ({latency:.1f} ms)" if latency is not None else ""))
            print(f"  native mix: {result['native_mix']['interpretation']} "
                  f"(raw={result['native_mix']['raw_messages']}, "
                  f"parsed={result['native_mix']['parsed_frames']}, "
                  f"events={result['native_mix']['websocket_events']}, "
                  f"rejected={result['native_mix']['rejected_frames']}, "
                  f"pcm_rms={result['native_mix']['pcm_rms']}, "
                  f"clipped={result['native_mix']['pcm_clipped_percent']}%, "
                  f"aes_xml={result['native_mix']['aes_extension_xml_frames']}, "
                  f"aes_media={result['native_mix']['aes_payload_media_magic_frames']})")
            if run_number < args.runs:
                await asyncio.sleep(0.5)
    finally:
        await client.close()
    summary = summarize(runs)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement": "HA tone request to confirmed 440 Hz in doorbell RTSP microphone audio",
        "limitations": "Includes WebSocket, native send, speaker, microphone, RTSP, and FFmpeg delay; excludes browser microphone capture.",
        "entity_id": args.entity_id,
        "tone_seconds": TONE_SECONDS,
        "observe_seconds": args.observe,
        "native_profile": {
            "sample_rate": profile.get("sample_rate"),
            "samples_per_frame": profile.get("samples_per_frame"),
            "audio_stream_mode": profile.get("audio_stream_mode"),
        },
        "raw_receive_dump": "native-receives.jsonl" if dump_raw else None,
        "raw_receive_frames": client.raw_dump_count if dump_raw else 0,
        "raw_receive_dropped": client.raw_dump_dropped if dump_raw else 0,
        "decrypted_header_capture": decrypt_headers,
        "runs": runs,
        "summary": summary,
    }
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"detected {summary['detected']}/{summary['runs']}; report: {args.output_dir / 'report.json'}")
    return 0 if summary["detected"] == args.runs else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", required=True, help="Home Assistant base URL")
    parser.add_argument("--ha-username", help="Home Assistant local-account username")
    parser.add_argument("--entity-id", required=True, help="Videolink camera entity ID")
    parser.add_argument("--host", required=True, help="doorbell IP address or hostname")
    parser.add_argument("--username", help="doorbell username (default: VIDEOLINK_USERNAME or admin)")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--stream", choices=("main", "sub"), default="main")
    parser.add_argument("--rtsp-port", type=int, default=554)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--observe", type=float, default=4.0,
                        help="seconds to observe after the 2-second tone starts")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new directory for per-run WAV recordings and report.json")
    parser.add_argument("--dump-raw-received", action="store_true",
                        help="save all post-subscription native receive frames as private JSONL (may contain audio)")
    parser.add_argument("--dump-decrypted-headers", action="store_true",
                        help="also include first 64 decrypted bytes of each data-bearing talk frame; implies --dump-raw-received and may contain audio")
    parser.add_argument("--save-mix-wav", action="store_true",
                        help="save private WAV files of decoded native mix audio for listening/quality checks")
    args = parser.parse_args()
    if args.runs < 1 or args.channel < 0 or args.observe <= 0 or not 1 <= args.rtsp_port <= 65535:
        parser.error("runs must be positive, channel nonnegative, observe positive, and RTSP port valid")
    try:
        return asyncio.run(benchmark(args))
    except Exception as err:
        print(f"benchmark failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
