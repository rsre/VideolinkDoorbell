# Videolink Doorbell dashboard card

This directory owns the dashboard card source and its version. The standalone
distribution contains no Python integration code and makes no changes to Home
Assistant's resource registry. It requires the Videolink Doorbell backend and
its camera entity; native audio uses that backend's WebSocket commands.

## Build

From the repository root, using Python 3.12 or later:

```sh
python tools/build_frontend.py
```

The output in `frontend/dist/` includes `videolink-doorbell.js`, this README,
the MIT license, SHA-256 checksums and a versioned ZIP. No Home Assistant or
third-party Python packages are needed to build it.

## Install the standalone card

1. Copy `videolink-doorbell.js` from the distribution to your Home Assistant
   configuration directory as `www/videolink-doorbell.js`.
2. In dashboard resource settings, add `/local/videolink-doorbell.js?v=VERSION`
   as a **JavaScript module**, replacing `VERSION` with the card's version.
   In YAML resource mode, add that URL with `type: module` under your dashboard
   resources instead.
3. Reload the dashboard and add:

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
```

Use HTTPS or localhost for microphone access. After updating the file, change
the resource's version query so browsers fetch the updated card. Register one
copy of the card: the current HACS backend distribution already bundles and
registers it, so its users can continue using the automatic installation.
The standalone artifact is intended for backend distributions that omit it,
including a future Core integration.

## Push to talk

While Home Assistant is the active browser tab, hold **Space** to turn your
microphone on and release it to turn it off again. The **Hold to talk** button
does not need focus. Switching tabs or leaving the window also stops the
microphone. Space retains its normal behavior in text fields and other controls.
If several cards are available, focus the desired card's **Hold to talk** button;
otherwise, the first available card handles the shortcut.

## Kiosk layout

```yaml
type: custom:videolink-doorbell
entity: camera.your_videolink_camera
card_style: kiosk
button_entity: button.open_gate
button_label: Open gate
```

Shows the video, a read-only SVG speaker icon in the video's top-right corner,
and two controls:
**Hold to talk** and a proxy for the configured Home Assistant `button.*`
entity. The proxy uses `button.press` and defaults to the entity's friendly
name when `button_label` is omitted. Missing or unavailable buttons are disabled.
Both controls acknowledge the repeating ring when pressed.

A short two-note ring repeats every two seconds when the card first becomes
visible, including when a conditional card or popup opens. Pressing PTT or the
configured button stops it immediately, even if that action later fails. Closing
the card or hiding the browser tab also stops ringing. Removing and reopening
the card rings again; state updates, stream reconnects, and returning to the tab
do not restart it.
An always-visible card rings on dashboard load, not on subsequent doorbell events.
Camera audio starts enabled, mutes during PTT by default, and resumes on release.
There is no mute button. Allow audio autoplay for Home Assistant in the kiosk
browser for unattended sound. If blocked, the card falls back to muted video
and shows a crossed speaker icon, with a tooltip explaining the blocked audio;
either control retries camera audio during its usual action.
A blocked ring is discarded instead of playing later after a gesture.
Ring sound errors appear in a red warning bar at the bottom of the video feed.

Set `debug: true` to show a **Play ring** button in **Stream diagnostics**.
Tap it to test the chime directly, including after automatic audio was blocked.
The test ring repeats until you press PTT or the configured button, or close
the card.

## Maintain the HACS bundle

Edit `frontend/videolink-doorbell.js`, then run:

```sh
python tools/build_frontend.py --sync-bundle
python tools/build_frontend.py --check-bundle
node --test tests/videolink-card.test.js
```

Synchronization updates the vendored HACS file and the installer's cache
version. CI verifies that both match the independent frontend source. The
frontend version can change independently of the backend's manifest version.

## Sections layout

Video cards use automatic row height so the complete stream and controls fit
before the next section, including streams with a 4:3 aspect ratio. Audio-only
cards keep their compact two-row layout. An explicit `grid_options.rows` value
in a dashboard overrides the default; use `rows: auto` if a fixed value clips
or overlaps the content.
