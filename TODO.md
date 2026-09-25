# TODO

## Firmware parity audit (2026-09-24)

Live benchmark on 2026-09-25 (`/tmp/videolink-mix-005`): 4/5 tone detections,
469 data-bearing 202/200 frames, no raw-capture drops. All 469 decrypted
payload prefixes contain a 64-byte `01dcH264` container header describing
two 2048-byte regions at offsets 0 and 2048. The fifth run had only empty
talk acknowledgements and needs separate investigation.

After deploying the mix decoder, `/tmp/videolink-mix-006` and `-007` each
detected the outgoing tone in 5/5 runs. All decoded candidates passed the
container/PCM guards, with no clipping. A direct channel probe then showed
`nearEndData` is the local outgoing signal (tone RMS ~7066) and `farEndData`
is the doorbell microphone (~353). The initial playback route filtered and
played the wrong buffer, causing metallic self-audio. After routing far-end
PCM to playback, `/tmp/videolink-mix-008` detected a continuous tone in 5/5
runs, and live listening confirmed that the card sounds good. Version 0.12.54
also restores the default mute-while-talking safeguard for feedback control.

Confirmed fixes in the current worktree:

- [x] Treat 202/200 data messages and empty 202 acknowledgements separately;
      neither can satisfy the stop command. Bound the control response queue,
      match stop by message ID, and surface reader failure to the sender.
- [x] Stop interpreting the encrypted 4160-byte native wire body as PCM.
      Decrypt and validate its observed 64-byte container before extracting
      two 2048-byte regions; retain WebRTC fallback for invalid/silent frames.
- [x] Negotiate WebRTC incoming audio as `recvonly` in native-talk mode, so the
      silent PCMU keepalive cannot transmit alongside Baichuan speech.
- [x] Buffer opt-in raw receives on Home Assistant with a 16 MiB cap and fetch
      them after trials; report frames dropped at the cap.
- [x] Add non-playback AES-CFB probes for recognizable XML extensions and
      native media magic in data-bearing 202 frames. These counters can guide
      the next on-device capture without exposing session keys or raw audio.
      The SDK decrypt routine uses the same fixed `0123456789abcdef` IV as
      the repository's AES-CFB helper.
- [x] Require Home Assistant `POLICY_CONTROL` permission on the camera entity
      for every native talk WebSocket action, including raw capture retrieval.
- [x] Bound native control acknowledgements to ten seconds, including login,
      ability discovery, open, and stop; match open/stop by message ID.

Still requires a verified camera/app capture or on-device test:

- [x] Validate the decoded 202/200 far/near channel orientation with a direct
      camera probe and verify the corrected playback path by benchmark and
      live listening. Full-duplex echo behavior still depends on browser/device
      cancellation or headphones when mute-while-talking is disabled.
- [ ] Compare complete outgoing official-app packets, including six-byte
      extension/header difference, message number, ADPCM block and predictor
      state. The present 677-byte outgoing packet is audibly working, but is
      not byte-identical to the app's observed 683-byte packet.
- [ ] Validate the SDK binary TalkAbility query (operation 2157) against the
      camera. The current XML query selects an ADPCM profile but is not the
      app's exact call path.
- [ ] Decide whether SDK ONNX/JNI AEC can be legally and practically reused.
      The Python LMS filter is not model parity and has been removed from the
      incoming playback path; do not re-enable it without channel-direction
      and quality validation.
- [ ] Test session open/close at PTT boundaries against the current pre-open
      design. Pre-open improves response time but differs from the app.
- [ ] Verify native `recvonly` WebRTC negotiation and captured raw-frame
      benchmark performance on the installed Home Assistant/camera.

## Native talk parity and latency

- [ ] Capture a known-good official-app talk packet and compare the native
      media header and ADPCM payload byte-for-byte.
- [x] Add an analyzer for the existing official-app relay trace. It confirms
      683-byte writes, a stable 24-byte Baichuan header, and approximately
      64 ms packet cadence; payload bytes were intentionally not retained.
- [x] Make the verified native TalkAbility request the default CLI behavior;
      no feature flag is required.
- [x] Reproduce the SDK `AudioTalkOpen` request and acknowledgement flow:
      send the selected `BC_TALK_CONFIG`, wait for its acknowledgement, then
      register the mix callback when `mixAudioStream` is selected.
- [x] Test the SDK-style 20-byte `AudioTalkOpen` control header. This camera
      acknowledges configuration but resets the connection on the first audio
      packet; keep the verified 24-byte path.
- [ ] Verify the native ADPCM media-header fields independently:
      `01wb` magic, block size, cumulative size, sample count, index, and
      predictor.
- [ ] Verify ADPCM predictor/index state across consecutive 1024-sample
      frames.
- [x] Add an independent regression test confirming consecutive DVI-4 blocks
      carry the encoder's predictor/index state into the next block header.
- [ ] Measure microphone callback cadence and packet arrival cadence.
- [x] Add CLI measurements for native audio TCP write duration and inter-frame
      cadence.
- [x] Pace CLI frames against absolute 64 ms deadlines so TCP write time does
      not accumulate into the audio cadence.
- [ ] Add timestamps for capture, Home Assistant/WebSocket enqueue, TCP send,
      camera response, and audible playback.
- [ ] Implement the SDK-equivalent mix-audio callback before selecting
      `mixAudioStream`; receive, split, and AEC-process the far/near PCM
      buffers, then verify the result is used by the integration. The HA
      subscription is established immediately after open acknowledgement;
      SDK AEC parity remains.
- [ ] Wire verified AEC-cleaned mix callback PCM into HA playback during native
      talk. The earlier synthetic callback path did not decode camera wire PCM.
- [x] Confirm native stop/close behavior and recovery after interruption;
      verified across three consecutive camera sessions.

## Verification rule

Test each change against the currently working version. Do not combine native
protocol, codec, buffering, and browser-capture changes in one experiment.
Commit only after the audio remains intelligible and non-choppy in an
end-to-end test.
