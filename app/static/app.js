const elements = {
  submitButton: document.getElementById("submit-btn"),
  openProfileButton: document.getElementById("open-profile-btn"),
  newChatButton: document.getElementById("new-chat-btn"),
  questionInput: document.getElementById("question"),
  statusBadge: document.getElementById("status-badge"),
  logsList: document.getElementById("logs"),
  screenshotsList: document.getElementById("screenshots"),
  profileMessage: document.getElementById("profile-message"),
  jobIdLabel: document.getElementById("job-id"),
  modeSwitch: document.getElementById("mode-switch"),
  imageInput: document.getElementById("image-input"),
  uploadPreview: document.getElementById("upload-preview"),
  uploadPreviewImage: document.getElementById("upload-preview-image"),
  uploadName: document.getElementById("upload-name"),
  uploadSize: document.getElementById("upload-size"),
  clearUploadButton: document.getElementById("clear-upload-btn"),
  conversationList: document.getElementById("conversation-list"),
  conversationTitle: document.getElementById("conversation-title"),
  conversationSubtitle: document.getElementById("conversation-subtitle"),
  conversationModeLogo: document.getElementById("conversation-mode-logo"),
  conversationModeLabel: document.getElementById("conversation-mode-label"),
  pinConversationButton: document.getElementById("pin-conversation-btn"),
  tabHealthPill: document.getElementById("tab-health-pill"),
  providerLink: document.getElementById("provider-link"),
  providerSwitch: document.getElementById("provider-switch"),
  chatThread: document.getElementById("chat-thread"),
  threadEmpty: document.getElementById("thread-empty"),
  composerHint: document.getElementById("composer-hint"),
  composerNote: document.getElementById("composer-note"),
  agentSettings: document.getElementById("agent-settings"),
  agentWorkspaceInput: document.getElementById("agent-workspace"),
  agentMaxStepsInput: document.getElementById("agent-max-steps"),
  agentCommandTimeoutInput: document.getElementById("agent-command-timeout"),
  agentExecutionBackendInput: document.getElementById("agent-execution-backend"),
  agentNetworkEnabledInput: document.getElementById("agent-network-enabled"),
  agentStepsList: document.getElementById("agent-steps"),
  uploadTrigger: document.getElementById("upload-trigger"),
  providerPresence: document.getElementById("provider-presence"),
  diagnosticsStats: document.getElementById("diagnostics-stats"),
  sessionSummary: document.getElementById("session-summary"),
  runtimeWorkspace: document.getElementById("runtime-workspace"),
  runtimeSteps: document.getElementById("runtime-steps"),
  runtimeStepsNote: document.getElementById("runtime-steps-note"),
  runtimeSessionState: document.getElementById("runtime-session-state"),
  runtimeSessionNote: document.getElementById("runtime-session-note"),
  stepSummaryPill: document.getElementById("step-summary-pill"),
  stepDetailTitle: document.getElementById("step-detail-title"),
  stepDetailMeta: document.getElementById("step-detail-meta"),
  inspectorTabRow: document.getElementById("inspector-tab-row"),
  stepDetailBody: document.getElementById("step-detail-body"),
};

const params = new URL(window.location.href).searchParams;
const initialConversationId = params.get("conversation");
const initialJobId = params.get("job");

const terminalStates = new Set([
  "completed",
  "failed",
  "manual_verification_required",
  "cancelled",
]);

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
    agent: "Agent",
  },
  chatgpt: {
    chat: "Ask ChatGPT",
    image_analyze: "Analyze Image",
    image_generate: "Create Image",
    agent: "Ordex Agent",
  },
};

const modePlaceholders = {
  gemini: {
    chat: "Ask Gemini anything and keep working in the current thread.",
    image_analyze: "Analyze this image and explain the important details.",
    image_generate: "Create or edit an image from these instructions.",
    agent: "Inspect this project, fix the tests, run validation, and do not stop early.",
  },
  chatgpt: {
    chat: "Ask ChatGPT anything and keep working in the current thread.",
    image_analyze: "Analyze this image and explain the important details.",
    image_generate: "Create or edit an image from these instructions.",
    agent: "مثلاً: پروژه را بررسی کن، کار موردنظر را کامل انجام بده، اجرا و تست کن و تا وقتی مطمئن نشدی درست است متوقف نشو.",
  },
};

const modeHints = {
  gemini: {
    chat: "Continues in the current Gemini tab.",
    image_analyze: "Uploads one image and continues in the same Gemini tab.",
    image_generate: "Keeps image work in the same Gemini thread.",
    agent: "Agent mode requires ChatGPT and a permitted local workspace.",
  },
  chatgpt: {
    chat: "Continues in the current ChatGPT tab.",
    image_analyze: "Uploads one image and continues in the same ChatGPT tab.",
    image_generate: "Keeps image work in the same ChatGPT thread.",
    agent: "Ordex will inspect, edit, run, and verify work inside the selected workspace.",
  },
};

const statusLabels = {
  idle: "Idle",
  queued: "Queued",
  running: "Running",
  checking_browser: "Checking Chrome",
  opening_provider_tab: "Opening Tab",
  opening_gemini_tab: "Opening Tab",
  opening_browser: "Opening Browser",
  navigating_to_gemini: "Opening Page",
  checking_login: "Checking Session",
  finding_input: "Finding Input",
  submitting_prompt: "Submitting Prompt",
  waiting_for_response: "Waiting for Answer",
  extracting_answer: "Extracting Result",
  preparing_agent: "Preparing Agent",
  waiting_for_agent: "Waiting for Ordex",
  parsing_agent_action: "Parsing Action",
  executing_agent_action: "Executing Tool",
  sending_agent_result: "Sending Result",
  finalizing_agent: "Finalizing",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  completed: "Completed",
  failed: "Failed",
  manual_verification_required: "Verification Required",
};

const toolLabels = {
  exec: "Run command",
  read_file: "Read file",
  list_directory: "List directory",
  write_file: "Write file",
  apply_patch: "Apply patch",
};

const agentSettingsKey = "ordak.agent.settings.v1";

const state = {
  socket: null,
  socketReconnectHandle: null,
  socketReconnectAttempts: 0,
  fallbackPollHandle: null,
  diagnosticsPollHandle: null,
  watchdogHandle: null,
  lastRealtimeEventAt: Date.now(),
  watchdogReloading: false,
  previewUrl: null,
  currentJobId: null,
  currentConversationId: null,
  currentConversationTitle: "ordak",
  currentConversationExternalUrl: null,
  currentConversationTabAlive: false,
  currentConversationPinned: false,
  currentConversationJobs: [],
  currentProvider: "chatgpt",
  currentMode: "agent",
  pendingNewChat: false,
  currentAgentSteps: [],
  selectedStepId: null,
  selectedInspectorTab: "summary",
  diagnostics: null,
};

function formatStatus(status) {
  if (!status) return "Idle";
  return statusLabels[status] || status.replaceAll("_", " ");
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "—";
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} s`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function safeJsonParse(rawValue) {
  if (!rawValue) return null;
  if (typeof rawValue === "object") return rawValue;
  try {
    return JSON.parse(rawValue);
  } catch {
    return null;
  }
}

function prettyJson(rawValue) {
  const parsed = safeJsonParse(rawValue);
  if (parsed === null) return rawValue || "No data.";
  return JSON.stringify(parsed, null, 2);
}

function truncate(value, limit = 150) {
  if (!value) return "";
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function setStatus(status, jobId = null) {
  elements.statusBadge.textContent = formatStatus(status);
  elements.statusBadge.className = `badge ${status || "idle"}`;
  elements.jobIdLabel.textContent = jobId ? `Job ${jobId.slice(0, 8)}` : "Ready for a prompt.";
}

function applyProviderTheme() {
  document.body.dataset.providerTheme = state.currentProvider;
}

function cleanupPreviewUrl() {
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
}

function renderUploadPreview(file) {
  cleanupPreviewUrl();
  if (!file) {
    elements.uploadPreview.classList.add("hidden");
    elements.uploadPreviewImage.removeAttribute("src");
    elements.uploadName.textContent = "";
    elements.uploadSize.textContent = "";
    return;
  }
  state.previewUrl = URL.createObjectURL(file);
  elements.uploadPreview.classList.remove("hidden");
  elements.uploadPreviewImage.src = state.previewUrl;
  elements.uploadName.textContent = file.name;
  elements.uploadSize.textContent = formatBytes(file.size);
}

function clearUpload() {
  elements.imageInput.value = "";
  renderUploadPreview(null);
}

function syncModeButtons() {
  if (state.currentMode === "agent") {
    state.currentProvider = "chatgpt";
  }
  applyProviderTheme();

  elements.providerSwitch.querySelectorAll(".provider-chip").forEach((button) => {
    button.classList.toggle("active", button.dataset.provider === state.currentProvider);
    button.disabled =
      state.currentMode === "agent" ||
      (
        state.currentConversationJobs.length > 0 &&
        button.dataset.provider !== state.currentProvider &&
        !state.pendingNewChat
      );
  });

  elements.modeSwitch.querySelectorAll(".mode-chip").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.currentMode);
  });

  elements.questionInput.placeholder = modePlaceholders[state.currentProvider][state.currentMode];
  elements.composerHint.textContent = modeHints[state.currentProvider][state.currentMode];
  elements.composerNote.textContent = state.pendingNewChat
    ? `Next send opens a fresh ${providerLabels[state.currentProvider]} tab.`
    : `Next send stays in the current ${providerLabels[state.currentProvider]} tab.`;
  elements.conversationModeLogo.src = providerIcons[state.currentProvider];
  elements.conversationModeLabel.textContent =
    providerModeLabels[state.currentProvider][state.currentMode] || state.currentMode;
  elements.agentSettings.classList.toggle("hidden", state.currentMode !== "agent");
  elements.uploadTrigger.classList.toggle("hidden", state.currentMode === "agent");
  elements.imageInput.classList.toggle("hidden", state.currentMode === "agent");

  if (state.currentMode === "agent") {
    clearUpload();
  }
}

function loadAgentSettings() {
  const saved = safeJsonParse(window.localStorage.getItem(agentSettingsKey));
  if (!saved) return;
  elements.agentWorkspaceInput.value = saved.workspace || "";
  elements.agentMaxStepsInput.value = saved.maxSteps || "";
  elements.agentCommandTimeoutInput.value = saved.commandTimeout || "";
  elements.agentExecutionBackendInput.value = saved.executionBackend || "";
  elements.agentNetworkEnabledInput.checked = Boolean(saved.networkEnabled);
}

function saveAgentSettings() {
  window.localStorage.setItem(
    agentSettingsKey,
    JSON.stringify({
      workspace: elements.agentWorkspaceInput.value.trim(),
      maxSteps: elements.agentMaxStepsInput.value,
      commandTimeout: elements.agentCommandTimeoutInput.value,
      executionBackend: elements.agentExecutionBackendInput.value,
      networkEnabled: elements.agentNetworkEnabledInput.checked,
    }),
  );
}

function hydrateAgentSettingsFromJob(job) {
  if (!job || job.mode !== "agent") return;
  elements.agentWorkspaceInput.value = job.agent_workspace || elements.agentWorkspaceInput.value;
  elements.agentMaxStepsInput.value =
    job.agent_max_steps ?? elements.agentMaxStepsInput.value;
  elements.agentCommandTimeoutInput.value =
    job.agent_command_timeout_seconds ?? elements.agentCommandTimeoutInput.value;
  elements.agentExecutionBackendInput.value =
    job.agent_execution_backend || elements.agentExecutionBackendInput.value;
  saveAgentSettings();
}

function emptyListItem(message) {
  const item = document.createElement("li");
  item.className = "empty-state";
  item.textContent = message;
  return item;
}

function detailCard(title, content) {
  const card = document.createElement("section");
  card.className = "detail-card";
  const heading = document.createElement("h4");
  heading.textContent = title;
  card.appendChild(heading);
  if (content instanceof Node) {
    card.appendChild(content);
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = content ?? "—";
    card.appendChild(paragraph);
  }
  return card;
}

function preBlock(content, light = false) {
  const block = document.createElement("pre");
  block.className = `pre-block${light ? " light" : ""}`;
  block.textContent = content || "No output.";
  return block;
}

function inlineList(values) {
  const list = document.createElement("div");
  list.className = "inline-list";
  values.forEach((value) => {
    const item = document.createElement("span");
    item.textContent = value;
    list.appendChild(item);
  });
  return list;
}

function updateUrl() {
  const url = new URL(window.location.href);
  if (state.currentConversationId) {
    url.searchParams.set("conversation", state.currentConversationId);
    url.searchParams.delete("job");
  } else {
    url.searchParams.delete("conversation");
    url.searchParams.delete("job");
  }
  window.history.replaceState({}, "", url);
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

async function fetchJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) return null;
  return response.json();
}

async function fetchAgentSteps(jobId) {
  const response = await fetch(`/api/jobs/${jobId}/steps`);
  if (!response.ok) return [];
  const payload = await response.json();
  return payload.steps || [];
}

async function refreshDiagnostics() {
  try {
    const response = await fetch("/api/diagnostics");
    if (!response.ok) return;
    state.diagnostics = await response.json();
    renderDiagnostics();
    renderRuntimeStrip();
  } catch {
    elements.sessionSummary.textContent = "Diagnostics are temporarily unavailable.";
  }
}

function buildImageGallery(paths, labelPrefix) {
  if (!paths?.length) return null;
  const list = document.createElement("div");
  list.className = "bubble-gallery";
  paths.forEach((path, index) => {
    const card = document.createElement("a");
    card.className = "bubble-image";
    card.href = `/${String(path).replace(/^\//, "")}`;
    card.target = "_blank";
    card.rel = "noreferrer";

    const image = document.createElement("img");
    image.src = card.href;
    image.alt = `${labelPrefix} ${index + 1}`;
    image.loading = "lazy";

    const caption = document.createElement("span");
    caption.textContent = `${labelPrefix} ${index + 1}`;

    card.append(image, caption);
    list.appendChild(card);
  });
  return list;
}

function buildAssistantSummary(job) {
  if (job.answer) return job.answer;
  if (!terminalStates.has(job.status)) {
    if (job.mode === "agent") {
      return "Ordex is working. Tool requests, command output, and file changes are updating in the inspector.";
    }
    return `${providerLabels[job.provider] || "Assistant"} is still working on this response.`;
  }
  if (job.error_title || job.error_message) {
    return [job.error_title, job.error_message].filter(Boolean).join(" • ");
  }
  return "No answer was captured.";
}

function buildErrorMeta(job) {
  if (!job.error_code && !job.suggested_action) return null;
  const wrap = document.createElement("div");
  wrap.className = "agent-run-summary";
  if (job.error_code) {
    const code = document.createElement("strong");
    code.textContent = job.error_code;
    wrap.appendChild(code);
  }
  if (job.suggested_action) {
    const note = document.createElement("p");
    note.className = "muted";
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

async function cancelJob(jobId) {
  try {
    const job = await apiPostJson(`/api/jobs/${jobId}/cancel`);
    upsertConversationJob(job);
    renderCurrentConversation();
    await fetchConversationList();
  } catch (error) {
    elements.profileMessage.textContent = error.message;
  }
}

async function retryJob(jobId, strategy) {
  try {
    const payload = await apiPostJson(`/api/jobs/${jobId}/retry`, { strategy });
    state.pendingNewChat = false;
    state.currentConversationId = payload.conversation_id;
    state.currentJobId = payload.job_id;
    connectSocket(payload.job_id);
    await loadConversation(payload.conversation_id);
  } catch (error) {
    elements.profileMessage.textContent = error.message;
  }
}

async function resumeJob(jobId, strategy) {
  try {
    const payload = await apiPostJson(`/api/jobs/${jobId}/resume`, { strategy });
    state.pendingNewChat = false;
    state.currentConversationId = payload.conversation_id;
    state.currentJobId = payload.job_id;
    connectSocket(payload.job_id);
    await loadConversation(payload.conversation_id);
  } catch (error) {
    elements.profileMessage.textContent = error.message;
  }
}

function buildActionRow(job) {
  const row = document.createElement("div");
  row.className = "action-row";
  if (!terminalStates.has(job.status)) {
    row.appendChild(createActionButton("Cancel", "danger", () => cancelJob(job.job_id)));
    return row;
  }
  if (job.mode === "agent") {
    row.appendChild(
      createActionButton("Continue same chat", "primary", () =>
        resumeJob(job.job_id, "same_tab")),
    );
    row.appendChild(
      createActionButton("Reopen chat + continue", "secondary", () =>
        resumeJob(job.job_id, "new_tab_same_conversation")),
    );
    row.appendChild(createActionButton("Fresh run", "secondary", startNewChat));
    return row;
  }
  if (job.recoverable) {
    row.appendChild(
      createActionButton("Retry same tab", "secondary", () => retryJob(job.job_id, "same_tab")),
    );
    row.appendChild(
      createActionButton("Retry new tab", "secondary", () =>
        retryJob(job.job_id, "new_tab_same_conversation")),
    );
    row.appendChild(
      createActionButton("Resume same tab", "secondary", () => resumeJob(job.job_id, "same_tab")),
    );
  }
  row.appendChild(createActionButton("Fresh run", "primary", startNewChat));
  return row;
}

function buildAgentRunSummary(job) {
  if (job.mode !== "agent") return null;
  const wrap = document.createElement("div");
  wrap.className = "agent-run-summary";

  const title = document.createElement("strong");
  title.textContent = "Ordex execution";
  wrap.appendChild(title);

  const pills = document.createElement("div");
  pills.className = "pill-row";
  [
    job.agent_workspace || "Workspace unavailable",
    `${job.agent_step_count || 0} steps`,
    job.agent_execution_backend || "default backend",
    job.agent_command_timeout_seconds
      ? `${job.agent_command_timeout_seconds}s timeout`
      : "default timeout",
  ].forEach((value) => {
    const pill = document.createElement("span");
    pill.className = "mini-pill";
    pill.textContent = value;
    pills.appendChild(pill);
  });
  wrap.appendChild(pills);
  return wrap;
}

function buildAssistantExtras(job) {
  const fragment = document.createElement("div");
  fragment.className = "message-stack";
  const resultGallery = buildImageGallery(job.output_images, "Result");
  const errorMeta = buildErrorMeta(job);
  const agentSummary = buildAgentRunSummary(job);

  if (agentSummary) fragment.appendChild(agentSummary);
  if (resultGallery) fragment.appendChild(resultGallery);
  if (errorMeta) fragment.appendChild(errorMeta);

  if (job.output_images?.length) {
    const links = document.createElement("div");
    links.className = "result-links";
    const downloadLink = document.createElement("a");
    downloadLink.href = `/${job.output_images[0]}`;
    downloadLink.textContent = "Open result";
    downloadLink.target = "_blank";
    downloadLink.rel = "noreferrer";
    links.appendChild(downloadLink);
    fragment.appendChild(links);
  }

  fragment.appendChild(buildActionRow(job));
  return fragment;
}

function buildMessage(role, content, job, extraNode = null) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const roleLabel = role === "user" ? "You" : providerLabels[job.provider] || "Assistant";
  meta.innerHTML = `
    <span class="message-role">${roleLabel}</span>
    <span>${formatTime(job.created_at)}</span>
    <span>${formatStatus(job.status)}</span>
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
    const status = document.createElement("div");
    status.className = "bubble-status";
    status.textContent = formatStatus(job.status);
    bubble.appendChild(status);
  }

  article.append(meta, bubble);
  return article;
}

function renderThread() {
  elements.chatThread.innerHTML = "";

  if (!state.currentConversationJobs.length && !state.pendingNewChat) {
    elements.threadEmpty.classList.remove("hidden");
    elements.chatThread.appendChild(elements.threadEmpty);
    return;
  }

  if (!state.currentConversationJobs.length && state.pendingNewChat) {
    const empty = elements.threadEmpty.cloneNode(true);
    empty.id = "";
    empty.querySelector("h3").textContent = "Fresh run is ready";
    empty.querySelector("p").textContent =
      `Your next prompt opens a clean ${providerLabels[state.currentProvider]} tab.`;
    elements.chatThread.appendChild(empty);
    return;
  }

  state.currentConversationJobs.forEach((job) => {
    const userGallery = buildImageGallery(job.uploads, "Reference");
    elements.chatThread.appendChild(buildMessage("user", job.question, job, userGallery));
    elements.chatThread.appendChild(
      buildMessage("assistant", buildAssistantSummary(job), job, buildAssistantExtras(job)),
    );
  });

  elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
}

function renderLogs(logs) {
  elements.logsList.innerHTML = "";
  if (!logs?.length) {
    elements.logsList.appendChild(emptyListItem("No runtime events yet."));
    return;
  }
  logs.slice(-30).reverse().forEach((entry) => {
    const item = document.createElement("li");
    item.className = `event-item ${entry.level || "info"}`;

    const level = document.createElement("span");
    level.className = "event-level";
    level.textContent = entry.level || "info";

    const time = document.createElement("strong");
    time.textContent = formatTime(entry.timestamp);

    const message = document.createElement("p");
    message.textContent = entry.message;

    item.append(level, time, message);
    elements.logsList.appendChild(item);
  });
}

function renderScreenshots(paths) {
  elements.screenshotsList.innerHTML = "";
  if (!paths?.length) {
    elements.screenshotsList.appendChild(emptyListItem("No screenshots captured."));
    return;
  }
  paths.forEach((path) => {
    const item = document.createElement("li");
    item.className = "artifact-card";
    const link = document.createElement("a");
    link.href = `/${String(path).replace(/^\//, "")}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = path.split("/").pop();
    const arrow = document.createElement("span");
    arrow.textContent = "↗";
    link.appendChild(arrow);
    item.appendChild(link);
    elements.screenshotsList.appendChild(item);
  });
}

function getStepData(step) {
  const request = safeJsonParse(step.request_json) || {};
  const result = safeJsonParse(step.result_json) || {};
  const title = toolLabels[step.tool] || formatStatus(step.tool);
  let subtitle = step.command_id;
  let commandLine = "";
  let targetPath = "";

  if (step.tool === "exec") {
    commandLine = Array.isArray(request.argv) ? request.argv.join(" ") : "";
    subtitle = truncate(`${request.cwd || "."} · ${commandLine}`, 180);
  } else if (["read_file", "write_file", "list_directory"].includes(step.tool)) {
    targetPath = request.path || result.path || "";
    subtitle = targetPath || step.command_id;
  } else if (step.tool === "apply_patch") {
    const modified = Array.isArray(result.modified_paths) ? result.modified_paths : [];
    subtitle = modified.length
      ? `Changed ${modified.join(", ")}`
      : truncate(request.patch, 180) || step.command_id;
  }

  return {
    step,
    request,
    result,
    title,
    subtitle,
    commandLine,
    targetPath,
    stdout: typeof result.stdout === "string" ? result.stdout : "",
    stderr: typeof result.stderr === "string" ? result.stderr : "",
    content: typeof result.content === "string" ? result.content : "",
    patch: typeof request.patch === "string" ? request.patch : "",
    entries: Array.isArray(result.entries) ? result.entries : [],
    modifiedPaths: Array.isArray(result.modified_paths) ? result.modified_paths : [],
    artifacts: Array.isArray(result.artifacts) ? result.artifacts : [],
  };
}

function renderAgentSteps(steps, enabled = false) {
  elements.agentStepsList.innerHTML = "";
  if (!enabled) {
    elements.agentStepsList.appendChild(emptyListItem("Switch to Agent mode to see tool calls."));
    elements.stepSummaryPill.textContent = "Agent timeline is inactive.";
    return;
  }
  if (!steps?.length) {
    elements.agentStepsList.appendChild(emptyListItem("Waiting for the first Ordex tool request."));
    elements.stepSummaryPill.textContent = "No steps yet.";
    return;
  }

  if (!steps.some((step) => step.id === state.selectedStepId)) {
    state.selectedStepId = steps[steps.length - 1].id;
  }

  elements.stepSummaryPill.textContent = `${steps.length} tool ${steps.length === 1 ? "call" : "calls"}`;
  steps.forEach((step) => {
    const data = getStepData(step);
    const item = document.createElement("li");
    item.className = `step-item ${step.status || ""}`;
    item.classList.toggle("active", step.id === state.selectedStepId);

    const top = document.createElement("div");
    top.className = "step-item-top";
    const title = document.createElement("span");
    title.className = "step-item-title";
    title.textContent = `${step.sequence}. ${data.title}`;
    const status = document.createElement("span");
    status.className = "step-chip";
    status.textContent = step.status;
    top.append(title, status);

    const subtitle = document.createElement("code");
    subtitle.textContent = data.subtitle;

    const timing = document.createElement("span");
    timing.className = "step-item-subtitle";
    timing.textContent = `${formatDuration(step.duration_ms)} · ${step.command_id}`;

    item.append(top, subtitle, timing);
    item.addEventListener("click", () => {
      state.selectedStepId = step.id;
      renderAgentSteps(state.currentAgentSteps, true);
      renderStepInspector();
    });
    elements.agentStepsList.appendChild(item);
  });
}

function appendPairGrid(container, pairs) {
  const grid = document.createElement("div");
  grid.className = "summary-grid";
  pairs.forEach(([label, value]) => {
    const card = document.createElement("section");
    card.className = "detail-card";
    const key = document.createElement("span");
    key.className = "runtime-label";
    key.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value ?? "—";
    card.append(key, content);
    grid.appendChild(card);
  });
  container.appendChild(grid);
}

function renderStepSummary(data) {
  const container = document.createDocumentFragment();
  appendPairGrid(container, [
    ["Status", data.step.status],
    ["Duration", formatDuration(data.step.duration_ms)],
    ["Command ID", data.step.command_id],
    ["Started", formatTime(data.step.started_at)],
  ]);

  if (data.step.tool === "exec") {
    const command = preBlock(data.commandLine || "No command captured.");
    const commandCard = detailCard("Command", command);
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = `Working directory: ${data.request.cwd || "."} · Timeout: ${data.request.timeout_seconds || "default"}s`;
    commandCard.appendChild(note);
    container.appendChild(commandCard);
  }

  if (data.targetPath) {
    container.appendChild(detailCard("Target path", data.targetPath));
  }

  if (data.patch) {
    container.appendChild(detailCard("Patch", preBlock(data.patch, true)));
  }

  if (data.modifiedPaths.length) {
    container.appendChild(detailCard("Changed files", inlineList(data.modifiedPaths)));
  }

  if (data.content) {
    container.appendChild(detailCard("File content", preBlock(data.content, true)));
  }

  if (data.entries.length) {
    const entries = data.entries.slice(0, 200).map((entry) => `${entry.type || "item"}  ${entry.path}`);
    if (data.entries.length > entries.length) {
      entries.push(`… ${data.entries.length - entries.length} more entries`);
    }
    container.appendChild(detailCard("Directory entries", preBlock(entries.join("\n"), true)));
  }

  if (data.stdout || data.step.tool === "exec") {
    container.appendChild(detailCard("stdout", preBlock(data.stdout || "No stdout.")));
  }

  if (data.stderr || data.step.error_message) {
    container.appendChild(
      detailCard("stderr / error", preBlock(data.stderr || data.step.error_message || "No stderr.")),
    );
  }

  if (data.artifacts.length) {
    const links = document.createElement("div");
    links.className = "inline-list";
    data.artifacts.forEach((artifact) => {
      const link = document.createElement("a");
      link.href = `/${String(artifact.path || "").replace(/^\//, "")}`;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = `${artifact.kind || "artifact"}: ${artifact.path}`;
      links.appendChild(link);
    });
    container.appendChild(detailCard("Command artifacts", links));
  }

  if (data.step.tool === "write_file") {
    appendPairGrid(container, [
      ["Bytes written", formatBytes(data.result.bytes_written)],
      ["Before hash", data.result.before_hash || "New file"],
      ["After hash", data.result.after_hash || "—"],
      ["Overwrite", String(Boolean(data.request.overwrite))],
    ]);
  }

  if (data.step.error_message && !data.stderr) {
    container.appendChild(detailCard("Tool error", data.step.error_message));
  }

  if (
    !data.commandLine &&
    !data.targetPath &&
    !data.patch &&
    !data.modifiedPaths.length &&
    !data.content &&
    !data.entries.length &&
    !data.stdout &&
    !data.stderr
  ) {
    container.appendChild(detailCard("Result", preBlock(prettyJson(data.step.result_json), true)));
  }

  return container;
}

function renderStepInspector() {
  const step = state.currentAgentSteps.find((item) => item.id === state.selectedStepId);
  elements.inspectorTabRow.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.inspectorTab === state.selectedInspectorTab);
  });

  if (!step) {
    elements.stepDetailTitle.textContent = "Step details";
    elements.stepDetailMeta.textContent = "No step selected.";
    elements.stepDetailBody.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "empty-state large";
    empty.textContent =
      "Ordex tool requests, command output, changed paths, and raw JSON will appear here.";
    elements.stepDetailBody.appendChild(empty);
    return;
  }

  const data = getStepData(step);
  elements.stepDetailTitle.textContent = `${step.sequence}. ${data.title}`;
  elements.stepDetailMeta.textContent = `${step.command_id} · ${formatDuration(step.duration_ms)}`;
  elements.stepDetailBody.innerHTML = "";

  if (state.selectedInspectorTab === "request") {
    elements.stepDetailBody.appendChild(preBlock(prettyJson(step.request_json), true));
    return;
  }
  if (state.selectedInspectorTab === "result") {
    elements.stepDetailBody.appendChild(preBlock(prettyJson(step.result_json), true));
    return;
  }

  elements.stepDetailBody.appendChild(renderStepSummary(data));
}

function renderConversationList(conversations) {
  elements.conversationList.innerHTML = "";
  if (!conversations?.length) {
    elements.conversationList.appendChild(emptyListItem("No runs yet."));
    return;
  }

  conversations.forEach((conversation) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-item";
    button.classList.toggle(
      "active",
      conversation.conversation_id === state.currentConversationId && !state.pendingNewChat,
    );

    const head = document.createElement("div");
    head.className = "history-head";
    const title = document.createElement("strong");
    title.textContent = `${conversation.pinned ? "Pinned · " : ""}${conversation.title}`;
    const status = document.createElement("span");
    status.className = `session-badge ${
      conversation.last_status === "completed"
        ? "success"
        : conversation.last_status === "failed"
          ? "error"
          : "warning"
    }`;
    status.textContent = formatStatus(conversation.last_status);
    head.append(title, status);

    const meta = document.createElement("small");
    meta.textContent =
      `${providerLabels[conversation.provider] || conversation.provider} · ` +
      `${conversation.job_count} ${conversation.job_count === 1 ? "run" : "runs"} · ` +
      `${conversation.tab_alive ? "tab alive" : "tab unavailable"}`;

    const preview = document.createElement("p");
    preview.textContent = truncate(conversation.preview, 92);

    button.append(head, meta, preview);
    button.addEventListener("click", () => loadConversation(conversation.conversation_id));
    item.appendChild(button);
    elements.conversationList.appendChild(item);
  });
}

function renderDiagnostics() {
  const diagnostics = state.diagnostics;
  elements.providerPresence.innerHTML = "";
  elements.diagnosticsStats.innerHTML = "";

  if (!diagnostics) {
    elements.sessionSummary.textContent = "Checking the current Chrome session and bound tabs.";
    return;
  }

  elements.sessionSummary.textContent = diagnostics.chrome_running
    ? "Chrome remote debugging is reachable. Ordex will reuse the existing logged-in session."
    : "Chrome remote debugging is not reachable. Open the configured session before starting a run.";

  ["chatgpt", "gemini"].forEach((provider) => {
    const session = diagnostics.provider_sessions?.[provider] || {};
    const card = document.createElement("article");
    card.className = "provider-session-card";

    const head = document.createElement("div");
    head.className = "provider-session-head";
    const title = document.createElement("strong");
    title.textContent = providerLabels[provider];
    const badge = document.createElement("span");
    badge.className = `session-badge ${session.logged_in ? "success" : "warning"}`;
    badge.textContent = session.logged_in ? "Logged in" : session.login_state || "Needs attention";
    head.append(title, badge);

    const note = document.createElement("p");
    note.className = "muted";
    note.textContent =
      `${session.open_tabs?.length || 0} open tabs · ${session.busy ? "busy" : "available"}`;
    card.append(head, note);
    elements.providerPresence.appendChild(card);
  });

  const stats = [
    ["Queue", diagnostics.queue_depth ?? 0],
    ["Alive tabs", diagnostics.tab_binding_health?.alive_tabs ?? 0],
    ["Lost tabs", diagnostics.tab_binding_health?.lost_tabs ?? 0],
    ["Agent", diagnostics.agent?.enabled ? "Enabled" : "Disabled"],
  ];
  stats.forEach(([label, value]) => {
    const wrap = document.createElement("div");
    const key = document.createElement("dt");
    key.textContent = label;
    const content = document.createElement("dd");
    content.textContent = String(value);
    wrap.append(key, content);
    elements.diagnosticsStats.appendChild(wrap);
  });
}

function getLatestJob() {
  return state.currentConversationJobs[state.currentConversationJobs.length - 1] || null;
}

function renderRuntimeStrip() {
  const latest = getLatestJob();
  const chatgptSession = state.diagnostics?.provider_sessions?.chatgpt;

  elements.runtimeWorkspace.textContent =
    latest?.agent_workspace ||
    elements.agentWorkspaceInput.value.trim() ||
    "No workspace selected";
  elements.runtimeSteps.textContent = String(
    latest?.agent_step_count ?? state.currentAgentSteps.length ?? 0,
  );
  elements.runtimeStepsNote.textContent = latest?.agent_max_steps
    ? `${latest.agent_step_count || 0} of ${latest.agent_max_steps} allowed`
    : state.currentAgentSteps.length
      ? "Tool calls captured for this run."
      : "No tool calls yet.";

  if (!state.diagnostics) {
    elements.runtimeSessionState.textContent = "Checking…";
    elements.runtimeSessionNote.textContent = "Waiting for diagnostics.";
    return;
  }

  if (!state.diagnostics.chrome_running) {
    elements.runtimeSessionState.textContent = "Chrome unavailable";
    elements.runtimeSessionNote.textContent = "Remote debugging is not reachable.";
    return;
  }

  elements.runtimeSessionState.textContent = chatgptSession?.logged_in
    ? "ChatGPT ready"
    : "Login required";
  elements.runtimeSessionNote.textContent = chatgptSession?.logged_in
    ? `${chatgptSession.open_tabs?.length || 0} ChatGPT tabs in the current session.`
    : "Open Session and log in before an Agent run.";
}

function updateConversationHeader() {
  elements.pinConversationButton.classList.toggle(
    "hidden",
    !state.currentConversationId && !state.pendingNewChat,
  );
  elements.pinConversationButton.disabled = !state.currentConversationId;
  elements.pinConversationButton.textContent = state.currentConversationPinned ? "Unpin" : "Pin";

  elements.tabHealthPill.textContent = state.currentConversationTabAlive
    ? "Bound tab alive"
    : "No bound tab";
  elements.tabHealthPill.classList.toggle(
    "danger",
    !state.currentConversationTabAlive && Boolean(state.currentConversationId),
  );
  elements.tabHealthPill.classList.toggle("success", state.currentConversationTabAlive);

  if (state.currentConversationExternalUrl) {
    elements.providerLink.href = state.currentConversationExternalUrl;
    elements.providerLink.classList.remove("hidden");
  } else {
    elements.providerLink.classList.add("hidden");
    elements.providerLink.removeAttribute("href");
  }

  if (state.pendingNewChat) {
    elements.conversationTitle.textContent = "Fresh run";
    elements.conversationSubtitle.textContent =
      `A clean ${providerLabels[state.currentProvider]} tab will open on the next send.`;
    elements.tabHealthPill.textContent = "Fresh tab on next send";
    elements.tabHealthPill.classList.remove("danger", "success");
    elements.pinConversationButton.disabled = true;
    return;
  }

  if (!state.currentConversationJobs.length) {
    elements.conversationTitle.textContent = "ordak";
    elements.conversationSubtitle.textContent = "Pick a thread or start a fresh run.";
    return;
  }

  const latest = getLatestJob();
  elements.conversationTitle.textContent = state.currentConversationTitle || "ordak";
  if (latest.error_title || latest.error_message) {
    elements.conversationSubtitle.textContent =
      `${formatStatus(latest.status)} · ${latest.error_title || latest.error_message}`;
    return;
  }
  elements.conversationSubtitle.textContent = latest.mode === "agent"
    ? `${formatStatus(latest.status)} · ${latest.agent_step_count || 0} tool calls · ${latest.agent_workspace}`
    : `${formatStatus(latest.status)} · ${providerLabels[latest.provider]}`;
}

function renderCurrentConversation() {
  const latest = getLatestJob();
  setStatus(latest?.status || "idle", latest?.job_id || null);
  elements.submitButton.disabled = Boolean(latest && !terminalStates.has(latest.status));
  renderLogs(latest?.logs || []);
  renderScreenshots(latest?.screenshots || []);
  renderAgentSteps(
    state.currentAgentSteps,
    latest?.mode === "agent" || state.currentMode === "agent",
  );
  renderStepInspector();
  renderThread();
  renderRuntimeStrip();
  updateConversationHeader();
}

function upsertConversationJob(job) {
  const existingIndex = state.currentConversationJobs.findIndex(
    (item) => item.job_id === job.job_id,
  );
  if (existingIndex >= 0) {
    state.currentConversationJobs.splice(existingIndex, 1, job);
  } else {
    state.currentConversationJobs.push(job);
  }
  state.currentConversationJobs.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
}

async function fetchConversationList() {
  const response = await fetch("/api/conversations");
  if (!response.ok) return [];
  const payload = await response.json();
  renderConversationList(payload.conversations);
  return payload.conversations || [];
}

async function toggleConversationPin() {
  if (!state.currentConversationId) return;
  try {
    const conversation = await apiPostJson(
      `/api/conversations/${state.currentConversationId}/${
        state.currentConversationPinned ? "unpin" : "pin"
      }`,
    );
    state.currentConversationPinned = Boolean(conversation.pinned);
    updateConversationHeader();
    await fetchConversationList();
  } catch (error) {
    elements.profileMessage.textContent = error.message;
  }
}

async function loadConversation(conversationId) {
  const response = await fetch(`/api/conversations/${conversationId}`);
  if (!response.ok) return;
  const conversation = await response.json();

  state.pendingNewChat = false;
  state.currentConversationId = conversation.conversation_id;
  state.currentConversationTitle = conversation.title;
  state.currentConversationExternalUrl = conversation.external_url;
  state.currentConversationTabAlive = Boolean(conversation.tab_alive);
  state.currentConversationPinned = Boolean(conversation.pinned);
  state.currentConversationJobs = conversation.jobs || [];
  state.currentProvider = conversation.provider || state.currentProvider;
  state.currentMode = conversation.mode || state.currentMode;
  state.currentJobId = getLatestJob()?.job_id || null;

  const latest = getLatestJob();
  hydrateAgentSettingsFromJob(latest);
  state.currentAgentSteps =
    state.currentJobId && latest?.mode === "agent"
      ? await fetchAgentSteps(state.currentJobId)
      : [];
  state.selectedStepId = state.currentAgentSteps.at(-1)?.id || null;

  syncModeButtons();
  updateUrl();
  renderCurrentConversation();
  await fetchConversationList();

  if (state.currentJobId && !terminalStates.has(latest?.status)) {
    connectSocket(state.currentJobId);
  }
}

function startNewChat() {
  state.pendingNewChat = true;
  state.currentConversationId = null;
  state.currentConversationTitle = "Fresh run";
  state.currentConversationExternalUrl = null;
  state.currentConversationTabAlive = false;
  state.currentConversationPinned = false;
  state.currentConversationJobs = [];
  state.currentJobId = null;
  state.currentAgentSteps = [];
  state.selectedStepId = null;
  state.currentProvider = "chatgpt";
  state.currentMode = "agent";
  setStatus("idle");
  syncModeButtons();
  updateUrl();
  renderCurrentConversation();
  fetchConversationList();
  elements.questionInput.focus();
}

function connectSocket(jobId) {
  if (state.socketReconnectHandle) {
    clearTimeout(state.socketReconnectHandle);
    state.socketReconnectHandle = null;
  }
  if (state.socket) {
    const previousSocket = state.socket;
    state.socket = null;
    previousSocket.close();
  }
  if (state.fallbackPollHandle) {
    clearInterval(state.fallbackPollHandle);
    state.fallbackPollHandle = null;
  }

  const socket = new WebSocket(
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/jobs/${jobId}`,
  );
  state.socket = socket;

  socket.onmessage = async (event) => {
    if (state.socket !== socket) return;
    state.lastRealtimeEventAt = Date.now();
    const payload = JSON.parse(event.data);
    const job = payload.job;
    const previousLastStepId = state.currentAgentSteps.at(-1)?.id || null;
    state.currentJobId = job.job_id;
    state.currentAgentSteps =
      job.mode === "agent" ? await fetchAgentSteps(job.job_id) : [];

    if (
      !state.selectedStepId ||
      state.selectedStepId === previousLastStepId ||
      !state.currentAgentSteps.some((step) => step.id === state.selectedStepId)
    ) {
      state.selectedStepId = state.currentAgentSteps.at(-1)?.id || null;
    }

    if (job.conversation_id && (!state.currentConversationId || job.conversation_id === state.currentConversationId)) {
      if (!state.currentConversationId) {
        state.currentConversationId = job.conversation_id;
        state.currentConversationTitle = job.conversation_title;
        state.currentProvider = job.provider || state.currentProvider;
        state.pendingNewChat = false;
      }
      state.currentConversationTitle = job.conversation_title || state.currentConversationTitle;
      state.currentProvider = job.provider || state.currentProvider;
      state.currentMode = job.mode || state.currentMode;
      upsertConversationJob(job);
      hydrateAgentSettingsFromJob(job);
      syncModeButtons();
      updateUrl();
      renderCurrentConversation();
    }

    await Promise.all([fetchConversationList(), refreshDiagnostics()]);
  };

  socket.onopen = () => {
    if (state.socket !== socket) return;
    state.socketReconnectAttempts = 0;
    state.lastRealtimeEventAt = Date.now();
    socket.send("subscribe");
  };

  const startFallbackPolling = () => {
    if (state.fallbackPollHandle) return;
    state.fallbackPollHandle = window.setInterval(async () => {
      const job = await fetchJob(jobId);
      if (!job) return;
      state.lastRealtimeEventAt = Date.now();
      if (job.mode === "agent") {
        state.currentAgentSteps = await fetchAgentSteps(jobId);
        state.selectedStepId = state.currentAgentSteps.at(-1)?.id || state.selectedStepId;
      }
      if (job.conversation_id === state.currentConversationId) {
        upsertConversationJob(job);
        renderCurrentConversation();
      }
      if (terminalStates.has(job.status)) {
        clearInterval(state.fallbackPollHandle);
        state.fallbackPollHandle = null;
      }
    }, 2000);
  };

  socket.onerror = startFallbackPolling;
  socket.onclose = () => {
    if (state.socket !== socket) return;
    startFallbackPolling();
    const latest = getLatestJob();
    if (!latest || terminalStates.has(latest.status) || state.currentJobId !== jobId) {
      return;
    }
    const delay = Math.min(15000, 1000 * (2 ** state.socketReconnectAttempts));
    state.socketReconnectAttempts += 1;
    state.socketReconnectHandle = window.setTimeout(() => {
      connectSocket(jobId);
    }, delay);
  };
}

async function createJob() {
  const question = elements.questionInput.value.trim();
  if (!question) {
    elements.questionInput.focus();
    return;
  }

  if (state.currentMode === "agent" && !elements.agentWorkspaceInput.value.trim()) {
    elements.composerNote.textContent = "Choose an allowed workspace before starting Ordex.";
    elements.agentWorkspaceInput.focus();
    return;
  }

  elements.submitButton.disabled = true;
  setStatus("queued");
  const startNew = state.pendingNewChat || !state.currentConversationId;
  let response;

  if (state.currentMode === "agent") {
    saveAgentSettings();
    response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        provider: "chatgpt",
        mode: "agent",
        start_new_chat: startNew,
        conversation_id:
          state.currentConversationId && !state.pendingNewChat
            ? state.currentConversationId
            : null,
        agent: {
          workspace: elements.agentWorkspaceInput.value.trim(),
          max_steps: elements.agentMaxStepsInput.value
            ? Number(elements.agentMaxStepsInput.value)
            : null,
          command_timeout_seconds: elements.agentCommandTimeoutInput.value
            ? Number(elements.agentCommandTimeoutInput.value)
            : null,
          execution_backend: elements.agentExecutionBackendInput.value || null,
          network_enabled: elements.agentNetworkEnabledInput.checked,
        },
      }),
    });
  } else {
    const formData = new FormData();
    formData.append("question", question);
    formData.append("provider", state.currentProvider);
    formData.append("mode", state.currentMode);
    formData.append("start_new_chat", String(startNew));
    if (state.currentConversationId && !state.pendingNewChat) {
      formData.append("conversation_id", state.currentConversationId);
    }
    const file = elements.imageInput.files?.[0];
    if (file) formData.append("image", file);
    response = await fetch("/api/jobs", { method: "POST", body: formData });
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    elements.submitButton.disabled = false;
    setStatus("failed");
    const message = payload.detail || payload.error_message || "Request failed.";
    elements.profileMessage.textContent = message;
    elements.composerNote.textContent = message;
    elements.questionInput.focus();
    return;
  }

  elements.questionInput.value = "";
  clearUpload();
  state.pendingNewChat = false;
  state.currentConversationId = payload.conversation_id;
  state.currentProvider = payload.provider || state.currentProvider;
  state.currentJobId = payload.job_id;
  state.currentAgentSteps = [];
  state.selectedStepId = null;
  state.currentConversationTitle =
    state.currentConversationTitle === "Fresh run"
      ? question.slice(0, 52)
      : state.currentConversationTitle;
  elements.profileMessage.textContent = "Run started in the current Chrome session.";
  elements.composerNote.textContent =
    `Next send stays in the current ${providerLabels[state.currentProvider]} tab.`;
  updateUrl();
  connectSocket(payload.job_id);
  await loadConversation(payload.conversation_id);
}

async function openProfile() {
  elements.profileMessage.textContent = "Opening the configured Chrome session…";
  try {
    const response = await fetch("/api/profile/open", { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    elements.profileMessage.textContent =
      payload.message || payload.detail || response.statusText;
    await refreshDiagnostics();
  } catch {
    elements.profileMessage.textContent = "Could not open the configured Chrome session.";
  }
}

elements.providerSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".provider-chip");
  if (!button) return;
  if (
    state.currentConversationJobs.length > 0 &&
    button.dataset.provider !== state.currentProvider &&
    !state.pendingNewChat
  ) {
    return;
  }
  state.currentProvider = button.dataset.provider;
  syncModeButtons();
  updateConversationHeader();
  renderThread();
});

elements.modeSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".mode-chip");
  if (!button) return;
  state.currentMode = button.dataset.mode;
  syncModeButtons();
  renderCurrentConversation();
});

elements.inspectorTabRow.addEventListener("click", (event) => {
  const button = event.target.closest(".tab-button");
  if (!button) return;
  state.selectedInspectorTab = button.dataset.inspectorTab;
  renderStepInspector();
});

elements.imageInput.addEventListener("change", () => {
  renderUploadPreview(elements.imageInput.files?.[0] || null);
});

elements.questionInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    createJob();
  }
});

[
  elements.agentWorkspaceInput,
  elements.agentMaxStepsInput,
  elements.agentCommandTimeoutInput,
  elements.agentExecutionBackendInput,
  elements.agentNetworkEnabledInput,
].forEach((input) => {
  input.addEventListener("change", () => {
    saveAgentSettings();
    renderRuntimeStrip();
  });
});

elements.agentWorkspaceInput.addEventListener("input", renderRuntimeStrip);
elements.clearUploadButton.addEventListener("click", clearUpload);
elements.submitButton.addEventListener("click", createJob);
elements.openProfileButton.addEventListener("click", openProfile);
elements.newChatButton.addEventListener("click", startNewChat);
elements.pinConversationButton.addEventListener("click", toggleConversationPin);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshDiagnostics();
  }
});

async function bootstrap() {
  loadAgentSettings();
  syncModeButtons();
  renderDiagnostics();
  renderCurrentConversation();

  const [conversations] = await Promise.all([
    fetchConversationList(),
    refreshDiagnostics(),
  ]);

  if (initialConversationId) {
    await loadConversation(initialConversationId);
  } else if (initialJobId) {
    const job = await fetchJob(initialJobId);
    if (job?.conversation_id) {
      await loadConversation(job.conversation_id);
    } else {
      startNewChat();
    }
  } else if (conversations[0]?.conversation_id) {
    await loadConversation(conversations[0].conversation_id);
  } else {
    startNewChat();
  }

  state.diagnosticsPollHandle = window.setInterval(() => {
    if (document.visibilityState === "visible") {
      refreshDiagnostics();
    }
  }, 30000);

  state.watchdogHandle = window.setInterval(() => {
    const latest = getLatestJob();
    if (
      document.visibilityState !== "visible" ||
      state.watchdogReloading ||
      !latest ||
      terminalStates.has(latest.status) ||
      Date.now() - state.lastRealtimeEventAt < 45000
    ) {
      return;
    }
    state.watchdogReloading = true;
    window.sessionStorage.setItem(
      "ordak.panel.lastWatchdogRefresh",
      new Date().toISOString(),
    );
    window.location.reload();
  }, 10000);
}

bootstrap();
