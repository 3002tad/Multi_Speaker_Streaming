const { Room, RoomEvent } = LivekitClient;

// Browser audio elements top out at volume=1.  The chain below adds a small
// fixed pre-gain and a compressor so quiet remote speech is clearer without
// letting a suddenly loud participant clip the selected system output.
const PLAYBACK_PRE_GAIN = 2.2;

const state = {
  room: null,
  playbackEnabled: false,
  playbackAudioContext: null,
  remoteAudio: new Map(),
  eventSocket: null,
  drafts: new Map(),
  transcripts: new Map(),
  minutesDocument: null,
  minutesStatus: "idle",
  minutesVersion: 0,
  recorder: null,
};

const $ = (id) => document.getElementById(id);

function resetTranscriptState() {
  state.drafts.clear();
  state.transcripts.clear();
  state.minutesDocument = null;
  state.minutesStatus = "idle";
  state.minutesVersion = 0;
  renderDrafts();
  renderMinutes();
}

function captureScrollState(container) {
  return {
    top: container.scrollTop,
    stickToBottom:
      container.scrollHeight <= container.clientHeight ||
      container.scrollHeight - container.scrollTop - container.clientHeight < 72,
  };
}

function restoreScrollState(container, scrollState) {
  requestAnimationFrame(() => {
    container.scrollTop = scrollState.stickToBottom
      ? container.scrollHeight
      : scrollState.top;
  });
}

async function checkApi() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("API unavailable");
    const data = await response.json();
    $("api-dot").className = "dot online";
    $("api-status").textContent = data.livekit_configured
      ? "AI backend sẵn sàng"
      : "Backend thiếu LiveKit key";
  } catch {
    $("api-dot").className = "dot offline";
    $("api-status").textContent = "AI backend offline";
  }
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(-2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function getDisplayName(participant) {
  return participant.name || participant.identity || "Thành viên";
}

function renderParticipants() {
  if (!state.room) return;
  const items = [
    {
      identity: state.room.localParticipant.identity,
      name: getDisplayName(state.room.localParticipant),
      local: true,
    },
    ...Array.from(state.room.remoteParticipants.values()).map((participant) => ({
      identity: participant.identity,
      name: getDisplayName(participant),
      local: false,
    })),
  ];

  $("participant-count").textContent = `${items.length} người`;
  $("participant-list").innerHTML = items
    .map(
      (item) => `
        <div class="participant">
          <div class="avatar">${initials(item.name)}</div>
          <div>
            <strong>${escapeHtml(item.name)}${item.local ? " (Bạn)" : ""}</strong>
            <small>${item.local ? "Micro cục bộ" : "Nguồn audio từ xa"}</small>
          </div>
        </div>
      `,
    )
    .join("");
}

function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

function playbackAudioContext() {
  if (state.playbackAudioContext) return state.playbackAudioContext;
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return null;
  state.playbackAudioContext = new Context();
  return state.playbackAudioContext;
}

function createPlaybackBoost(element) {
  const context = playbackAudioContext();
  if (!context || !context.createMediaElementSource) return null;

  const source = context.createMediaElementSource(element);
  const gain = context.createGain();
  const compressor = context.createDynamicsCompressor();
  gain.gain.setValueAtTime(PLAYBACK_PRE_GAIN, context.currentTime);
  compressor.threshold.setValueAtTime(-18, context.currentTime);
  compressor.knee.setValueAtTime(18, context.currentTime);
  compressor.ratio.setValueAtTime(4, context.currentTime);
  compressor.attack.setValueAtTime(0.006, context.currentTime);
  compressor.release.setValueAtTime(0.22, context.currentTime);

  source.connect(gain);
  gain.connect(compressor);
  compressor.connect(context.destination);
  return { source, gain, compressor };
}

function resumePlaybackAudio() {
  if (state.playbackAudioContext?.state === "suspended") {
    state.playbackAudioContext.resume().catch(() => {});
  }
}

function attachRemoteAudio(track, participant) {
  const key = `${participant.identity}:${track.sid || Math.random()}`;
  const element = track.attach();
  element.autoplay = true;
  element.volume = 1;
  element.muted = !state.playbackEnabled;
  element.dataset.remoteAudio = key;
  document.body.appendChild(element);
  let processing = null;
  try {
    processing = createPlaybackBoost(element);
  } catch {
    // Web Audio can be unavailable on older browsers. The direct element is
    // still audible at full native volume as a graceful fallback.
  }
  state.remoteAudio.set(key, { track, element, processing });
  if (state.playbackEnabled) {
    resumePlaybackAudio();
    element.play().catch(() => {});
  }
}

function detachRemoteAudio(track) {
  for (const [key, item] of state.remoteAudio) {
    if (item.track === track) {
      item.track.detach(item.element);
      for (const node of Object.values(item.processing || {})) {
        try {
          node.disconnect();
        } catch {
          // A disconnected Web Audio node is safe to ignore.
        }
      }
      item.element.remove();
      state.remoteAudio.delete(key);
    }
  }
}

function setPlayback(enabled) {
  state.playbackEnabled = enabled;
  if (enabled) resumePlaybackAudio();
  for (const { element } of state.remoteAudio.values()) {
    element.muted = !enabled;
    element.volume = 1;
    if (enabled) element.play().catch(() => {});
  }
  $("playback-button").classList.toggle("active", enabled);
  $("playback-button").querySelector(".control-icon").textContent = enabled ? "🎧" : "🔇";
  $("playback-button").querySelector("span:last-child").textContent = enabled
    ? "Playback bật"
    : "Playback tắt";
}

async function joinRoom(mode) {
  const displayName = $("display-name").value.trim();
  const meetingCode = $("meeting-code").value.trim();
  const meetingTitle = $("meeting-title").value.trim();
  $("join-error").textContent = "";
  if (!displayName) {
    $("join-error").textContent = "Vui lòng nhập tên hiển thị.";
    return;
  }

  const endpoint = mode === "create" ? "/api/meeting/create" : "/api/meeting/join";
  const body =
    mode === "create"
      ? { host_name: displayName, meeting_title: meetingTitle || null }
      : { display_name: displayName, meeting_code: meetingCode };

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không thể tham gia phòng");
    if (mode === "create") resetTranscriptState();

    const room = new Room({
      adaptiveStream: false,
      dynacast: false,
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    state.room = room;
    wireRoomEvents(room);
    await room.connect(data.livekit_url, data.token, { autoSubscribe: true });
    await room.localParticipant.setMicrophoneEnabled(true);

    $("join-view").classList.add("hidden");
    $("meeting-view").classList.remove("hidden");
    const title = data.meeting_title || data.meeting_code;
    $("room-title").textContent = `${title} · ${displayName}`;
    $("connection-state").textContent = "Đã kết nối";
    $("connection-state").className = "badge online";
    renderParticipants();
    connectEventSocket();
    loadMinutes();
  } catch (error) {
    $("join-error").textContent = error.message;
    if (state.room) {
      await state.room.disconnect();
      state.room = null;
    }
  }
}

function wireRoomEvents(room) {
  room
    .on(RoomEvent.ParticipantConnected, renderParticipants)
    .on(RoomEvent.ParticipantDisconnected, renderParticipants)
    .on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (track.kind === "audio") attachRemoteAudio(track, participant);
    })
    .on(RoomEvent.TrackUnsubscribed, (track) => detachRemoteAudio(track))
    .on(RoomEvent.Disconnected, () => {
      $("connection-state").textContent = "Mất kết nối";
      $("connection-state").className = "badge warning";
    });
}

function connectEventSocket() {
  if (state.eventSocket) state.eventSocket.close();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/meeting`);
  state.eventSocket = socket;
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    handleMeetingEvent(payload);
  };
  socket.onclose = () => {
    if (state.room) setTimeout(connectEventSocket, 2000);
  };
  const heartbeat = setInterval(() => {
    if (socket.readyState === WebSocket.OPEN) socket.send("ping");
    if (socket.readyState === WebSocket.CLOSED) clearInterval(heartbeat);
  }, 25000);
}

function handleMeetingEvent(payload) {
  if (payload.type === "transcript.partial") {
    state.drafts.set(payload.segment_id || payload.source_id, payload);
    renderDrafts();
  } else if (payload.type === "transcript.speaker") {
    const draft = state.drafts.get(payload.segment_id);
    if (draft) {
      draft.speaker = payload.speaker;
      draft.speaker_id = payload.speaker_id;
      renderDrafts();
    }
  } else if (payload.type === "transcript.final") {
    state.drafts.delete(payload.segment_id);
    state.transcripts.set(payload.segment_id, payload);
    renderDrafts();
    renderMinutes();
  } else if (payload.type === "transcript.retracted") {
    state.drafts.delete(payload.segment_id);
    state.transcripts.delete(payload.segment_id);
    renderDrafts();
    renderMinutes();
  } else if (payload.type === "minutes.status") {
    state.minutesStatus = payload.status || "queued";
    renderMinutes();
  } else if (payload.type === "minutes.updated") {
    if ((payload.version || 0) >= state.minutesVersion) {
      state.minutesVersion = payload.version || 0;
      state.minutesDocument = payload.document || null;
      state.minutesStatus = payload.status || "ready";
      renderMinutes();
    }
  } else if (payload.type === "minutes.cleared") {
    state.minutesDocument = null;
    state.minutesStatus = "idle";
    state.minutesVersion = 0;
    renderMinutes();
  } else if (payload.type === "transcript.cleared") {
    resetTranscriptState();
  }
}

function renderDrafts() {
  const container = $("draft-list");
  const scrollState = captureScrollState(container);
  if (!state.drafts.size) {
    container.innerHTML = '<div class="empty-state">Đang chờ người tiếp theo phát biểu...</div>';
    restoreScrollState(container, scrollState);
    return;
  }
  container.innerHTML = Array.from(state.drafts.values())
    .map(
      (item) => `
        <article class="draft">
          <div class="draft-speaker">${escapeHtml(item.speaker || "Đang nhận diện...")}</div>
          <div class="draft-text">${escapeHtml(item.text || "")}</div>
        </article>
      `,
    )
    .join("");
  restoreScrollState(container, scrollState);
}

function formatTime(timestamp) {
  if (!timestamp) return "--:--:--";
  return new Date(timestamp * 1000).toLocaleTimeString("vi-VN");
}

function sourceLabel(sourceIds = []) {
  if (!sourceIds.length) return "";
  const labels = sourceIds.map((id) =>
    escapeHtml(String(id).replace(/^seg-/, "").slice(0, 8)),
  );
  return `<span class="source-ref">Nguồn: ${labels.join(", ")}</span>`;
}

function evidenceList(items = [], className = "") {
  if (!items.length) return "";
  return `
    <ul class="minutes-bullets ${className}">
      ${items
        .map(
          (item) => `
            <li>
              ${item.speaker ? `<strong>${escapeHtml(item.speaker)}:</strong> ` : ""}
              ${escapeHtml(item.content || "")}
              ${sourceLabel(item.source_segment_ids)}
            </li>
          `,
        )
        .join("")}
    </ul>`;
}

function actionList(actions = []) {
  if (!actions.length) return "";
  return `
    <ul class="minutes-bullets minutes-actions">
      ${actions
        .map(
          (action) => `
            <li>
              <strong>${escapeHtml(action.task || "")}</strong>
              ${action.owner ? ` · Phụ trách: ${escapeHtml(action.owner)}` : ""}
              ${action.deadline ? ` · Hạn: ${escapeHtml(action.deadline)}` : ""}
              ${sourceLabel(action.source_segment_ids)}
            </li>
          `,
        )
        .join("")}
    </ul>`;
}

function renderTranscriptSource() {
  const items = Array.from(state.transcripts.values()).sort(
    (a, b) => (a.start_time || 0) - (b.start_time || 0),
  );
  if (!items.length) return "";
  return `
    <details class="source-transcript">
      <summary>Xem transcript nguồn (${items.length})</summary>
      <div class="source-transcript-list">
        ${items
          .map(
            (item) => `
              <article>
                <strong>${escapeHtml(item.speaker || "Chưa xác định")}</strong>
                <span>${formatTime(item.start_time)}</span>
                <p>${escapeHtml(item.text || item.raw_text || "")}</p>
              </article>
            `,
          )
          .join("")}
      </div>
    </details>`;
}

function renderMinutes() {
  const container = $("minutes-list");
  const scrollState = captureScrollState(container);
  const status = $("minutes-status");
  const statusText = {
    idle: "Chờ nội dung",
    queued: "Đang cập nhật",
    ready: "Đã cập nhật",
    manual: "Đã chỉnh sửa",
    error: "Cần thử lại",
  }[state.minutesStatus] || "Đang cập nhật";
  status.textContent = statusText;
  status.className = `badge minutes-status ${state.minutesStatus}`;

  const document = state.minutesDocument;
  if (!document || (!document.summary?.length && !document.topics?.length)) {
    const waiting = state.transcripts.size
      ? "Đã nhận transcript. Đang cập nhật biên bản theo timeline..."
      : "Chưa có nội dung được chốt.";
    container.innerHTML = `<div class="empty-state">${waiting}</div>${renderTranscriptSource()}`;
    restoreScrollState(container, scrollState);
    return;
  }
  const topics = (document.topics || [])
    .map(
      (topic) => `
        <article class="minute-topic">
          <h4>${escapeHtml(topic.title || "Nội dung trao đổi")}</h4>
          ${evidenceList(topic.details, "topic-details")}
          ${topic.proposals?.length ? `<h5>Đề xuất</h5>${evidenceList(topic.proposals, "topic-proposals")}` : ""}
          ${topic.decisions?.length ? `<h5>Quyết định / thống nhất</h5>${evidenceList(topic.decisions, "topic-decisions")}` : ""}
          ${topic.actions?.length ? `<h5>Việc cần làm</h5>${actionList(topic.actions)}` : ""}
        </article>`,
    )
    .join("");
  container.innerHTML = `
    <section class="minutes-overview">
      <p class="minutes-title">${escapeHtml(document.meeting?.title || "Biên bản cuộc họp")}</p>
      ${document.summary?.length ? `<h4>Tóm tắt</h4>${evidenceList(document.summary, "minutes-summary")}` : ""}
    </section>
    ${topics}
    ${renderTranscriptSource()}`;
  restoreScrollState(container, scrollState);
}

async function loadMinutes() {
  const [minutesResponse, transcriptsResponse] = await Promise.all([
    fetch("/api/minutes"),
    fetch("/api/transcripts"),
  ]);
  if (minutesResponse.ok) {
    const data = await minutesResponse.json();
    state.minutesDocument = data.document || null;
    state.minutesStatus = data.status || "idle";
    state.minutesVersion = data.version || 0;
  }
  if (transcriptsResponse.ok) {
    const data = await transcriptsResponse.json();
    state.transcripts.clear();
    for (const item of data.items) state.transcripts.set(item.segment_id, item);
  }
  renderMinutes();
}

async function toggleMicrophone() {
  if (!state.room) return;
  const enabled = !state.room.localParticipant.isMicrophoneEnabled;
  await state.room.localParticipant.setMicrophoneEnabled(enabled);
  $("mic-button").classList.toggle("active", enabled);
  $("mic-button").querySelector(".control-icon").textContent = enabled ? "🎙️" : "🔇";
  $("mic-button").querySelector("span:last-child").textContent = enabled
    ? "Micro bật"
    : "Micro tắt";
}

async function leaveRoom() {
  if (state.eventSocket) state.eventSocket.close();
  if (state.room) await state.room.disconnect();
  location.reload();
}

function encodeWav(chunks, sampleRate) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const writeText = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (const sample of chunk) {
      const value = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function startEnrollment() {
  const speakerName = $("display-name").value.trim();
  if (!speakerName) {
    $("record-message").textContent = "Nhập tên hiển thị trước khi ghi âm.";
    return;
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  const startedAt = Date.now();
  let stopped = false;

  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(context.destination);
  $("record-dot").classList.add("active");
  $("record-enrollment").textContent = "Dừng và đăng ký";
  $("record-message").textContent = "Đang ghi âm...";

  const timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    $("record-time").textContent =
      `${String(Math.floor(seconds / 60)).padStart(2, "0")}:` +
      String(seconds % 60).padStart(2, "0");
    if (seconds >= 30) stopEnrollment();
  }, 250);

  async function stopEnrollment() {
    if (stopped) return;
    stopped = true;
    const seconds = (Date.now() - startedAt) / 1000;
    clearInterval(timer);
    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
    state.recorder = null;
    $("record-dot").classList.remove("active");

    if (seconds < 20) {
      $("record-message").textContent = "Mẫu cần dài ít nhất 20 giây. Hãy ghi lại.";
      $("record-enrollment").textContent = "Ghi lại";
      return;
    }

    $("record-message").textContent = "AI đang tạo dấu giọng nói...";
    $("record-enrollment").disabled = true;
    const form = new FormData();
    form.append("speaker_name", speakerName);
    form.append("file", encodeWav(chunks, context.sampleRate), "enrollment.wav");
    try {
      const response = await fetch("/api/enrollment", {
        method: "POST",
        body: form,
      });
      const contentType = response.headers.get("content-type") || "";
      const responseBody = await response.text();
      let result = {};
      if (contentType.includes("application/json")) {
        try {
          result = JSON.parse(responseBody);
        } catch {
          throw new Error("API enrollment trả về JSON không hợp lệ.");
        }
      }
      if (response.status === 413) {
        throw new Error(
          "File ghi âm vượt giới hạn upload của Nginx. " +
            "Hãy cấu hình client_max_body_size 10m.",
        );
      }
      if (!contentType.includes("application/json")) {
        throw new Error(
          `Máy chủ trả về HTML thay vì JSON (HTTP ${response.status}).`,
        );
      }
      if (!response.ok) throw new Error(result.detail || "Đăng ký thất bại");
      $("record-message").textContent =
        `Đã đăng ký ${speakerName} từ ${result.chunks_enrolled} mẫu giọng.`;
      $("record-enrollment").textContent = "Đăng ký lại";
    } catch (error) {
      $("record-message").textContent = error.message;
      $("record-enrollment").textContent = "Thử lại";
    } finally {
      $("record-enrollment").disabled = false;
    }
  }

  state.recorder = { stop: stopEnrollment };
}

$("create-button").addEventListener("click", () => joinRoom("create"));
$("join-button").addEventListener("click", () => joinRoom("join"));
$("open-enrollment").addEventListener("click", () =>
  $("enrollment-modal").classList.remove("hidden"),
);
$("cancel-enrollment").addEventListener("click", () => {
  if (state.recorder) state.recorder.stop();
  $("enrollment-modal").classList.add("hidden");
});
$("record-enrollment").addEventListener("click", async () => {
  if (state.recorder) await state.recorder.stop();
  else {
    try {
      await startEnrollment();
    } catch (error) {
      $("record-message").textContent = `Không mở được micro: ${error.message}`;
    }
  }
});
$("mic-button").addEventListener("click", toggleMicrophone);
$("leave-button").addEventListener("click", leaveRoom);
$("playback-button").addEventListener("click", () => {
  if (state.playbackEnabled) setPlayback(false);
  else $("playback-modal").classList.remove("hidden");
});
$("cancel-playback").addEventListener("click", () =>
  $("playback-modal").classList.add("hidden"),
);
$("confirm-playback").addEventListener("click", () => {
  $("playback-modal").classList.add("hidden");
  setPlayback(true);
});
$("clear-button").addEventListener("click", async () => {
  if (!confirm("Xóa toàn bộ biên bản của phiên demo?")) return;
  const response = await fetch("/api/transcripts", { method: "DELETE" });
  if (response.ok) resetTranscriptState();
});

checkApi();
