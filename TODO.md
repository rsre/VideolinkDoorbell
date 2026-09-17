# TODO

## Native talk parity and latency

- [ ] Capture a known-good official-app talk packet and compare the native
      media header and ADPCM payload byte-for-byte.
- [x] Add an analyzer for the existing official-app relay trace. It confirms
      683-byte writes, a stable 24-byte Baichuan header, and approximately
      64 ms packet cadence; payload bytes were intentionally not retained.
- [x] Make the verified native TalkAbility request the default CLI behavior;
      no feature flag is required.
- [ ] Reproduce the SDK `AudioTalkOpen` request and acknowledgement flow.
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
      `mixAudioStream`.
- [ ] Wire the AEC-cleaned mix callback PCM into HA playback during native
      talk, while muting the duplicate WebRTC audio stream.
- [ ] Confirm native stop/close behavior and recovery after interruption.

## Verification rule

Test each change against the currently working version. Do not combine native
protocol, codec, buffering, and browser-capture changes in one experiment.
Commit only after the audio remains intelligible and non-choppy in an
end-to-end test.
