const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

global.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {};
  }
};
global.customElements = { get: () => undefined, define: () => undefined };
global.window = { customCards: [] };

const source = fs.readFileSync(
  "custom_components/videolink_doorbell/frontend/videolink-doorbell.js",
  "utf8",
);
vm.runInThisContext(`${source}\nglobal.CardUnderTest = VideolinkDoorbellCard;`);

test("new cards default to scaled video", () => {
  assert.equal(CardUnderTest.getStubConfig(undefined, ["camera.front_door"]).video_fit, "contain");
});

test("new cards prefill the title from the camera entity", () => {
  const config = CardUnderTest.getStubConfig(
    { states: { "camera.front_door": { attributes: { friendly_name: "Front Door" } } } },
    ["camera.front_door"],
  );
  assert.equal(config.title, "Front Door");
});

test("the editor exposes the supported video modes", () => {
  const field = CardUnderTest.getConfigForm().schema.find(({ name }) => name === "video_fit");
  assert.equal(field.selector.select.mode, "dropdown");
  assert.deepEqual(field.selector.select.options.map(({ value }) => value), [
    "cover", "contain", "fill", "full",
  ]);
});

test("talking mutes audio by default and exposes the override", () => {
  const field = CardUnderTest.getConfigForm().schema.find(
    ({ name }) => name === "mute_while_talking",
  );
  assert.equal(field.default, true);
  const card = new CardUnderTest();
  card._render = () => undefined;
  card.setConfig({ entity: "camera.front_door" });
  assert.equal(card._config.mute_while_talking, true);
  card.setConfig({ entity: "camera.front_door", mute_while_talking: false });
  assert.equal(card._config.mute_while_talking, false);
});

test("native talk mutes both playback paths before microphone permission", async () => {
  const previousSecureContext = window.isSecureContext;
  window.isSecureContext = true;
  const card = new CardUnderTest();
  card._config = { talk_mode: "native", mute_while_talking: true };
  card._streamReady = true;
  card._audioSender = {};
  card._video = { muted: false };
  card._nativeUsesMix = true;
  card._nativePlaybackGain = { gain: { value: 1 } };
  card._talkButton = { setPointerCapture: () => undefined };
  card._prepareNativeMixPlayback = () => undefined;
  card._updateTalkButton = () => undefined;
  card._updateSoundButton = () => undefined;
  card._updateDiagnosticsView = () => undefined;
  let resolveMic;
  card._getMicrophoneStream = () => new Promise((resolve) => { resolveMic = resolve; });
  const starting = card._beginTalk({ preventDefault: () => undefined, pointerId: 1 });
  assert.equal(card._muted, true);
  assert.equal(card._video.muted, true);
  assert.equal(card._nativePlaybackGain.gain.value, 0);
  await card._endTalk({ preventDefault: () => undefined });
  resolveMic({ getTracks: () => [] });
  await starting;
  assert.equal(card._muted, true);
  window.isSecureContext = previousSecureContext;
});

test("native talk resumes incoming audio after a successful PTT", async () => {
  const card = new CardUnderTest();
  card._config = { talk_mode: "native", mute_while_talking: true };
  card._audioSender = {};
  card._video = { muted: true };
  card._nativeUsesMix = true;
  card._nativePlaybackGain = { gain: { value: 0 } };
  card._muted = true;
  card._mutedBeforeTalk = true;
  card._talking = true;
  card._updateTalkButton = () => undefined;
  card._updateSoundButton = () => undefined;
  card._updateDiagnosticsView = () => undefined;
  await card._stopMicrophone();
  assert.equal(card._muted, false);
  assert.equal(card._video.muted, true);
  assert.equal(card._nativePlaybackGain.gain.value, 1);
});

test("native microphone asks browser to cancel local speaker echo", async () => {
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const requests = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async (request) => { requests.push(request); return {}; } } },
  });
  try {
    const card = new CardUnderTest();
    card._config = { talk_mode: "native" };
    await card._getMicrophoneStream();
    assert.equal(requests[0].audio.echoCancellation, true);
    card._config.talk_mode = "rtsp";
    await card._getMicrophoneStream();
    assert.equal(requests[1].audio.echoCancellation, "remote-only");
  } finally {
    if (previousNavigator) Object.defineProperty(globalThis, "navigator", previousNavigator);
    else delete globalThis.navigator;
  }
});

test("native playback unlocks on push-to-talk gesture while temporarily muted", () => {
  const previousAudioContext = window.AudioContext;
  window.AudioContext = class {
    constructor() { this.state = "suspended"; this.currentTime = 0; this.destination = {}; }
    createGain() { return { gain: { value: 0 }, connect: () => undefined }; }
    resume() { this.state = "running"; return Promise.resolve(); }
  };
  try {
    const card = new CardUnderTest();
    card._config = { talk_mode: "native" };
    card._muted = true;
    card._prepareNativeMixPlayback(true);
    assert.equal(card._nativePlaybackContext.state, "running");
    assert.equal(card._nativePlaybackGain.gain.value, 0);
  } finally {
    window.AudioContext = previousAudioContext;
  }
});

test("card style selects compact audio-only behavior", () => {
  const card = new CardUnderTest();
  card._render = () => undefined;
  card.setConfig({ entity: "camera.front_door", card_style: "audio" });
  assert.equal(card._audioOnly, true);
  assert.equal(card._controlsHidden, false);
  assert.equal(card.getCardSize(), 2);
});

test("video-only card style hides controls", () => {
  const card = new CardUnderTest();
  card._render = () => undefined;
  card.setConfig({ entity: "camera.front_door", card_style: "video" });
  assert.equal(card._audioOnly, false);
  assert.equal(card._controlsHidden, true);
});

test("blank title hides the header while omitted title remains visible", () => {
  const card = new CardUnderTest();
  card._render = () => undefined;
  card.setConfig({ entity: "camera.front_door" });
  assert.equal(card._titleVisible, true);
  card.setConfig({ entity: "camera.front_door", title: "" });
  assert.equal(card._titleVisible, false);
});

test("native mix playback mutes WebRTC audio and follows the sound control", () => {
  const card = new CardUnderTest();
  card._video = { muted: false, play: () => Promise.resolve() };
  card._updateSoundButton = () => undefined;
  card._nativeUsesMix = true;
  card._nativePlaybackGain = { gain: { value: 0 } };
  card._nativePlaybackContext = { resume: () => Promise.resolve() };
  card._muted = true;
  card._toggleSound();
  assert.equal(card._video.muted, true);
  assert.equal(card._nativePlaybackGain.gain.value, 1);
  card._toggleSound();
  assert.equal(card._nativePlaybackGain.gain.value, 0);
});

test("native talk keeps a working FLV WebRTC audio track audible", () => {
  const card = new CardUnderTest();
  card._video = { muted: true, play: () => Promise.resolve() };
  card._nativeMixAvailable = true;
  card._remoteStream = { getAudioTracks: () => [{ readyState: "live" }] };
  card._muted = false;
  card._updateNativeAudioRoute();
  assert.equal(card._nativeUsesMix, false);
  assert.equal(card._video.muted, false);
});

test("native mix replaces WebRTC only while playable frames arrive", async () => {
  const card = new CardUnderTest();
  card._config = { talk_mode: "native" };
  card._video = { muted: false };
  card._nativeMixAvailable = true;
  card._remoteStream = { getAudioTracks: () => [{ readyState: "live" }] };
  card._muted = false;
  card._nativePlaybackContext = {
    state: "running",
    currentTime: 0,
    createBuffer: (_channels, size) => ({
      duration: size / 16000,
      getChannelData: () => new Float32Array(size),
    }),
    createBufferSource: () => ({ connect: () => undefined, start: () => undefined }),
  };
  card._nativePlaybackGain = { gain: { value: 1 } };
  card._updateNativeAudioRoute();
  assert.equal(card._nativeUsesMix, false);
  assert.equal(card._video.muted, false);
  const previousSetTimeout = window.setTimeout;
  const previousClearTimeout = window.clearTimeout;
  let fallback;
  window.setTimeout = (callback) => { fallback = callback; return 1; };
  window.clearTimeout = () => undefined;
  try {
    await card._playNativeMixFrame(Buffer.from([0, 0, 0, 0]).toString("base64"));
    assert.equal(card._nativeUsesMix, false);
    await card._playNativeMixFrame(Buffer.from([100, 0, 100, 0]).toString("base64"));
    assert.equal(card._nativeUsesMix, true);
    assert.equal(card._video.muted, true);
    assert.equal(card._nativePlaybackGain.gain.value, 1);
    assert.equal(card._nativeMixFramesReceived, 2);
    assert.equal(card._nativeMixFramesScheduled, 1);
    fallback();
    assert.equal(card._nativeUsesMix, false);
    assert.equal(card._video.muted, false);
    assert.equal(card._nativePlaybackGain.gain.value, 0);
  } finally {
    window.setTimeout = previousSetTimeout;
    window.clearTimeout = previousClearTimeout;
  }
});

test("suspended native playback keeps WebRTC audio selected", async () => {
  const card = new CardUnderTest();
  card._config = { talk_mode: "native" };
  card._video = { muted: false };
  card._muted = false;
  card._nativeMixAvailable = true;
  card._remoteStream = { getAudioTracks: () => [{ readyState: "live" }] };
  let resumes = 0;
  card._nativePlaybackContext = {
    state: "suspended", resume: () => { resumes += 1; return Promise.resolve(); },
  };
  card._nativePlaybackGain = { gain: { value: 1 } };
  await card._playNativeMixFrame(Buffer.from([100, 0]).toString("base64"));
  assert.equal(card._nativeUsesMix, false);
  assert.equal(card._video.muted, false);
  assert.equal(card._nativeMixFramesReceived, 1);
  assert.equal(card._nativeMixFramesScheduled, 0);
  assert.equal(resumes, 0);
});

test("sound button unlocks native mix playback without selecting it early", () => {
  const previousAudioContext = window.AudioContext;
  let resumes = 0;
  window.AudioContext = class {
    constructor() { this.state = "suspended"; this.currentTime = 0; this.destination = {}; }
    createGain() { return { gain: { value: 0 }, connect: () => undefined }; }
    resume() { resumes += 1; this.state = "running"; return Promise.resolve(); }
  };
  try {
    const card = new CardUnderTest();
    card._config = { talk_mode: "native" };
    card._video = { muted: true, play: () => Promise.resolve() };
    card._nativeMixAvailable = true;
    card._toggleSound();
    assert.equal(resumes, 1);
    assert.equal(card._nativePlaybackContext.state, "running");
    assert.equal(card._nativePlaybackGain.gain.value, 0);
    assert.equal(card._nativeUsesMix, false);
    assert.equal(card._video.muted, false);
  } finally {
    window.AudioContext = previousAudioContext;
  }
});

test("debug view identifies the selected audio source and talkback path", () => {
  const card = new CardUnderTest();
  card._config = { talk_mode: "native" };
  card._diagnosticsOutput = { textContent: "" };
  card._nativeMixAvailable = true;
  card._remoteStream = { getAudioTracks: () => [{ readyState: "live" }] };
  card._muted = false;
  card._updateNativeAudioRoute();
  assert.match(card._diagnosticsOutput.textContent, /Incoming audio source: WebRTC camera track/);
  assert.match(card._diagnosticsOutput.textContent, /Outgoing talk path: Native Baichuan/);
  assert.match(card._diagnosticsOutput.textContent, /Audio output: unmuted/);

  card._remoteStream = { getAudioTracks: () => [] };
  card._updateNativeAudioRoute();
  assert.match(card._diagnosticsOutput.textContent, /Incoming audio source: none \(waiting for audio track\)/);
  card._setNativeMixActive(true);
  assert.match(card._diagnosticsOutput.textContent, /Incoming audio source: Native mix \(Baichuan\)/);
  assert.match(card._diagnosticsOutput.textContent, /Native mix advertised: yes/);
  assert.match(card._diagnosticsOutput.textContent, /Native mix frames: 0 received, 0 scheduled/);

  card._nativeMixAvailable = false;
  card._config.talk_mode = "rtsp";
  card._muted = true;
  card._updateNativeAudioRoute();
  assert.match(card._diagnosticsOutput.textContent, /Incoming audio source: none \(waiting for audio track\)/);
  assert.match(card._diagnosticsOutput.textContent, /Outgoing talk path: WebRTC \/ RTSP backchannel/);
  assert.match(card._diagnosticsOutput.textContent, /Audio output: muted/);
  card._video = { muted: true, play: () => Promise.resolve() };
  card._toggleSound();
  assert.match(card._diagnosticsOutput.textContent, /Audio output: unmuted/);
});

test("native mix fallback reasons are logged once and only in debug mode", () => {
  const card = new CardUnderTest();
  const previousWarn = console.warn;
  const warnings = [];
  console.warn = (message) => warnings.push(message);
  try {
    card._render = () => undefined;
    card.setConfig({ entity: "camera.front_door", talk_mode: "native", debug: false });
    card._noteNativeMixFallback("native mix PCM frames are silent");
    assert.equal(warnings.length, 0);
    card.setConfig({ entity: "camera.front_door", talk_mode: "native", debug: true });
    card._noteNativeMixFallback("native mix PCM frames are silent");
    card._noteNativeMixFallback("native mix PCM frames are silent");
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /Native mix fallback: native mix PCM frames are silent/);
    card._noteNativeMixFallback("native mix frames stopped for 500 ms");
    assert.equal(warnings.length, 2);
  } finally {
    console.warn = previousWarn;
  }
});

test("debug continuously checks missing native mix frames, including during talk", async () => {
  const previousWarn = console.warn;
  const previousSetInterval = window.setInterval;
  const previousClearInterval = window.clearInterval;
  const warnings = [];
  let watchdog;
  let watchdogStopped = false;
  console.warn = (message) => warnings.push(message);
  window.setInterval = (callback) => { watchdog = callback; return 1; };
  window.clearInterval = () => { watchdogStopped = true; };
  try {
    const makeCard = (audio_stream_mode) => {
      const card = new CardUnderTest();
      card._config = { entity: "camera.front_door", talk_mode: "native", debug: true };
      card._hass = {
        callWS: async () => ({ token: "owner", audio_stream_mode }),
        connection: { subscribeMessage: async () => () => undefined },
      };
      return card;
    };
    await makeCard("followVideoStream")._ensureNativeTalkSession();
    assert.match(warnings[0], /did not advertise mixAudioStream/);
    const card = makeCard("mixAudioStream");
    await card._ensureNativeTalkSession();
    card._nativeMixSubscribedAt = performance.now() - 2100;
    watchdog();
    assert.match(warnings[1], /no native mix PCM frames received within 2 seconds/);
    watchdog();
    assert.equal(warnings.length, 2);
    card._nativeTalking = true;
    card._nativeMixTalkStartedAt = performance.now() - 2100;
    card._nativeMixTalkStartFrameCount = 0;
    watchdog();
    assert.match(warnings[2], /no native mix PCM frames received while talking for 2 seconds/);
    card._nativeMixFramesReceived = 1;
    card._nativeMixLastFrameAt = performance.now();
    watchdog();
    assert.equal(warnings.length, 3);
    await card._stopNativeTalkSession();
    assert.equal(watchdogStopped, true);
  } finally {
    console.warn = previousWarn;
    window.setInterval = previousSetInterval;
    window.clearInterval = previousClearInterval;
  }
});

test("native card explicitly claims its talk session", async () => {
  const card = new CardUnderTest();
  card._render = () => undefined;
  card.setConfig({ entity: "camera.front_door", talk_mode: "native" });
  const messages = [];
  card._hass = {
    callWS: async (message) => {
      messages.push(message);
      return { token: "new-owner", sample_rate: 16000, samples_per_frame: 1024 };
    },
    connection: { subscribeMessage: async () => () => undefined },
  };
  await card._ensureNativeTalkSession();
  assert.equal(messages[0].action, "start");
  assert.equal(messages[0].claim, true);
  assert.equal(card._nativeToken, "new-owner");
});

test("native mode receives WebRTC audio without sending a keepalive track", async () => {
  const previousDocument = global.document;
  const previousPeer = window.RTCPeerConnection;
  const previousGlobalPeer = global.RTCPeerConnection;
  const previousSetTimeout = window.setTimeout;
  const previousStream = global.MediaStream;
  const directions = [];
  let status;
  try {
    global.document = { hidden: false };
    global.MediaStream = class { getTracks() { return []; } };
    window.RTCPeerConnection = class {
      constructor() { this.connectionState = "new"; }
      addTransceiver(kind, options) {
        directions.push([kind, options.direction]);
        return { receiver: {}, sender: { replaceTrack: async () => { throw new Error("unexpected track"); } } };
      }
      createOffer() { return Promise.resolve({ sdp: "v=0\r\n" }); }
      setLocalDescription(offer) { this.localDescription = offer; return Promise.resolve(); }
      close() {}
    };
    global.RTCPeerConnection = window.RTCPeerConnection;
    window.setTimeout = () => 1;
    const card = new CardUnderTest();
    card._render = () => undefined;
    card._startDiagnostics = () => undefined;
    card._updateTalkButton = () => undefined;
    card._updateDiagnosticsView = () => undefined;
    card._setStatus = (message) => { status = message; };
    card.setConfig({ entity: "camera.front_door", talk_mode: "native" });
    card.isConnected = true;
    card._hass = {
      callWS: async () => ({ configuration: {} }),
      connection: { subscribeMessage: () => () => undefined },
    };
    await card._start();
    assert.deepEqual(directions[0], ["audio", "recvonly"], status);
    assert.equal(card._keepaliveTrack, undefined);
    assert.equal(status, "Connecting…");
  } finally {
    global.document = previousDocument;
    window.RTCPeerConnection = previousPeer;
    global.RTCPeerConnection = previousGlobalPeer;
    window.setTimeout = previousSetTimeout;
    global.MediaStream = previousStream;
  }
});
