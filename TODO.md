# TODO

## Native talk parity and latency

- [ ] Capture a known-good official-app talk packet and compare the native
      media header and ADPCM payload byte-for-byte.
- [x] Add an analyzer for the existing official-app relay trace. It confirms
      683-byte writes, a stable 24-byte Baichuan header, and approximately
      64 ms packet cadence; payload bytes were intentionally not retained.
- [ ] Reproduce the SDK `AudioTalkOpen` request and acknowledgement flow.
- [x] Test the SDK-style 20-byte `AudioTalkOpen` control header. This camera
      acknowledges configuration but resets the connection on the first audio
      packet; keep the verified 24-byte path.
- [ ] Verify the native ADPCM media-header fields independently:
      `01wb` magic, block size, cumulative size, sample count, index, and
      predictor.
- [ ] Verify ADPCM predictor/index state across consecutive 1024-sample
      frames.
- [ ] Measure microphone callback cadence and packet arrival cadence.
- [ ] Add timestamps for capture, Home Assistant/WebSocket enqueue, TCP send,
      camera response, and audible playback.
- [ ] Implement the SDK-equivalent mix-audio callback before selecting
      `mixAudioStream`.
- [ ] Confirm native stop/close behavior and recovery after interruption.

## Verification rule

Test each change against the currently working version. Do not combine native
protocol, codec, buffering, and browser-capture changes in one experiment.
Commit only after the audio remains intelligible and non-choppy in an
end-to-end test.
