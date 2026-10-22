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
  "frontend/videolink-doorbell.js",
  "utf8",
);
vm.runInThisContext(`${source}\nglobal.CardUnderTest = VideolinkDoorbellCard;`);

function keyboardEnvironment(t) {
  const previousWindow = global.window;
  const previousDocument = global.document;
  global.window = Object.assign(new EventTarget(), { isSecureContext: true });
  global.document = Object.assign(new EventTarget(), { hidden: false, hasFocus: () => true });
  const cards = [];
  t.after(async () => {
    for (const card of cards) {
      card.disconnectedCallback();
      await card._haCleanup;
    }
    global.window = previousWindow;
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  });

  return {
    card(talk_mode = "rtsp") {
      const card = new CardUnderTest();
      card.isConnected = true;
      card._config = { talk_mode, mute_while_talking: true };
      card._streamReady = true;
      card._talkButton = { disabled: false, getClientRects: () => [{}] };
      card._render = card._bindHaConnection = card._updateTalkButton
        = card._updateSoundButton = card._updateDiagnosticsView
        = card._prepareNativeMixPlayback = () => undefined;
      card._start = async () => undefined;
      card._cleanup = () => card._stopMicrophone();
      card._ensureNativeTalkSession = async () => { card._nativeSessionReady = true; };
      card._startNativeTalkCapture = async () => { card._nativeTalking = true; };
      card._stopNativeTalkCapture = async () => { card._nativeTalking = false; };
      card.attachedTracks = [];
      card._audioSender = {
        getStats: async () => new Map(),
        replaceTrack: async (track) => { card.attachedTracks.push(track); },
      };
      card.micRequests = 0;
      card.tracks = [];
      card._getMicrophoneStream = async () => {
        card.micRequests += 1;
        const track = { readyState: "live", stop() { this.readyState = "ended"; } };
        card.tracks.push(track);
        return { getTracks: () => [track], getAudioTracks: () => [track] };
      };
      cards.push(card);
      card.connectedCallback();
      return card;
    },
    key(type, properties = {}, path = []) {
      const event = Object.assign(new Event(type, { cancelable: true }), {
        key: " ", code: "Space", composedPath: () => path, ...properties,
      });
      document.dispatchEvent(event);
      return event;
    },
    flush: () => new Promise(setImmediate),
  };
}

for (const mode of ["rtsp", "native"]) {
  test(`Space opens the ${mode} microphone without button focus and release stops it`, async (t) => {
    const env = keyboardEnvironment(t);
    const card = env.card(mode);
    assert.equal(env.key("keydown").defaultPrevented, true);
    await env.flush();
    assert.equal(card._talking, true);
    assert.equal(card.micRequests, 1);
    assert.equal(card.tracks[0].readyState, "live");
    assert.equal(env.key("keydown", { repeat: true }).defaultPrevented, true);
    await env.flush();
    assert.equal(card.micRequests, 1);
    // Release must stop capture even if focus moved into an input meanwhile.
    env.key("keyup", {}, [{ matches: () => true }]);
    await env.flush();
    assert.equal(card._talking, false);
    assert.equal(card._micStream, undefined);
    assert.equal(card.tracks[0].readyState, "ended");
    if (mode === "rtsp") assert.equal(card.attachedTracks.at(-1), null);
    else assert.equal(card._nativeTalking, false);
  });
}

test("Space leaves typing, other controls and modified shortcuts alone", async (t) => {
  const env = keyboardEnvironment(t);
  const card = env.card();
  // composedPath includes controls nested inside HA's shadow roots.
  for (const node of [{ matches: () => true }, { isContentEditable: true }]) {
    assert.equal(env.key("keydown", {}, [{}, node, {}]).defaultPrevented, false);
  }
  for (const property of ["ctrlKey", "altKey", "metaKey", "shiftKey", "repeat", "isComposing"]) {
    assert.equal(env.key("keydown", { [property]: true }).defaultPrevented, false);
  }
  const prevented = new Event("keydown", { cancelable: true });
  Object.assign(prevented, { key: " ", code: "Space" });
  prevented.preventDefault();
  document.dispatchEvent(prevented);
  await env.flush();
  assert.equal(card.micRequests, 0);
});

test("Space requires an active tab and an available visible talk control", async (t) => {
  const env = keyboardEnvironment(t);
  const card = env.card();
  for (const [object, property, value] of [
    [document, "hidden", true], [document, "hasFocus", () => false],
    [card, "isConnected", false], [card._config, "card_style", "video"],
    [card._talkButton, "disabled", true], [card._talkButton, "getClientRects", () => []],
    [card, "_talking", true], [card, "_micPending", true], [card, "_toneTesting", true],
  ]) {
    const previous = object[property];
    object[property] = value;
    assert.equal(env.key("keydown").defaultPrevented, false, property);
    object[property] = previous;
  }
  await env.flush();
  assert.equal(card.micRequests, 0);
});

test("only one card handles Space and focusing a card selects it", async (t) => {
  const env = keyboardEnvironment(t);
  const first = env.card();
  const second = env.card();
  env.key("keydown");
  await env.flush();
  assert.equal(first.micRequests, 1);
  assert.equal(second.micRequests, 0);
  env.key("keyup");
  await env.flush();
  env.key("keydown", {}, [second._talkButton, second]);
  await env.flush();
  assert.equal(first.micRequests, 1);
  assert.equal(second.micRequests, 1);
  env.key("keyup");
  await env.flush();
});

for (const interruption of ["blur", "hidden", "removed"]) {
  test(`Space stops the microphone when the page is ${interruption}`, async (t) => {
    const env = keyboardEnvironment(t);
    const card = env.card();
    env.key("keydown");
    await env.flush();
    if (interruption === "blur") window.dispatchEvent(new Event("blur"));
    if (interruption === "hidden") {
      document.hidden = true;
      document.dispatchEvent(new Event("visibilitychange"));
    }
    if (interruption === "removed") {
      card.isConnected = false;
      card.disconnectedCallback();
    }
    await env.flush();
    assert.equal(card._talking, false);
    assert.equal(card._spaceHeld, false);
    assert.equal(card.tracks[0].readyState, "ended");
    document.hidden = false;
    card.isConnected = true;
    // Returning while Space is still physically held must require a new press.
    assert.equal(env.key("keydown", { repeat: true }).defaultPrevented, false);
    env.key("keyup");
    await env.flush();
    assert.equal(card.micRequests, 1);
    if (interruption === "removed") {
      assert.equal(env.key("keydown").defaultPrevented, false);
      await env.flush();
      assert.equal(card.micRequests, 1);
    }
  });
}

test("releasing Space before microphone permission resolves stops the late track", async (t) => {
  const env = keyboardEnvironment(t);
  const card = env.card();
  let resolveMic;
  card._getMicrophoneStream = () => new Promise((resolve) => { resolveMic = resolve; });
  env.key("keydown");
  await env.flush();
  env.key("keyup");
  await env.flush();
  const track = { readyState: "live", stop() { this.readyState = "ended"; } };
  resolveMic({ getTracks: () => [track], getAudioTracks: () => [track] });
  await env.flush();
  assert.equal(track.readyState, "ended");
  assert.equal(card._talking, false);
  assert.equal(card._micStream, undefined);
  assert.deepEqual(card.attachedTracks, [null]);
});

test("new cards default to scaled video", () => {
  assert.equal(CardUnderTest.getStubConfig(undefined, ["camera.front_door"]).video_fit, "contain");
});

test("audio-only packetization preserves valid SDP line termination", () => {
  const card = new CardUnderTest();
  const offer = { type: "offer", sdp: "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 0\r\na=recvonly\r\n" };
  const result = card._setLowLatencyAudioPacketization(offer);
  assert.equal(result.type, "offer");
  assert.equal(result.sdp, "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 0\r\na=recvonly\r\na=ptime:20\r\na=maxptime:20\r\n");
  assert.equal(card._setLowLatencyAudioPacketization(result).sdp, result.sdp);
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

test("video cards use content height in Sections grids", () => {
  const card = new CardUnderTest();
  card._render = () => undefined;
  for (const card_style of [undefined, "default", "video"]) {
    card.setConfig({ entity: "camera.front_door", card_style });
    assert.deepEqual(card.getGridOptions(), {
      rows: "auto", columns: 12, min_rows: 3, min_columns: 6,
    });
  }
  card.setConfig({ entity: "camera.front_door", card_style: "audio" });
  assert.deepEqual(card.getGridOptions(), {
    rows: 2, columns: 12, min_rows: 1, min_columns: 4,
  });
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

test("native audio diagnostics read the actual offer and answer directions", () => {
  const card = new CardUnderTest();
  const offer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n";
  const answer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n";
  assert.equal(card._audioSdpDirection(offer), "recvonly");
  assert.equal(card._audioSdpDirection(answer), "sendonly");
  assert.equal(card._audioSdpDirection("v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n"), undefined);
});

test("native browser queue counts frames dropped during WebSocket stalls", () => {
  const card = new CardUnderTest();
  card._nativeTalking = true;
  card._nativePendingSends.add(Promise.resolve());
  card._nativePendingSends.add(Promise.resolve());
  card._diagnostics.nativeBrowserDroppedFrames = 0;
  card._handleNativePcm(new ArrayBuffer(2048));
  assert.equal(card._diagnostics.nativeQueueOverflow, true);
  assert.equal(card._diagnostics.nativeBrowserDroppedFrames, 1);
  assert.equal(card._nativePendingSends.size, 2);
});

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test("native microphone release does not wait for an audio acknowledgement", async () => {
  const card = new CardUnderTest();
  const ack = deferred();
  let stopped = false;
  card._config = { talk_mode: "native" };
  card._micStream = { getTracks: () => [{ stop: () => { stopped = true; } }] };
  card._nativeTalking = true;
  card._nativePendingSends.add(ack.promise);
  const cleanup = card._stopMicrophone();
  assert.equal(stopped, true);
  await cleanup;
  assert.equal(card._nativeTalking, false);
  ack.resolve();
});

for (const phase of ["start", "subscribe"]) {
  test(`native camera change during ${phase} releases the original camera`, async () => {
    const card = new CardUnderTest();
    const pending = deferred();
    const messages = [];
    card._config = { entity: "camera.a", talk_mode: "native" };
    card._hass = {
      callWS: async (msg) => {
        messages.push(msg);
        if (msg.action === "start") {
          if (phase === "start") await pending.promise;
          return { token: "owner-a" };
        }
      },
      connection: { subscribeMessage: async (_handler, msg) => {
        messages.push(msg);
        if (phase === "subscribe") await pending.promise;
        return () => undefined;
      } },
    };
    const startup = card._ensureNativeTalkSession();
    await Promise.resolve();
    card._config = { entity: "camera.b", talk_mode: "native" };
    const cleanup = card._stopNativeTalkSession();
    pending.resolve();
    await Promise.all([startup, cleanup]);
    assert.ok(messages.some((msg) => msg.action === "stop" && msg.entity_id === "camera.a" && msg.token === "owner-a"));
    assert.ok(messages.every((msg) => msg.entity_id === "camera.a"));
    assert.equal(card._nativeSessionReady, false);
  });
}

test("displaced native capture invalidates readiness and can explicitly reacquire", async () => {
  const card = new CardUnderTest();
  card._config = { entity: "camera.a", talk_mode: "native" };
  card._setStatus = () => undefined;
  card._hass = {
    callWS: async (msg) => {
      if (msg.action === "audio") throw { message: "Native talk session is no longer active" };
      return { token: "new-owner" };
    },
    connection: { subscribeMessage: async () => () => undefined },
  };
  card._nativeSession = { entity: "camera.a", hass: card._hass, token: "old-owner" };
  card._nativeToken = "old-owner";
  card._nativeSessionReady = true;
  card._nativeTalking = true;
  card._handleNativePcm(new ArrayBuffer(2048));
  await Promise.allSettled([...card._nativePendingSends]);
  assert.equal(card._nativeSessionReady, false);
  assert.equal(card._nativeTalking, false);
  assert.equal(card._nativeSessionLost, true);
  await card._ensureNativeTalkSession();
  assert.equal(card._nativeSessionReady, true);
  assert.equal(card._nativeToken, "new-owner");
});
