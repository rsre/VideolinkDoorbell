# Audit implementation

Implemented against `AUDIT.md` dated 2026-10-13. The original audit is retained
as the historical report; this file records the resulting changes and limits.

| Finding | Implementation |
| --- | --- |
| F01 | Reject all CGI/snapshot redirects and constrain benchmark auth redirects. Real trusted-TLS tests cover 307/308 to HTTP and cross-origin HTTPS. |
| F02 | Use the audit's interim scope correction: label verification as CGI-only, default new forms to verification, preserve existing entries, and document independent FLV/HLS trust policies. |
| F03 | Validate hardware identity before runtime/platform setup; reject missing/replacement serials while preserving explicit legacy address identities. |
| F04 | Validate cached CGI sessions every 30 seconds for resolved or failed sources, renewing revoked tokens through the existing bounded retry; reboot regression uses only source acquisition. |
| F05 | Restore reachability availability on successful source/health checks, retry failed initial sources, validate RTSP reachability through CGI, and log transitions once. |
| F06 | Stop microphone tracks and disconnect local capture before remote work; bound remote settling to two seconds and ignore old capture completions. |
| F07 | Retain the original HA object, camera, token, and cancellation state for native startup, subscription, audio, diagnostics, tone, and cleanup. Release late startup/subscription results. |
| F08 | Return/recognize `session_lost`, stop displaced capture, show camera-in-use status, and allow explicit PTT reacquisition without background reclaim loops. |
| F09 | Apply one frame deadline to header and body, including silent/partial headers; preserve cancellation/closure. |
| F10 | Cap JSON at 1 MiB and snapshots at 8 MiB, including bounded decompression before allocation; validate mono PCM16 profiles, rates and even frames up to 4096 samples before sending configuration; reject oversized base64 before decoding; bound tones, snapshots and raw capture frame count. |
| F11 | Require aiohttp 3.14.3 in development and standalone metadata; add minimum/latest HTTP CI and scheduled dependency advisory checks. |

The additional recommendations are addressed through complete config-flow
coverage, a real fake-camera TCP login/negotiation suite, baseline/latest stable
and scheduled development-Core CI, protocol type checking, explicit platform
concurrency, snapshot limiting, bounded CGI logout on validation and teardown,
retained non-secret form settings, channel/removal/hardware/security/AEC
documentation, portable benchmark configuration, private error reports, bounded
RTSP response parsing and finite writes. Failed go2rtc provider compatibility
now disables the card's RTSP talk control with an explanatory tooltip.

The independently buildable protocol package is prepared as version 0.1.1,
and the card is synchronized at 0.12.58. Public package publication and a Core
submission/distribution are external follow-ups: HACS retains its vendored
client and installer until a public package release exists. No unpublished
runtime requirement is introduced.

## Validation and remaining external work

The local environment exercises Home Assistant 2026.9.1/Python 3.14.8, Chromium,
Python 3.12 protocol tests, Node card tests, Ruff, protocol mypy, full config-flow
coverage, and standalone wheel/source/card builds. CI matrix jobs are configured
but have not run on GitHub during this implementation.

F02's interim documentation does **not** implement integration-owned media TLS
verification, a proxy, or pinning. Actual go2rtc/HLS certificate-policy tests for
IP addresses, hostnames and self-signed certificates, live firmware/acoustic
validation, and package publication require external infrastructure. Native and
RTSP protocol security limitations are explicitly documented and unchanged.
