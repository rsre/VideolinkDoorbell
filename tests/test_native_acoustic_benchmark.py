"""Tests for the repeatable native speaker observation tool."""

from __future__ import annotations

import asyncio
import argparse
import importlib.util
import json
import stat
from pathlib import Path
import sys

import pytest
from aiohttp import ThreadedResolver


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "native_talk_acoustic_benchmark_under_test",
    TOOLS / "native_talk_acoustic_benchmark.py",
)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_summary_keeps_failed_runs_out_of_latency_statistics() -> None:
    runs = [
        {"latency_ms": 300.0},
        {"latency_ms": None},
        {"latency_ms": 500.0},
    ]
    assert benchmark.summarize(runs) == {
        "detected": 2,
        "runs": 3,
        "mean_ms": 400.0,
        "median_ms": 400.0,
        "min_ms": 300.0,
        "max_ms": 500.0,
        "first_ms": 300.0,
        "subsequent_mean_ms": 500.0,
    }


@pytest.mark.asyncio
async def test_local_account_login_exchanges_and_revokes_token(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        async def json(self):
            return self.payload

    class Session:
        def __init__(self, connector):
            self.connector = connector

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.connector.close()

        def post(self, url, *, json=None, data=None):
            calls.append((url, json, data))
            if url.endswith("/auth/login_flow"):
                return Response({"type": "form", "flow_id": "flow-1"})
            if url.endswith("/auth/login_flow/flow-1"):
                return Response({"type": "create_entry", "result": "code-1"})
            if url.endswith("/auth/token"):
                return Response({"access_token": "access-1", "refresh_token": "refresh-1"})
            return Response({})

    def make_session(**kwargs):
        assert isinstance(kwargs["connector"]._resolver, ThreadedResolver)
        return Session(kwargs["connector"])

    monkeypatch.setattr(benchmark, "ClientSession", make_session)
    client = benchmark.HomeAssistantNativeTone(
        "https://ha.example", None, "camera.front_door",
        username="local-user", password="private-password",
    )
    access, refresh = await client._login_with_password()
    assert (access, refresh) == ("access-1", "refresh-1")
    assert calls[0][1]["handler"] == ["homeassistant", None]
    assert calls[1][1]["username"] == "local-user"
    assert calls[1][1]["password"] == "private-password"
    assert calls[2][2]["client_id"] == "https://ha.example/"
    client.refresh_token = refresh
    await client._revoke_refresh_token()
    assert calls[3][0].endswith("/auth/revoke")
    assert calls[3][2] == {"token": "refresh-1"}


def test_tone_continuity_reports_gaps_in_detected_audio() -> None:
    quality = benchmark.tone_continuity(
        [False, True, True, False, False, True], tone_seconds=0.384
    )
    assert quality == {
        "tone_presence_percent": 60.0,
        "longest_tone_gap_ms": 128.0,
        "evaluated_ms": 320.0,
    }
    assert benchmark.tone_continuity([False, True, False], 2.0) is None


@pytest.mark.asyncio
async def test_ha_native_tone_uses_owned_session_for_every_command(tmp_path) -> None:
    class Socket:
        def __init__(self) -> None:
            self.sent = []
            self.pending = asyncio.Queue()
            self.closed = False

        async def send(self, raw):
            message = json.loads(raw)
            self.sent.append(message)
            result = {"token": "session-1"} if message["action"] == "start" else {"ok": True}
            if message["action"] == "diagnostics":
                result = {
                    "raw_messages": 2, "headers": {"202/0": 2},
                    "talk_candidates": 2, "parsed_frames": 2,
                    "forwarded_frames": 2, "reader_error": None,
                }
            if message["action"] == "raw_fetch":
                result = {"frames": [{
                    "message_id": 202, "response_code": 200,
                    "extension_b64": "", "payload_b64": "AQIDBA==",
                    "decrypted_payload_prefix_b64": "MTIzNA==",
                }], "next_cursor": 1, "done": True, "dropped": 0}
            if message["action"] == "tone":
                await self.pending.put(json.dumps({
                    "id": 2, "type": "event", "event": {"pcm": "AQIDBA=="},
                }))
            await self.pending.put(json.dumps({
                "id": message["id"], "type": "result", "success": True, "result": result,
            }))

        async def recv(self):
            return await self.pending.get()

        async def close(self):
            self.closed = True

    client = benchmark.HomeAssistantNativeTone(
        "https://ha.example", "test-access-token", "camera.front_door"
    )
    socket = Socket()
    client.socket = socket
    dump_path = tmp_path / "native-receives.jsonl"
    await client.start(raw_dump_path=dump_path, decrypt_headers=True)
    await client.command("tone")
    snapshot = await client.mix_snapshot()
    assert snapshot["backend"]["parsed_frames"] == 2
    assert snapshot["websocket_events"] == 1
    assert snapshot["websocket_pcm_bytes"] == 4
    assert client.raw_dump_count == 0
    await client.close()
    assert [item["action"] for item in socket.sent] == [
        "start", "subscribe", "tone", "diagnostics", "raw_fetch", "stop",
    ]
    assert all(item.get("token") == "session-1" for item in socket.sent[1:])
    assert socket.sent[1]["dump_raw"] is True
    assert socket.sent[1]["dump_decrypted_header"] is True
    assert socket.closed
    dump = [json.loads(line) for line in dump_path.read_text().splitlines()]
    assert dump[0]["message_id"] == 202
    assert dump[0]["response_code"] == 200
    assert dump[0]["payload_b64"] == "AQIDBA=="
    assert dump[0]["decrypted_payload_prefix_b64"] == "MTIzNA=="
    assert stat.S_IMODE(dump_path.stat().st_mode) == 0o600


def test_mix_delta_distinguishes_camera_filter_and_websocket_losses() -> None:
    before = {
        "backend": {"raw_messages": 1, "headers": {"11/200": 1},
                    "talk_candidates": 0, "parsed_frames": 0,
                    "forwarded_frames": 0, "reader_error": None},
        "websocket_events": 0, "websocket_pcm_bytes": 0,
    }
    after = {
        "backend": {"raw_messages": 3, "headers": {"11/200": 1, "202/200": 2},
                    "talk_candidates": 0, "parsed_frames": 0,
                    "forwarded_frames": 0, "reader_error": None},
        "websocket_events": 0, "websocket_pcm_bytes": 0,
    }
    result = benchmark.mix_delta(before, after)
    assert result["headers"] == {"202/200": 2}
    assert result["raw_messages"] == 2
    assert "no data-bearing talk ID 202" in result["interpretation"]
    after["backend"].update({"talk_candidates": 2, "parsed_frames": 2, "forwarded_frames": 2})
    assert "no mix events reached the CLI" in benchmark.mix_delta(before, after)["interpretation"]
    after["websocket_events"] = 2
    after["websocket_pcm_bytes"] = 4096
    after["pcm_samples"] = 1024
    after["pcm_squared_sum"] = 1024 * 1000 * 1000
    after["pcm_non_silent_frames"] = 1
    after["pcm_clipped_samples"] = 0
    assert benchmark.mix_delta(before, after)["websocket_pcm_bytes"] == 4096
    assert benchmark.mix_delta(before, after)["pcm_rms"] == 1000.0
    assert benchmark.mix_delta(before, after)["pcm_non_silent_frames"] == 1
    assert benchmark.mix_delta(before, after)["pcm_clipped_percent"] == 0.0


def test_save_mix_wav_is_private_and_16khz(tmp_path) -> None:
    import wave

    path = tmp_path / "mix.wav"
    benchmark.save_mix_wav(path, [b"\x00\x01" * 1024])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with wave.open(str(path), "rb") as recording:
        assert recording.getframerate() == 16000
        assert recording.getnchannels() == 1
        assert recording.readframes(1024) == b"\x00\x01" * 1024


@pytest.mark.asyncio
async def test_legacy_start_is_stopped_and_reports_upgrade_path() -> None:
    class Socket:
        def __init__(self):
            self.sent = []
            self.pending = asyncio.Queue()
            self.closed = False

        async def send(self, raw):
            message = json.loads(raw)
            self.sent.append(message)
            await self.pending.put(json.dumps({
                "id": message["id"], "type": "result", "success": True,
                "result": {"ok": True},
            }))

        async def recv(self):
            return await self.pending.get()

        async def close(self):
            self.closed = True

    client = benchmark.HomeAssistantNativeTone(
        "https://ha.example", "test-access-token", "camera.front_door"
    )
    socket = Socket()
    client.socket = socket
    with pytest.raises(RuntimeError, match="update custom_components/videolink_doorbell"):
        await client.start()
    await client.close()
    assert [message["action"] for message in socket.sent] == ["start", "stop"]
    assert "token" not in socket.sent[1]
    assert socket.closed


@pytest.mark.asyncio
async def test_trial_records_detection_from_command_send(monkeypatch, tmp_path) -> None:
    class Capture:
        def __init__(self, url, *, record):
            self.record = record
            self.first_audio_at = None
            self.background_tone_detected_at = None
            self.tone_sent_at = None
            self.tone_detected_at = None
            self.tone_presence_flags = [True, True, True]

        async def start(self):
            self.first_audio_at = 1.0

        async def run(self, stop):
            await stop.wait()

        async def close(self):
            pass

    class Client:
        async def command(self, action, *, timeout, on_send):
            assert action == "tone"
            on_send(10.0)
            capture.tone_detected_at = 10.35

    capture = Capture("rtsp://camera", record="")
    monkeypatch.setattr(benchmark, "RtspAudioCapture", lambda *args, **kwargs: capture)
    result = await benchmark._run_trial(Client(), "rtsp://camera", tmp_path, 1, 1.0)
    assert result["status"] == "detected"
    assert result["latency_ms"] == 350.0
    assert result["recording"] == "run-01.wav"


@pytest.mark.asyncio
async def test_benchmark_writes_report_without_credentials(monkeypatch, tmp_path) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            self.snapshots = 0

        async def connect(self):
            pass

        async def start(self):
            return {
                "sample_rate": 16000,
                "samples_per_frame": 1024,
                "audio_stream_mode": "mixAudioStream",
            }

        async def close(self):
            pass

        async def mix_snapshot(self):
            self.snapshots += 1
            return {
                "backend": {"raw_messages": self.snapshots - 1, "headers": {},
                            "talk_candidates": 0, "parsed_frames": 0,
                            "forwarded_frames": 0, "reader_error": None},
                "websocket_events": 0, "websocket_pcm_bytes": 0,
            }

    async def trial(*args):
        return {
            "run": 1, "recording": "run-01.wav", "latency_ms": 420.0,
            "status": "detected", "rtsp_audio_received": True,
        }

    monkeypatch.setenv("VIDEOLINK_HA_TOKEN", "private-ha-token")
    monkeypatch.setenv("VIDEOLINK_PASSWORD", "private-doorbell-password")
    monkeypatch.setattr(benchmark, "HomeAssistantNativeTone", Client)
    monkeypatch.setattr(benchmark, "_run_trial", trial)
    output_dir = tmp_path / "new-report"
    args = argparse.Namespace(
        ha_url="https://ha.example", ha_username=None, entity_id="camera.front_door",
        host="192.0.2.1", username="admin", channel=0, stream="main",
        rtsp_port=554, runs=1, observe=4.0, output_dir=output_dir,
    )
    assert await benchmark.benchmark(args) == 0
    report_text = (output_dir / "report.json").read_text()
    report = json.loads(report_text)
    assert report["summary"]["mean_ms"] == 420.0
    assert report["runs"][0]["recording"] == "run-01.wav"
    assert report["runs"][0]["native_mix"]["raw_messages"] == 1
    assert "private-ha-token" not in report_text
    assert "private-doorbell-password" not in report_text
