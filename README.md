# Videolink Doorbell for Home Assistant

A local custom integration that uses the token-based CGI API and the native authenticated FLV live stream.

There's two options for 2 way audio:

- a secondary RTSP producer supplies the go2rtc audio backchannel for supported cameras.
- an implementation of the custom Reolink protocol.

## Install with HACS

1. In HACS, open **Integrations**, select the three-dot menu, then **Custom repositories**.
2. Add `https://github.com/rsre/VideolinkDoorbell` with the **Integration** category.
3. Install **Videolink Doorbell** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for **Videolink Doorbell**.

## Manual install

1. Copy `custom_components/videolink_doorbell` into the matching directory in your Home Assistant configuration folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for **Videolink Doorbell**.
4. Enter the camera host and the credentials used by its web console.

For cameras with their factory/self-signed certificate, leave **Verify HTTPS certificate** disabled.

Select `main` for the console's Clear stream or `sub` for Fluent.

RTSP must be enabled for two-way audio, but video continues to use FLV.

Connection, credential, channel, stream, and certificate settings can be updated
from the integration's **Reconfigure** action. Authentication failures prompt for
replacement credentials without requiring the integration to be removed.

## Card

The integration bundles and automatically registers the **Videolink Doorbell** dashboard card. Add it through the dashboard card picker, or use YAML:

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
```

Home Assistant must be used over HTTPS (or localhost) because browsers block microphone capture on insecure origins.

### Settings

- `title` to set a custom title on the card. If omitted, the camera entity name
  is used; leave it blank to hide the title.
- `card_style` selects the card layout (defaults to `audio_video`):
  - `audio_video` shows both video and audio controls.
  - `video` shows video only.
  - `audio` shows the audio-only intercom with controls.
- `talk_mode` selects the talkback path (defaults to `rtsp`):
  - `rtsp` uses the WebRTC/RTSP backchannel.
  - `native` uses the experimental native Baichuan protocol.
- `video_fit` controls the video layout (default is `contain`):
  - `contain` scales the entire frame with letterboxing,
  - `cover` crops it,
  - `fill` stretches it,
  - `full` sizes the card to the stream's native aspect ratio.
- `enable_popup` to enable Home Assistant's native camera dialog when clicking the video.
- `debug` to show debug information and metrics like live WebRTC transport and PTT timing diagnostics.

## Versions

Releases use semantic versioning (`MAJOR.MINOR.PATCH`). The integration version in `manifest.json` always matches the GitHub release tag without its leading `v`.
See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Development validation

```bash
python -m compileall custom_components/videolink_doorbell
python -m pytest -q tests/test_api.py tests/test_go2rtc_adapter.py
node --test tests/videolink-card.test.js
```

For local native-talk timing tests, capture the camera's RTSP audio directly
with `ffmpeg` and optionally send a 440 Hz tone through the native Baichuan
path:

```bash
python tools/native_talk_rtsp_probe.py \
  --host 192.168.1.40 --username admin --channel 0 \
  --tone-seconds 2 --observe 5 --play --record /tmp/videolink-rtsp.wav
```

The probe reports whether the tone is detected in the RTSP audio and estimates
the time from the first native tone frame to that observation.

The older comparison benchmark separates the native and direct RTSP send/listen
paths. It currently uses the camera and Home Assistant constants at the top of
`tools/two_way_audio_benchmark.py`; set those for your installation before use:

- `native`: Baichuan native send, direct camera RTSP listen.
- `direct-rtsp`: direct camera RTSP backchannel send, direct camera RTSP listen.

To validate direct RTSP audio reception without sending, run capture-only tests:

```bash
python tools/two_way_audio_benchmark.py \
  --capture-runs 3 --play
```

To compare repeated native and direct RTSP backchannel runs, use the benchmark
harness:

```bash
python tools/two_way_audio_benchmark.py \
  --native-runs 5 --direct-rtsp-runs 5
```

The report shows mean, median, min/max, first-run (cold), and subsequent-run
(warm) latency for native and direct RTSP.

### Repeatable native speaker measurement

To measure when the doorbell microphone hears a tone sent through Home
Assistant's native talk path, use a Home Assistant local-account username and
the doorbell password, then run:

```bash
python tools/native_talk_acoustic_benchmark.py \
  --ha-url https://your-home-assistant.example \
  --ha-username your_ha_username \
  --entity-id camera.front_door \
  --host 192.168.1.40 \
  --runs 5 \
  --output-dir /tmp/videolink-acoustic-001
```

Set `VIDEOLINK_PASSWORD` as an environment variable or let the tool prompt for
the doorbell password. The tool also prompts for the Home Assistant password;
`VIDEOLINK_HA_PASSWORD` can supply it from the environment. A local-account
login is exchanged for a temporary token in memory, which the tool revokes
after the run. If your Home Assistant login uses MFA or another provider, set
`VIDEOLINK_HA_TOKEN` instead. The benchmark requires
`ffmpeg` and the Python `websockets` package. The output directory must be new.
Each run records `run-XX.wav`, and `report.json` contains every detection,
failure, summary latency, and an approximate tone-continuity score. Listen to
the recordings as well as reading the numbers: a missed detection or a gap in
the tone score may mean the doorbell's echo cancellation hid the tone from its
own microphone.

The reported interval starts when the test sends Home Assistant's WebSocket
tone command and ends when the tone is detected in decoded doorbell RTSP audio.
It includes speaker-to-microphone pickup and the RTSP return path. It does not
measure browser microphone capture or give an exact speaker-onset timestamp.
Keep the doorbell's microphone clear of other 440 Hz sounds during the test.
