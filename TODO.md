# TODO

## Native talk parity and latency

- [ ] Capture a known-good official-app talk packet and compare the native
      media header and ADPCM payload byte-for-byte.
- [ ] Reproduce the SDK `AudioTalkOpen` request and acknowledgement flow.
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
