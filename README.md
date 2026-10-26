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

**Verify CGI HTTPS certificate** is unchecked by default for factory/self-signed camera certificates. Enable it when the camera uses a trusted certificate. Existing entries retain their setting. This setting covers CGI login, commands, and snapshots only.

Select `main` for the console's Clear stream or `sub` for Fluent.

RTSP must be enabled for the `rtsp` talk mode. The `native` talk mode uses the
camera's Baichuan service on TCP port 9000; video continues to use FLV.

Connection, credential, stream, and CGI certificate settings can be updated
from the integration's **Reconfigure** action. Authentication failures prompt for
replacement credentials without requiring the integration to be removed.

Camera identity uses the UID returned by `GetP2p`, matching the UID shown in
the camera interface. The `GetDevInfo` serial is informational. When upgrading
from version 0.12.60 or earlier, remove the existing integration entry and add
it again; serial-based entries are not migrated. Update dashboard and automation
entity references if Home Assistant assigns different entity IDs.

## Doorbell button automations

The integration creates a **Doorbell** event entity on the camera device. It
receives visitor notifications through the camera's Baichuan service on TCP
port 9000 and emits a `ring` event when the button is pressed. The listener
runs even when the dashboard card and camera stream are closed.

In **Settings → Automations & scenes**, add an **Event received** trigger,
select the Doorbell event entity, and choose **Ring**. For YAML automations:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.your_videolink_doorbell
    options:
      event_type:
        - ring
```

Replace the entity ID with the one created for your camera. Reolink's visitor
signal must be available on the configured channel for rings to appear.

## Card

The integration bundles and automatically registers the **Videolink Doorbell** dashboard card. Add it through the dashboard card picker, or use YAML:

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
```

Home Assistant must be used over HTTPS (or localhost) because browsers block microphone capture on insecure origins.

Hold **Space** to turn on your microphone and talk; release it to turn the
microphone off again. This works while Home Assistant is the active browser
tab, without focusing the **Hold to talk** button. Switching tabs or leaving
the window also turns the microphone off. Space keeps its usual behavior when
typing or using other controls. With multiple cards, focus the desired card's
**Hold to talk** button; otherwise, the first available card handles Space.

The card also has an independent [source and distribution](frontend/README.md)
for backend installations that omit automatic card installation.

### Kiosk card

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
card_style: kiosk
button_entity: button.open_gate
button_label: Open gate
```

The kiosk style starts the video and repeats a short two-note ring every two
seconds when the card first becomes visible. Pressing **Hold to talk** or the
configured button acknowledges the ring and stops it immediately, even if the
microphone request or button action later fails. Closing the card or hiding the
browser tab also stops ringing. It rings again if the card is removed and
reopened; state updates, stream reconnects, and returning to the browser tab do
not restart it. Use a conditional card or popup to display it when a visitor rings.
An always-visible kiosk card rings when the dashboard opens, rather than on
every doorbell event.

Under the video are two buttons: **Hold to talk** (hold to transmit, release to
stop, with the same Space shortcut) and the configured Home Assistant button.
The second button calls `button.press`; it is disabled until `button_entity`
is configured and available. `button_label` defaults to the entity's friendly
name. Camera audio starts enabled, mutes during PTT by default, and resumes
on release. A read-only SVG speaker icon in the video's top-right corner shows
camera audio status, with a crossed speaker when muted. There is no mute button.

Allow audio autoplay for Home Assistant in the kiosk browser so the ring and
camera audio can play without a gesture. Browsers that block autoplay still
show muted video and a crossed speaker icon; its tooltip explains the blocked
audio. Either kiosk control retries
camera audio during its normal action; blocked rings are not queued for later.
See the [browser autoplay rules](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay).

To test the ring manually, set `debug: true` and tap **Play ring** in the
card's **Stream diagnostics** panel. This retries the chime with a direct
user gesture, including when automatic playback was blocked. The test ring also
repeats until you press PTT or the configured button, or close the card.

### Settings

- `title` to set a custom title on the card. If omitted, the camera entity name
  is used; leave it blank to hide the title.
- `card_style` selects the card layout (defaults to `audio_video`):
  - `audio_video` shows both video and audio controls.
  - `video` shows video only.
  - `audio` shows the audio-only intercom with controls.
  - `kiosk` shows video, mute status, PTT, and a configurable button, and rings
    until acknowledged when opened.
- `button_entity` selects the `button.*` entity for the kiosk's second button.
- `button_label` overrides that button's label.
- `talk_mode` selects the talkback path (defaults to `rtsp`):
  - `rtsp` uses the WebRTC/RTSP backchannel.
  - `native` uses the experimental native Baichuan protocol.
- `mute_while_talking` defaults to `true` in both modes. It silences camera
  audio while the microphone is held open to prevent speaker-to-microphone
  feedback, then resumes listening when native push-to-talk ends. Set it to
  `false` for simultaneous listening and talking only if your browser/device
  echo cancellation or headphones prevent feedback.
- In native mode, incoming audio uses the decoded camera-microphone channel of
  the Baichuan mix when playable frames arrive. The card falls back to the
  WebRTC camera-audio track if native mix is unavailable, silent at startup, or
  stops. The app's proprietary AEC is not used.
- `video_fit` controls the video layout (default is `contain`):
  - `contain` scales the entire frame with letterboxing,
  - `cover` crops it,
  - `fill` stretches it,
  - `full` sizes the card to the stream's native aspect ratio.
- `enable_popup` to enable Home Assistant's native camera dialog when clicking the video.
- `debug` to show debug information and metrics like live WebRTC transport and PTT timing diagnostics.

### Native talk timing check

After installing this integration version in Home Assistant, restart Home
Assistant, refresh the browser, and set `talk_mode: native` and `debug: true`.
Hold **Hold to talk** for a few seconds while speaking, then release it. In
the card's debug panel, confirm that the audio direction line reads
`recvonly / sendonly / recvonly` and that inbound audio packets increase. The
three directions come from the actual SDP offer, SDP answer, and browser
transceiver rather than the requested card setting.

The panel reports the latest native frame's browser callback interval, PTT to
first callback, callback to WebSocket dispatch and enqueue acknowledgement,
Home Assistant receive to enqueue, queue wait, ADPCM encoding, TCP
write/drain, queue depth, and dropped-frame count. These are stage durations
measured on their own machine; they do not require synchronized browser and
Home Assistant clocks. WebSocket acknowledgement confirms enqueueing, not
camera playback. To measure audible end-to-end delay, use the acoustic
benchmark below or an external recording of the doorbell speaker.

The native browser and Home Assistant queues each retain at most two 64 ms
frames. When a queue fills, frames are dropped to keep latency bounded; the
debug panel counts drops on each side. The microphone startup time shown
by `Mic permission` includes browser device acquisition and may dominate the
first press.

## Versions

Releases use semantic versioning (`MAJOR.MINOR.PATCH`). The integration version in `manifest.json` always matches the GitHub release tag without its leading `v`.
See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Development validation

```bash
python -m compileall custom_components/videolink_doorbell
python -m pytest -q
ruff check .
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
failure, summary latency, an approximate tone-continuity score, and native-mix
receive counters. The counters distinguish no camera messages, data-bearing
talk messages, decoded PCM frames, and missing WebSocket events. The observed
`202/200` mix container is decrypted and validated before its camera-microphone
PCM is used; unknown or malformed payloads are not played. Deploy this version
of the integration to Home Assistant and restart it before using the new
diagnostics command. Listen to
the recordings as well as reading the numbers: a missed detection or a gap in
the tone score may mean the doorbell's echo cancellation hid the tone from its
own microphone.

The report also measures tone arrival in decoded native mix PCM. The tool
waits until the native mix is quiet before each trial, so a previous tone's
tail cannot count as a new response. `native_mix_latency_summary` measures from
the Home Assistant tone request to two consecutive 440 Hz camera-microphone
mix frames arriving at the CLI. The original `summary` measures the same
request to RTSP/FFmpeg detection. Neither is a direct measurement at the
doorbell speaker; both include camera microphone and transport delays. Use
`--save-mix-wav` to retain the decoded native mix privately for inspection.

Short direct probes must keep the native talk session open long enough for the
camera's buffered audio to play. In a one-second direct-tone experiment, an
immediate `AudioTalkStop` suppressed the tone in native mix; waiting two
seconds before stop allowed detection. The dashboard keeps its native session
open after releasing Hold to talk, so queued speech can finish playing.

The `aes_extension_xml_frames` and `aes_payload_media_magic_frames` counters
are diagnostic probes for recognizable headers. Use the decoded-frame and
rejected-frame counters to assess native mix playback; a zero in either probe
counter does not establish that no audio was received.

For protocol inspection, add `--dump-raw-received`. The benchmark then writes
`native-receives.jsonl` in the output directory, with one record
per native message received after subscription. Home Assistant buffers up to
16 MiB during the timed trials, then the CLI fetches the records after the
last trial; `raw_receive_dropped` reports any frames lost at the cap. Header fields are readable;
extension and payload bytes are base64-encoded. The file is created with
owner-only permissions and may contain camera audio or metadata. Keep it
private and delete it when no longer needed.

For the native mix wrapper investigation, `--dump-decrypted-headers` also
records the first 64 AES-decrypted bytes of each data-bearing talk message in
the same private JSONL file. It implies raw capture. These bytes may contain
audio; do not share the file publicly. No AES key or full decrypted payload is
written. This option adds a small amount of per-frame work, so use the normal
benchmark without it for final latency measurements.

Add `--save-mix-wav` to save separate, owner-only WAV files of the decoded
native incoming audio. These may contain private conversations; listen locally
and do not share them publicly.

The reported interval starts when the test sends Home Assistant's WebSocket
tone command and ends when the tone is detected in decoded doorbell RTSP audio.
It includes speaker-to-microphone pickup and the RTSP return path. It does not
measure browser microphone capture or give an exact speaker-onset timestamp.
Keep the doorbell's microphone clear of other 440 Hz sounds during the test.


## Running tests

The protocol and frontend tests need Python 3.12 or later:

```sh
python -m venv .venv-unit
.venv-unit/bin/python -m pip install -r requirements-test.txt
.venv-unit/bin/python -m pytest -q --ignore=tests/ha --ignore=tests/test_event.py
.venv-unit/bin/ruff check .
node --test tests/videolink-card.test.js
```

Home Assistant tests use a separate Python 3.14.2 or later environment. The
fixture package pins Home Assistant 2026.9.1; the extra dependencies match its
camera, stream and go2rtc manifests. Reolink tests pin client 0.21.17; the runtime
allows 0.21.15 or newer so Home Assistant can retain its compatible shared version.

```sh
python3.14 -m venv .venv-ha
.venv-ha/bin/python -m pip install -r requirements-ha-test.txt
.venv-ha/bin/python -m pytest -q tests/ha tests/test_event.py
```

These tests exercise real entry setup, scheduled retries, entity registration,
reauthentication and repeated reload/unload cycles with mocked device I/O.

The browser suite additionally needs Node.js and Chromium. It runs the actual
card against Home Assistant's HTTP/WebSocket server using the official HA
connection library. Native device I/O is simulated; WebRTC uses a real browser
peer pair and microphone capture uses Chromium's fake device. It covers socket
loss, reconnect, interrupted subscriptions and competing card ownership.

```sh
.venv-ha/bin/python -m pip install -r requirements-browser-test.txt
.venv-ha/bin/python -m playwright install chromium
npm ci --prefix tests/browser
npm run build --prefix tests/browser
VIDEOLINK_BROWSER_TESTS=1 .venv-ha/bin/python -m pytest -q tests/ha/test_browser_lifecycle.py tests/ha/test_browser_layout.py
```

The layout suite opens the shipped Home Assistant Sections dashboard and checks
that 16:9 and 4:3 video, controls, and the following section do not overlap at
mobile and desktop widths, including after the stream dimensions change.

The browser tests are skipped in ordinary Python runs and run in a dedicated CI
job. On Linux, use `playwright install --with-deps chromium` for system libraries.


## Standalone protocol client

The CGI and native Baichuan implementation lives in the independently buildable
[`videolink_client` package](custom_components/videolink_doorbell/videolink_client/README.md).
It has its own dependencies, license, package metadata and protocol tests, and
imports no Home Assistant modules. HACS uses the bundled copy. Publishing the
package and pinning the published dependency remain prerequisites for a future
Core submission.

## Dashboard packaging and Core scope

The HACS entry point (`__init__.py`) composes two independent setup hooks:
`backend.py` owns device setup, unload and native-talk WebSocket
commands; `hacs_frontend.py` owns static card serving and Lovelace resource
installation/migration. The backend never invokes the card installer. HACS
continues to bundle and automatically install the card, including its YAML
dashboard fallback.

For a Core submission, use `backend.py` as the integration's `__init__.py` and
exclude `hacs_frontend.py` and the `frontend/` assets. Remove `frontend` and
`lovelace` from the Core manifest dependencies; those dependencies belong to
the HACS wrapper. The independent source lives in the repository's top-level
`frontend/` directory; the integration's asset is a vendored HACS copy checked
by CI. Build the separate JavaScript distribution with
`python tools/build_frontend.py`; its ZIP contains no backend Python files.
Users of a backend that omits the bundle install the separate card through
dashboard resource settings. The backend's WebSocket commands remain
available to that card.
The current manifest describes the HACS distribution; this separation does
not replace the pending public protocol-client dependency or the remaining
Core compatibility work.

## Runtime ownership

Each config entry owns a lifecycle manager, a lazy doorbell subscription,
bounded diagnostic captures and a stream source manager. Shutdown and unload
cancel outstanding device work and close these resources. Event entities only
consume availability and ring notifications; disabled entities open no event
subscription. Camera entities apply source changes to Home Assistant's HLS
worker, while the entry owns token refresh scheduling and configuration updates.

On Home Assistant versions with the public multiple-source camera API, Core
alone registers the video and RTSP backchannel producers. Home Assistant
2026.9 needs a serialized composite-stream compatibility adapter. This legacy
path still reads Core's private go2rtc client. Source renewal on newer versions
uses a guarded provider refresh hook because Core has no public source-change
notification API; that boundary is isolated in `go2rtc.py` and covered by tests.

## Diagnostics

Home Assistant's integration diagnostics download reports known connection
settings, model/firmware and cached runtime/subscription/stream health. It
redacts the host, credentials, camera name, serial number and UID. Tokens,
authenticated URLs, raw captures and audio are excluded. Downloads make no
device requests and do not start disabled entities' lazy services; they also
work before successful setup and after unload.


### Transport security and recovery

CGI requests reject redirects and enforce the selected CGI certificate policy.
FLV is fetched independently by go2rtc or FFmpeg/HLS: in go2rtc 1.9.14, IP
HTTPS sources skip certificate verification and hostname sources use normal
verification. The CGI setting cannot override either media provider's TLS policy.
Use a trusted hostname certificate and configure the media provider separately
when authenticated media transport is required. A self-signed hostname may fail
in go2rtc even with CGI verification disabled. HLS/FFmpeg has its own TLS settings.
There is currently no integration-owned media proxy or certificate pinning.

Native Baichuan talk uses raw TCP with camera digest/AES-CFB framing, without
TLS peer authentication or a message authentication tag. Outgoing ADPCM speech
is unencrypted even when its XML extension is encrypted. RTSP/RTP talk also uses
unencrypted transport. CGI verification does not protect either talk path; use
a trusted isolated network or an authenticated secure relay where supported.

Enabled cameras with a resolved stream or failed source receive a bounded CGI
health check every 30 seconds. This validates cached tokens after camera reboot
without unconditional logins. A successful check/source restores availability;
RTSP URL construction alone does not establish health. Snapshots share that same
camera reachability state. Disabled/unused cameras do not initiate checks.

### Supported hardware and removal

New configurations use channel zero; the UI does not expose channel selection.
The automated suite uses simulated camera responses, Home Assistant 2026.9.1,
and browser media peers. No model/firmware combination has been validated on
physical hardware by this audit. Hardware and acoustic compatibility must be
confirmed on the intended camera before relying on talkback.

To remove the integration, delete its entry in Settings → Devices & services,
remove its cards from dashboards, and uninstall it through HACS. After deleting
the final entry, remove the Videolink JavaScript resource from Settings →
Dashboards → Resources (enable advanced mode), then restart Home Assistant.
The resource is shared across entries and is not automatically deleted.

For a future Core distribution, publish the standalone protocol package and
replace vendored imports only after that release is available. Keep the HACS
card/resource installer out of the Core distribution. The package includes
`py.typed`; it does not yet claim complete strict typing.

Reconfigure forms retain non-secret settings after errors. Leave the password
blank to keep the stored credential; enter a replacement to change it. Initial
setup and reauthentication require the password to be entered explicitly.
