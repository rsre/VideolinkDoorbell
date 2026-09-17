# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [0.12.6] - 2026-09-17

### Fixed

- Kept the WebRTC/RTSP audio backchannel alive with a silent audio track between
  push-to-talk presses so short messages are not delayed in the camera queue.

## [0.12.5] - 2026-09-17

### Changed

- Reduced WebRTC backchannel latency by preferring camera-compatible G.711
  codecs, requesting 10 ms audio packetization, and explicitly enabling the
  RTSP backchannel.
- Added WebRTC transport and codec diagnostics for separating browser/network
  delay from camera-side speaker delay.

## [0.12.4] - 2026-09-17

### Fixed

- Persisted the bundled Lovelace card as a module resource for storage-mode
  dashboards, while retaining the frontend-module fallback for YAML dashboards.
- Migrated the legacy card resource URL and bumped the frontend cache version.

## [0.12.3] - 2026-09-16

### Fixed

- Kept device registry identifiers tied to the persisted config entry.
- Prevented duplicate setup entries for the same host, port, and channel.

## [0.12.0] - 2026-09-10

### Added

- Added validated reconfiguration and reauthentication flows.
- Added Python API/compatibility tests and frontend card tests to CI.

### Fixed

- Made token renewal concurrency-safe and retried snapshots after session expiry.
- Validated camera hosts, including IPv6 URL formatting.
- Prevented stale WebRTC startup operations, buffered early ICE candidates, and
  added bounded reconnection after connection failures.
- Preserved active media across presentation-only card edits and added additional
  safeguards for releasing push-to-talk microphone capture.
- Included the channel in config-entry identity so multiple device channels can
  be configured independently.

### Changed

- Isolated the private go2rtc multi-producer compatibility boundary and removed
  unconditional FFmpeg debug logging.
- Pinned CI actions to immutable revisions and added automated unit-test jobs.
- Renamed the frontend source file to match the public card name.

## [0.11.0] - 2026-09-10

### Added

- Added a `hide_video` camera-card setting for compact audio-only operation.

### Changed

- Replaced the separate camera and audio cards with one `custom:videolink-doorbell`
  card. Existing dashboard configurations must be updated to the new card type.

## [0.10.0] - 2026-09-10

### Added

- Added a `full` video-fit mode that sizes the card to the stream's native aspect
  ratio without cropping, stretching, or letterboxing.

### Changed

- The video-fit editor control is now a dropdown with shorter option labels and
  Scaled selected by default.

## [0.9.0] - 2026-09-10

### Added

- Added a `video_fit` camera-card setting with cropped (`cover`), scaled
  (`contain`), and stretched (`fill`) display modes.

## [0.8.0] - 2026-09-10

### Added

- Added a `hide_controls` camera-card setting that hides both the mute and
  push-to-talk buttons.

## [0.7.1] - 2026-09-09

### Fixed

- Made diagnostic report text selectable and added a copy button with a text
  selection fallback when clipboard access is unavailable.

## [0.7.0] - 2026-09-09

### Added

- Added an opt-in `debug` card setting with live WebRTC connection, RTT, jitter,
  jitter-buffer, codec, packet, microphone acquisition, track attachment, and
  first-outbound-packet diagnostics.

## [0.6.0] - 2026-09-09

### Changed

- The PTT button now shows a loading spinner while the WebRTC stream connects
  and while browser microphone capture is being assigned.
- PTT remains unavailable until the stream is ready and changes to the talking
  state only after its microphone track has been attached.

## [0.5.2] - 2026-09-09

### Changed

- Reverted persistent Lovelace resource creation. The cards are again exposed
  through Home Assistant's extra-module registration when the integration loads.

## [0.5.1] - 2026-09-08

### Fixed

- Persist the bundled cards as a Lovelace module resource so both cards appear
  in the picker after installing or reinstalling the integration.
- Retain automatic extra-module registration as a fallback for YAML dashboards.

## [0.5.0] - 2026-09-08

### Added

- Added the bundled `custom:videolink-doorbell-audio-card` for camera audio and
  push-to-talk without loading or rendering a video stream.

### Changed

- Renamed the integration, domain, component directory, frontend resource, card
  elements, documentation, and repository to **Videolink Doorbell**. Existing
  installations must remove the old integration, install this release, re-add
  their camera, and update dashboard card types.

## [0.4.3] - 2026-09-08

### Added

- Added a `disable_popup` card setting to disable opening Home Assistant's native
  camera dialog when the video is clicked.

## [0.4.2] - 2026-09-08

### Added

- Detect insecure browser contexts, show a persistent HTTPS requirement warning,
  and disable push-to-talk when microphone capture cannot be securely requested.

## [0.4.1] - 2026-09-08

### Changed

- Streams now always start muted. Push-to-talk keeps inbound sound muted during
  transmission, then automatically enables it on release for the response.
- Removed the `start_unmuted` card setting.

## [0.4.0] - 2026-09-08

### Added

- Added visual-editor and YAML settings to hide the card title and request unmuted
  playback on load.
- Clicking the video now opens Home Assistant's native camera more-info dialog.

### Removed

- Removed the card's dedicated fullscreen button.

## [0.3.2] - 2026-09-08

### Changed

- Temporarily mute inbound camera audio while push-to-talk is active to prevent
  feedback, then restore the speaker's previous mute state on release.

## [0.3.1] - 2026-09-08

### Fixed

- Register the complete Videolink RTSP producer so go2rtc can discover and negotiate
  its ONVIF `sendonly` PCMU backchannel track. The previous audio-only media filter
  could omit the camera-speaker backchannel.

## [0.3.0] - 2026-09-08

### Added

- Bundled Videolink Doorbell Camera Lovelace card with native Home Assistant WebRTC
  signaling, hold-to-talk microphone control, speaker mute, and fullscreen.
- Automatic frontend module registration; no separate card repository or dashboard
  resource installation is required.

## [0.2.0] - 2026-09-08

### Added

- Added a secondary RTSP audio producer with go2rtc backchannel support while
  retaining the authenticated web-console FLV stream as the primary video source.

## [0.1.1] - 2026-09-08

### Fixed

- Fixed config-flow loading by defining the integration's channel key locally.
- Replaced RTSP playback with the authenticated FLV endpoint used by the camera's
  original web console.

## [0.1.0] - 2026-09-08

### Added

- Home Assistant UI configuration flow.
- Videolink web-console CGI authentication and automatic token renewal.
- Authenticated JPEG camera snapshots.
- Native main and sub RTSP live streams with audio support.
- Configurable channel, HTTPS port, RTSP port, and TLS verification.
- HACS metadata and automated HACS/Hassfest validation.

[0.12.3]: https://github.com/rsre/VideolinkDoorbell/compare/v0.12.0...v0.12.3
[0.12.4]: https://github.com/rsre/VideolinkDoorbell/compare/v0.12.3...v0.12.4
[0.12.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/rsre/VideolinkDoorbell/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.5.2...v0.6.0
[0.5.2]: https://github.com/rsre/VideolinkDoorbell/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/rsre/VideolinkDoorbell/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/rsre/VideolinkDoorbell/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/rsre/VideolinkDoorbell/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/rsre/VideolinkDoorbell/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/rsre/VideolinkDoorbell/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/rsre/VideolinkDoorbell/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rsre/VideolinkDoorbell/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/rsre/VideolinkDoorbell/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rsre/VideolinkDoorbell/releases/tag/v0.1.0
