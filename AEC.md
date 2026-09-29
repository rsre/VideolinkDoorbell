# Reolink app echo processing: feasibility notes

The card currently plays validated native `farEndData` (doorbell microphone)
PCM and sends the browser microphone to the native talk socket. It does **not**
run the Reolink app's acoustic echo canceller (AEC). By default the card mutes
incoming audio while push-to-talk is held; disabling that option relies on
browser/device echo cancellation or headphones.

## What the APK does

The inspected Android APK's `TalkStreamAecHandler` reads two 2048-byte PCM16
buffers from each SDK mix callback. Direct camera probing identified the
`farEndData` buffer as camera microphone audio and `nearEndData` as the local
outgoing talk signal on this doorbell. The handler calls
`echoCancellationByJni(nearEndData, farEndData)` and publishes its result to
the app's talk-audio observer, which sends it to the app audio player. This is
**not** the same operation as simply decoding the native camera-audio stream.
The practical effect of that processed playback, and whether the same signal
pair would cancel browser speaker-to-microphone feedback, still needs a
source-separated live test.

For the normal IPC path, the handler processes 1024-sample (64 ms at 16 kHz)
frames. It converts PCM16 to floating point, calls native
`libJniAudio.so`/`JniAec.preProcess` on both buffers, retrieves a feature
tensor, runs `aec_model.onnx`, updates recurrent noise-suppression/AEC/AGC
state, calls `JniAec.postProcess`, and converts the output to PCM16. The model
input `feat` has shape `1 x 9 x 257 x 8`; its outputs include audio features
and the next recurrent state. The APK also has a separate SD20 variant, so
model selection must match the camera/device path.

## What reproducing it would require

1. Capture isolated speech and playback-reference trials from the official app
   and our card. Confirm channel direction, sample alignment, output routing,
   and whether the app's processed signal actually removes the feedback heard
   in a browser. Record latency and speech-quality baselines before changing
   playback or capture.
2. Reconstruct or invoke the native JNI preprocessing and postprocessing
   exactly, including buffering, transforms, normalization, and persistent
   state. The ONNX model alone does not accept raw PCM and cannot produce
   playable PCM without these stages.
3. Match model input/output names and shapes, initialize and carry forward the
   NS/AEC/AGC tensors, and reset state at the same talk-session boundaries as
   the app. Compare intermediate tensors and final PCM with controlled APK
   captures before integrating the algorithm.
4. Decide where processing belongs. Browser-microphone AEC needs an aligned
   reference to the **actual browser speaker output** and must run before
   outbound encoding. The camera's mix callback arrives after camera/network
   buffering and may not provide a suitably aligned reference. A browser-side
   AudioWorklet/native-WASM path or an explicitly timed backend path would
   need separate latency and CPU evaluation.
5. Review rights to redistribute the APK's proprietary ONNX model and JNI
   library, plus Home Assistant host-architecture/runtime compatibility. A
   clean-room or licensed alternative may be required for a releasable feature.

This is a substantial research and DSP implementation project, not a small
decoder change. The first useful milestone is the source-separated app/card
capture in step 1; if it does not show a benefit over browser AEC, reproducing
the proprietary pipeline would not be justified. No proprietary model or
binary is included in the distributable integration.

## Browser AEC baseline

Open `tools/aec_browser_measure.html` through a local server on the same
computer used for the Home Assistant card:

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Then visit `http://127.0.0.1:8765/tools/aec_browser_measure.html`. Use the
normal speakers and microphone, keep the room quiet, and run the test. It
plays four known tones in an Off/On/Off sequence with the card's noise
suppression and automatic gain settings. The page reports the speaker tone
power measured at the microphone, the attenuation with browser echo
cancellation enabled, and the browser's actual track settings. It keeps no
audio recording and sends no data to Home Assistant or another server.

This is a browser speaker-to-microphone baseline. It does not compare the
Reolink app's JNI/ONNX output or prove full-duplex quality with native mix
playback. A later source-separated test must make the camera feed a known
signal into the browser speakers while the browser microphone is recorded.

On 2026-09-29, a live test on the Home Assistant browser device at 44.1 kHz
used the normal speakers and microphone at 8% test volume. The two AEC-off
passes measured the four test tones at -44.0 and -42.9 dB in the browser's
microphone spectrum (relative digital levels, not calibrated sound pressure).
With AEC on, the tone bins fell below the measured noise floor. The page
reported **at least 45.2 dB** of attenuation and confirmed that the browser
applied the requested `echoCancellation` settings. This supports using browser
AEC for local speaker echo on this device. It does not establish how much
near-end speech survives while AEC is active, or how it handles actual camera
audio during simultaneous talk and listen.
