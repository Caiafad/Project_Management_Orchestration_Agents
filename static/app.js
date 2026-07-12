/* ═══════════════════════════════════════════════════════════════
   PROJECT MANAGEMENT AGENT — FRONTEND APPLICATION
   ═══════════════════════════════════════════════════════════════ */

// ── AGENT DEFINITIONS ─────────────────────────────────────────
const AGENTS = [
  { id: "delegate_to_project_planning",    name: "Project Planning",    icon: "fa-chart-gantt",       color: "#4A9FFF" },
  { id: "delegate_to_scope_definition",    name: "Scope Definition",    icon: "fa-bullseye",          color: "#9B59B6" },
  { id: "delegate_to_project_orchestration",name:"Orchestration",       icon: "fa-people-group",      color: "#27AE60" },
  { id: "delegate_to_business_manager",    name: "Business Manager",    icon: "fa-briefcase",         color: "#E67E22" },
  { id: "delegate_to_financial_manager",   name: "Financial Manager",   icon: "fa-calculator",        color: "#1ABC9C" },
  { id: "delegate_to_internal_comms",      name: "Internal Comms",      icon: "fa-comments",          color: "#E91E63" },
  { id: "delegate_to_external_comms",      name: "External Comms",      icon: "fa-envelope-open-text",color: "#E74C3C" },
  { id: "delegate_to_prioritization",      name: "Prioritization",      icon: "fa-ranking-star",      color: "#F39C12" },
];

// ── STATE ──────────────────────────────────────────────────────
let ws = null;
let isProcessing = false;
const docCounts = { word: 0, excel: 0, pptx: 0 };
let activeAgentId = null;

// ── INIT ───────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  renderAgents();
  connectWebSocket();
  checkGmailStatus();

  // Show success banner if redirected back after OAuth
  if (location.search.includes("gmail=connected")) {
    history.replaceState({}, "", "/");
    appendThought("doc", "fa-envelope-circle-check", "#3dd68c", "Gmail connected successfully — emails will now send from your account.");
  }

  const input = document.getElementById("chat-input");
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
});

// ── WEBSOCKET ──────────────────────────────────────────────────
function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${protocol}://${location.host}/ws`);

  ws.onopen = () => {
    setConnectionStatus("connected", "Connected");
    loadAllFiles();    // populate existing output files on connect
    loadProjectDocs(); // populate project document RAG uploads
  };

  ws.onclose = () => {
    setConnectionStatus("error", "Disconnected");
    setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => {
    setConnectionStatus("error", "Connection error");
  };

  ws.onmessage = (e) => {
    const event = JSON.parse(e.data);
    handleEvent(event);
  };
}

function setConnectionStatus(state, label) {
  const dot   = document.getElementById("conn-dot");
  const lbl   = document.getElementById("conn-label");
  dot.className = "conn-dot " + (state === "connected" ? "connected" : "error");
  lbl.textContent = label;
}

// ── EVENT HANDLER ──────────────────────────────────────────────
function handleEvent(event) {
  switch (event.type) {

    case "thinking":
      hidePlaceholder();
      appendThought("thinking", "fa-circle-nodes", "#4f78f1", event.text);
      setOutputStatus("agent", "fa-brain", "#4f78f1", "Orchestrator", event.text);
      break;

    case "agent_activated":
      hidePlaceholder();
      setAgentActive(event.tool_name, event.color);
      appendThought("agent", "fa-robot", event.color, `<strong style="color:${event.color}">${event.agent}</strong> — ${event.task}`);
      setOutputStatus("agent", "fa-robot", event.color, event.agent, event.description);
      break;

    case "agent_done":
      setAgentDone(event.tool_name);
      appendThought("thinking", "fa-check", "#3dd68c", `${event.agent} completed`);
      break;

    case "tool_call":
      hidePlaceholder();
      const toolMeta = getToolMeta(event.tool);
      appendThought("tool", toolMeta.icon, toolMeta.color, event.description);
      setOutputStatus(event.tool, toolMeta.faIcon, toolMeta.color, toolMeta.label, event.description);

      // Flash the doc icon if it's a document tool
      if (["word","excel","powerpoint"].includes(event.tool)) {
        setDocIconCreating(event.tool);
      }
      break;

    case "document_created":
      docCounts[event.doc_type === "powerpoint" ? "pptx" : event.doc_type]++;
      updateDocCount(event.doc_type);
      setDocIconActive(event.doc_type);
      addDocFile(event.doc_type, event.filename);
      appendThought("doc", getDocFaIcon(event.doc_type), getDocColor(event.doc_type),
        `<strong>${event.filename}</strong> created`);
      break;

    case "response":
      removeTypingIndicator();
      appendAgentMessage(event.text);
      break;

    case "error":
      removeTypingIndicator();
      appendThought("error", "fa-triangle-exclamation", "#ef4444", event.message);
      setProcessing(false);
      resetOutputStatus();
      resetAllAgents();
      break;

    case "done":
      setProcessing(false);
      resetOutputStatus();
      resetAllAgents();
      break;
  }
}

// ── SEND MESSAGE ───────────────────────────────────────────────
function sendMessage() {
  const input = document.getElementById("chat-input");
  const text  = input.value.trim();
  if (!text || isProcessing || !ws || ws.readyState !== WebSocket.OPEN) return;

  input.value = "";
  appendUserMessage(text);
  addTypingIndicator();
  setProcessing(true);
  ws.send(JSON.stringify({ message: text }));
}

// ── AGENT PANEL ────────────────────────────────────────────────
function renderAgents() {
  const list = document.getElementById("agents-list");
  list.innerHTML = AGENTS.map(a => `
    <div class="agent-item" id="agent-${a.id}" style="--agent-color: ${a.color}">
      <div class="agent-icon-wrap">
        <i class="fas ${a.icon}"></i>
      </div>
      <div class="agent-info">
        <div class="agent-name">${a.name}</div>
        <div class="agent-status">Ready</div>
      </div>
      <div class="agent-pulse"></div>
    </div>
  `).join("");
}

function setAgentActive(toolName, color) {
  // Reset all
  AGENTS.forEach(a => {
    const el = document.getElementById("agent-" + a.id);
    if (el) { el.classList.remove("active","done"); el.querySelector(".agent-status").textContent = "Ready"; }
  });
  const el = document.getElementById("agent-" + toolName);
  if (el) {
    el.classList.add("active");
    el.querySelector(".agent-status").textContent = "Working...";
    el.style.setProperty("--agent-color", color);
    activeAgentId = toolName;
  }
}

function setAgentDone(toolName) {
  const el = document.getElementById("agent-" + toolName);
  if (el) {
    el.classList.remove("active");
    el.classList.add("done");
    el.querySelector(".agent-status").textContent = "Complete ✓";
  }
  activeAgentId = null;
}

function resetAllAgents() {
  AGENTS.forEach(a => {
    const el = document.getElementById("agent-" + a.id);
    if (el) { el.classList.remove("active","done"); el.querySelector(".agent-status").textContent = "Ready"; }
  });
}

// ── CHAIN OF THOUGHT ───────────────────────────────────────────
function appendThought(type, faIconClass, color, html) {
  const scroll = document.getElementById("thought-scroll");
  const now = new Date();
  const ts = String(now.getHours()).padStart(2,"0") + ":" + String(now.getMinutes()).padStart(2,"0");

  const entry = document.createElement("div");
  entry.className = `thought-entry thought-${type}`;
  entry.innerHTML = `
    <span class="thought-ts">${ts}</span>
    <div class="thought-icon" style="background:color-mix(in srgb,${color} 15%,transparent);color:${color}">
      <i class="fas ${faIconClass}"></i>
    </div>
    <div class="thought-body">${html}</div>
  `;
  scroll.appendChild(entry);
  scroll.scrollTop = scroll.scrollHeight;
}

function hidePlaceholder() {
  const ph = document.getElementById("thought-placeholder");
  if (ph) ph.style.display = "none";
}

function clearThoughts() {
  const scroll = document.getElementById("thought-scroll");
  scroll.innerHTML = `
    <div class="thought-placeholder" id="thought-placeholder">
      <i class="fas fa-brain"></i>
      <p>Agent reasoning and tool activity will appear here in real time.</p>
    </div>
  `;
}

// ── OUTPUT STATUS ──────────────────────────────────────────────
function setOutputStatus(tool, faIconClass, color, label, description) {
  const el = document.getElementById("output-status");
  const cls = ["word","excel","powerpoint"].includes(tool) ? tool.replace("powerpoint","pptx") : tool;
  el.innerHTML = `
    <div class="op-active">
      <div class="op-active-icon ${cls}" style="background:color-mix(in srgb,${color} 18%,transparent);color:${color}">
        <i class="fas ${faIconClass}"></i>
      </div>
      <div class="op-text">
        <strong>${label}</strong>
        <span>${description || ""}</span>
      </div>
    </div>
  `;
}

function resetOutputStatus() {
  document.getElementById("output-status").innerHTML = `
    <div class="op-idle">
      <i class="fas fa-circle-pause"></i>
      <span>Idle — ready for your request</span>
    </div>
  `;
}

// ── DOC ICONS ──────────────────────────────────────────────────
function getDocKey(docType) {
  return docType === "powerpoint" ? "pptx" : docType;
}

function getDocEl(docType) {
  const map = { word: "icon-word", excel: "icon-excel", powerpoint: "icon-pptx", pptx: "icon-pptx" };
  return document.getElementById(map[docType]);
}

function setDocIconCreating(docType) {
  const el = getDocEl(docType);
  if (el) { el.classList.add("creating"); el.classList.remove("active"); }
}

function setDocIconActive(docType) {
  const el = getDocEl(docType);
  if (el) { el.classList.remove("creating"); el.classList.add("active"); }
}

function updateDocCount(docType) {
  const key = getDocKey(docType);
  const countEl = document.getElementById("count-" + key);
  if (!countEl) return;
  countEl.textContent = docCounts[key];
  countEl.classList.add("visible");
}

function _recomputeCounts() {
  docCounts.word = 0; docCounts.excel = 0; docCounts.pptx = 0;
  document.querySelectorAll("#doc-file-list [data-filename]").forEach(el => {
    const k = el.dataset.doctype;
    if (k) docCounts[k] = (docCounts[k] || 0) + 1;
  });
  ["word", "excel", "pptx"].forEach(k => {
    const countEl = document.getElementById("count-" + k);
    const iconEl  = document.getElementById("icon-" + k);
    if (countEl) { countEl.textContent = docCounts[k]; countEl.classList.toggle("visible", docCounts[k] > 0); }
    if (iconEl)  { iconEl.classList.toggle("active", docCounts[k] > 0); }
  });
  const list  = document.getElementById("doc-file-list");
  const empty = document.getElementById("doc-empty");
  const hasFiles = list.querySelectorAll("[data-filename]").length > 0;
  if (empty) empty.style.display = hasFiles ? "none" : "";
}

function addDocFile(docType, filename) {
  const list = document.getElementById("doc-file-list");
  const empty = document.getElementById("doc-empty");
  if (empty) empty.style.display = "none";

  const iconMap = { word: "fa-file-word", excel: "fa-file-excel", powerpoint: "fa-file-powerpoint" };
  const icon = iconMap[docType] || "fa-file";
  const key  = docType === "powerpoint" ? "pptx" : docType;

  const wrap = document.createElement("div");
  wrap.className = "doc-file-row";
  wrap.dataset.filename = filename;
  wrap.dataset.doctype  = key;

  const link = document.createElement("a");
  link.className = `doc-file-item ${docType}-file`;
  link.href = `/download/${encodeURIComponent(filename)}`;
  link.download = filename;
  link.title = `Click to download ${filename}`;
  link.innerHTML = `
    <i class="fas ${icon} type-icon"></i>
    <span class="doc-file-name">${filename}</span>
    <i class="fas fa-download dl-btn"></i>
  `;

  const del = document.createElement("button");
  del.className = "file-del-btn";
  del.title = "Delete this file";
  del.innerHTML = '<i class="fas fa-times"></i>';
  del.addEventListener("click", async (e) => {
    e.preventDefault();
    del.disabled = true;
    try {
      await fetch(`/api/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
      wrap.remove();
      _recomputeCounts();
    } catch (err) {
      del.disabled = false;
      console.warn("delete failed:", err);
    }
  });

  wrap.appendChild(link);
  wrap.appendChild(del);
  list.appendChild(wrap);
  list.scrollTop = list.scrollHeight;
}

let _clearPending = false;
let _clearTimer   = null;
async function clearAllFiles() {
  const btn = document.querySelector(".file-clear-btn");
  if (!_clearPending) {
    // First click: show confirmation state on the button itself
    _clearPending = true;
    if (btn) { btn.title = "Click again to confirm"; btn.style.opacity = "1"; btn.innerHTML = '<i class="fas fa-trash"></i> Confirm?'; }
    _clearTimer = setTimeout(() => {
      _clearPending = false;
      if (btn) { btn.title = "Clear all output files"; btn.style.opacity = ""; btn.innerHTML = '<i class="fas fa-trash"></i>'; }
    }, 3000);
    return;
  }
  // Second click within 3 s: actually delete
  clearTimeout(_clearTimer);
  _clearPending = false;
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
  try {
    await fetch("/api/files", { method: "DELETE" });
    const list  = document.getElementById("doc-file-list");
    const empty = document.getElementById("doc-empty");
    list.querySelectorAll(".doc-file-row").forEach(r => r.remove());
    _recomputeCounts();
  } catch (err) {
    console.warn("clearAllFiles failed:", err);
  } finally {
    if (btn) { btn.disabled = false; btn.title = "Clear all output files"; btn.innerHTML = '<i class="fas fa-trash"></i>'; }
  }
}

// ── PROJECT DOCUMENTS ──────────────────────────────────────────

function _projDocsSetStatus(msg, cls) {
  const el = document.getElementById("proj-docs-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "proj-docs-status" + (cls ? " " + cls : "");
}

function _addProjDocRow(filename, sizeKb) {
  const list  = document.getElementById("proj-docs-list");
  const empty = document.getElementById("proj-docs-empty");
  if (empty) empty.style.display = "none";

  const ext = filename.split(".").pop().toLowerCase();
  const iconMap = { pdf: "fa-file-pdf", docx: "fa-file-word", pptx: "fa-file-powerpoint", txt: "fa-file-lines" };
  const icon = iconMap[ext] || "fa-file";

  const row = document.createElement("div");
  row.className = "proj-doc-row";
  row.dataset.filename = filename;
  row.innerHTML = `
    <i class="fas ${icon}" style="font-size:11px;color:var(--text-dim);flex-shrink:0"></i>
    <span class="proj-doc-name" title="${filename}">${filename}</span>
    <span class="proj-doc-size">${sizeKb ? sizeKb + " KB" : ""}</span>
    <button class="proj-doc-del" title="Delete" onclick="deleteProjDoc('${filename.replace(/'/g,"\\'")}', this.closest('.proj-doc-row'))">
      <i class="fas fa-times"></i>
    </button>
  `;
  list.appendChild(row);
}

async function loadProjectDocs() {
  try {
    const resp = await fetch("/api/project-docs");
    if (!resp.ok) return;
    const docs = await resp.json();

    const list  = document.getElementById("proj-docs-list");
    const empty = document.getElementById("proj-docs-empty");
    list.querySelectorAll(".proj-doc-row").forEach(r => r.remove());

    if (docs.length === 0) {
      if (empty) empty.style.display = "";
    } else {
      if (empty) empty.style.display = "none";
      docs.forEach(d => _addProjDocRow(d.filename, d.size_kb));
    }
  } catch (err) {
    console.warn("loadProjectDocs failed:", err);
  }
}

async function _uploadFile(file) {
  const list  = document.getElementById("proj-docs-list");
  const empty = document.getElementById("proj-docs-empty");
  if (empty) empty.style.display = "none";

  // Placeholder row while uploading
  const placeholder = document.createElement("div");
  placeholder.className = "proj-doc-row";
  placeholder.innerHTML = `
    <i class="fas fa-spinner fa-spin" style="font-size:11px;color:var(--accent);flex-shrink:0"></i>
    <span class="proj-doc-name">${file.name}</span>
    <span class="proj-doc-uploading">uploading…</span>
  `;
  list.prepend(placeholder);

  try {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/api/project-docs", { method: "POST", body: form });
    placeholder.remove();

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      _projDocsSetStatus("Upload failed: " + (err.detail || resp.statusText), "err");
      _checkEmptyProjDocs();
      return;
    }
    const result = await resp.json();
    _addProjDocRow(result.filename, null);
    const indexing = result.datastore_status === "creating"
      ? "Datastore initializing (~15 min first time)"
      : "Indexing… available for search in ~2 min";
    _projDocsSetStatus(indexing, "");
    setTimeout(() => _projDocsSetStatus(""), 8000);
  } catch (err) {
    placeholder.remove();
    _projDocsSetStatus("Upload error: " + err.message, "err");
    _checkEmptyProjDocs();
  }
}

function _checkEmptyProjDocs() {
  const list  = document.getElementById("proj-docs-list");
  const empty = document.getElementById("proj-docs-empty");
  if (!list || !empty) return;
  const hasRows = list.querySelectorAll(".proj-doc-row").length > 0;
  empty.style.display = hasRows ? "none" : "";
}

async function deleteProjDoc(filename, rowEl) {
  if (rowEl) rowEl.style.opacity = "0.4";
  try {
    await fetch(`/api/project-docs/${encodeURIComponent(filename)}`, { method: "DELETE" });
    if (rowEl) rowEl.remove();
    _checkEmptyProjDocs();
  } catch (err) {
    if (rowEl) rowEl.style.opacity = "1";
    _projDocsSetStatus("Delete failed", "err");
  }
}

function handleDocSelect(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = "";
  files.forEach(f => _uploadFile(f));
}

function handleDocDrop(event) {
  event.preventDefault();
  document.getElementById("proj-docs-drop").classList.remove("drag-over");
  const files = Array.from(event.dataTransfer.files || []);
  files.forEach(f => _uploadFile(f));
}

async function loadAllFiles() {
  try {
    const resp = await fetch("/api/files");
    if (!resp.ok) return;
    const files = await resp.json();

    // Only update if server returned files — never wipe the list on empty response
    // (empty can happen on reconnect to a different instance)
    if (files.length === 0) return;

    const list = document.getElementById("doc-file-list");

    // Find filenames already shown so we don't add duplicates
    const shown = new Set(
      [...list.querySelectorAll(".doc-file-row")].map(el => el.dataset.filename)
    );

    // Clear and repopulate from server list (authoritative when non-empty)
    list.querySelectorAll(".doc-file-row").forEach(r => r.remove());

    files.forEach(f => addDocFile(f.doc_type, f.filename));
    _recomputeCounts();
  } catch (err) {
    console.warn("loadAllFiles failed:", err);
  }
}

// ── CHAT MESSAGES ──────────────────────────────────────────────
function appendUserMessage(text) {
  const messages = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "chat-msg user";
  div.innerHTML = `
    <div class="msg-avatar"><i class="fas fa-user"></i></div>
    <div class="msg-bubble">${escapeHtml(text)}</div>
  `;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function appendAgentMessage(text) {
  const messages = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "chat-msg agent";
  div.innerHTML = `
    <div class="msg-avatar"><i class="fas fa-robot"></i></div>
    <div class="msg-bubble">${formatMarkdown(text)}</div>
  `;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function addTypingIndicator() {
  const messages = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "chat-msg agent typing-indicator";
  div.id = "typing-indicator";
  div.innerHTML = `
    <div class="msg-avatar"><i class="fas fa-robot"></i></div>
    <div class="msg-bubble">
      <div class="typing-dots">
        <span></span><span></span><span></span>
      </div>
    </div>
  `;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function removeTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

// ── HELPERS ────────────────────────────────────────────────────
function setProcessing(val) {
  isProcessing = val;
  const btn   = document.getElementById("send-btn");
  const input = document.getElementById("chat-input");
  btn.disabled   = val;
  input.disabled = val;
}

function getToolMeta(tool) {
  const map = {
    python:     { icon: "fa-code",              faIcon: "fa-code",              color: "#f59e0b", label: "Python Executor"  },
    search:     { icon: "fa-magnifying-glass",  faIcon: "fa-magnifying-glass",  color: "#4f78f1", label: "Knowledge Search" },
    word:       { icon: "fa-file-word",         faIcon: "fa-file-word",         color: "#5b9bd5", label: "Word Document"    },
    excel:      { icon: "fa-file-excel",        faIcon: "fa-file-excel",        color: "#70c172", label: "Excel Spreadsheet"},
    powerpoint: { icon: "fa-file-powerpoint",   faIcon: "fa-file-powerpoint",   color: "#e07060", label: "PowerPoint"       },
  };
  return map[tool] || { icon: "fa-gear", faIcon: "fa-gear", color: "#4e6080", label: tool };
}

function getDocFaIcon(docType) {
  return { word:"fa-file-word", excel:"fa-file-excel", powerpoint:"fa-file-powerpoint" }[docType] || "fa-file";
}

function getDocColor(docType) {
  return { word:"#5b9bd5", excel:"#70c172", powerpoint:"#e07060" }[docType] || "#fff";
}

// ── GMAIL CONNECT ──────────────────────────────────────────────
async function checkGmailStatus() {
  try {
    const resp = await fetch("/api/gmail/status");
    if (!resp.ok) return;
    const data = await resp.json();
    const btn    = document.getElementById("gmail-btn");
    const label  = document.getElementById("gmail-btn-text");
    const status = document.getElementById("gmail-status-text");
    if (data.connected) {
      btn.classList.add("connected");
      btn.onclick = null;           // disable click when already connected
      label.textContent  = "Gmail Connected";
      status.textContent = "✓ Sending from your account";
    } else {
      btn.classList.remove("connected");
      btn.onclick = connectGmail;
      label.textContent  = "Connect Gmail";
      status.textContent = "Not connected";
    }
  } catch (e) {
    console.warn("Gmail status check failed:", e);
  }
}

function connectGmail() {
  window.location.href = "/oauth/gmail/start";
}

function escapeHtml(str) {
  return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function formatMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/`(.*?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}
