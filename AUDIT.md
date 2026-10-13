# Videolink Doorbell audit

Date: 2026-10-13. Reviewed commit: `35135ec13ed5bd30efee4f9ee3fc68585430196f`.
Integration version: `0.12.56`; bundled card version: `0.12.57`.
Home Assistant baseline exercised: `2026.9.1`, with Python `3.14.8`.

The integration has a sound overall structure, but several security and recovery
paths need correction. The most serious finding is that an HTTPS CGI login can
follow a redirect that sends the camera password to another server over plain
HTTP. The principal reliability gaps concern camera restart recovery, camera
availability, and browser native-talk lifecycle transitions.

This review found **11 actionable findings: 1 high, 8 medium, and 2 low**. Severity
reflects impact and prerequisites; a high finding here does not imply an
unauthenticated attack against the Home Assistant API. Runtime fixes were not
applied as part of this audit.

## Scope and validation

Reviewed all integration Python modules, the bundled protocol client, the
browser card, configuration and translations, packaging, CI, existing tests,
and developer tools. Consulted current Home Assistant guidance and the source
of the Home Assistant and Reolink versions exercised by the tests. Verified the
go2rtc TLS behavior against its `v1.9.14` source, the version recommended by the
tested Home Assistant baseline.

| Check | Result |
| --- | --- |
| Complete existing Python suite, including HA and Chromium tests | **193 passed** |
| Separate Python 3.12 protocol and boundary suite | **135 passed** |
| Node card tests | **24 passed** |
| Ruff | Passed |
| Bundled card consistency and standalone card ZIP | Passed |
| Standalone client wheel and source distribution | Built; installed wheel imports without loading Home Assistant |
| Coverage across the complete Python suite | **84.9% statements; 66.9% branches; 81.1% combined** |
| Targeted audit reproductions | Confirmed redirect, token, identity, availability, browser lifecycle, header timeout, and invalid-profile findings |
| Dependency advisory queries | Queried OSV for selected runtime and development versions; assessed relevance against maintainer advisories |

The full suite included all three existing Chromium lifecycle tests, with no
skips. These exercise the real HA WebSocket server but simulate camera hardware
and use a browser peer in place of a real go2rtc media connection. They therefore
do not establish compatibility with actual camera firmware or all media paths.

No physical camera, real credentials, live Home Assistant installation, or
production service was contacted. HACS and Hassfest GitHub Actions were reviewed
but were not executed locally. The local tests ran on macOS; HA OS/container
deployment, prolonged streaming, and hardware acoustic performance remain
unverified. Dependency checking covered the selected versions below, not the
entire resolved Home Assistant dependency graph or historical Git commits.

## Findings

| ID | Severity | Finding | Evidence |
| --- | --- | --- | --- |
| F01 | High | CGI redirects can disclose the login password over HTTP | Local verified-TLS reproduction |
| F02 | Medium | HTTPS verification settings do not govern the FLV media transport | Integration and go2rtc source |
| F03 | Medium | Setup accepts a different camera under an existing device identity | Focused setup reproduction |
| F04 | Medium | Stream-only camera reboot recovery retains revoked tokens | Local camera API reproduction |
| F05 | Medium | Camera availability does not recover with stream source acquisition | Real Camera base class reproduction |
| F06 | Medium | Microphone release waits for remote audio acknowledgements | Real card method reproduction |
| F07 | Medium | Changing cameras during native startup leaks the old session | Real card method reproduction |
| F08 | Medium | A card cannot recover after another card takes its native session | Existing ownership behavior and card reproduction |
| F09 | Low | The native frame timeout excludes the header | Partial-header reproduction |
| F10 | Medium | Camera responses and negotiated audio profiles lack sufficient resource and format limits | Source and invalid-profile reproduction |
| F11 | Low | Development HTTP dependency is vulnerable; standalone metadata allows affected releases | Maintainer advisory and package metadata |

### F01 — CGI redirects can disclose the login password over HTTP

Sources: [client request](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:126),
[login body](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:155).

The POST request uses aiohttp's default redirect behavior. The login password is
in its JSON body. A `307` or `308` redirect preserves that body and can change
both origin and scheme. TLS verification of the initial camera endpoint does
not constrain where the body is subsequently sent.

A local HTTPS camera endpoint with a certificate explicitly trusted by the
client returned a `307` redirect to a second, plain HTTP server. Calling
`login()` with `verify_ssl=True` delivered the complete dummy password to that
second server and completed successfully. This used the HA baseline's aiohttp
`3.14.3`; it is separate from the older dependency vulnerabilities in F11.

An attacker needs control of the responding endpoint or its response. The
default unverified TLS setting also makes response manipulation possible for an
active attacker on the camera network. No HA WebSocket authentication bypass
was demonstrated.

**Fix:** Set `allow_redirects=False` on CGI login, command, and snapshot requests
and reject unexpected redirects. If a firmware genuinely requires a redirect,
validate an explicit same-origin HTTPS destination before sending credentials
or tokens. Also constrain authentication redirects in the standalone HA login
benchmark.

**Regression:** Trusted HTTPS to HTTP and cross-origin HTTPS redirects must not
receive the login body. Include both `307` and `308` responses.

### F02 — HTTPS verification settings do not govern the FLV media transport

Sources: [SSL default](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/const.py:18),
[FLV URL](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:296),
[provider registration](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/stream.py:146).

`verify_ssl` is supplied to Python CGI requests, but the FLV source URL is
identical for both values. go2rtc makes the media HTTP request independently.
In the tested baseline's recommended go2rtc version, `https` sources using an IP
address disable certificate verification, while HTTPS hostname sources use
normal verification. The implementation is explicit in
[go2rtc's TLS request code](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/pkg/tcp/request.go).

Consequently, enabling verification does not enforce it for an IP-based FLV
stream. Conversely, disabling it does not make a self-signed hostname-based FLV
source work through this provider. The HLS/FFmpeg transport has its own policy,
which is also not configured by this integration's checkbox.

**Fix:** Define the setting's scope precisely and implement a supported way to
enforce the selected trust policy for each media transport. An integration-owned
proxy or upstream provider support may be needed where a URL cannot express
that policy. Until then, describe the checkbox as applying to CGI only and
document the media behavior. Consider verified TLS or certificate pinning for
new configurations, with an explicit self-signed-certificate choice. Preserve
existing users' settings during migration.

**Regression:** Exercise verified and self-signed certificates for both an IP
address and a hostname through actual go2rtc and HLS transports.

### F03 — Setup accepts a different camera under an existing device identity

Sources: [setup](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/backend.py:54),
[flow identity guard](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/config_flow.py:144).

Reconfiguration and reauthentication compare the returned serial with the
configured identity. Normal setup reads `GetDevInfo` but does not make that
comparison before forwarding platforms. A DHCP address reassignment or camera
replacement using the same credentials can therefore bind a different camera
to the old entity IDs, device, and automations.

The reproduction configured `expected_channel_0` and returned serial
`different` from `device_info()`. Setup returned `True` and forwarded platforms.
This is also a privacy concern because the existing camera entity's permissions
would now apply to a different physical camera.

**Fix:** Validate hardware identity before constructing runtime services and
forwarding platforms. Reject missing or mismatched serials for entries that have
a hardware identity, and surface an actionable setup error or repair. Keep an
explicit compatibility path for legacy address-based entries.

**Regression:** Normal setup must reject a replacement camera, not just the
reconfigure and reauth flows. Include missing serials and legacy entries.

### F04 — Stream-only camera reboot recovery retains revoked tokens

Sources: [token cache](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:193),
[FLV source](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:296),
[refresh timer](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/stream.py:149),
[existing reboot test](/Users/ricardo/Documents/GitHub/VideoLink/tests/ha/test_camera_restart.py:78).

After `GetNetPort` is cached, `flv_url()` normally performs no device request
until the cached token's lease expires. A reboot revokes the camera's token,
but the stream refresh timer continues returning the old URL. Media transport
authentication failures are not fed back to the API client to invalidate it.
With the tested one-hour lease, recovery may be delayed until the remaining
lease expires unless another CGI request discovers the revocation.

The local camera reproduction changed its token generation after the first
FLV URL was resolved. A subsequent refresh returned the same invalidated URL
and made **zero network requests**. The current reboot test explicitly requests
a snapshot after restarting the camera, which refreshes the token and hides
this case. The independent doorbell transport can recover while the CGI client
continues holding its stale token.

**Fix:** Connect stream authentication failures and validated device reconnects
to token invalidation and source refresh. If the provider cannot report
authentication failures, add a bounded validation/recovery mechanism while a
stream is in use. Avoid unconditional login every 30 seconds.

**Regression:** Reboot with only a WebRTC or HLS viewer active. Do not request a
snapshot or manually expire the client token. Assert prompt token replacement
and media recovery.

### F05 — Camera availability does not recover with stream source acquisition

Sources: [availability](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/camera.py:54),
[source error handling](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/stream.py:125).

Snapshot and source errors set `_attr_available=False`. Only a successful
snapshot sets it back to `True`. Successful later source acquisition, including
a new CGI login, never clears the failure flag. The camera inherits HA's
non-polling camera behavior, and doorbell health is not connected to camera
health.

With the real HA Camera base class, a source failure followed by a successful
source request left `camera.available` false. A stream can recover while the
entity remains unavailable. When the first source resolution fails, `_url` is
also `None`, so the integration's periodic refresh returns immediately.

**Fix:** Introduce an explicit camera health/recovery signal and update it on
validated recovery. Define how snapshot health and stream health contribute to
availability. Simply constructing an RTSP URL is not proof of device health.
Use a bounded recovery check where no active consumer can produce one. Log
camera unavailability and recovery once per transition, as the doorbell service
already does. This follows HA's
[availability guidance](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable/).

**Regression:** A transient failure must recover through normal streaming or
health updates without calling `async_camera_image()` directly in the test.

### F06 — Microphone release waits for remote audio acknowledgements

Sources: [microphone stop](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:932),
[native capture stop](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1184).

`_stopMicrophone()` awaits `_stopNativeTalkCapture()` before stopping microphone
tracks. Native capture teardown awaits every pending WebSocket send without a
deadline. A slow camera restart or an unacknowledged command on a still-open
socket therefore delays release of the hardware microphone, AudioContext,
talking state, and subsequent card cleanup.

The real card method was exercised with one unresolved audio acknowledgement.
The microphone track remained open until that acknowledgement was resolved.
The audio graph is disconnected before waiting, so this reproduction establishes
continued microphone acquisition, not continued transmission of speech.
Detected socket loss normally rejects commands in the official HA connection
library; that explains why the existing disconnect tests pass.

**Fix:** Stop microphone tracks, disable capture callbacks, and release local
audio resources before awaiting remote work. Settle remote requests separately
with a deadline and handle late completions by generation/session ID.

**Regression:** Release PTT, remove the card, and hide the page while an audio
acknowledgement is deliberately held on an otherwise connected socket. Local
microphone tracks must stop immediately.

### F07 — Changing cameras during native startup leaks the old session

Sources: [configuration change](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:123),
[session startup](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1115),
[session stop](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1202).

Native startup captures the HA object but continues reading the mutable
`this._config.entity` after asynchronous waits. If the card changes from camera
A to camera B while A's start command is pending, the result is subscribed and
stopped using B's entity ID and A's token. Subscription fails; stopping B does
not release A; then the local token is discarded. The server's startup cleanup
for A remains until that HA socket disconnects or the entry unloads.

The focused card reproduction produced exactly
`start:camera.a`, `subscribe:camera.b`, `stop:camera.b`, with A's dummy session
still active and the card's local token cleared.

**Fix:** Capture entity ID, HA connection, token, and generation in an immutable
session object. Use that object's identity for every subscribe and cleanup
operation. When configuration changes, explicitly release any late startup
result for the original camera before accepting a new session.

**Regression:** Change the entity while native startup is pending, and while
subscription acknowledgement is pending. Verify the original camera session is
released on the same still-connected HA socket.

### F08 — A card cannot recover after another card takes its native session

Sources: [automatic claim](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1123),
[ready flag](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1118),
[audio errors](/Users/ricardo/Documents/GitHub/VideoLink/frontend/videolink-doorbell.js:1098).

Opening a native card automatically claims the device's single session. Server
ownership checks correctly reject audio from the former owner, but that card
keeps `_nativeSessionReady=True` and its stale token. Later PTT attempts skip
acquisition. Audio errors only update diagnostics, so the default UI can show
talking even though every outgoing frame is rejected.

The focused reproduction rejected an audio command with “session is no longer
active.” The card retained its ready flag and stale token and continued capture.
The existing second-card test verifies rejection of the old token but does not
verify how the displaced card recovers.

**Fix:** Notify the displaced consumer or recognize a specific session-lost
error. Stop capture, invalidate readiness, and display that the camera is in
use. Allow an explicit user action to reacquire the session. Avoid automatic
reclaim loops between two visible cards.

**Regression:** Open two native cards for one camera, displace the first, then
use PTT on it again. Assert accurate UI state and a deliberate recovery path.

### F09 — The native frame timeout excludes the header

Source: [TCP receive](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/native_talk.py:547).

`readexactly(20)` runs before the frame timeout starts. A peer that sends a
partial header and keeps the connection open can leave the background mix
reader waiting indefinitely without setting its failure state. Login and
control-response callers have outer deadlines, so those operations are already
bounded; the gap is the unsolicited reader.

The reproduction fed one header byte, reduced the frame timeout to 10 ms, and
confirmed that receive remained pending after 30 ms.

**Fix:** Bound partial-header assembly and add a receive-health deadline for
streams expected to produce continuous audio. If idle connections are allowed,
distinguish allowed idle time from an incomplete frame. Preserve cancellation
and final socket closure.

**Regression:** Test partial headers, silent half-open connections, and complete
frames arriving close to the deadline.

### F10 — Camera responses and negotiated audio profiles lack sufficient limits

Sources: [JSON read](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:134),
[snapshot read](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:277),
[profile parser](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/native_talk.py:972),
[profile validation](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/native_talk.py:775),
[tone allocation](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/api.py:422).

JSON and snapshot requests buffer the entire decoded response without a byte
limit. A total timeout bounds duration, not allocation size. The native TCP body
does have a useful 1 MiB limit, but audio profile numbers parsed from it have no
practical upper bounds. Only positivity of sample rate and frame length is
checked, after sending the configuration to the camera.

A synthetic camera profile with a **1,000,000,000 Hz** sample rate, **8-bit**
precision, **3 samples per frame**, and **stereo** passed negotiation and
`open_talk()`. The sender actually requires even-length mono PCM16 frames.
The tone command allocates a Python sample list proportional to the negotiated
sample rate; malformed values can therefore exhaust memory or block HA's event
loop. No large allocation or denial-of-service payload was executed.

**Fix:** Apply separate decoded-byte limits to CGI JSON and JPEG responses.
Validate supported sample rates, PCM precision, track count, and a small even
frame size before opening talk or returning values to the browser. Reject
oversized encoded WebSocket PCM before decoding it. Bound concurrent benchmark
tone commands and consider a frame-count limit for raw capture in addition to
its approximate byte budget.

**Regression:** Oversized and compressed HTTP responses, extreme and negative
numeric profiles, odd frame lengths, unsupported precision/tracks, and large
encoded PCM must fail safely without proportional allocation.

### F11 — Development HTTP dependency is vulnerable; standalone metadata allows affected releases

Sources: [development pin](/Users/ricardo/Documents/GitHub/VideoLink/requirements-test.txt:1),
[standalone dependency](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/pyproject.toml:13).

The unit environment pins aiohttp `3.11.11`, and the standalone client permits
it through `aiohttp>=3.11.11,<4`. The maintainer reports a client C-parser memory
error affecting releases through `3.14.2`, fixed in `3.14.3`; a malicious or
malformed server response can cause denial of service. See the
[aiohttp maintainer advisory](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-cq5v-8q36-5273).

The HA baseline exercised here already uses `3.14.3`. Thus the obsolete test pin
is not evidence that this HACS integration downgrades HA's runtime aiohttp.
The risk concerns development installations and a standalone client installed
alongside an already-satisfied older dependency.

**Fix:** Update development pins and the standalone client's supported minimum
to an appropriate patched release. Add a scheduled dependency advisory check,
and test the supported minimum as well as the HA-managed version. Continue
respecting HA's ownership of shared runtime dependencies.

## Architecture and Home Assistant practices

| Area | Assessment |
| --- | --- |
| Runtime data | Good: typed `ConfigEntry[VideolinkRuntime]`, entry-owned lazy services, and no separate global device cache |
| Setup failures | Good: device validation before platform forwarding, auth vs transient failure distinction, and rollback after partial forwarding |
| Unload/shutdown | Good foundation: explicit cancellation, resource cleanup, native ownership, and bounded cleanup operations; browser gaps are F06–F08 |
| Shared HTTP session | Good: HA supplies the aiohttp session and the protocol client does not close a shared session |
| Entity identity | Serial/channel identifiers and shared physical-device identifiers are sensible; F03 leaves a setup-time identity gap |
| Doorbell events | Good: standard doorbell event class/type, translated name, rising-edge detection, and subscriptions established when entities are added |
| Disabled entities | Good: disabled event entities do not connect, and disabled cameras are rejected by custom native commands |
| Camera platform | Standard `Camera` and stream feature used; availability and restart recovery need F04–F05 |
| WebSocket authorization | Good: HA user permissions, camera domain/platform, enabled state, loaded entry, and runtime checks; no unauthenticated native-command path found |
| Native ownership | Good server protection: unpredictable session tokens, explicit takeover, and old-owner audio/cleanup rejection |
| Diagnostics | Good: allowlisted configuration, identity/credential redaction, cached health only, no device I/O, URLs, tokens, audio, or raw capture |
| Frontend rendering | No unescaped camera title or error inserted into HTML found: those use `textContent`; interpolated layout values are enumerated |
| Protocol separation | Good extraction into an independently buildable package, but the HACS integration still vendors it |
| go2rtc boundary | Isolated and tested, but depends on private Core implementation details on the actual supported baseline |
| Config flows | Reauth/reconfigure and duplicate checks exist; initial setup success and several error branches lack test coverage |
| Documentation | Detailed operation and development guidance; removal, supported hardware/firmware, and some configuration claims need improvement |
| CI | Good separation of unit, real HA, and browser jobs; action SHAs pinned and workflow permissions limited to contents read |

A `DataUpdateCoordinator` is not required merely because this is an integration.
The push subscription and on-demand snapshots are reasonable. A shared health
service or a small coordinator could help implement F04–F05, but should serve a
specific recovery need rather than add polling to every entity.

The current HACS implementation should not be represented as Core-ready or as
meeting a complete quality tier. Home Assistant's
[quality requirements](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
include full config-flow testing, documentation requirements, and higher-tier
coverage and reliability requirements. This is an advisory assessment, not an
official Home Assistant rating.

### Further improvements

1. **Complete config-flow coverage through the real flow manager.** Current
   statement coverage is 80.8%, branch coverage 72.2%. The successful initial
   creation path at `config_flow.py:182` is not executed by the existing suite.
   Include initial forms, every validation failure, both duplicate checks,
   source-only reconfigure, reauth failures, and migration/identity variants.
   HA calls for
   [full config-flow coverage](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage/).
   Add meaningful coverage gates; the current CI only runs pass/fail tests.
   The native login sequence at `native_talk.py:637` is also unexecuted in the
   complete suite. Add a local fake Baichuan server to exercise the real
   transport, negotiation, login rejection, and encryption modes together.

2. **Test upgrades against actual Core APIs.** The declared `2026.9.1` baseline
   has no `Camera.async_get_stream_sources`; the composite-registration adapter
   is the path used today. Tests for the newer public-source path partly inject
   the API with monkeypatching. Run a baseline/latest stable matrix and a
   scheduled development-version job. Keep private `_rest_client` and
   `_update_stream_source` access isolated until the public replacement is
   available. Handle unsupported provider shapes with an actionable repair or
   disabled talk controls, not a card that appears ready.

3. **Finish the public library boundary for a Core submission.** Publish the
   protocol client, declare its release, and replace bundled imports. Remove
   the HACS dashboard installer and its frontend/Lovelace dependencies from
   that distribution, as the existing README already proposes. HA requires
   [transparent, independently accessible dependencies](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dependency-transparency/).
   Several callback and return types remain untyped despite `py.typed`; add a
   real type-checking job before claiming strict typing.

4. **Make concurrency policy explicit.** Declare `PARALLEL_UPDATES` on platforms
   with an explanation, and place a client-level bound/cache around snapshot
   traffic if needed. HA's
   [parallel-update convention](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates/)
   does not itself rate-limit custom WebSocket commands or every camera image
   request. Existing login, stream, and native locks are useful foundations.

5. **Close CGI login sessions deliberately.** Config-flow validation creates a
   login token and abandons it; entry shutdown stops native talk but has no CGI
   logout. Add a client close/logout operation with clear ownership and a
   deadline, used by temporary validation and runtime teardown. Whether this
   causes login-slot exhaustion depends on the device firmware and was not
   established in this audit.

6. **Retain form input after validation errors.** Initial setup calls `_schema()`
   with no submitted defaults; reconfigure rebuilds from old entry data. Pass
   submitted values back as suggestions so users do not have to repeat host,
   stream, and advanced settings after a typo. Handle secret fields deliberately.

7. **Resolve documentation and removal gaps.** README says channel settings can
   be reconfigured, but the schema exposes no channel selector and new entries
   force channel zero. Either expose and validate supported channels or state
   the restriction. Add tested model/firmware
   coverage and removal instructions, including removal of the automatically
   created Lovelace resource after uninstall. The persisted resource has no
   automatic final-entry removal hook. HA documents this expectation in its
   [removal rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-removal-instructions/).

8. **Make developer tools portable and keep their errors private.** The older
   benchmark contains an installation-specific HA hostname and device
   identifier. Replace these with required CLI arguments or environment values.
   Prefer prompts/environment values over password CLI arguments, and redact
   RTSP URLs from subprocess failures and report files. Dummy credentials in
   tests are expected and were not treated as exposed production secrets.
   The standalone RTSP test sender also needs header/body size limits and
   absolute I/O deadlines; streaming switches its socket to blocking mode with
   no timeout, so a stalled write can hang a benchmark worker.

9. **Keep protocol security limitations explicit.** Native Baichuan uses a raw
   TCP transport and camera-specific digest/AES-CFB framing, with no TLS peer
   authentication or message authentication tag. RTSP/RTP talkback also uses
   the configured unencrypted transport. These are protocol limitations rather
   than evidence of an AES implementation bug. Use authenticated transports or
   a secure relay where supported, and document that enabling CGI certificate
   verification does not secure these paths.
   In the current [native audio serializer](/Users/ricardo/Documents/GitHub/VideoLink/custom_components/videolink_doorbell/videolink_client/native_talk.py:351),
   outgoing ADPCM media is sent without encryption even when the XML extension
   is encrypted. A passive capture of that TCP stream can expose outgoing
   speech; AES-decrypted incoming mix frames do not establish confidentiality
   for the outgoing direction.

## Dependency and packaging observations

OSV returned no advisories for the queried `reolink-aio==0.21.17`,
`go2rtc-client==0.4.0`, and `aiohttp==3.14.3` versions. This is a point-in-time
database result, not a guarantee about the packages or their transitive
dependencies. aiohttp `3.11.11` returned multiple advisory matches; F11 uses a
maintainer-confirmed client issue rather than treating all server issues as
reachable from this integration.

OSV also returned three distinct advisory candidates for cryptography `48.0.1`
(plus duplicate PYSEC records). Two concern its X.509 verifier and one concerns
PKCS#7 decryption. This integration uses AES-CFB primitives and does not call
those APIs; aiohttp's TLS verification uses Python/OpenSSL. The two X.509
maintainer pages list an affected range through `48.0.0`, inconsistent with the
OSV match for `48.0.1`. These results were not classified as demonstrated
integration vulnerabilities. Relevant primary references are the
[certificate-chain advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-jwv3-5hgf-82ww),
[name-constraint advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-m2h6-j472-rp4c),
and [PKCS#7 advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5).

The flexible `reolink-aio>=0.21.15` runtime requirement deliberately permits
Home Assistant's shared version; replacing it with an arbitrary strict pin
would risk conflicts with the built-in Reolink integration. Add compatibility
coverage for the stated minimum and future HA-managed releases. The tested
version was `0.21.17`.

The standalone client built a source distribution and a wheel containing the
protocol modules, typing marker, package metadata, and license. The separate
card bundle check passed. The integration/card version difference is explained
in the changelog and is intentional. No stale bundled-card finding is raised.

## Suggested implementation order

1. Block credential-bearing redirects, clarify/enforce TLS scope, and validate
   setup identity: F01–F03.
2. Fix token invalidation and camera availability recovery: F04–F05.
3. Stop local microphone resources immediately and make native sessions retain
   immutable identities with explicit ownership-loss handling: F06–F08.
4. Bound partial frames and untrusted allocations; validate audio formats and
   refresh development dependencies: F09–F11.
5. Add the listed regression cases, real config-flow coverage, upgrade testing,
   and documentation/removal improvements.

## Reproduction artifacts

Focused probes were written outside the repository and used only dummy camera
data and loopback endpoints:

- [Python probes](/private/tmp/videolink-audit-repro.py)
- [Browser method probes](/private/tmp/videolink-audit-repro.js)
- [Full coverage JSON](/private/tmp/videolink-audit-full-coverage.json)

Observed Python results:

```text
redirect: verify_ssl=True; cross_origin_received_plaintext_password=True
reboot: same_invalidated_url=True; refresh_network_requests=0
availability: source_succeeded=True; camera_available_after_success=False
identity: setup_accepted_different_serial=True; platforms_forwarded=1
partial_header: still_pending_after_frame_deadline=True
profile: rate=1000000000; precision=8; frame_samples=3; tracks=stereo; accepted
```

Observed browser method results:

```text
pending_audio_ack: microphoneStoppedBeforeAck=false
camera_change_during_start:
  requests=[start:camera.a, subscribe:camera.b, stop:camera.b]
  ownerAActive=true; localToken=null
takeover_recovery:
  sessionStillMarkedReady=true; tokenStillStale=true; stillCapturing=true
```

These temporary files are available in the audit workspace until its temporary
directory is cleaned. The report contains the triggers and expected regression
behavior needed to turn each reproduction into a maintained test.
