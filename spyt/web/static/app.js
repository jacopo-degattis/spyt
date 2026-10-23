/* Spyt web UI — client side */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  jobId: null,
  source: null,
  lastEventId: 0,
  finished: false,
  reconnectTimer: null,
  tracks: new Map(),
  filter: "all",
  spotifyName: "",
  snapshot: null,
};

const els = {};
let credentials = {};

document.addEventListener("DOMContentLoaded", init);

function init() {
  els.formView = $("#form-view");
  els.progressView = $("#progress-view");
  els.form = $("#transfer-form");
  els.startButton = $("#start-button");
  els.startLabel = $(".button-label", els.startButton);
  els.startSpinner = $(".spinner", els.startButton);
  els.formError = $("#form-error");
  els.playlist = $("#playlist");
  els.playlistName = $("#playlist-name");
  els.privacy = $("#privacy");
  els.maxItems = $("#max-items");
  els.minScore = $("#min-score");
  els.minScoreValue = $("#min-score-value");
  els.preview = $("#preview");

  els.stageTitle = $("#stage-title");
  els.stageMessage = $("#stage-message");
  els.progressEyebrow = $("#progress-eyebrow");
  els.bar = $("#bar");
  els.barFill = $("#bar-fill");
  els.progressText = $("#progress-text");
  els.progressPct = $("#progress-pct");
  els.counts = $("#counts");
  els.steps = $$("#steps li");
  els.trackToolbar = $("#track-toolbar");
  els.trackList = $("#track-list");
  els.emptyTracks = $("#empty-tracks");
  els.countAll = $("#count-all");
  els.cancelButton = $("#cancel-button");

  els.donePanel = $("#done-panel");
  els.doneTitle = $("#done-title");
  els.doneMessage = $("#done-message");
  els.openPlaylist = $("#open-playlist");
  els.downloadReport = $("#download-report");
  els.againButton = $("#again-button");

  els.previewPanel = $("#preview-panel");
  els.previewName = $("#preview-name");
  els.previewPrivacy = $("#preview-privacy");
  els.createButton = $("#create-button");
  els.downloadPreview = $("#download-preview");
  els.previewError = $("#preview-error");

  els.errorPanel = $("#error-panel");
  els.errorTitle = $("#error-title");
  els.errorMessage = $("#error-message");
  els.errorPlaylist = $("#error-playlist");
  els.retryButton = $("#retry-button");

  credentials = {
    cookies: createCredential("cookies"),
    headers: createCredential("headers"),
  };

  els.minScore.addEventListener("input", () => {
    els.minScoreValue.textContent = `${Math.round(Number(els.minScore.value) * 100)}%`;
  });

  els.preview.addEventListener("change", () => {
    const badge = $("#headers-card .badge");
    badge.textContent = els.preview.checked ? "Optional" : "Required";
    badge.classList.toggle("required", !els.preview.checked);
    badge.classList.toggle("subtle", els.preview.checked);
  });

  els.form.addEventListener("submit", startTransfer);
  els.cancelButton.addEventListener("click", cancelTransfer);
  els.createButton.addEventListener("click", createFromPreview);
  els.againButton.addEventListener("click", resetApp);
  els.retryButton.addEventListener("click", resetApp);
  $$(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      applyFilter();
    });
  });

  restoreSession();
}

/* --------------------------------------------------------- credentials */

function createCredential(kind) {
  const drop = $(`#${kind}-drop`);
  const input = $(`#${kind}-file`);
  const textarea = $(`#${kind}-text`);
  const status = $(`#${kind}-state`);
  let fileName = "";

  const refresh = () => {
    const hasText = textarea.value.trim().length > 0;
    drop.classList.toggle("is-loaded", hasText);
    status.hidden = !hasText;
    status.textContent = hasText
      ? fileName
        ? `${fileName} · ready`
        : "Pasted content ready"
      : "";
  };

  const loadFile = (file) => {
    if (!file) return;
    if (file.size > 3_000_000) {
      showFormError(`${kind === "cookies" ? "Cookies" : "Headers"} file is too large (${formatBytes(file.size)}).`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      textarea.value = String(reader.result || "");
      fileName = file.name;
      refresh();
      clearFormError();
    };
    reader.onerror = () => showFormError(`Could not read ${file.name}.`);
    reader.readAsText(file, "utf-8");
  };

  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });

  ["dragenter", "dragover"].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      drop.classList.add("is-dragging");
    });
  });

  ["dragleave", "dragend"].forEach((name) => {
    drop.addEventListener(name, () => drop.classList.remove("is-dragging"));
  });

  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    drop.classList.remove("is-dragging");
    loadFile(event.dataTransfer.files && event.dataTransfer.files[0]);
  });

  input.addEventListener("change", () => loadFile(input.files && input.files[0]));

  textarea.addEventListener("input", () => {
    fileName = "";
    refresh();
    clearFormError();
  });

  return {
    get value() {
      return textarea.value.trim();
    },
    clear() {
      textarea.value = "";
      fileName = "";
      input.value = "";
      refresh();
    },
  };
}

/* --------------------------------------------------------------- start */

async function startTransfer(event) {
  event.preventDefault();
  clearFormError();

  const playlist = els.playlist.value.trim();
  const cookies = credentials.cookies.value;
  const headers = credentials.headers.value;
  const preview = els.preview.checked;

  if (!playlist) return showFormError("Paste a Spotify playlist link first.");
  if (!cookies) return showFormError("Add your Spotify cookies.");
  if (!headers && !preview) {
    return showFormError(
      "Add your YouTube Music headers, or enable “Preview matches first” to match without creating."
    );
  }

  const maxItemsValue = els.maxItems.value.trim();
  const maxItems = maxItemsValue ? Number(maxItemsValue) : null;
  if (maxItems !== null && (!Number.isInteger(maxItems) || maxItems < 1)) {
    return showFormError("The track limit must be a positive whole number.");
  }

  const payload = {
    playlist,
    cookies,
    headers: headers || null,
    name: els.playlistName.value.trim() || null,
    privacy: els.privacy.value,
    max_items: maxItems,
    min_score: Number(els.minScore.value),
    preview,
  };

  setSubmitting(true);
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiError(data));

    startJob(data.id);
  } catch (error) {
    showFormError(error.message || "Could not start the transfer.");
  } finally {
    setSubmitting(false);
  }
}

function startJob(jobId) {
  state.jobId = jobId;
  state.lastEventId = 0;
  state.source = null;
  state.finished = false;
  state.filter = "all";
  state.spotifyName = "";
  state.snapshot = null;
  state.tracks.clear();
  sessionStorage.setItem("spyt-job", jobId);

  resetProgressUI();
  showProgressView();
  connect();
}

/* ----------------------------------------------------------- SSE stream */

function connect() {
  if (!state.jobId) return;
  if (state.source) {
    state.source.close();
    state.source = null;
  }

  const source = new EventSource(
    `/api/jobs/${encodeURIComponent(state.jobId)}/events?after=${state.lastEventId}`
  );
  state.source = source;

  source.addEventListener("state", (event) => {
    rememberEvent(event);
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    applyState(data);
  });

  source.addEventListener("track", (event) => {
    rememberEvent(event);
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    addTrack(data);
  });

  source.onerror = () => {
    source.close();
    if (state.source === source) state.source = null;
    if (state.finished) return;
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(() => {
      if (state.jobId && !state.finished) connect();
    }, 1500);
  };
}

function rememberEvent(event) {
  const id = Number(event.lastEventId);
  if (Number.isFinite(id) && id > state.lastEventId) state.lastEventId = id;
}

function closeStream() {
  state.finished = true;
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  clearTimeout(state.reconnectTimer);
}

/* --------------------------------------------------------------- state */

function applyState(snapshot) {
  state.snapshot = snapshot;
  if (snapshot.spotify && snapshot.spotify.name) {
    state.spotifyName = snapshot.spotify.name;
  }

  els.stageTitle.textContent = stageTitle(snapshot);
  els.stageMessage.textContent = snapshot.message || "";
  els.progressEyebrow.textContent = eyebrow(snapshot);

  renderProgress(snapshot);
  renderCounts(snapshot);
  renderSteps(snapshot);

  if (snapshot.status === "running") {
    els.cancelButton.hidden = false;
    els.cancelButton.disabled = false;
    els.cancelButton.textContent = "Cancel";
    hidePanels({ error: true });
  }

  if (snapshot.status === "ready") {
    closeStream();
    showPreview(snapshot);
  } else if (snapshot.status === "done") {
    closeStream();
    showDone(snapshot);
  } else if (snapshot.status === "failed" || snapshot.status === "cancelled") {
    closeStream();
    showFailure(snapshot);
  }
}

function stageTitle(snapshot) {
  switch (snapshot.status) {
    case "queued":
      return "Starting…";
    case "ready":
      return "Matches ready";
    case "done":
      return "Transfer complete";
    case "cancelled":
      return "Transfer cancelled";
    case "failed":
      return "Transfer failed";
    default:
      break;
  }
  switch (snapshot.stage) {
    case "spotify":
      return "Reading the Spotify playlist";
    case "youtube":
      return "Connecting to YouTube Music";
    case "matching":
      return "Matching tracks";
    case "creating":
      return "Creating the playlist";
    default:
      return "Working…";
  }
}

function eyebrow(snapshot) {
  if (snapshot.status === "ready") return "Preview";
  if (snapshot.status === "done") return "Done";
  if (snapshot.status === "failed") return "Failed";
  if (snapshot.status === "cancelled") return "Cancelled";
  return "Transferring";
}

function renderProgress(snapshot) {
  const progress = snapshot.progress || {};
  const total = Number.isFinite(progress.total) ? progress.total : null;
  const done = Number(progress.done) || 0;
  const percent = total && total > 0 ? Math.min(100, (done / total) * 100) : null;

  if (snapshot.status === "done") {
    els.bar.classList.remove("indeterminate");
    els.barFill.style.width = "100%";
    els.progressText.textContent = "Complete";
    els.progressPct.textContent = "100%";
    return;
  }

  if (percent === null) {
    els.bar.classList.add("indeterminate");
    els.barFill.style.width = "";
    els.progressText.textContent = done ? `${done} processed` : "Working…";
    els.progressPct.textContent = "";
    return;
  }

  els.bar.classList.remove("indeterminate");
  els.barFill.style.width = `${percent}%`;
  els.progressText.textContent = `${done} of ${total}`;
  els.progressPct.textContent = `${Math.round(percent)}%`;
}

function renderCounts(snapshot) {
  const counts = snapshot.counts || {};
  const chips = [];

  if (counts.matched) chips.push(chip("matched", `${counts.matched} matched`));
  if (counts.unmatched) chips.push(chip("unmatched", `${counts.unmatched} need review`));
  if (counts.skipped) chips.push(chip("skipped", `${counts.skipped} skipped`));
  if (counts.duplicate) chips.push(chip("duplicate", `${counts.duplicate} duplicates`));
  if (counts.added) chips.push(chip("added", `${counts.added} added`));

  els.counts.replaceChildren(...chips);
}

function chip(kind, text) {
  const span = document.createElement("span");
  span.className = `count-chip ${kind}`;
  span.textContent = text;
  return span;
}

function renderSteps(snapshot) {
  const stageIndex = {
    spotify: 0,
    youtube: 0,
    matching: 1,
    creating: 2,
    done: 2,
    ready: 1,
  };
  const index = stageIndex[snapshot.stage];
  const isDone = snapshot.status === "done";
  const isProblem = snapshot.status === "failed" || snapshot.status === "cancelled";

  els.steps.forEach((step, stepIndex) => {
    step.classList.remove("is-active", "is-done", "is-error");
    if (isDone || (index !== undefined && stepIndex < index)) {
      step.classList.add("is-done");
    }
    if (snapshot.status === "ready" && stepIndex <= index) {
      step.classList.add("is-done");
      step.classList.remove("is-active");
    } else if (!isDone && stepIndex === index) {
      step.classList.add("is-active");
      if (isProblem) step.classList.add("is-error");
    }
  });
}

/* -------------------------------------------------------------- tracks */

function addTrack(track) {
  state.tracks.set(track.position, track);
  const existing = els.trackList.querySelector(`[data-position="${track.position}"]`);
  const row = buildTrackRow(track);

  if (existing) {
    existing.replaceWith(row);
    applyFilterToRow(row);
  } else {
    const nearBottom =
      els.trackList.scrollHeight - els.trackList.scrollTop - els.trackList.clientHeight < 80;
    els.trackList.appendChild(row);
    applyFilterToRow(row);
    if (nearBottom || state.tracks.size <= 4) {
      els.trackList.scrollTop = els.trackList.scrollHeight;
    }
  }

  els.emptyTracks.hidden = true;
  els.trackToolbar.hidden = false;
  els.countAll.textContent = String(state.tracks.size);
}

function buildTrackRow(track) {
  const row = document.createElement("li");
  row.className = "track";
  row.dataset.status = track.status;
  row.dataset.position = String(track.position);
  row.title = track.reason || "";

  const icon = document.createElement("span");
  icon.className = "track-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = statusIcon(track.status);

  const main = document.createElement("div");
  main.className = "track-main";
  main.appendChild(trackLine(track.spotify && track.spotify.name, "track-title"));
  main.appendChild(
    trackLine(formatArtists(track.spotify && track.spotify.artists), "track-artist")
  );

  const arrow = document.createElement("span");
  arrow.className = "track-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";

  const match = document.createElement("div");
  match.className = "track-match";

  if (track.youtube) {
    match.appendChild(trackLine(track.youtube.title, "track-title"));
    const detail = [formatArtists(track.youtube.artists), durationText(track.youtube.duration_seconds)]
      .filter(Boolean)
      .join(" · ");
    match.appendChild(trackLine(detail, "track-artist"));
  } else {
    match.appendChild(trackLine(matchFallback(track), "track-title"));
    if (track.reason) match.appendChild(trackLine(track.reason, "track-artist"));
  }

  const score = document.createElement("span");
  score.className = "score";
  score.textContent =
    track.score === null || track.score === undefined
      ? ""
      : `${Math.round(Number(track.score) * 100)}%`;

  row.append(icon, main, arrow, match, score);
  return row;
}

function trackLine(text, className) {
  const element = document.createElement("p");
  element.className = className;
  element.textContent = text || "";
  return element;
}

function statusIcon(status) {
  switch (status) {
    case "matched":
      return "✓";
    case "duplicate":
      return "⧉";
    case "skipped":
      return "–";
    default:
      return "?";
  }
}

function matchFallback(track) {
  if (track.status === "skipped") return `Skipped · ${track.reason || "not transferable"}`;
  if (track.status === "unmatched") return "No confident match";
  return "—";
}

function formatArtists(artists) {
  if (!Array.isArray(artists)) return "";
  return artists.filter(Boolean).join(", ");
}

function durationText(seconds) {
  if (!Number.isFinite(Number(seconds))) return "";
  const total = Number(seconds);
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}

function applyFilter() {
  $$(".filter").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.filter === state.filter);
  });
  $$("#track-list .track").forEach(applyFilterToRow);
}

function applyFilterToRow(row) {
  const status = row.dataset.status;
  const filter = state.filter;
  const visible =
    filter === "all" ||
    (filter === "matched" && (status === "matched" || status === "duplicate")) ||
    (filter === "unmatched" && status === "unmatched") ||
    (filter === "skipped" && status === "skipped");
  row.classList.toggle("hidden", !visible);
}

/* -------------------------------------------------------------- panels */

function showDone(snapshot) {
  hidePanels();
  els.cancelButton.hidden = true;

  const target = snapshot.target || {};
  const added = (snapshot.counts && snapshot.counts.added) || 0;
  const name = target.name || state.spotifyName || "Your playlist";

  els.doneTitle.textContent = `“${name}” is on YouTube Music`;
  els.doneMessage.textContent =
    added === 1 ? "1 track was added." : `${added} tracks were added.`;
  els.donePanel.hidden = false;

  if (target.url) {
    els.openPlaylist.href = target.url;
    els.openPlaylist.hidden = false;
  } else {
    els.openPlaylist.hidden = true;
  }
  els.downloadReport.href = reportUrl();
}

function showPreview(snapshot) {
  hidePanels({ error: true });
  els.cancelButton.hidden = true;

  els.previewName.value =
    (snapshot.options && snapshot.options.name) || state.spotifyName || "";
  els.previewPrivacy.value =
    (snapshot.options && snapshot.options.privacy) || "private";
  els.previewError.hidden = true;
  els.previewPanel.hidden = false;
  els.downloadPreview.href = reportUrl();
  els.createButton.disabled = false;
  els.createButton.textContent = "Create playlist";
}

function showFailure(snapshot) {
  hidePanels();
  els.cancelButton.hidden = true;

  els.errorTitle.textContent =
    snapshot.status === "cancelled" ? "Transfer cancelled" : "Transfer failed";
  els.errorMessage.textContent =
    snapshot.error || snapshot.message || "Something unexpected happened.";

  const target = snapshot.target || {};
  if (target.url) {
    els.errorPlaylist.href = target.url;
    els.errorPlaylist.hidden = false;
  } else {
    els.errorPlaylist.hidden = true;
  }
  els.errorPanel.hidden = false;
}

function hidePanels({ error = false } = {}) {
  els.donePanel.hidden = true;
  els.previewPanel.hidden = true;
  if (error) els.errorPanel.hidden = true;
}

/* ------------------------------------------------------------- actions */

async function cancelTransfer() {
  if (!state.jobId) return;
  els.cancelButton.disabled = true;
  els.cancelButton.textContent = "Cancelling…";
  try {
    await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {
      method: "POST",
    });
  } catch {
    els.cancelButton.disabled = false;
    els.cancelButton.textContent = "Cancel";
  }
}

async function createFromPreview() {
  if (!state.jobId) return;
  els.previewError.hidden = true;
  els.createButton.disabled = true;
  els.createButton.textContent = "Creating…";

  try {
    const response = await fetch(
      `/api/jobs/${encodeURIComponent(state.jobId)}/create`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: els.previewName.value.trim() || null,
          privacy: els.previewPrivacy.value,
        }),
      }
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiError(data));

    hidePanels({ error: true });
    state.finished = false;
    applyState(data);
    connect();
  } catch (error) {
    els.previewError.textContent = error.message || "Could not create the playlist.";
    els.previewError.hidden = false;
    els.createButton.disabled = false;
    els.createButton.textContent = "Create playlist";
  }
}

function resetApp() {
  closeStream();
  state.jobId = null;
  state.lastEventId = 0;
  state.tracks.clear();
  state.filter = "all";
  state.spotifyName = "";
  sessionStorage.removeItem("spyt-job");

  els.form.reset();
  els.minScoreValue.textContent = "80%";
  const badge = $("#headers-card .badge");
  badge.textContent = "Required";
  badge.classList.add("required");
  badge.classList.remove("subtle");
  credentials.cookies.clear();
  credentials.headers.clear();

  clearFormError();
  resetProgressUI();
  els.progressView.classList.add("hidden");
  els.formView.classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function resetProgressUI() {
  els.stageTitle.textContent = "Starting…";
  els.stageMessage.textContent = "";
  els.progressEyebrow.textContent = "Transferring";
  els.bar.classList.add("indeterminate");
  els.barFill.style.width = "";
  els.progressText.textContent = "Starting…";
  els.progressPct.textContent = "";
  els.counts.replaceChildren();
  els.trackList.replaceChildren();
  els.emptyTracks.hidden = false;
  els.trackToolbar.hidden = true;
  els.countAll.textContent = "0";
  els.cancelButton.hidden = false;
  els.cancelButton.disabled = false;
  els.cancelButton.textContent = "Cancel";
  hidePanels();
  $$(".filter").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.filter === "all");
  });
  els.steps.forEach((step) => step.classList.remove("is-active", "is-done", "is-error"));
}

function showProgressView() {
  els.formView.classList.add("hidden");
  els.progressView.classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ------------------------------------------------------------- session */

async function restoreSession() {
  const saved = sessionStorage.getItem("spyt-job");
  if (!saved) return;

  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(saved)}`);
    if (!response.ok) throw new Error("gone");
    const snapshot = await response.json();

    state.jobId = saved;
    state.lastEventId = 0;
    state.finished = false;
    resetProgressUI();
    showProgressView();
    applyState(snapshot);
    if (!state.finished) connect();
  } catch {
    sessionStorage.removeItem("spyt-job");
  }
}

/* --------------------------------------------------------------- utils */

function reportUrl() {
  return `/api/jobs/${encodeURIComponent(state.jobId)}/report`;
}

function setSubmitting(submitting) {
  els.startButton.disabled = submitting;
  els.startSpinner.hidden = !submitting;
  els.startLabel.textContent = submitting ? "Starting…" : "Transfer playlist";
}

function showFormError(message) {
  els.formError.textContent = message;
  els.formError.hidden = false;
}

function clearFormError() {
  els.formError.hidden = true;
  els.formError.textContent = "";
}

function apiError(data) {
  if (!data) return "Request failed.";
  const detail = data.detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => entry.msg || entry.message || JSON.stringify(entry))
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  if (typeof data.error === "string") return data.error;
  return "Request failed.";
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
