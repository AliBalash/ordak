const submitButton = document.getElementById("submit-btn");
const openProfileButton = document.getElementById("open-profile-btn");
const newChatButton = document.getElementById("new-chat-btn");
const questionInput = document.getElementById("question");
const statusBadge = document.getElementById("status-badge");
const logsList = document.getElementById("logs");
const screenshotsList = document.getElementById("screenshots");
const profileMessage = document.getElementById("profile-message");
const jobIdLabel = document.getElementById("job-id");
const modeSwitch = document.getElementById("mode-switch");
const imageInput = document.getElementById("image-input");
const uploadPreview = document.getElementById("upload-preview");
const uploadPreviewImage = document.getElementById("upload-preview-image");
const uploadName = document.getElementById("upload-name");
const uploadSize = document.getElementById("upload-size");
const clearUploadButton = document.getElementById("clear-upload-btn");
const conversationList = document.getElementById("conversation-list");
const conversationTitle = document.getElementById("conversation-title");
const conversationSubtitle = document.getElementById("conversation-subtitle");
const conversationMode = document.getElementById("conversation-mode");
const conversationModeLogo = document.getElementById("conversation-mode-logo");
const conversationModeLabel = document.getElementById("conversation-mode-label");
const pinConversationButton = document.getElementById("pin-conversation-btn");
const tabHealthPill = document.getElementById("tab-health-pill");
const providerLink = document.getElementById("provider-link");
const providerSwitch = document.getElementById("provider-switch");
const chatThread = document.getElementById("chat-thread");
const threadEmpty = document.getElementById("thread-empty");
const composerHint = document.getElementById("composer-hint");
const composerNote = document.getElementById("composer-note");

let socket = null;
let fallbackPollHandle = null;
let previewUrl = null;
let currentJobId = null;
let currentConversationId = null;
let currentConversationTitle = "ordak";
let currentConversationExternalUrl = null;
let currentConversationTabAlive = false;
let currentConversationPinned = false;
let currentConversationJobs = [];
let currentProvider = "gemini";
let currentMode = "chat";
let pendingNewChat = false;

const params = new URL(window.location.href).searchParams;
const initialConversationId = params.get("conversation");
const initialJobId = params.get("job");

const terminalStates = new Set(["completed", "failed", "manual_verification_required", "cancelled"]);

const providerLabels = {
  gemini: "Gemini",
  chatgpt: "ChatGPT",
};

const providerIcons = {
  gemini: "/static/icons/gemini.svg",
  chatgpt: "/static/icons/chatgpt.svg",
};

const providerModeLabels = {
  gemini: {
    chat: "Ask Gemini",
    image_analyze: "Analyze Image",
    image_generate: "Create Image",
  },
  chatgpt: {
    chat: "Ask ChatGPT",
    image_analyze: "Analyze Image",
    image_generate: "Create Image",
  },
};

const modePlaceholders = {
  gemini: {
    chat: "سلام Gemini خوبی؟",
    image_analyze: "این تصویر را تحلیل کن و کوتاه توضیح بده.",
    image_generate: "پس‌زمینه این تصویر را حذف کن و سفید کن.",
  },
  chatgpt: {
    chat: "سلام ChatGPT خوبی؟",
    image_analyze: "این تصویر را تحلیل کن و کوتاه توضیح بده.",
    image_generate: "پس‌زمینه این تصویر را حذف کن و سفید کن.",
  },
};

const modeHints = {
  gemini: {
    chat: "Continues in the current Gemini tab.",
    image_analyze: "Uploads one image and continues in the same Gemini tab.",
    image_generate: "Keeps image generation in the same Gemini thread.",
  },
  chatgpt: {
    chat: "Continues in the current ChatGPT tab.",
    image_analyze: "Uploads one image and continues in the same ChatGPT tab.",
    image_generate: "Keeps image generation in the same ChatGPT thread.",
  },
};

const statusLabels = {
  idle: "Idle",
  queued: "Queued",
  running: "Running",
  checking_browser: "Checking Chrome",
  opening_provider_tab: "Opening Tab",
  opening_gemini_tab: "Opening Tab",
  navigating_to_gemini: "Opening Page",
  checking_login: "Checking Session",
  finding_input: "Finding Input",
  submitting_prompt: "Submitting Prompt",
  waiting_for_response: "Waiting for Answer",
  extracting_answer: "Extracting Result",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  completed: "Completed",
  failed: "Failed",
  manual_verification_required: "Verification Required",
};

function formatStatus(status) {
  return statusLabels[status] || status.replaceAll("_", " ");
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function setStatus(status, jobId = null) {
  statusBadge.textContent = formatStatus(status);
  statusBadge.className = `badge ${status}`;
  jobIdLabel.textContent = jobId ? `Job ${jobId.slice(0, 8)}` : "Ready.";
}

function applyProviderTheme() {
  document.body.dataset.providerTheme = currentProvider;
}

function syncModeButtons() {
  applyProviderTheme();
  providerSwitch.querySelectorAll(".provider-chip").forEach((button) => {
    button.classList.toggle("active", button.dataset.provider === currentProvider);
    button.disabled = currentConversationJobs.length > 0 && button.dataset.provider !== currentProvider && !pendingNewChat;
  });
  modeSwitch.querySelectorAll(".mode-chip").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === currentMode);
  });
  questionInput.placeholder = modePlaceholders[currentProvider][currentMode];
  composerHint.textContent = modeHints[currentProvider][currentMode];
  composerNote.textContent = pendingNewChat
    ? `Next send opens a fresh ${providerLabels[currentProvider]} tab.`
    : `Next send stays in the current ${providerLabels[currentProvider]} tab.`;
  conversationModeLogo.src = providerIcons[currentProvider];
  conversationModeLabel.textContent = providerModeLabels[currentProvider][currentMode] || currentMode;
}

function emptyListItem(message) {
  const item = document.createElement("li");
  item.className = "empty-state";
  item.textContent = message;
  return item;
}

function updateUrl() {
  const url = new URL(window.location.href);
  if (currentConversationId) {
    url.searchParams.set("conversation", currentConversationId);
    url.searchParams.delete("job");
  } else {
    url.searchParams.delete("conversation");
    url.searchParams.delete("job");
  }
  window.history.replaceState({}, "", url);
}

function cleanupPreviewUrl() {
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
}

function renderUploadPreview(file) {
  cleanupPreviewUrl();
  if (!file) {
    uploadPreview.classList.add("hidden");
    uploadPreviewImage.removeAttribute("src");
    uploadName.textContent = "";
    uploadSize.textContent = "";
    return;
  }
  previewUrl = URL.createObjectURL(file);
  uploadPreview.classList.remove("hidden");
  uploadPreviewImage.src = previewUrl;
  uploadName.textContent = file.name;
  uploadSize.textContent = formatBytes(file.size);
}

function clearUpload() {
  imageInput.value = "";
  renderUploadPreview(null);
}

function renderLogs(logs) {
  logsList.innerHTML = "";
  if (!logs?.length) {
    logsList.appendChild(emptyListItem("No events yet."));
    return;
  }
  logs.slice(-10).reverse().forEach((entry) => {
    const item = document.createElement("li");
    item.className = `mini-log-item ${entry.level || "info"}`;
    item.innerHTML = `
      <span class="mini-log-level">${entry.level || "info"}</span>
      <strong>${new Date(entry.timestamp).toLocaleTimeString()}</strong>
      <p>${entry.message}</p>
    `;
    logsList.appendChild(item);
  });
}

function renderScreenshots(paths) {
  screenshotsList.innerHTML = "";
  if (!paths?.length) {
    screenshotsList.appendChild(emptyListItem("No screenshots."));
    return;
  }
  paths.forEach((path) => {
    const item = document.createElement("li");
    item.className = "artifact-card";
    const link = document.createElement("a");
    link.href = `/${path}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = path.split("/").pop();
    item.appendChild(link);
    screenshotsList.appendChild(item);
  });
}

function buildImageGallery(paths, labelPrefix) {
  if (!paths?.length) return null;
  const list = document.createElement("div");
  list.className = "bubble-gallery";
  paths.forEach((path, index) => {
    const card = document.createElement("a");
    card.className = "bubble-image";
    card.href = `/${path}`;
    card.target = "_blank";
    card.rel = "noreferrer";

    const image = document.createElement("img");
    image.src = `/${path}`;
    image.alt = `${labelPrefix} ${index + 1}`;
    image.loading = "lazy";

    const caption = document.createElement("span");
    caption.textContent = `${labelPrefix} ${index + 1}`;

    card.append(image, caption);
    list.appendChild(card);
  });
  return list;
}

function buildCompareNode(referencePath, resultPath) {
  if (!referencePath || !resultPath) return null;
  const wrap = document.createElement("div");
  wrap.className = "compare-card";
  wrap.innerHTML = `
    <div class="compare-head">
      <strong>Before / After</strong>
      <span>Reference vs generated result</span>
    </div>
    <div class="compare-stage">
      <img class="compare-base" src="/${referencePath}" alt="Reference image" />
      <div class="compare-overlay" style="clip-path: inset(0 0 0 50%);">
        <img src="/${resultPath}" alt="Result image" />
      </div>
      <div class="compare-divider" style="left: 50%;"></div>
      <input class="compare-slider" type="range" min="0" max="100" value="50" />
    </div>
  `;
  const slider = wrap.querySelector(".compare-slider");
  const overlay = wrap.querySelector(".compare-overlay");
  const divider = wrap.querySelector(".compare-divider");
  slider.addEventListener("input", () => {
    const value = slider.value;
    overlay.style.clipPath = `inset(0 0 0 ${value}%)`;
    divider.style.left = `${value}%`;
  });
  return wrap;
}

function buildAssistantSummary(job) {
  if (job.answer) return job.answer;
  if (!terminalStates.has(job.status)) {
    return `${providerLabels[job.provider] || "Assistant"} is still working on this response...`;
  }
  if (job.error_title || job.error_message) {
    return [job.error_title, job.error_message].filter(Boolean).join(" • ");
  }
  return "No answer was captured.";
}

function buildErrorMeta(job) {
  if (!job.error_code && !job.suggested_action) return null;
  const wrap = document.createElement("div");
  wrap.className = "error-meta";
  if (job.error_code) {
    const code = document.createElement("span");
    code.className = "error-code";
    code.textContent = job.error_code;
    wrap.appendChild(code);
  }
  if (job.suggested_action) {
    const note = document.createElement("p");
    note.textContent = job.suggested_action;
    wrap.appendChild(note);
  }
  return wrap;
}

function createActionButton(label, variant, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `action-pill ${variant}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

async function apiPostJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error_message || "Request failed.");
  }
  return payload;
}

async function toggleConversationPin() {
  if (!currentConversationId) return;
  try {
    const conversation = await apiPostJson(
      `/api/conversations/${currentConversationId}/${currentConversationPinned ? "unpin" : "pin"}`
    );
    currentConversationPinned = Boolean(conversation.pinned);
    updateConversationHeader();
    await fetchConversationList();
  } catch (error) {
    profileMessage.textContent = error.message;
  }
}

async function cancelJob(jobId) {
  try {
    const job = await apiPostJson(`/api/jobs/${jobId}/cancel`);
    upsertConversationJob(job);
    renderCurrentConversation();
    await fetchConversationList();
  } catch (error) {
    profileMessage.textContent = error.message;
  }
}

async function retryJob(jobId, strategy) {
  try {
    const payload = await apiPostJson(`/api/jobs/${jobId}/retry`, { strategy });
    pendingNewChat = false;
    currentConversationId = payload.conversation_id;
    currentJobId = payload.job_id;
    connectSocket(payload.job_id);
    await loadConversation(payload.conversation_id);
  } catch (error) {
    profileMessage.textContent = error.message;
  }
}

async function resumeJob(jobId, strategy) {
  try {
    const payload = await apiPostJson(`/api/jobs/${jobId}/resume`, { strategy });
    pendingNewChat = false;
    currentConversationId = payload.conversation_id;
    currentJobId = payload.job_id;
    connectSocket(payload.job_id);
    await loadConversation(payload.conversation_id);
  } catch (error) {
    profileMessage.textContent = error.message;
  }
}

function buildActionRow(job) {
  const row = document.createElement("div");
  row.className = "bubble-actions";
  if (!terminalStates.has(job.status)) {
    row.appendChild(createActionButton("Cancel", "danger", () => cancelJob(job.job_id)));
    return row;
  }
  if (job.recoverable) {
    row.appendChild(createActionButton("Retry same tab", "secondary", () => retryJob(job.job_id, "same_tab")));
    row.appendChild(createActionButton("Retry new tab", "secondary", () => retryJob(job.job_id, "new_tab_same_conversation")));
    row.appendChild(createActionButton("Resume same tab", "secondary", () => resumeJob(job.job_id, "same_tab")));
    row.appendChild(createActionButton("Resume new tab", "secondary", () => resumeJob(job.job_id, "new_tab_same_conversation")));
  }
  row.appendChild(createActionButton("New clean chat", "primary", startNewChat));
  return row;
}

function buildAssistantExtras(job, referencePath) {
  const fragment = document.createElement("div");
  fragment.className = "assistant-rich";
  const compareNode = referencePath && job.output_images?.[0]
    ? buildCompareNode(referencePath, job.output_images[0])
    : null;
  const resultGallery = buildImageGallery(job.output_images, "Result");
  const errorMeta = buildErrorMeta(job);
  const actions = buildActionRow(job);

  if (compareNode) fragment.appendChild(compareNode);
  if (resultGallery) fragment.appendChild(resultGallery);
  if (errorMeta) fragment.appendChild(errorMeta);
  if (job.output_images?.length) {
    const actionLinks = document.createElement("div");
    actionLinks.className = "result-links";
    const downloadLink = document.createElement("a");
    downloadLink.href = `/${job.output_images[0]}`;
    downloadLink.textContent = "Download result";
    downloadLink.target = "_blank";
    downloadLink.rel = "noreferrer";
    actionLinks.appendChild(downloadLink);
    if (currentConversationExternalUrl) {
      const openLink = document.createElement("a");
      openLink.href = currentConversationExternalUrl;
      openLink.textContent = "Open in Chrome";
      openLink.target = "_blank";
      openLink.rel = "noreferrer";
      actionLinks.appendChild(openLink);
    }
    fragment.appendChild(actionLinks);
  }
  fragment.appendChild(actions);
  return fragment.childNodes.length ? fragment : null;
}

function buildMessage(role, content, job, extraNode = null) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const assistantLabel = providerLabels[job.provider] || "Assistant";
  meta.innerHTML = `
    <span class="message-role">${role === "user" ? "You" : assistantLabel}</span>
    <span>${new Date(job.created_at).toLocaleTimeString()}</span>
  `;

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;

  const text = document.createElement("div");
  text.className = "bubble-text";
  text.textContent = content;
  bubble.appendChild(text);

  if (extraNode) {
    bubble.appendChild(extraNode);
  }

  if (role === "assistant" && !terminalStates.has(job.status)) {
    const state = document.createElement("div");
    state.className = "bubble-status";
    state.textContent = formatStatus(job.status);
    bubble.appendChild(state);
  }

  article.append(meta, bubble);
  return article;
}

function renderThread() {
  chatThread.innerHTML = "";

  if (!currentConversationJobs.length && !pendingNewChat) {
    threadEmpty.classList.remove("hidden");
    chatThread.appendChild(threadEmpty);
    return;
  }

  if (!currentConversationJobs.length && pendingNewChat) {
    const newChatState = threadEmpty.cloneNode(true);
    newChatState.id = "";
    newChatState.querySelector("h3").textContent = "New chat is ready";
    newChatState.querySelector("p").textContent =
      `Your next message will open a fresh ${providerLabels[currentProvider]} tab with empty history.`;
    chatThread.appendChild(newChatState);
    return;
  }

  currentConversationJobs.forEach((job) => {
    const userGallery = buildImageGallery(job.uploads, "Reference");
    chatThread.appendChild(buildMessage("user", job.question, job, userGallery));

    const assistantExtras = buildAssistantExtras(job, job.uploads?.[0]);
    chatThread.appendChild(buildMessage("assistant", buildAssistantSummary(job), job, assistantExtras));
  });

  chatThread.scrollTop = chatThread.scrollHeight;
}

function updateConversationHeader() {
  pinConversationButton.classList.toggle("hidden", !currentConversationId && !pendingNewChat);
  pinConversationButton.disabled = !currentConversationId;
  pinConversationButton.textContent = currentConversationPinned ? "Unpin" : "Pin";
  tabHealthPill.textContent = currentConversationTabAlive ? "Bound tab alive" : "No bound tab";
  tabHealthPill.classList.toggle("danger", !currentConversationTabAlive && Boolean(currentConversationId));
  tabHealthPill.classList.toggle("success", currentConversationTabAlive);
  if (currentConversationExternalUrl) {
    providerLink.href = currentConversationExternalUrl;
    providerLink.classList.remove("hidden");
  } else {
    providerLink.classList.add("hidden");
    providerLink.removeAttribute("href");
  }

  if (pendingNewChat) {
    conversationTitle.textContent = "New chat";
    conversationSubtitle.textContent =
      `Fresh ${providerLabels[currentProvider]} tab on next send.`;
    tabHealthPill.textContent = "Fresh tab on next send";
    tabHealthPill.classList.remove("danger", "success");
    pinConversationButton.disabled = true;
    return;
  }

  if (!currentConversationJobs.length) {
    conversationTitle.textContent = "ordak";
    conversationSubtitle.textContent = "Pick a thread or start clean.";
    return;
  }

  const latest = currentConversationJobs[currentConversationJobs.length - 1];
  conversationTitle.textContent = currentConversationTitle || "ordak";
  if (latest.error_title || latest.error_message) {
    conversationSubtitle.textContent = `${formatStatus(latest.status)} • ${latest.error_title || latest.error_message}`;
    return;
  }
  conversationSubtitle.textContent = terminalStates.has(latest.status)
    ? `Last update: ${formatStatus(latest.status)}`
    : `Current update: ${formatStatus(latest.status)}`;
}

function renderConversationList(conversations) {
  conversationList.innerHTML = "";
  if (!conversations?.length) {
    conversationList.appendChild(emptyListItem("No chats yet."));
    return;
  }

  conversations.forEach((conversation) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-item";
    if (conversation.conversation_id === currentConversationId && !pendingNewChat) {
      button.classList.add("active");
    }

    const preview = (conversation.preview || "").slice(0, 68);
    button.innerHTML = `
      <strong>${conversation.pinned ? "Pinned • " : ""}${conversation.title}</strong>
      <small><img class="inline-provider-logo" src="${providerIcons[conversation.provider] || providerIcons.gemini}" alt="" />${providerLabels[conversation.provider] || conversation.provider} • ${formatStatus(conversation.last_status)} • ${conversation.tab_alive ? "tab alive" : "tab lost"}</small>
      <p>${preview}</p>
    `;
    button.addEventListener("click", () => loadConversation(conversation.conversation_id));
    item.appendChild(button);
    conversationList.appendChild(item);
  });
}

function getLatestJob() {
  return currentConversationJobs[currentConversationJobs.length - 1] || null;
}

function renderCurrentConversation() {
  const latest = getLatestJob();
  setStatus(latest?.status || "idle", latest?.job_id || null);
  submitButton.disabled = Boolean(latest && !terminalStates.has(latest.status));
  renderLogs(latest?.logs || []);
  renderScreenshots(latest?.screenshots || []);
  renderThread();
  updateConversationHeader();
}

function upsertConversationJob(job) {
  const existingIndex = currentConversationJobs.findIndex((item) => item.job_id === job.job_id);
  if (existingIndex >= 0) {
    currentConversationJobs.splice(existingIndex, 1, job);
  } else {
    currentConversationJobs.push(job);
  }
  currentConversationJobs.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
}

async function fetchConversationList() {
  const response = await fetch("/api/conversations");
  const payload = await response.json();
  renderConversationList(payload.conversations);
  return payload.conversations;
}

async function fetchJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) return null;
  return response.json();
}

async function loadConversation(conversationId) {
  const response = await fetch(`/api/conversations/${conversationId}`);
  if (!response.ok) return;
  const conversation = await response.json();

  pendingNewChat = false;
  currentConversationId = conversation.conversation_id;
  currentConversationTitle = conversation.title;
  currentConversationExternalUrl = conversation.external_url;
  currentConversationTabAlive = Boolean(conversation.tab_alive);
  currentConversationPinned = Boolean(conversation.pinned);
  currentConversationJobs = conversation.jobs || [];
  currentProvider = conversation.provider || currentProvider;
  currentMode = conversation.mode || currentMode;
  currentJobId = getLatestJob()?.job_id || null;
  syncModeButtons();
  updateUrl();
  renderCurrentConversation();
  await fetchConversationList();

  if (currentJobId && !terminalStates.has(getLatestJob()?.status)) {
    connectSocket(currentJobId);
  }
}

function startNewChat() {
  pendingNewChat = true;
  currentConversationId = null;
  currentConversationTitle = "New chat";
  currentConversationExternalUrl = null;
  currentConversationTabAlive = false;
  currentConversationPinned = false;
  currentConversationJobs = [];
  currentJobId = null;
  setStatus("idle");
  syncModeButtons();
  updateUrl();
  renderCurrentConversation();
  fetchConversationList();
}

function connectSocket(jobId) {
  if (socket) {
    socket.close();
  }
  if (fallbackPollHandle) {
    clearInterval(fallbackPollHandle);
    fallbackPollHandle = null;
  }

  socket = new WebSocket(
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/jobs/${jobId}`
  );

  socket.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    const job = payload.job;
    currentJobId = job.job_id;

    if (job.conversation_id && (!currentConversationId || job.conversation_id === currentConversationId)) {
      if (!currentConversationId) {
        currentConversationId = job.conversation_id;
        currentConversationTitle = job.conversation_title;
        currentProvider = job.provider || currentProvider;
        pendingNewChat = false;
      }
      currentConversationTitle = job.conversation_title || currentConversationTitle;
      currentProvider = job.provider || currentProvider;
      upsertConversationJob(job);
      currentMode = job.mode || currentMode;
      syncModeButtons();
      updateUrl();
      renderCurrentConversation();
    }

    await fetchConversationList();
  };

  socket.onopen = () => {
    socket.send("subscribe");
  };

  socket.onerror = () => {
    if (!fallbackPollHandle) {
      fallbackPollHandle = window.setInterval(async () => {
        const job = await fetchJob(jobId);
        if (!job) return;
        if (job.conversation_id === currentConversationId) {
          upsertConversationJob(job);
          renderCurrentConversation();
        }
        if (terminalStates.has(job.status)) {
          clearInterval(fallbackPollHandle);
          fallbackPollHandle = null;
        }
      }, 2000);
    }
  };
}

async function createJob() {
  const question = questionInput.value.trim();
  if (!question) return;

  submitButton.disabled = true;
  setStatus("queued");

  const formData = new FormData();
  formData.append("question", question);
  formData.append("provider", currentProvider);
  formData.append("mode", currentMode);
  formData.append("start_new_chat", String(pendingNewChat || !currentConversationId));
  if (currentConversationId && !pendingNewChat) {
    formData.append("conversation_id", currentConversationId);
  }

  const file = imageInput.files?.[0];
  if (file) {
    formData.append("image", file);
  }

  const response = await fetch("/api/jobs", {
    method: "POST",
    body: formData,
  });
  const payload = await response.json();

  if (!response.ok) {
    submitButton.disabled = false;
    setStatus("failed");
    profileMessage.textContent = payload.detail || payload.error_message || "Request failed.";
    composerNote.textContent = payload.detail || payload.error_message || "Request failed.";
    questionInput.focus();
    return;
  }

  questionInput.value = "";
  clearUpload();
  pendingNewChat = false;
  currentConversationId = payload.conversation_id;
  currentProvider = payload.provider || currentProvider;
  currentJobId = payload.job_id;
  currentConversationTitle =
    currentConversationTitle === "New chat"
      ? question.slice(0, 52)
      : currentConversationTitle;
  profileMessage.textContent = "";
  composerNote.textContent = `Next send stays in the current ${providerLabels[currentProvider]} tab.`;
  updateUrl();
  connectSocket(payload.job_id);
  await loadConversation(payload.conversation_id);
}

async function openProfile() {
  profileMessage.textContent = "Opening session...";
  const response = await fetch("/api/profile/open", { method: "POST" });
  const payload = await response.json();
  profileMessage.textContent = payload.message || payload.detail || response.statusText;
}

providerSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".provider-chip");
  if (!button) return;
  if (currentConversationJobs.length > 0 && button.dataset.provider !== currentProvider && !pendingNewChat) {
    return;
  }
  currentProvider = button.dataset.provider;
  syncModeButtons();
  updateConversationHeader();
  renderThread();
});

modeSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".mode-chip");
  if (!button) return;
  currentMode = button.dataset.mode;
  syncModeButtons();
});

imageInput.addEventListener("change", () => {
  renderUploadPreview(imageInput.files?.[0] || null);
});

clearUploadButton.addEventListener("click", clearUpload);
submitButton.addEventListener("click", createJob);
openProfileButton.addEventListener("click", openProfile);
newChatButton.addEventListener("click", startNewChat);
pinConversationButton.addEventListener("click", toggleConversationPin);

async function bootstrap() {
  syncModeButtons();
  renderLogs([]);
  renderScreenshots([]);
  renderCurrentConversation();

  const conversations = await fetchConversationList();
  if (initialConversationId) {
    await loadConversation(initialConversationId);
    return;
  }
  if (initialJobId) {
    const job = await fetchJob(initialJobId);
    if (job?.conversation_id) {
      await loadConversation(job.conversation_id);
      return;
    }
  }
  if (conversations[0]?.conversation_id) {
    await loadConversation(conversations[0].conversation_id);
    return;
  }
  startNewChat();
}

bootstrap();
