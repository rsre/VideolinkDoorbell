# Videolink Doorbell for Home Assistant

A local custom integration that uses the token-based CGI API and the native authenticated FLV live stream. A secondary RTSP producer supplies the go2rtc audio backchannel for supported cameras.

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

## Two-way audio

Home Assistant's built-in go2rtc integration combines the FLV video producer with an RTSP audio-backchannel producer. Open the camera through a WebRTC-capable card and grant the browser microphone permission. Two-way audio depends on the camera firmware exposing a compatible RTSP/ONVIF backchannel; unsupported models continue to provide video and camera-to-browser audio normally.

The stream starts muted; using push-to-talk keeps inbound sound muted while transmitting to avoid feedback, then enables it on release so the reply can be heard.

## Cards

The integration bundles and automatically registers the **Videolink Doorbell** dashboard card. Add it through the dashboard card picker, or use YAML:

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
```

Hold **Hold to talk** while speaking and release it to stop. The card requests microphone access only when the control is pressed and releases the microphone immediately afterward. Home Assistant must be used over HTTPS (or localhost) because browsers block microphone capture on insecure origins.

For an audio-only intercom, set `hide_video: true` on the camera card:

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
hide_video: true
```

Audio-only mode negotiates only camera audio and the push-to-talk backchannel.

### Settings

- `title` to set a custom title on the card.
- `video_fit` controls the video layout: `cover` crops it, `contain` scales the
  entire frame with letterboxing, `fill` stretches it, and `full` sizes the card
  to the stream's native aspect ratio. The default is `contain`.
- `enable_popup` to enable Home Assistant's native camera dialog when clicking the video. It defaults to false.
- `hide_title` for a titleless card.
- `hide_video` to switch the card to its compact, audio-only intercom mode.
- `hide_controls` to hide both the mute and push-to-talk buttons.
- `debug` to show debug information and metrics like live WebRTC transport and PTT timing diagnostics.
- `native_talk` to opt into the experimental native Baichuan talk path. It is
  disabled by default and currently targets Reolink Doorbell firmware.

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

The benchmark separates the native and direct RTSP send/listen paths:

* `native`: Baichuan native send, direct camera RTSP listen.
* `direct-rtsp`: direct camera RTSP backchannel send, direct camera RTSP listen.

To validate direct RTSP audio reception without sending, run capture-only tests:

```bash
python tools/two_way_audio_benchmark.py \
  --host 192.168.1.40 --username admin --channel 0 \
  --capture-runs 3 --observe 5 --play
```

To compare repeated native and direct RTSP backchannel runs, use the benchmark
harness:

```bash
python tools/two_way_audio_benchmark.py \
  --host 192.168.1.40 --username admin --channel 0 \
  --native-runs 5 --direct-rtsp-runs 5 \
  --observe 5
```

The report shows mean, median, min/max, first-run (cold), and subsequent-run
(warm) latency for native and direct RTSP.
