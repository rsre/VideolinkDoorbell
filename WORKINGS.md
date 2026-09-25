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
  - Detach the outbound audio track; do not continue sending silent RTP.
  - Restore the previous mute state.

The stream starts muted. With `mute_while_talking` enabled (the default),
inbound sound is muted during PTT to avoid feedback, then its previous state
is restored on release.

### Native mode: native_talk: true

When controls are visible and the card becomes ready:

- Open one persistent Home Assistant WebSocket command path.
- Backend performs the native camera login.
- Request and select TalkAbility.
- Open AudioTalkOpen using the negotiated profile.
- Subscribe to native mix/echo frames.
- Keep the native camera session warm.

When PTT is pressed:

- Apply `mute_while_talking` before requesting microphone access. By default,
  mute both the WebRTC track and native playback gain while PTT is held.
- Request microphone access.
- Capture microphone audio at 16 kHz mono.
- Convert it into the camera’s negotiated frame size, currently 1024 samples.
- Send ordered PCM frames through the Home Assistant WebSocket.
- The backend keeps at most four pending frames and drops stale frames so speech
  cannot build up seconds of delay during a network slowdown.
- The backend sends frames through one native TCP session owned by this card.
- Decode incoming 202/200 mix frames and play the far-end (doorbell microphone)
  PCM when valid. Keep FLV/WebRTC audio as a fallback until playable mix frames
  arrive or when the mix stops.
- With `mute_while_talking` disabled for full duplex, request browser echo
  cancellation for local speaker output. Its effectiveness is browser/device
  dependent; headphones are the reliable way to prevent feedback.
- Check native mix delivery throughout the session. In debug mode, report a
  distinct warning if an active PTT attempt receives no new mix frames for
  two seconds; the initial idle warning alone does not establish that mix
  audio is unavailable during talk.

When PTT is released:

- Stop microphone capture only.
- Wait for queued outbound frames to drain.
- Keep the native camera session open for the next PTT press.
- Resume incoming audio so the camera's response can be heard.

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
