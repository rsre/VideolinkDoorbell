const CARD_VERSION = "0.12.14-native-talk1";

class VideolinkDoorbellCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = undefined;
    this._peer = undefined;
    this._remoteStream = undefined;
    this._micStream = undefined;
    this._keepaliveContext = undefined;
    this._keepaliveSource = undefined;
    this._keepaliveGain = undefined;
    this._microphoneSource = undefined;
    this._microphoneGain = undefined;
    this._keepaliveTrack = undefined;
    this._audioSender = undefined;
    this._nativeContext = undefined;
    this._nativeSource = undefined;
    this._nativeProcessor = undefined;
    this._nativeGain = undefined;
    this._nativePlaybackContext = undefined;
    this._nativePlaybackNextTime = 0;
    this._nativePlaybackChain = Promise.resolve();
    this._nativeMixUnsubscribe = undefined;
    this._nativePcm = [];
    this._nativeSendChain = Promise.resolve();
    this._nativeFrameCount = 0;
    this._nativeLastCaptureAt = undefined;
    this._nativeTalking = false;
    this._sessionId = undefined;
    this._pendingCandidates = [];
    this._pendingRemoteCandidates = [];
    this._unsubscribe = undefined;
    this._startingGeneration = undefined;
    this._connectionGeneration = 0;
    this._reconnectTimer = undefined;
    this._reconnectAttempt = 0;
    this._streamReady = false;
    this._micPending = false;
    this._talking = false;
    this._talkRequested = false;
    this._muted = true;
    this._mutedBeforeTalk = undefined;
    this._diagnosticTimer = undefined;
    this._collectingStats = false;
    this._diagnostics = {};
    this._outboundPacketsAtAttach = undefined;
  }

  static getStubConfig(hass, entities) {
    const entity = entities?.find((candidate) => candidate.startsWith("camera."));
    return { entity: entity || "", video_fit: "contain" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "camera" } } },
        { name: "title", selector: { text: {} } },
        { name: "video_fit", default: "contain", selector: { select: { mode: "dropdown", options: [
          { value: "cover", label: "Cropped" },
          { value: "contain", label: "Scaled" },
          { value: "fill", label: "Stretched" },
          { value: "full", label: "Full" },
        ] } } },
        { name: "hide_title", selector: { boolean: {} } },
        { name: "hide_video", selector: { boolean: {} } },
        { name: "hide_controls", selector: { boolean: {} } },
        { name: "disable_popup", selector: { boolean: {} } },
        { name: "native_talk", selector: { boolean: {} } },
        { name: "debug", selector: { boolean: {} } },
      ],
      computeLabel: (schema) => ({
        entity: "Camera entity",
        title: "Title",
        video_fit: "Video fit",
        hide_title: "Hide card title",
        hide_video: "Hide video stream",
        hide_controls: "Hide PTT and mute buttons",
        disable_popup: "Disable video popup",
        native_talk: "Experimental native Baichuan talk",
        debug: "Show stream diagnostics",
      })[schema.name],
    };
  }

  setConfig(config) {
    if (!config.entity || !config.entity.startsWith("camera.")) {
      throw new Error("A camera entity is required");
    }
    const previous = this._config;
    const changed = previous?.entity !== config.entity;
    const mediaChanged = changed || previous?.hide_video !== Boolean(config.hide_video);
    const controlsHidden = !previous?.hide_controls && Boolean(config.hide_controls);
    const videoFit = ["cover", "contain", "fill", "full"].includes(config.video_fit)
      ? config.video_fit
      : "contain";
    this._config = {
      hide_title: false,
      hide_video: false,
      hide_controls: false,
      disable_popup: false,
      native_talk: false,
      debug: false,
      ...config,
      video_fit: videoFit,
    };
    if (!previous || changed) this._muted = true;
    if (controlsHidden) this._stopMicrophone();
    this._render();
    if (mediaChanged && this.isConnected) {
      this._restart();
    }
  }

  set hass(hass) {
    const firstUpdate = !this._hass;
    this._hass = hass;
    if (firstUpdate && this.isConnected) {
      this._start();
    }
    this._updateTitle();
  }

  getCardSize() {
    return this._audioOnly ? 2 : 5;
  }

  get _audioOnly() {
    return Boolean(this._config?.hide_video);
  }

  getGridOptions() {
    return this._audioOnly
      ? { rows: 2, columns: 12, min_rows: 1, min_columns: 4 }
      : { rows: 5, columns: 12, min_rows: 3, min_columns: 6 };
  }

  connectedCallback() {
    this._render();
    this._start();
    document.addEventListener("visibilitychange", this._visibilityHandler);
    window.addEventListener("blur", this._windowBlurHandler);
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._visibilityHandler);
    window.removeEventListener("blur", this._windowBlurHandler);
    this._cleanup();
  }

  _visibilityHandler = () => {
    if (document.hidden) {
      this._cleanup();
    } else {
      this._start();
    }
  };

  _windowBlurHandler = () => {
    this._endTalk();
  };

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const audioOnly = this._audioOnly;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; background: var(--ha-card-background, var(--card-background-color)); }
        .header { padding: 12px 16px; font-size: 16px; font-weight: 500; }
        .audio-only .header { padding-bottom: 0; }
        .stage { position: relative; background: #000; aspect-ratio: 16 / 9; }
        .stage.fit-full { aspect-ratio: auto; }
        .stage.popup-enabled { cursor: pointer; }
        video { width: 100%; height: 100%; display: block; object-fit: ${this._config.video_fit}; background: #000; }
        .stage.fit-full video { height: auto; object-fit: contain; }
        audio { display: none; }
        .status { position: absolute; inset: auto 10px 10px; padding: 6px 9px; border-radius: 6px;
          color: white; background: rgba(0,0,0,.68); font-size: 12px; pointer-events: none; }
        .status:empty { display: none; }
        .audio-only .status { position: static; padding: 8px 16px 0; text-align: center;
          color: var(--secondary-text-color); background: none; }
        .security-warning { margin: 12px 12px 0; padding: 10px 12px; border-radius: 8px;
          color: var(--warning-color, #fbd150); background: color-mix(in srgb, var(--warning-color, #fbd150) 14%, transparent);
          font-size: 13px; line-height: 1.4; }
        .controls { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 12px; }
        button { border: 0; border-radius: 999px; min-width: 44px; height: 44px; padding: 0 14px;
          background: var(--secondary-background-color); color: var(--primary-text-color); cursor: pointer;
          touch-action: none; user-select: none; font: inherit; }
        button:hover { filter: brightness(1.08); }
        button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        .talk { min-width: 132px; background: var(--primary-color); color: var(--text-primary-color, white); }
        .talk.loading::before { content: ""; display: inline-block; width: 14px; height: 14px; margin-right: 8px;
          border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; vertical-align: -2px;
          animation: spin .8s linear infinite; }
        .talk.active { background: var(--error-color, #db4437); transform: scale(.97); }
        .talk:disabled { opacity: .55; cursor: wait; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .diagnostics { margin: 0 12px 12px; padding: 8px 10px; border-radius: 8px;
          background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 12px; }
        .diagnostics summary { cursor: pointer; color: var(--primary-text-color); font-weight: 500; }
        .diagnostics pre { margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 11px/1.45 monospace;
          user-select: text; -webkit-user-select: text; cursor: text; }
        .copy-diagnostics { min-width: 0; height: 32px; padding: 0 12px; font-size: 12px; }
      </style>
      <ha-card class="${audioOnly ? "audio-only" : ""}">
        ${this._config.hide_title ? "" : '<div class="header"></div>'}
        ${audioOnly ? `<audio autoplay playsinline muted></audio>
        <div class="status">Connecting…</div>` : `<div class="stage ${this._config.disable_popup ? "" : "popup-enabled"} ${this._config.video_fit === "full" ? "fit-full" : ""}"
          ${this._config.disable_popup ? "" : 'role="button" tabindex="0" aria-label="Open camera stream"'}>
          <video autoplay playsinline muted></video>
          <div class="status">Connecting…</div>
        </div>`}
        ${window.isSecureContext || this._config.hide_controls ? "" : '<div class="security-warning" role="alert">HTTPS is required for microphone access. Open Home Assistant through a secure HTTPS address to use push-to-talk.</div>'}
        ${this._config.hide_controls ? "" : `<div class="controls">
          <button class="sound" type="button" title="Enable camera audio" aria-label="Enable camera audio">🔇</button>
          <button class="talk" type="button" aria-label="Hold to talk">Hold to talk</button>
        </div>`}
        ${this._config.debug ? '<details class="diagnostics" open><summary>Stream diagnostics</summary><pre></pre><button class="copy-diagnostics" type="button">Copy diagnostics</button></details>' : ""}
      </ha-card>`;

    this._video = this.shadowRoot.querySelector("video, audio");
    this._video.muted = this._muted;
    this._attachRemoteStream();
    this._status = this.shadowRoot.querySelector(".status");
    this._talkButton = this.shadowRoot.querySelector(".talk");
    this._soundButton = this.shadowRoot.querySelector(".sound");
    this._diagnosticsOutput = this.shadowRoot.querySelector(".diagnostics pre");
    this._copyDiagnosticsButton = this.shadowRoot.querySelector(".copy-diagnostics");

    this._talkButton?.addEventListener("pointerdown", this._beginTalk);
    this._talkButton?.addEventListener("pointerup", this._endTalk);
    this._talkButton?.addEventListener("pointercancel", this._endTalk);
    this._talkButton?.addEventListener("pointerleave", this._endTalk);
    this._talkButton?.addEventListener("lostpointercapture", this._endTalk);
    this._talkButton?.addEventListener("blur", this._endTalk);
    this._talkButton?.addEventListener("keydown", this._talkKeyDown);
    this._talkButton?.addEventListener("keyup", this._talkKeyUp);
    this._soundButton?.addEventListener("click", this._toggleSound);
    this._copyDiagnosticsButton?.addEventListener("click", this._copyDiagnostics);
    const stage = this.shadowRoot.querySelector(".stage");
    if (stage && !this._config.disable_popup) {
      stage.addEventListener("click", this._openMoreInfo);
      stage.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") this._openMoreInfo(event);
      });
    }
    this._updateTitle();
    this._updateSoundButton();
    this._updateTalkButton();
    this._updateDiagnosticsView();
    if (this._config.debug && this._peer) this._startDiagnostics();
    else if (!this._config.debug) this._stopDiagnostics();
  }

  _attachRemoteStream() {
    if (!this._video || !this._remoteStream) return;
    this._video.srcObject = this._remoteStream;
    this._video.muted = this._muted;
    this._video.play().catch(() => {
      if (!this._muted) {
        this._muted = true;
        this._video.muted = true;
        this._updateSoundButton();
        this._setStatus("Tap the speaker button to enable audio");
        this._video.play().catch(() => undefined);
      }
    });
  }

  _updateTitle() {
    const header = this.shadowRoot?.querySelector(".header");
    if (!header || !this._config) return;
    const state = this._hass?.states?.[this._config.entity];
    header.textContent = this._config.title || state?.attributes?.friendly_name || this._config.entity;
  }

  _setStatus(message) {
    if (this._status) this._status.textContent = message || "";
  }

  _openMoreInfo = (event) => {
    event.preventDefault();
    if (this._config.disable_popup) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId: this._config.entity },
    }));
  };

  async _restart() {
    await this._cleanup();
    await this._start();
  }

  _isCurrentConnection(generation) {
    return generation === this._connectionGeneration && this.isConnected && !document.hidden;
  }

  _scheduleReconnect(immediate = false) {
    if (this._reconnectTimer !== undefined || !this.isConnected || document.hidden) return;
    const delay = immediate ? 0 : Math.min(30000, 1000 * (2 ** this._reconnectAttempt));
    this._reconnectAttempt += 1;
    this._reconnectTimer = window.setTimeout(() => {
      this._reconnectTimer = undefined;
      this._restart();
    }, delay);
  }

  async _start() {
    if (this._startingGeneration !== undefined || this._peer || !this._hass || !this._config || document.hidden) return;
    const generation = ++this._connectionGeneration;
    this._startingGeneration = generation;
    this._streamReady = false;
    this._diagnostics = { startedAt: performance.now(), phase: "connecting" };
    this._updateTalkButton();
    this._updateDiagnosticsView();
    this._setStatus("Connecting…");
    try {
      if (!window.RTCPeerConnection) throw new Error("This browser does not support WebRTC");
      const clientConfig = await this._hass.callWS({
        type: "camera/webrtc/get_client_config",
        entity_id: this._config.entity,
      });
      if (!this._isCurrentConnection(generation)) return;
      const peer = new RTCPeerConnection(clientConfig.configuration);
      this._peer = peer;
      this._startDiagnostics();
      if (clientConfig.dataChannel) peer.createDataChannel(clientConfig.dataChannel);

      this._createKeepaliveAudio();

      this._remoteStream = new MediaStream();
      peer.ontrack = (event) => {
        if (!this._isCurrentConnection(generation) || this._peer !== peer) return;
        this._remoteStream.addTrack(event.track);
        this._attachRemoteStream();
      };
      peer.onicecandidate = (event) => {
        this._handleLocalCandidate(event.candidate, generation).catch((error) => {
          if (this._isCurrentConnection(generation)) this._setStatus(`ICE failed: ${error?.message || error}`);
        });
      };
      peer.onconnectionstatechange = () => {
        if (!this._isCurrentConnection(generation) || this._peer !== peer) return;
        if (peer.connectionState === "connected") {
          if (this._reconnectTimer !== undefined) window.clearTimeout(this._reconnectTimer);
          this._reconnectTimer = undefined;
          this._reconnectAttempt = 0;
          this._streamReady = true;
          this._diagnostics.phase = "connected";
          this._diagnostics.connectMs = performance.now() - this._diagnostics.startedAt;
          this._setStatus("");
          this._updateTalkButton();
        }
        if (["failed", "disconnected"].includes(peer.connectionState)) {
          this._streamReady = false;
          this._diagnostics.phase = peer.connectionState;
          this._setStatus(`WebRTC ${peer.connectionState}`);
          this._updateTalkButton();
          this._scheduleReconnect(peer.connectionState === "failed");
        }
      };

      const audioTransceiver = peer.addTransceiver("audio", { direction: "sendrecv" });
      this._preferLowLatencyAudio(audioTransceiver);
      this._audioSender = audioTransceiver.sender;
      if (this._keepaliveTrack) await this._audioSender.replaceTrack(this._keepaliveTrack);
      if (!this._audioOnly) peer.addTransceiver("video", { direction: "recvonly" });
      const offer = await peer.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: !this._audioOnly,
      });
      await peer.setLocalDescription(this._setLowLatencyAudioPacketization(offer));
      if (!this._isCurrentConnection(generation) || this._peer !== peer) {
        peer.close();
        return;
      }

      this._unsubscribe = this._hass.connection.subscribeMessage(
        (event) => this._handleSignal(event, generation).catch((error) => {
          if (this._isCurrentConnection(generation)) {
            this._setStatus(`WebRTC signaling failed: ${error?.message || error}`);
            this._scheduleReconnect();
          }
        }),
        {
          type: "camera/webrtc/offer",
          entity_id: this._config.entity,
          offer: peer.localDescription?.sdp || offer.sdp,
        }
      );
    } catch (error) {
      if (generation === this._connectionGeneration) {
        this._setStatus(error?.message || String(error));
        await this._cleanup(false);
        this._scheduleReconnect();
      }
    } finally {
      if (this._startingGeneration === generation) this._startingGeneration = undefined;
    }
  }

  _createKeepaliveAudio() {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor || this._keepaliveTrack) return;
    try {
      const context = new AudioContextConstructor({ latencyHint: "interactive" });
      const source = context.createConstantSource();
      const keepaliveGain = context.createGain();
      const microphoneGain = context.createGain();
      const destination = context.createMediaStreamDestination();
      keepaliveGain.gain.value = 0;
      microphoneGain.gain.value = 0;
      source.connect(keepaliveGain).connect(destination);
      microphoneGain.connect(destination);
      source.start();
      this._keepaliveContext = context;
      this._keepaliveSource = source;
      this._keepaliveGain = keepaliveGain;
      this._microphoneGain = microphoneGain;
      this._keepaliveTrack = destination.stream.getAudioTracks()[0];
    } catch {
      // Browsers without an AudioContext keep the previous behavior.
      this._keepaliveContext = undefined;
      this._keepaliveSource = undefined;
      this._keepaliveGain = undefined;
      this._microphoneGain = undefined;
      this._keepaliveTrack = undefined;
    }
  }

  async _closeKeepaliveAudio() {
    this._keepaliveTrack?.stop();
    this._keepaliveTrack = undefined;
    try {
      this._keepaliveSource?.stop();
    } catch {
      // The source may already have stopped during connection cleanup.
    }
    this._keepaliveSource = undefined;
    this._keepaliveGain = undefined;
    this._microphoneGain = undefined;
    await this._keepaliveContext?.close().catch(() => undefined);
    this._keepaliveContext = undefined;
  }

  _preferLowLatencyAudio(transceiver) {
    const getCapabilities = globalThis.RTCRtpReceiver?.getCapabilities;
    if (!getCapabilities || !transceiver.setCodecPreferences) return;
    const codecs = getCapabilities("audio")?.codecs;
    if (!codecs?.length) return;
    const lowLatency = codecs.filter((codec) => /audio\/(PCMU|PCMA)$/i.test(codec.mimeType));
    if (!lowLatency.length) return;
    const remaining = codecs.filter((codec) => !lowLatency.includes(codec));
    try {
      // Prefer G.711 for the camera backchannel. It avoids an Opus↔G.711
      // transcode when the ONVIF speaker advertises PCMU/PCMA, while retaining
      // the other browser codecs as fallbacks.
      transceiver.setCodecPreferences([...lowLatency, ...remaining]);
    } catch {
      // Older browsers may expose capabilities but reject this preference list.
    }
  }

  _setLowLatencyAudioPacketization(offer) {
    if (!offer?.sdp) return offer;
    const lines = offer.sdp.split("\\r\\n");
    const audioIndex = lines.findIndex((line) => line.startsWith("m=audio "));
    if (audioIndex < 0) return offer;
    const nextMediaIndex = lines.findIndex(
      (line, index) => index > audioIndex && line.startsWith("m="),
    );
    const audioEnd = nextMediaIndex < 0 ? lines.length : nextMediaIndex;
    if (!lines.slice(audioIndex, audioEnd).some((line) => line.startsWith("a=ptime:"))) {
      lines.splice(audioEnd, 0, "a=ptime:10");
    }
    return { ...offer, sdp: lines.join("\\r\\n") };
  }

  async _handleSignal(event, generation) {
    if (!this._isCurrentConnection(generation) || !this._peer) return;
    if (event.type === "session") {
      this._sessionId = event.session_id;
      for (const candidate of this._pendingCandidates.splice(0)) {
        await this._sendCandidate(candidate, generation);
      }
    } else if (event.type === "answer") {
      await this._peer.setRemoteDescription({ type: "answer", sdp: event.answer });
      for (const candidate of this._pendingRemoteCandidates.splice(0)) {
        await this._peer.addIceCandidate(candidate);
      }
    } else if (event.type === "candidate") {
      const candidate = { ...event.candidate };
      if (candidate.sdpMid == null && candidate.sdpMLineIndex == null) candidate.sdpMid = "0";
      if (this._peer.remoteDescription) await this._peer.addIceCandidate(candidate);
      else this._pendingRemoteCandidates.push(candidate);
    } else if (event.type === "error") {
      this._setStatus(`WebRTC failed: ${event.message}`);
      await this._cleanup(false);
      this._scheduleReconnect();
    }
  }

  async _handleLocalCandidate(candidate, generation) {
    if (!candidate?.candidate || !this._isCurrentConnection(generation)) return;
    if (!this._sessionId) {
      this._pendingCandidates.push(candidate.toJSON());
      return;
    }
    await this._sendCandidate(candidate.toJSON(), generation);
  }

  _sendCandidate(candidate, generation) {
    if (!this._isCurrentConnection(generation) || !this._sessionId) return Promise.resolve();
    return this._hass.callWS({
      type: "camera/webrtc/candidate",
      entity_id: this._config.entity,
      session_id: this._sessionId,
      candidate,
    });
  }

  _beginTalk = async (event) => {
    event.preventDefault();
    if (!window.isSecureContext) {
      this._setStatus("HTTPS is required for microphone access");
      return;
    }
    if (this._talking || this._micPending || !this._streamReady || !this._audioSender) return;
    this._talkRequested = true;
    this._micPending = true;
    this._diagnostics.micRequestedAt = performance.now();
    this._diagnostics.micPermissionMs = undefined;
    this._diagnostics.trackAttachMs = undefined;
    this._diagnostics.firstOutboundPacketMs = undefined;
    if (event.pointerId != null) this._talkButton.setPointerCapture?.(event.pointerId);
    this._updateTalkButton();
    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
      this._diagnostics.micPermissionMs = performance.now() - this._diagnostics.micRequestedAt;
      if (!this._talkRequested) {
        this._micStream.getTracks().forEach((track) => track.stop());
        this._micStream = undefined;
        return;
      }
      const track = this._micStream.getAudioTracks()[0];
      if (!track) throw new Error("Browser did not provide a microphone audio track");
      const sender = this._audioSender;
      if (!sender) throw new Error("WebRTC audio sender is unavailable");
      await this._keepaliveContext?.resume().catch(() => undefined);
      this._outboundPacketsAtAttach = await this._getOutboundAudioPackets();
      if (!this._talkRequested || track.readyState === "ended") {
        await this._stopMicrophone();
        return;
      }
      if (this._config.native_talk) {
        this._nativeMixUnsubscribe = await this._hass.connection.subscribeMessage(
          (message) => this._playNativeMix(message),
          {
            type: "videolink_doorbell/native_talk",
            action: "start",
            entity_id: this._config.entity,
          },
        );
        await this._startNativeTalkCapture(this._micStream);
      } else if (this._keepaliveContext && this._microphoneGain && this._keepaliveTrack) {
        this._microphoneSource = this._keepaliveContext.createMediaStreamSource(this._micStream);
        this._microphoneSource.connect(this._microphoneGain);
        this._microphoneGain.gain.setValueAtTime(1, this._keepaliveContext.currentTime);
      } else {
        await sender.replaceTrack(track);
      }
      if (!this._talkRequested || sender !== this._audioSender) {
        await this._stopMicrophone();
        return;
      }
      this._diagnostics.trackAttachedAt = performance.now();
      this._diagnostics.trackAttachMs = this._diagnostics.trackAttachedAt - this._diagnostics.micRequestedAt;
      this._talking = true;
      this._micPending = false;
      // Keep inbound audio muted while transmitting to prevent feedback. Once
      // PTT ends, listening is enabled automatically so the reply is audible.
      this._mutedBeforeTalk = false;
      this._muted = true;
      if (this._video) this._video.muted = true;
      if (this._soundButton) this._soundButton.disabled = true;
      this._updateTalkButton();
      this._updateSoundButton();
      this._updateDiagnosticsView();
    } catch (error) {
      this._setStatus(`Microphone unavailable: ${error?.message || error}`);
      this._diagnostics.microphoneError = error?.message || String(error);
      await this._stopMicrophone();
    } finally {
      this._micPending = false;
      this._updateTalkButton();
    }
  };

  _endTalk = async (event) => {
    event?.preventDefault();
    this._talkRequested = false;
    await this._stopMicrophone();
  };

  _talkKeyDown = (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) this._beginTalk(event);
  };

  _talkKeyUp = (event) => {
    if (event.key === " " || event.key === "Enter") this._endTalk(event);
  };

  async _stopMicrophone() {
    const restoreMuted = this._talking ? this._mutedBeforeTalk : undefined;
    this._talkRequested = false;
    if (this._microphoneGain && this._keepaliveContext) {
      this._microphoneGain.gain.setValueAtTime(0, this._keepaliveContext.currentTime);
    }
    this._microphoneSource?.disconnect();
    this._microphoneSource = undefined;
    if (this._nativeTalking || this._nativeMixUnsubscribe || this._nativeContext) {
      await this._stopNativeTalkCapture();
    }
    this._micStream?.getTracks().forEach((track) => track.stop());
    const sender = this._audioSender;
    this._talking = false;
    this._micPending = false;
    this._mutedBeforeTalk = undefined;
    if (restoreMuted !== undefined) {
      this._muted = restoreMuted;
      if (this._video) this._video.muted = restoreMuted;
    }
    if (this._soundButton) this._soundButton.disabled = false;
    this._updateSoundButton();
    this._updateTalkButton();
    this._micStream = undefined;
    if (sender && !this._keepaliveTrack) {
      await sender.replaceTrack(this._keepaliveTrack).catch(() => undefined);
    }
  }

  async _startNativeTalkCapture(stream) {
    const context = new AudioContext({ sampleRate: 16000 });
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(1024, 1, 1);
    const gain = context.createGain();
    gain.gain.value = 0;
    this._nativeContext = context;
    this._nativeSource = source;
    this._nativeProcessor = processor;
    this._nativeGain = gain;
    this._nativePcm = [];
    this._nativeSendChain = Promise.resolve();
    this._nativeFrameCount = 0;
    this._nativeLastCaptureAt = undefined;
    this._nativeTalking = true;
    processor.onaudioprocess = (event) => {
      if (!this._nativeTalking) return;
      const input = event.inputBuffer.getChannelData(0);
      const ratio = context.sampleRate / 16000;
      for (const sample of input) this._nativePcm.push(sample);
      const needed = Math.ceil(1024 * ratio);
      while (this._nativePcm.length >= needed) {
        const pcm = new Int16Array(1024);
        for (let index = 0; index < pcm.length; index++) {
          const sample = this._nativePcm[Math.min(Math.floor(index * ratio), this._nativePcm.length - 1)];
          pcm[index] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
        }
        this._nativePcm.splice(0, needed);
        const capturedAt = performance.now();
        this._nativeFrameCount += 1;
        this._diagnostics.nativeFrames = this._nativeFrameCount;
        if (this._nativeLastCaptureAt !== undefined) {
          this._diagnostics.nativeCallbackIntervalMs = capturedAt - this._nativeLastCaptureAt;
        }
        this._nativeLastCaptureAt = capturedAt;
        const bytes = new Uint8Array(pcm.buffer);
        let binary = "";
        for (let index = 0; index < bytes.length; index += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
        }
        const encoded = btoa(binary);
        this._nativeSendChain = this._nativeSendChain
          .then(() => {
            this._diagnostics.nativeQueueWaitMs = performance.now() - capturedAt;
            const sentAt = performance.now();
            return this._hass.callWS({
            type: "videolink_doorbell/native_talk",
            action: "audio",
            entity_id: this._config.entity,
            pcm: encoded,
            }).then(() => {
              this._diagnostics.nativeWsAckMs = performance.now() - sentAt;
            });
          })
          .catch((error) => {
            this._diagnostics.nativeTalkError = error?.message || String(error);
          });
      }
    };
    source.connect(processor);
    processor.connect(gain);
    gain.connect(context.destination);
  }

  async _stopNativeTalkCapture() {
    this._nativeTalking = false;
    this._nativeProcessor?.disconnect();
    this._nativeSource?.disconnect();
    this._nativeGain?.disconnect();
    if (this._nativeMixUnsubscribe) {
      this._nativeMixUnsubscribe();
      this._nativeMixUnsubscribe = undefined;
    }
    await this._nativeSendChain.catch(() => undefined);
    await this._hass.callWS({
      type: "videolink_doorbell/native_talk",
      action: "stop",
      entity_id: this._config.entity,
    }).catch((error) => {
      this._diagnostics.nativeTalkError = error?.message || String(error);
    });
    if (this._nativeContext) await this._nativeContext.close().catch(() => undefined);
    await this._nativePlaybackChain.catch(() => undefined);
    if (this._nativePlaybackContext) await this._nativePlaybackContext.close().catch(() => undefined);
    this._nativeContext = undefined;
    this._nativeSource = undefined;
    this._nativeProcessor = undefined;
    this._nativeGain = undefined;
    this._nativePlaybackContext = undefined;
    this._nativePlaybackNextTime = 0;
    this._nativePlaybackChain = Promise.resolve();
    this._nativePcm = [];
  }

  _playNativeMix(message) {
    this._nativePlaybackChain = this._nativePlaybackChain
      .then(() => this._playNativeMixFrame(message))
      .catch((error) => {
        this._diagnostics.nativeTalkError = error?.message || String(error);
      });
  }

  async _playNativeMixFrame(message) {
    const encoded = message?.event?.pcm;
    if (!encoded) return;
    try {
      const binary = atob(encoded);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      if (bytes.length % 2 === 1) return;
      const samples = new Int16Array(bytes.buffer);
      if (!this._nativePlaybackContext) {
        this._nativePlaybackContext = new AudioContext({ sampleRate: 16000 });
        await this._nativePlaybackContext.resume();
        this._nativePlaybackNextTime = this._nativePlaybackContext.currentTime;
      }
      const context = this._nativePlaybackContext;
      const buffer = context.createBuffer(1, samples.length, 16000);
      const channel = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index++) channel[index] = samples[index] / 32768;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const start = Math.max(context.currentTime, this._nativePlaybackNextTime);
      source.start(start);
      this._nativePlaybackNextTime = start + buffer.duration;
    } catch (error) {
      this._diagnostics.nativeTalkError = error?.message || String(error);
    }
  }

  _toggleSound = () => {
    if (!this._video) return;
    this._muted = !this._muted;
    this._video.muted = this._muted;
    this._video.play().catch(() => undefined);
    this._updateSoundButton();
  };

  _updateSoundButton() {
    if (!this._soundButton) return;
    this._soundButton.textContent = this._muted ? "🔇" : "🔊";
    this._soundButton.title = this._muted ? "Enable camera audio" : "Mute camera audio";
    this._soundButton.setAttribute("aria-label", this._soundButton.title);
  }

  _updateTalkButton() {
    if (!this._talkButton) return;
    const insecure = !window.isSecureContext;
    const loading = !insecure && (!this._streamReady || this._micPending);
    this._talkButton.disabled = insecure || !this._streamReady;
    this._talkButton.classList.toggle("loading", loading);
    this._talkButton.classList.toggle("active", this._talking);
    this._talkButton.textContent = insecure
      ? "HTTPS required"
      : this._talking
        ? "Talking…"
        : loading
          ? "Loading…"
          : "Hold to talk";
    this._talkButton.title = insecure ? "HTTPS is required for microphone access" : "";
    this._talkButton.setAttribute("aria-busy", String(loading));
  }

  _startDiagnostics() {
    this._stopDiagnostics();
    if (!this._config?.debug) return;
    this._diagnosticTimer = window.setInterval(() => this._collectDiagnostics(), 250);
    this._collectDiagnostics();
  }

  _stopDiagnostics() {
    if (this._diagnosticTimer !== undefined) window.clearInterval(this._diagnosticTimer);
    this._diagnosticTimer = undefined;
  }

  async _getOutboundAudioPackets() {
    if (!this._audioSender) return 0;
    const stats = await this._audioSender.getStats();
    return [...stats.values()]
      .filter((report) => report.type === "outbound-rtp" && (report.kind || report.mediaType) === "audio")
      .reduce((total, report) => total + (report.packetsSent || 0), 0);
  }

  async _collectDiagnostics() {
    if (!this._config?.debug || !this._peer || this._collectingStats) return;
    this._collectingStats = true;
    try {
      const stats = await this._peer.getStats();
      const reports = [...stats.values()];
      const inbound = reports.find((report) => report.type === "inbound-rtp" && (report.kind || report.mediaType) === "audio");
      const outbound = reports.find((report) => report.type === "outbound-rtp" && (report.kind || report.mediaType) === "audio");
      const remoteInbound = reports.find((report) => report.type === "remote-inbound-rtp" && (report.kind || report.mediaType) === "audio");
      const transport = reports.find((report) => report.type === "transport" && report.selectedCandidatePairId);
      const pair = stats.get(transport?.selectedCandidatePairId)
        || reports.find((report) => report.type === "candidate-pair" && report.state === "succeeded" && report.nominated);
      const codec = inbound && stats.get(inbound.codecId);
      const outboundCodec = outbound && stats.get(outbound.codecId);
      const packetsSent = outbound?.packetsSent || 0;
      if (this._diagnostics.trackAttachedAt !== undefined && this._diagnostics.firstOutboundPacketMs === undefined
          && packetsSent > (this._outboundPacketsAtAttach || 0)) {
        this._diagnostics.firstOutboundPacketMs = performance.now() - this._diagnostics.trackAttachedAt;
      }
      this._diagnostics.rttMs = pair?.currentRoundTripTime == null ? undefined : pair.currentRoundTripTime * 1000;
      this._diagnostics.remoteInboundRttMs = remoteInbound?.roundTripTime == null
        ? undefined : remoteInbound.roundTripTime * 1000;
      this._diagnostics.inboundJitterMs = inbound?.jitter == null ? undefined : inbound.jitter * 1000;
      this._diagnostics.jitterBufferMs = inbound?.jitterBufferEmittedCount
        ? inbound.jitterBufferDelay * 1000 / inbound.jitterBufferEmittedCount
        : undefined;
      const transportRttMs = this._diagnostics.remoteInboundRttMs ?? this._diagnostics.rttMs;
      this._diagnostics.webrtcAudioBudgetMs = transportRttMs == null
        ? undefined
        : transportRttMs / 2 + 10 + (this._diagnostics.jitterBufferMs || 0);
      this._diagnostics.inboundPackets = inbound?.packetsReceived;
      this._diagnostics.inboundLost = inbound?.packetsLost;
      this._diagnostics.outboundPackets = outbound?.packetsSent;
      this._diagnostics.outboundBytes = outbound?.bytesSent;
      this._diagnostics.codec = codec?.mimeType;
      this._diagnostics.codecClockRate = codec?.clockRate;
      this._diagnostics.codecChannels = codec?.channels;
      this._diagnostics.outboundCodec = outboundCodec?.mimeType;
      this._diagnostics.outboundCodecClockRate = outboundCodec?.clockRate;
      this._diagnostics.outboundCodecChannels = outboundCodec?.channels;
      this._updateDiagnosticsView();
    } catch (error) {
      this._diagnostics.statsError = error?.message || String(error);
      this._updateDiagnosticsView();
    } finally {
      this._collectingStats = false;
    }
  }

  _updateDiagnosticsView() {
    if (!this._diagnosticsOutput) return;
    const ms = (value) => value == null ? "waiting" : `${value.toFixed(1)} ms`;
    const value = (item) => item == null ? "waiting" : String(item);
    const codec = (name, clockRate, channels) => {
      if (name == null) return "waiting";
      const rate = clockRate == null ? "?" : `${clockRate}`;
      const channelCount = channels == null ? "?" : `${channels}`;
      return `${name}/${rate}/${channelCount}`;
    };
    this._diagnosticsOutput.textContent = [
      `Phase: ${this._diagnostics.phase || "idle"}`,
      `WebRTC connect: ${ms(this._diagnostics.connectMs)}`,
      `WebRTC RTT: ${ms(this._diagnostics.rttMs)}`,
      `Remote inbound RTT: ${ms(this._diagnostics.remoteInboundRttMs)}`,
      `Estimated browser/WebRTC audio budget: ${ms(this._diagnostics.webrtcAudioBudgetMs)}`,
      `Inbound jitter: ${ms(this._diagnostics.inboundJitterMs)}`,
      `Inbound jitter buffer: ${ms(this._diagnostics.jitterBufferMs)}`,
      `Inbound audio: ${value(this._diagnostics.inboundPackets)} packets, ${value(this._diagnostics.inboundLost)} lost`,
      `Outbound audio: ${value(this._diagnostics.outboundPackets)} packets, ${value(this._diagnostics.outboundBytes)} bytes`,
      `Inbound codec: ${codec(this._diagnostics.codec, this._diagnostics.codecClockRate, this._diagnostics.codecChannels)}`,
      `Outbound codec/backchannel: ${codec(this._diagnostics.outboundCodec, this._diagnostics.outboundCodecClockRate, this._diagnostics.outboundCodecChannels)}`,
      "Camera/RTSP speaker delay: not exposed by WebRTC stats",
      `Mic permission: ${ms(this._diagnostics.micPermissionMs)}`,
      `PTT to track attached: ${ms(this._diagnostics.trackAttachMs)}`,
      `Track assignment: ${ms(this._diagnostics.trackAttachMs == null || this._diagnostics.micPermissionMs == null
        ? undefined : this._diagnostics.trackAttachMs - this._diagnostics.micPermissionMs)}`,
      `Track attached to first packet: ${ms(this._diagnostics.firstOutboundPacketMs)}`,
      `Native audio frames: ${value(this._diagnostics.nativeFrames)}`,
      `Native capture interval: ${ms(this._diagnostics.nativeCallbackIntervalMs)}`,
      `Native queue wait: ${ms(this._diagnostics.nativeQueueWaitMs)}`,
      `Native WebSocket ack: ${ms(this._diagnostics.nativeWsAckMs)}`,
      this._diagnostics.microphoneError ? `Microphone error: ${this._diagnostics.microphoneError}` : "",
      this._diagnostics.statsError ? `Stats error: ${this._diagnostics.statsError}` : "",
    ].filter(Boolean).join("\n");
  }

  _copyDiagnostics = async () => {
    const text = this._diagnosticsOutput?.textContent;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      this._copyDiagnosticsButton.textContent = "Copied";
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(this._diagnosticsOutput);
      selection.removeAllRanges();
      selection.addRange(range);
      this._copyDiagnosticsButton.textContent = "Text selected";
    }
    window.setTimeout(() => {
      if (this._copyDiagnosticsButton) this._copyDiagnosticsButton.textContent = "Copy diagnostics";
    }, 1500);
  };

  async _cleanup(clearStatus = true) {
    this._connectionGeneration += 1;
    this._startingGeneration = undefined;
    if (this._reconnectTimer !== undefined) window.clearTimeout(this._reconnectTimer);
    this._reconnectTimer = undefined;
    this._streamReady = false;
    this._stopDiagnostics();
    await this._stopMicrophone();
    await this._closeKeepaliveAudio();
    this._remoteStream?.getTracks().forEach((track) => track.stop());
    this._remoteStream = undefined;
    this._peer?.close();
    this._peer = undefined;
    this._audioSender = undefined;
    this._sessionId = undefined;
    this._pendingCandidates = [];
    this._pendingRemoteCandidates = [];
    if (this._video) this._video.srcObject = null;
    if (this._unsubscribe) {
      const unsubscribe = await this._unsubscribe.catch(() => undefined);
      unsubscribe?.();
      this._unsubscribe = undefined;
    }
    if (clearStatus) this._setStatus("");
  }
}

if (!customElements.get("videolink-doorbell")) {
  customElements.define("videolink-doorbell", VideolinkDoorbellCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "videolink-doorbell",
    name: "Videolink Doorbell",
    description: "Videolink camera and intercom card with WebRTC push-to-talk",
    preview: true,
    documentationURL: "https://github.com/rsre/VideolinkDoorbell",
  });
  console.info(`%c VIDEOLINK-DOORBELL-CARD %c ${CARD_VERSION} `, "color:white;background:#067a9c", "color:#067a9c");
}
