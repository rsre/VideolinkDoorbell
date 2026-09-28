# TODO

## Official-app outgoing capture (2026-09-28)

A private Wireshark capture of the Android emulator's official Reolink app
contains 419 complete client-to-camera talk frames from one session. Every
frame is a 683-byte MSG 202 message: 24-byte Baichuan header, 131-byte
encrypted extension, and 528-byte media block. The previous serializer emitted
677 bytes with a 125-byte extension and the same 528-byte media block, so the
six-byte size difference was entirely in the extension. The app uses Baichuan
message number 0 and a constant media-header final field of 2; our sender
now matches both. The app's media header is otherwise constant, and its
ADPCM predictor/index carries correctly across all 418 frame boundaries.
An independent audit of all 419 media blocks found the same 12-byte header
fields in every frame: `01wb` magic (`0x62773130`), two 520-byte size fields,
data marker `0x0100`, and final field `2`. Serializing each captured ADPCM
block with our media serializer reproduced all 419 media blocks byte for byte.
The block itself is 516 bytes: a 4-byte predictor/index header and 512 bytes
of ADPCM nibbles, representing 1024 samples. The media header has no explicit
sample-count field; the precise meaning of its two size fields and final field
remains unconfirmed.
418 of 419 ADPCM bodies are distinct, confirming non-silent emulator input.
Mean frame interval was 63.8 ms (median 60.1 ms). The capture is kept only in
an owner-readable file under `/tmp`; no credentials or audio bytes are stored
in this repository.

The app's extension was decrypted locally using a same-session login nonce.
It is TinyXML formatted with a spaced declaration, line breaks, `binaryData`
before `channelId`, and a trailing newline. The serializer now emits this
exact 131-byte XML and a 683-byte packet. A four-frame silent camera probe
completed with mix frames before and after this extension-only change. Two
further four-frame probes isolated message number 0 and media field 2; the
camera accepted both, and packet captures verified the final `(0, 2)` fields.
A later one-second tone probe received RTSP audio but did not detect the tone.
The decoded native mix also had a silent near-end channel with both the new
and previous incrementing header values, including after closing the app.
This does not isolate a header regression. Audio delivery with the final fields
was subsequently confirmed in Home Assistant; the standalone probe discrepancy
remains unexplained. A later immediate-start 16-frame tone probe detected a
strong 440 Hz signal in native mix near-end (amplitude about 10,030), showing
that a fixed warm-up delay was not needed in that run.

The same app session resolves the SDK TalkAbility operation `2157` to a
Baichuan MSG 10 request with message number 0, a 125-byte encrypted XML
extension containing `channelId` and `chnType`, and no payload. The camera's
successful MSG 10 response carries the `TalkAbility` XML in its payload. The
integration now sends the captured request format. On-camera tests first
changed only the extension, then changed message number to 0; both returned
the ADPCM profile and allowed AudioTalkOpen.

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
- [x] Reproduce the app's 131-byte extension XML byte-for-byte. The resulting
      683-byte packet was accepted by the camera in a silent four-frame probe;
      the media block remains 528 bytes.
- [x] Test the app's constant Baichuan message number 0 and constant media
      header field 2, one change at a time. Both silent probes succeeded; the
      final wire capture showed four `(0, 2)` talk frames.
- [x] Confirm Home Assistant audio delivery with the final `(0, 2)` header
      fields. The user verified working audio after the change.
- [ ] Explain why the standalone tone probe received RTSP microphone audio but
      detected no tone and showed silent native mix near-end for both new and
      old header fields. A later immediate-start probe did detect the tone in
      native mix near-end, so the failure is intermittent.
- [x] Harden login-negotiation XML recognition: require a parseable XML
      document before accepting a decrypted candidate. This prevents random
      encrypted angle brackets from hiding the nonce. Three fresh logins and
      an on-camera TalkAbility/Open probe succeeded afterward.
- [x] Validate the SDK TalkAbility query (operation 2157) against the camera.
      The app's wire request is MSG 10 with an encrypted 125-byte XML extension
      and message number 0. Both differences were tested separately.
- [ ] Decide whether SDK ONNX/JNI AEC can be legally and practically reused.
      The Python LMS filter is not model parity and has been removed from the
      incoming playback path; do not re-enable it without channel-direction
      and quality validation.
- [x] Test session open/stop at PTT boundaries on one logged-in connection.
      Three silent cycles succeeded after fixing a duplicate mix-reader task;
      open acknowledgements took 55.5, 106.2, and 68.3 ms. Keep the card's
      pre-open design to avoid this delay on each press. Browser timing remains
      a separate measurement.
- [x] Verify native `recvonly` WebRTC negotiation on the installed Home
      Assistant/camera. The live offer/answer/negotiated directions were
      `recvonly / sendonly / recvonly`, with inbound packets and no outbound
      WebRTC audio. In one native PTT run, microphone acquisition took 1166 ms
      of 1240 ms to the first captured frame. Browser and HA queues showed
      transient pressure (browser overflow, 190 ms HA queue wait); ADPCM
      encoding took 3.7 ms and TCP write/drain 0.1 ms. After tightening both
      queues, a follow-up live run showed 3 ms microphone acquisition, 79 ms
      PTT-to-first-frame time, 64 ms callback cadence, 4 ms WebSocket ack,
      0.1 ms HA queue wait, and zero browser/HA dropped frames. This supports
      the current queue bounds for that run. The user confirmed that speech
      remained continuous and intelligible at the doorbell speaker.
- [ ] Verify captured raw-frame benchmark performance on the installed Home
      Assistant/camera.

## Native talk parity and latency

- [x] Capture complete, non-silent official-app talk packets and compare the
      native media header and ADPCM predictor/index continuity. The compressed
      audio bytes differ because the app and integration captured different PCM.
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
- [x] Verify the native ADPCM media bytes against all 419 official-app frames.
      The 12-byte header and block framing match exactly. Predictor/index are
      in the 4-byte ADPCM block header; 1024 samples follow from 512 nibble
      bytes. Semantic names for the two size fields and final field are still
      unconfirmed.
- [x] Verify the official app's ADPCM predictor/index state across consecutive
      1024-sample frames; all 418 captured frame boundaries match.
- [x] Add an independent regression test confirming consecutive DVI-4 blocks
      carry the encoder's predictor/index state into the next block header.
- [x] Measure native microphone callback and official-app packet cadence.
      The follow-up browser callback interval was 64 ms; the official app
      packet capture averaged 63.8 ms (median 60.1 ms).
- [x] Add CLI measurements for native audio TCP write duration and inter-frame
      cadence.
- [x] Pace CLI frames against absolute 64 ms deadlines so TCP write time does
      not accumulate into the audio cadence.
- [ ] Add timestamps for capture, Home Assistant/WebSocket enqueue, TCP send,
      camera response, and audible playback.
- [x] Decode the camera's mix container, verify far/near channel orientation,
      and play validated far-end PCM in the card. The HA subscription is
      established immediately after open acknowledgement; WebRTC remains the
      fallback if native mix is not playable.
- [ ] Evaluate SDK-style AEC for the local near-end microphone signal using
      far-end audio as reference. This is separate from incoming mix playback;
      JNI/model parity and browser capture/reference alignment remain unverified.
- [x] Confirm native stop/close behavior and recovery after interruption;
      verified across three consecutive camera sessions.

## Verification rule

Test each change against the currently working version. Do not combine native
protocol, codec, buffering, and browser-capture changes in one experiment.
Commit only after the audio remains intelligible and non-choppy in an
end-to-end test.
