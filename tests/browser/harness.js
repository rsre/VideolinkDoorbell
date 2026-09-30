import { createConnection, createLongLivedTokenAuth } from "home-assistant-js-websocket";

// The native commands use the actual HA endpoint and official connection
// library. Only the camera's WebRTC media/signaling boundary is emulated,
// with a real in-browser peer pair sending a silent audio track.
window.mountCard = async (token, entityId) => {
  const connection = await createConnection({
    auth: createLongLivedTokenAuth(location.origin, token),
  });
  const peers = new Map();
  const pendingCandidates = [];
  const events = [];
  connection.addEventListener("disconnected", () => events.push("disconnected"));
  connection.addEventListener("ready", () => events.push("ready"));

  const callWS = async (message) => {
    if (message.type === "camera/webrtc/get_client_config") {
      return { configuration: { iceServers: [] } };
    }
    if (message.type === "camera/webrtc/candidate") {
      const peer = peers.get(message.session_id);
      if (peer) await peer.addIceCandidate(message.candidate);
      else pendingCandidates.push(message.candidate);
      return {};
    }
    return connection.sendMessagePromise(message);
  };

  const subscribeMessage = async (callback, message, options) => {
    if (message.type !== "camera/webrtc/offer") {
      return connection.subscribeMessage(callback, message, options);
    }
    const peer = new RTCPeerConnection({ iceServers: [] });
    const sessionId = crypto.randomUUID();
    peers.set(sessionId, peer);
    const context = new AudioContext();
    const source = context.createConstantSource();
    const gain = context.createGain();
    const destination = context.createMediaStreamDestination();
    gain.gain.value = 0;
    source.connect(gain).connect(destination);
    source.start();
    peer.addTrack(destination.stream.getAudioTracks()[0]);
    peer.onicecandidate = (event) => {
      if (event.candidate) callback({ type: "candidate", candidate: event.candidate.toJSON() });
    };
    await peer.setRemoteDescription({ type: "offer", sdp: message.offer });
    for (const candidate of pendingCandidates.splice(0)) await peer.addIceCandidate(candidate);
    callback({ type: "session", session_id: sessionId });
    await peer.setLocalDescription(await peer.createAnswer());
    callback({ type: "answer", answer: peer.localDescription.sdp });
    let closed = false;
    const disconnected = () => queueMicrotask(() => unsubscribe());
    const unsubscribe = async () => {
      if (closed) return;
      closed = true;
      connection.removeEventListener("disconnected", disconnected);
      peer.close();
      peers.delete(sessionId);
      source.stop();
      destination.stream.getTracks().forEach((track) => track.stop());
      await context.close();
    };
    // A real HA WebRTC subscription is disposed by the server on socket loss.
    connection.addEventListener("disconnected", disconnected);
    return unsubscribe;
  };
  const bridge = new Proxy(connection, {
    get(target, key) {
      if (key === "subscribeMessage") return subscribeMessage;
      const value = Reflect.get(target, key);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  const card = document.createElement("videolink-doorbell");
  card.setConfig({ entity: entityId, talk_mode: "native", card_style: "audio" });
  card.hass = {
    connection: bridge,
    callWS,
    states: { [entityId]: { attributes: { friendly_name: "Test camera" } } },
  };
  document.body.append(card);
  let resume;
  window.browserTest = {
    card,
    connection,
    events,
    peers,
    callWS,
    cutConnection() {
      connection.suspendReconnectUntil(new Promise((resolve) => { resume = resolve; }));
      connection.socket.close(4000, "Test network interruption");
    },
    resumeConnection() { resume(); },
    async removeCard() {
      card.remove();
    },
    async close() {
      await this.removeCard();
      connection.close();
    },
  };
};
