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

test("native mix is used only until a WebRTC audio track arrives", () => {
  const card = new CardUnderTest();
  card._video = { muted: false };
  card._nativeMixAvailable = true;
  card._remoteStream = { getAudioTracks: () => [] };
  card._muted = false;
  card._updateNativeAudioRoute();
  assert.equal(card._nativeUsesMix, true);
  assert.equal(card._video.muted, true);
  card._remoteStream = { getAudioTracks: () => [{ readyState: "live" }] };
  card._updateNativeAudioRoute();
  assert.equal(card._nativeUsesMix, false);
  assert.equal(card._video.muted, false);
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
  assert.match(card._diagnosticsOutput.textContent, /Incoming audio source: Native mix \(Baichuan\)/);

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
