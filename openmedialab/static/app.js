const form = document.querySelector("#job-form");
const uploadTab = document.querySelector('[data-mode="upload"]');
const urlTab = document.querySelector('[data-mode="url"]');
const uploadSource = document.querySelector("#upload-source");
const urlSource = document.querySelector("#url-source");
const fileInput = document.querySelector("#media-file");
const fileName = document.querySelector("#file-name");
const dropZone = document.querySelector("#drop-zone");
const submitButton = document.querySelector("#submit-job");
const formError = document.querySelector("#form-error");
const jobPanel = document.querySelector("#job-panel");
let sourceMode = "upload";
let activeJobId = null;
let pollTimer = null;

function setSourceMode(mode) {
  sourceMode = mode;
  const uploadActive = mode === "upload";
  uploadTab.classList.toggle("active", uploadActive);
  urlTab.classList.toggle("active", !uploadActive);
  uploadTab.setAttribute("aria-selected", String(uploadActive));
  urlTab.setAttribute("aria-selected", String(!uploadActive));
  uploadSource.classList.toggle("hidden", !uploadActive);
  urlSource.classList.toggle("hidden", uploadActive);
}

uploadTab.addEventListener("click", () => setSourceMode("upload"));
urlTab.addEventListener("click", () => setSourceMode("url"));
fileInput.addEventListener("change", () => {
  fileName.textContent = fileInput.files[0]?.name || "";
});

for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
}
dropZone.addEventListener("drop", (event) => {
  const [file] = event.dataTransfer.files;
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  fileName.textContent = file.name;
});

function checkedFormats(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((item) => item.value);
}

function getOptions() {
  return {
    extract_audio: document.querySelector("#extract-audio").checked,
    audio_formats: checkedFormats("audio-format"),
    transcribe: document.querySelector("#transcribe").checked,
    separate_sources: document.querySelector("#separate").checked,
    source_formats: checkedFormats("source-format"),
    detect_beats: document.querySelector("#beats").checked,
    extract_frames: document.querySelector("#frames").checked,
    frame_interval: Number(document.querySelector("#frame-interval").value),
    video_formats: checkedFormats("video-format"),
    extract_subtitles: document.querySelector("#embedded-subs").checked,
    whisper_model: document.querySelector("#whisper-model").value,
  };
}

function showError(message) {
  formError.textContent = message;
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function createPlayer(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["mp3", "ogg", "wav", "m4a", "aac", "opus", "mp4", "webm", "mov"].includes(extension)) return;
  const isVideo = ["mp4", "webm", "mov"].includes(extension);
  const row = document.createElement("div");
  row.className = "player-row";
  const label = document.createElement("span");
  label.textContent = file.name;
  const player = document.createElement(isVideo ? "video" : "audio");
  player.controls = true;
  player.preload = "metadata";
  player.src = file.url;
  row.append(label, player);
  document.querySelector("#players").append(row);
}

async function refreshJob() {
  if (!activeJobId) return;
  try {
    const response = await fetch(`/api/jobs/${activeJobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "Could not read the processing status.");
    document.querySelector("#job-title").textContent =
      job.status === "completed" ? "Your results are ready" :
      job.status === "failed" ? "Processing needs attention" : "Processing your media";
    const badge = document.querySelector("#job-badge");
    badge.textContent = job.status;
    badge.dataset.status = job.status;
    document.querySelector("#job-stage").textContent = job.stage || "";
    document.querySelector("#job-error").textContent = job.error || "";
    const warningList = document.querySelector("#warnings");
    warningList.replaceChildren(...(job.warnings || []).map((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      return item;
    }));

    const sourceVideo = document.querySelector("#source-video");
    const sourceAudio = document.querySelector("#source-audio");
    if (!sourceVideo.src && job.status !== "queued" && job.status !== "downloading") {
      const sourceUrl = `/api/jobs/${activeJobId}/source`;
      const sourceResponse = await fetch(sourceUrl, { method: "HEAD" });
      if (sourceResponse.ok) {
        const type = sourceResponse.headers.get("content-type") || "";
        const player = type.startsWith("video/") ? sourceVideo : sourceAudio;
        player.src = sourceUrl;
        player.classList.remove("hidden");
        document.querySelector("#source-preview").classList.remove("hidden");
      }
    }

    if (job.status === "completed") {
      submitButton.disabled = false;
      submitButton.querySelector("span").textContent = "Start processing";
      const list = document.querySelector("#outputs");
      const players = document.querySelector("#players");
      list.replaceChildren();
      players.replaceChildren();
      for (const file of job.files) {
        const row = document.createElement("div");
        row.className = "output-item";
        const name = document.createElement("span");
        name.className = "output-name";
        name.textContent = file.name;
        const size = document.createElement("span");
        size.className = "output-meta";
        size.textContent = formatSize(file.size);
        const link = document.createElement("a");
        link.href = file.url;
        link.download = file.name;
        link.textContent = "Download";
        row.append(name, size, link);
        list.append(row);
        createPlayer(file);
      }
      document.querySelector("#output-preview").classList.toggle("hidden", players.childElementCount === 0);
    } else if (job.status === "failed") {
      submitButton.disabled = false;
      submitButton.querySelector("span").textContent = "Start processing";
    } else {
      pollTimer = window.setTimeout(refreshJob, 1800);
    }
  } catch (error) {
    submitButton.disabled = false;
    document.querySelector("#job-error").textContent = error.message;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const options = getOptions();
  if (!options.extract_audio && !options.transcribe && !options.separate_sources &&
      !options.detect_beats && !options.extract_frames && !options.video_formats.length &&
      !options.extract_subtitles) {
    showError("Select at least one extraction or export operation.");
    return;
  }
  if (options.extract_audio && !options.audio_formats.length) {
    showError("Choose at least one audio format.");
    return;
  }
  if (options.separate_sources && !options.source_formats.length) {
    showError("Choose at least one source separation format.");
    return;
  }
  if (sourceMode === "upload" && !fileInput.files[0]) {
    showError("Choose an audio or video file to upload.");
    return;
  }
  if (sourceMode === "url" && !document.querySelector("#media-url").value.trim()) {
    showError("Enter a direct media, YouTube, or Vimeo URL.");
    return;
  }
  if (pollTimer) window.clearTimeout(pollTimer);
  activeJobId = null;
  submitButton.disabled = true;
  submitButton.querySelector("span").textContent = "Starting…";
  jobPanel.classList.remove("hidden");
  document.querySelector("#outputs").replaceChildren();
  document.querySelector("#players").replaceChildren();
  document.querySelector("#output-preview").classList.add("hidden");
  document.querySelector("#source-preview").classList.add("hidden");
  for (const player of [document.querySelector("#source-video"), document.querySelector("#source-audio")]) {
    player.pause();
    player.removeAttribute("src");
    player.load();
    player.classList.add("hidden");
  }
  document.querySelector("#job-error").textContent = "";
  document.querySelector("#job-stage").textContent = "Submitting job…";
  jobPanel.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    let response;
    if (sourceMode === "upload") {
      const data = new FormData();
      data.append("file", fileInput.files[0]);
      data.append("options", JSON.stringify(options));
      response = await fetch("/api/jobs/upload", { method: "POST", body: data });
    } else {
      response = await fetch("/api/jobs/url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: document.querySelector("#media-url").value.trim(), options }),
      });
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not start the processing job.");
    activeJobId = result.id;
    submitButton.querySelector("span").textContent = "Processing…";
    await refreshJob();
  } catch (error) {
    submitButton.disabled = false;
    submitButton.querySelector("span").textContent = "Start processing";
    jobPanel.classList.add("hidden");
    showError(error.message);
  }
});
