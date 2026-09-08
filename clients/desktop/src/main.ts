import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { AudioCapture } from "./lib/audio";
import { cancelReply, confirmReply, shouldShowWriteConfirm } from "./lib/confirmation";
import { normalizeBrainUrl } from "./lib/format";
import { editSeedValue, formatPreferenceDisplay, type PreferenceRow } from "./lib/prefs";
import type {
  AppSettings,
  ChatClosedEnvelope,
  ChatEventEnvelope,
  ChatMessage,
  ConversationSummary,
  HealthBadge,
  LaunchResult,
  SseEvent,
} from "./lib/types";

const TREE_ART = `
         # #### ####
       ### \\/#|### |/####
      ##\\/#/ \\||/##/_/##/_#
    ###  \\/###|/ \\/ # ###
  ##_\\_#\\_\\## | #/###_/_####
 ## #### # \\ #| /  #### ##/##
  __#_--###\`  |{,###---###-~
            \\ }{
             }}{
             }}{
            {{}
       , -=-~{ .-^- _
             \`}
              {
`.replace(/^\n/, "").replace(/\n$/, "");

const HELP_TEXT = [
  "Commands:",
  "  /new       — new conversation",
  "  /history   — resume a past conversation",
  "  /settings  — edit brain preferences",
  "  /connect   — brain URL + token",
  "  /copy      — copy last assistant reply",
  "  /help      — this help",
  "  /quit      — close the window",
  "Keys: Enter send · Shift+Enter newline · mic voice · Esc interrupt",
].join("\n");

type UiState = {
  settings: AppSettings | null;
  conversationId: string | null;
  messages: ChatMessage[];
  busy: boolean;
  activeStreamId: number | null;
  status: string;
  confirmMessageId: string | null;
  health: HealthBadge | null;
  recording: boolean;
  prefsEditingKey: string | null;
};

const state: UiState = {
  settings: null,
  conversationId: null,
  messages: [],
  busy: false,
  activeStreamId: null,
  status: "",
  confirmMessageId: null,
  health: null,
  recording: false,
  prefsEditingKey: null,
};

const capture = new AudioCapture();
let recordStartedAt = 0;
let msgSeq = 0;

function nextId(prefix: string): string {
  msgSeq += 1;
  return `${prefix}-${msgSeq}`;
}

const el = {
  meta: document.querySelector("#meta") as HTMLElement,
  splash: document.querySelector("#splash") as HTMLElement,
  tree: document.querySelector("#tree") as HTMLElement,
  transcript: document.querySelector("#transcript") as HTMLElement,
  workStatus: document.querySelector("#work-status") as HTMLElement,
  confirmBar: document.querySelector("#confirm-bar") as HTMLElement,
  composer: document.querySelector("#composer") as HTMLTextAreaElement,
  micBtn: document.querySelector("#mic-btn") as HTMLButtonElement,
  historyDialog: document.querySelector("#history-dialog") as HTMLDialogElement,
  historyList: document.querySelector("#history-list") as HTMLElement,
  btnHistoryClose: document.querySelector("#btn-history-close") as HTMLButtonElement,
  prefsDialog: document.querySelector("#prefs-dialog") as HTMLDialogElement,
  prefsList: document.querySelector("#prefs-list") as HTMLElement,
  prefsEditWrap: document.querySelector("#prefs-edit-wrap") as HTMLElement,
  prefsEditLabel: document.querySelector("#prefs-edit-label") as HTMLElement,
  prefsEdit: document.querySelector("#prefs-edit") as HTMLInputElement,
  btnPrefsClose: document.querySelector("#btn-prefs-close") as HTMLButtonElement,
  connectDialog: document.querySelector("#connect-dialog") as HTMLDialogElement,
  connectForm: document.querySelector("#connect-form") as HTMLFormElement,
  connectUrl: document.querySelector("#connect-url") as HTMLInputElement,
  connectToken: document.querySelector("#connect-token") as HTMLInputElement,
  connectCancel: document.querySelector("#connect-cancel") as HTMLButtonElement,
  btnConfirm: document.querySelector("#btn-confirm") as HTMLButtonElement,
  btnCancelWrite: document.querySelector("#btn-cancel-write") as HTMLButtonElement,
};

function setStatus(text: string) {
  state.status = text;
  if (!text) {
    el.workStatus.classList.add("hidden");
    el.workStatus.textContent = "";
    return;
  }
  el.workStatus.classList.remove("hidden");
  el.workStatus.textContent = text;
}

function updateMeta() {
  const url = state.settings?.brain_url || "http://127.0.0.1:8000";
  let host = url;
  try {
    const u = new URL(url);
    host = `${u.hostname}${u.port ? `:${u.port}` : ""}`;
  } catch {
    /* keep */
  }
  const cid = state.conversationId ? state.conversationId.slice(0, 8) : "new";
  const note = state.health?.badge || "…";
  el.meta.textContent = `${host} · convo ${cid} · ${note}`;
  el.meta.title = state.health?.detail || state.health?.status || "";
}

function syncSplash() {
  const empty = state.messages.length === 0;
  el.splash.classList.toggle("hidden", !empty);
  el.transcript.classList.toggle("hidden", empty);
}

function renderTranscript() {
  el.transcript.replaceChildren();
  for (const msg of state.messages) {
    const div = document.createElement("div");
    div.className = `line ${msg.role}`;
    div.dataset.id = msg.id;
    if (msg.role === "user") {
      div.textContent = `> ${msg.content}`;
    } else {
      div.textContent = msg.content || (msg.streaming ? "…" : "");
    }
    el.transcript.appendChild(div);
  }
  el.transcript.scrollTop = el.transcript.scrollHeight;
  const showConfirm = state.confirmMessageId != null && !state.busy && !state.recording;
  el.confirmBar.classList.toggle("hidden", !showConfirm);
  syncSplash();
  syncMicEnabled();
  updateMeta();
}

function appendMessage(msg: Omit<ChatMessage, "id"> & { id?: string }): ChatMessage {
  const full: ChatMessage = { id: msg.id ?? nextId(msg.role), ...msg };
  state.messages.push(full);
  renderTranscript();
  return full;
}

function lastAssistant(): ChatMessage | undefined {
  for (let i = state.messages.length - 1; i >= 0; i--) {
    if (state.messages[i].role === "assistant") return state.messages[i];
  }
  return undefined;
}

function setBusy(busy: boolean) {
  state.busy = busy;
  syncMicEnabled();
  renderTranscript();
}

function syncMicEnabled() {
  const allow = !state.busy || state.recording;
  el.micBtn.disabled = !allow;
  el.micBtn.classList.toggle("recording", state.recording);
  el.micBtn.title = state.recording
    ? "Stop recording"
    : "Voice (click to record)";
}

async function refreshHealth() {
  try {
    const badge = await invoke<HealthBadge>("get_health_badge");
    state.health = badge;
  } catch (e) {
    state.health = {
      badge: "unreachable",
      status: "unreachable",
      detail: String(e),
    };
  }
  updateMeta();
  return state.health;
}

async function persistConversationId(id: string | null) {
  state.conversationId = id;
  await invoke("set_active_conversation", { conversationId: id });
  updateMeta();
}

async function loadHistoryMessages(conversationId: string) {
  setStatus("Restoring…");
  const msgs = await invoke<Array<{ role: string; content: string }>>("load_messages", {
    conversationId,
  });
  state.messages = msgs
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      id: nextId(m.role),
      role: m.role as "user" | "assistant",
      content: m.content || "",
    }));
  state.confirmMessageId = null;
  setStatus("");
  renderTranscript();
}

async function openHistory() {
  setStatus("Loading history…");
  el.historyList.replaceChildren();
  try {
    const convos = await invoke<ConversationSummary[]>("list_convos", { limit: 50 });
    for (const c of convos) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      if (c.id === state.conversationId) btn.classList.add("active");
      const preview = document.createElement("span");
      preview.className = "history-preview";
      preview.textContent = c.preview || c.id;
      btn.appendChild(preview);
      btn.addEventListener("click", async () => {
        el.historyDialog.close();
        await persistConversationId(c.id);
        await loadHistoryMessages(c.id);
      });
      li.appendChild(btn);
      el.historyList.appendChild(li);
    }
    if (convos.length === 0) {
      const li = document.createElement("li");
      li.textContent = "No conversations yet.";
      el.historyList.appendChild(li);
    }
    setStatus("");
    el.historyDialog.showModal();
  } catch (e) {
    setStatus("");
    appendMessage({ role: "error", content: String(e) });
  }
}

function closeHistory() {
  if (el.historyDialog.open) el.historyDialog.close();
}

async function startNewConversation() {
  await cancelRecording(true);
  if (state.activeStreamId != null) {
    await invoke("cancel_chat", { streamId: state.activeStreamId });
    state.activeStreamId = null;
  }
  await persistConversationId(null);
  state.messages = [];
  state.confirmMessageId = null;
  setStatus("");
  setBusy(false);
  renderTranscript();
  el.composer.focus();
}

async function copyLastAssistant() {
  const last = lastAssistant();
  if (!last || !last.content) {
    appendMessage({ role: "system", content: "Nothing to copy." });
    return;
  }
  try {
    await invoke("copy_text", { text: last.content });
    setStatus("Copied last reply.");
  } catch (e) {
    appendMessage({ role: "error", content: String(e) });
  }
}

function openConnect() {
  const s = state.settings;
  el.connectUrl.value = s?.brain_url || "http://127.0.0.1:8000";
  el.connectToken.value = s?.token || "";
  el.connectToken.placeholder = s?.has_token
    ? "(saved — leave blank to keep)"
    : "MIMIR_CLIENT_TOKEN";
  el.connectDialog.showModal();
}

async function saveConnectFromForm(ev: Event) {
  ev.preventDefault();
  let url: string;
  try {
    url = normalizeBrainUrl(el.connectUrl.value);
  } catch (e) {
    appendMessage({ role: "error", content: String(e) });
    return;
  }
  const rawToken = el.connectToken.value;
  const keepExisting = rawToken.trim() === "" && !!state.settings?.has_token;
  try {
    const saved = await invoke<AppSettings>("save_app_settings", {
      input: {
        brain_url: url,
        token: keepExisting ? null : rawToken,
        conversation_id: state.conversationId,
        keep_existing_token: keepExisting,
      },
    });
    state.settings = saved;
    el.connectDialog.close();
    await refreshHealth();
    appendMessage({ role: "system", content: "Connect settings saved." });
  } catch (e) {
    appendMessage({ role: "error", content: String(e) });
  }
}

async function openPrefs() {
  setStatus("Loading settings…");
  el.prefsList.replaceChildren();
  el.prefsEditWrap.classList.add("hidden");
  state.prefsEditingKey = null;
  try {
    const rows = await invoke<PreferenceRow[]>("list_preferences");
    if (!rows.length) {
      setStatus("");
      appendMessage({ role: "system", content: "No preferences available." });
      return;
    }
    for (const row of rows) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = formatPreferenceDisplay(row.key, row.value);
      btn.addEventListener("click", () => beginPrefsEdit(row.key, row.value));
      li.appendChild(btn);
      el.prefsList.appendChild(li);
    }
    setStatus("");
    el.prefsDialog.showModal();
  } catch (e) {
    setStatus("");
    appendMessage({ role: "error", content: String(e) });
  }
}

function beginPrefsEdit(key: string, value: string | null | undefined) {
  state.prefsEditingKey = key;
  el.prefsEditLabel.textContent = key;
  el.prefsEdit.value = editSeedValue(key, value);
  el.prefsEditWrap.classList.remove("hidden");
  el.prefsEdit.focus();
  el.prefsEdit.select();
}

async function savePrefsEdit() {
  const key = state.prefsEditingKey;
  if (!key) return;
  const text = el.prefsEdit.value.trim();
  if (!text) return;
  setStatus("Saving…");
  try {
    const saved = await invoke<PreferenceRow>("save_preference", { key, value: text });
    el.prefsDialog.close();
    setStatus("");
    appendMessage({
      role: "system",
      content: `Preference saved: ${saved.key} = ${saved.value ?? text}`,
    });
  } catch (e) {
    setStatus("");
    appendMessage({ role: "error", content: `Could not save preference: ${e}` });
  }
}

function closePrefs() {
  if (state.prefsEditingKey && !el.prefsEditWrap.classList.contains("hidden")) {
    state.prefsEditingKey = null;
    el.prefsEditWrap.classList.add("hidden");
    return;
  }
  if (el.prefsDialog.open) el.prefsDialog.close();
}

async function handleCommand(raw: string): Promise<boolean> {
  const cmd = raw.trim().toLowerCase();
  if (!cmd.startsWith("/")) return false;
  const head = cmd.split(/\s+/)[0];
  switch (head) {
    case "/new":
      await startNewConversation();
      return true;
    case "/history":
      await openHistory();
      return true;
    case "/settings":
      await openPrefs();
      return true;
    case "/connect":
      openConnect();
      return true;
    case "/copy":
      await copyLastAssistant();
      return true;
    case "/help":
      appendMessage({ role: "system", content: HELP_TEXT });
      return true;
    case "/quit":
    case "/exit":
    case "/q":
      await getCurrentWindow().close();
      return true;
    default:
      appendMessage({
        role: "error",
        content: `Unknown command: ${head}. Try /help.`,
      });
      return true;
  }
}

async function sendMessage(raw: string) {
  const message = raw.replace(/^\s+|\s+$/g, "");
  if (!message) return;
  if (state.busy || state.recording) return;

  if (message.startsWith("/")) {
    el.composer.value = "";
    autoSizeComposer();
    await handleCommand(message);
    return;
  }

  if (!state.settings?.has_token && !state.settings?.token) {
    openConnect();
    appendMessage({ role: "error", content: "Bad token — check /connect." });
    return;
  }

  state.confirmMessageId = null;
  const priorUser = message;
  appendMessage({ role: "user", content: message });
  el.composer.value = "";
  autoSizeComposer();

  const assistant = appendMessage({
    role: "assistant",
    content: "",
    streaming: true,
  });

  setBusy(true);
  setStatus("Working…");

  let streamId: number;
  try {
    streamId = await invoke<number>("start_chat", {
      message,
      conversationId: state.conversationId,
    });
  } catch (e) {
    assistant.role = "error";
    assistant.content = String(e);
    assistant.streaming = false;
    setBusy(false);
    setStatus("");
    renderTranscript();
    return;
  }

  state.activeStreamId = streamId;
  let toolsUsed: string[] = [];

  const unlistenEvent = await listen<ChatEventEnvelope>("chat:event", (ev) => {
    if (ev.payload.stream_id !== streamId) return;
    handleSse(ev.payload.event, assistant, (tools) => {
      toolsUsed = tools;
    });
  });

  const unlistenClosed = await listen<ChatClosedEnvelope>("chat:closed", (ev) => {
    if (ev.payload.stream_id !== streamId) return;
    unlistenEvent();
    unlistenClosed();
    assistant.streaming = false;
    if (
      shouldShowWriteConfirm(assistant.content, toolsUsed, priorUser) &&
      assistant.role === "assistant"
    ) {
      state.confirmMessageId = assistant.id;
    }
    state.activeStreamId = null;
    setBusy(false);
    setStatus("");
    renderTranscript();
    void refreshHealth();
  });
}

function handleSse(
  event: SseEvent,
  assistant: ChatMessage,
  setTools: (t: string[]) => void,
) {
  const type = event.type;
  if (type === "meta") {
    const cid = (event as { conversation_id?: string }).conversation_id;
    if (cid) void persistConversationId(cid);
    return;
  }
  if (type === "token") {
    assistant.content += (event as { text?: string }).text || "";
    renderTranscript();
    return;
  }
  if (type === "tool_start") {
    const name = (event as { name?: string }).name || "tool";
    setStatus(`${name}… (Esc to detach)`);
    return;
  }
  if (type === "tool_end") {
    setStatus("Working…");
    return;
  }
  if (type === "sentence") return;
  if (type === "done") {
    const done = event as { conversation_id?: string; tools_used?: string[] };
    if (done.conversation_id) void persistConversationId(done.conversation_id);
    setTools(done.tools_used || []);
    return;
  }
  if (type === "error") {
    const msg = (event as { message?: string }).message || "error";
    const cid = (event as { conversation_id?: string }).conversation_id;
    if (cid) void persistConversationId(cid);
    if (!assistant.content) {
      assistant.role = "error";
      assistant.content = msg;
    } else {
      appendMessage({ role: "error", content: msg });
    }
    renderTranscript();
  }
}

async function cancelRecording(silent: boolean): Promise<void> {
  if (!state.recording && !capture.isRecording) return;
  try {
    await capture.stop("cancel");
  } catch {
    /* ignore */
  }
  state.recording = false;
  syncMicEnabled();
  if (!silent) {
    setStatus("");
    appendMessage({ role: "system", content: "Recording cancelled." });
  }
}

async function toggleMic() {
  if (state.busy && !state.recording) return;

  if (state.recording) {
    setStatus("Transcribing…");
    const recordMs = Math.round(performance.now() - recordStartedAt);
    let wav: Uint8Array | null = null;
    try {
      wav = await capture.stop("stop");
    } catch (e) {
      state.recording = false;
      syncMicEnabled();
      setStatus("");
      appendMessage({ role: "error", content: String(e) });
      return;
    }
    state.recording = false;
    syncMicEnabled();
    if (!wav) {
      setStatus("");
      return;
    }
    const sttStarted = performance.now();
    try {
      const result = await invoke<{ text: string; language?: string | null }>("transcribe_wav", {
        wav: Array.from(wav),
      });
      const sttMs = Math.round(performance.now() - sttStarted);
      void invoke("log_voice_latency", { recordMs, sttMs, ok: true });
      setStatus("");
      await sendMessage(result.text);
    } catch (e) {
      const sttMs = Math.round(performance.now() - sttStarted);
      void invoke("log_voice_latency", { recordMs, sttMs, ok: false });
      setStatus("");
      appendMessage({ role: "error", content: String(e) });
    }
    return;
  }

  try {
    await capture.start();
    state.recording = true;
    recordStartedAt = performance.now();
    syncMicEnabled();
    setStatus("Recording… (click mic or Esc when done)");
  } catch (e) {
    state.recording = false;
    syncMicEnabled();
    appendMessage({
      role: "error",
      content: `Mic unavailable: ${e}`,
    });
  }
}

async function handleEscape() {
  if (state.recording) {
    await cancelRecording(false);
    return;
  }
  if (el.prefsDialog.open) {
    closePrefs();
    return;
  }
  if (el.historyDialog.open) {
    closeHistory();
    return;
  }
  if (el.connectDialog.open) {
    el.connectDialog.close();
    return;
  }
  if (state.activeStreamId != null) {
    await invoke("cancel_chat", { streamId: state.activeStreamId });
    setStatus("Detached from stream (brain may keep working).");
  }
}

function autoSizeComposer() {
  const ta = el.composer;
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight, window.innerHeight * 0.4)}px`;
}

function wireKeys() {
  el.composer.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(el.composer.value);
    }
  });
  el.composer.addEventListener("input", autoSizeComposer);

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      void handleEscape();
    }
  });
}

async function boot() {
  el.tree.textContent = TREE_ART;
  wireKeys();

  el.micBtn.addEventListener("click", () => void toggleMic());
  el.btnHistoryClose.addEventListener("click", closeHistory);
  el.btnPrefsClose.addEventListener("click", closePrefs);
  el.connectCancel.addEventListener("click", () => el.connectDialog.close());
  el.connectForm.addEventListener("submit", (e) => void saveConnectFromForm(e));
  el.prefsEdit.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void savePrefsEdit();
    }
  });
  el.btnConfirm.addEventListener("click", () => {
    const msg = state.messages.find((m) => m.id === state.confirmMessageId);
    if (!msg) return;
    state.confirmMessageId = null;
    void sendMessage(confirmReply(msg.content));
  });
  el.btnCancelWrite.addEventListener("click", () => {
    const msg = state.messages.find((m) => m.id === state.confirmMessageId);
    if (!msg) return;
    state.confirmMessageId = null;
    void sendMessage(cancelReply(msg.content));
  });

  try {
    state.settings = await invoke<AppSettings>("get_app_settings");
  } catch (e) {
    appendMessage({ role: "error", content: `Settings load failed: ${e}` });
  }

  // TUI policy: each launch starts a fresh conversation; /history resumes past ones.
  state.conversationId = null;
  try {
    await persistConversationId(null);
  } catch {
    /* ignore */
  }

  if (!state.settings?.has_token) {
    openConnect();
  }

  try {
    const launch = await invoke<LaunchResult>("ensure_brain");
    if (launch.message && !launch.already_running) {
      appendMessage({ role: "system", content: launch.message });
    }
  } catch (e) {
    appendMessage({ role: "system", content: `Brain launch: ${e}` });
  }

  await refreshHealth();
  renderTranscript();

  el.composer.focus();
  window.setInterval(() => void refreshHealth(), 30_000);
}

void boot();
