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
