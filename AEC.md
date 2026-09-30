# Echo cancellation decision

The native Baichuan talk path plays the doorbell microphone from the validated
`farEndData` mix channel and sends the browser microphone to the camera. It
does not run the Reolink app's proprietary acoustic echo canceller (AEC). After
testing the official app's AEC, we decided its benefit does not justify
reproducing the JNI/ONNX pipeline in this integration.

The card defaults to `mute_while_talking: true` to prevent local speaker audio
from reaching the microphone. When simultaneous listening and talking is
needed, the card requests browser echo cancellation for native talk capture.
Headphones are another way to avoid local speaker echo.

## Evidence

The inspected Android APK's `TalkStreamAecHandler` reads two 2048-byte PCM16
buffers from each SDK mix callback. Direct camera probing identified
`farEndData` as doorbell microphone audio and `nearEndData` as the local
outgoing talk signal. The app calls `echoCancellationByJni(nearEndData,
farEndData)` and plays the processed result. The normal IPC path uses
`libJniAudio.so` preprocessing and postprocessing around `aec_model.onnx`,
with recurrent noise suppression, AEC, and gain state. The model alone cannot
process raw PCM. No proprietary model or binary is included here.

On 2026-09-29, [the local browser test](tools/aec_browser_measure.html) played
four tones through the Home Assistant browser device's speakers at 8% test
volume and measured their level at the microphone with browser AEC off, on,
then off. The off passes measured -44.0 and -42.9 dB relative digital levels.
With AEC on, the tones fell below the measured noise floor, giving a lower
bound of 45.2 dB attenuation. The browser reported that it applied the
requested `echoCancellation` settings. This test measures local speaker echo;
it does not measure speech quality or prove full duplex performance with
simultaneous camera audio.
