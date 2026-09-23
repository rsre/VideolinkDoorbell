# Workings

## Hold to talk

Hold **Hold to talk** while speaking and release it to stop.
The card requests microphone access only when the control is pressed and releases the microphone immediately afterward.

## Modes

The integration should operate in two distinct talk modes.

### Standard mode: native_talk: false

Home Assistant's built-in go2rtc integration combines the FLV video producer with an RTSP audio-backchannel producer.

- Establish the normal WebRTC camera connection.
- Keep the WebRTC audio sender attached to the keepalive track.
- When PTT is pressed:
  - Request microphone permission.
  - Capture microphone audio.
  - Attach the microphone track to the WebRTC sender.
  - Let go2rtc/camera handle the backchannel.

- While transmitting, apply the normal WebRTC echo-cancellation and mute behavior.
- When PTT is released:
  - Detach and stop the microphone.
  - Restore the keepalive audio track.
  - Restore the previous mute state.

The stream starts muted; using push-to-talk keeps inbound sound muted while transmitting to avoid feedback, then enables it on release so the reply can be heard.

### Native mode: native_talk: true

When controls are visible and the card becomes ready:

- Open one persistent Home Assistant WebSocket command path.
- Backend performs the native camera login.
- Request and select TalkAbility.
- Open AudioTalkOpen using the negotiated profile.
- Subscribe to native mix/echo frames.
- Keep the native camera session warm.

When PTT is pressed:

- Unmute inbound audio immediately.
- Request microphone access.
- Capture microphone audio at 16 kHz mono.
- Convert it into the camera’s negotiated frame size, currently 1024 samples.
- Send ordered PCM frames through the Home Assistant WebSocket.
- The backend keeps at most four pending frames and drops stale frames so speech
  cannot build up seconds of delay during a network slowdown.
- The backend sends frames through one native TCP session owned by this card.
- When the camera supplies a native mix stream, play it and mute the duplicate
  WebRTC audio. Otherwise, use the WebRTC audio stream.

When PTT is released:

- Stop microphone capture only.
- Wait for queued outbound frames to drain.
- Keep the native camera session open for the next PTT press.
- Keep native inbound audio active.

When the card is hidden, removed, disconnected, or controls are disabled:

- Stop microphone capture.
- Drain pending audio.
- Unsubscribe from native mix frames.
- Send AudioTalkStop.
- Close the native camera session.
- Close native playback resources.

The WebSocket subscription also closes its owned native session if the browser
disconnects before the card can send AudioTalkStop.

The key architectural principle is:

Card lifecycle controls native-session lifecycle.
PTT lifecycle controls microphone-capture lifecycle.

The native login and negotiation should not happen on every PTT press or audio frame.
