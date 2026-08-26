"use strict";

const demoCredentials = {
  northstar: { initial: "N" },
  lumenworks: { initial: "L" },
  beacon: { initial: "B" },
  axis: { initial: "A" },
};

const quickPrompts = {
  "ACCT-001": [
    "Can I cancel ORD-1001 without a fee? Explain the agreement override.",
    "Check TKT-501 severity and SLA, then prepare an escalation.",
    "Why does TKT-504 still show BOOKED after driver pickup?",
  ],
  "ACCT-002": [
    "Is ORD-2002 eligible for a service credit? Show the calculation.",
    "Why does the 4,200-row CSV in TKT-502 fail if 5,000 rows are supported?",
    "Can I cancel ORD-2001 and what fee applies?",
  ],
  "ACCT-003": [
    "Can I cancel ORD-3001 without a fee?",
    "What is the status of TKT-503?",
    "Is bulk upload included on my plan?",
  ],
  "ACCT-004": [
    "What is the current status of ORD-4001?",
    "Check TKT-505 severity and prepare an escalation.",
    "What support plan applies to my account?",
  ],
};

const state = {
  session: null,
  csrfToken: null,
  conversationId: null,
  pendingAction: null,
  busy: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || "Request failed");
  return payload;
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

function toast(message) {
  $("toast").textContent = message;
  show("toast");
  window.setTimeout(() => hide("toast"), 3500);
}

function setBusy(busy) {
  state.busy = busy;
  $("send-button").disabled = busy;
  $("message-input").disabled = busy;
  busy ? show("typing-indicator") : hide("typing-indicator");
}

function renderDemoAccounts(accounts) {
  const target = $("demo-accounts");
  target.replaceChildren();
  accounts.forEach((account) => {
    const credential = demoCredentials[account.username];
    const button = document.createElement("button");
    button.className = "demo-account";
    button.type = "button";
    button.innerHTML = `<span class="demo-avatar">${credential?.initial || "C"}</span><span><strong>${escapeHtml(account.account_name)}</strong><small>${escapeHtml(account.username)} · select customer ID</small></span><span>›</span>`;
    button.addEventListener("click", () => {
      $("username").value = account.username;
      $("password").value = "";
      $("password").focus();
      hide("login-error");
    });
    target.appendChild(button);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
}

function openLogin(session) {
  hide("loading-screen");
  hide("app-screen");
  show("login-screen");
  renderDemoAccounts(session.demo_accounts || []);
}

function capability(label, ready) {
  const span = document.createElement("span");
  span.className = `capability-badge ${ready ? "ready" : ""}`;
  span.textContent = label;
  return span;
}

function openWorkspace(session) {
  state.session = session;
  state.csrfToken = session.csrf_token;
  hide("loading-screen");
  hide("login-screen");
  show("app-screen");
  const account = session.account;
  $("header-account").textContent = `${account.account_name} · private workspace`;
  $("account-name").textContent = account.account_name;
  $("account-id").textContent = account.account_id;
  $("account-plan").textContent = account.plan;
  $("account-csm").textContent = account.csm;
  $("account-agreement").textContent = account.contract_file ? "Active" : "Standard policy";
  $("account-avatar").textContent = account.account_name.slice(0, 1);

  const badges = $("capability-badges");
  badges.replaceChildren(
    capability(session.capabilities?.llm === "groq" ? "Groq ready" : "Safe wording", session.capabilities?.llm === "groq"),
    capability(session.capabilities?.retrieval === "bge+chroma" ? "Semantic RAG" : "Source fallback", session.capabilities?.retrieval === "bge+chroma"),
    capability("Confirmation gate", true),
  );

  const prompts = $("quick-prompts");
  prompts.replaceChildren();
  (quickPrompts[account.account_id] || []).forEach((prompt) => {
    const button = document.createElement("button");
    button.className = "quick-prompt";
    button.type = "button";
    button.textContent = prompt;
    button.addEventListener("click", () => sendMessage(prompt));
    prompts.appendChild(button);
  });
  $("message-list").replaceChildren();
  appendMessage("assistant", `Hi! I’m your ParcelPilot support assistant for ${account.account_name}. I can use only this account’s orders, tickets, agreement, and the supplied current ParcelPilot sources.`, null);
}

function appendMessage(role, text, result) {
  const row = document.createElement("article");
  row.className = `message-row ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? (state.session?.account?.account_name || "C").slice(0, 1) : "P";
  const content = document.createElement("div");
  content.className = "message-content";
  const name = document.createElement("p");
  name.className = "message-name";
  name.textContent = role === "user" ? "You" : "ParcelPilot Support";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  content.append(name, bubble);
  if (result) {
    const meta = document.createElement("div");
    meta.className = "answer-meta";
    [`${result.mode === "llm" ? "AI grounded" : "Safe fallback"}`, `${result.confidence} confidence`, result.needs_human ? "Human review" : "Evidence checked"].forEach((label, index) => {
      const chip = document.createElement("span");
      chip.className = `answer-chip ${index === 1 ? result.confidence : ""}`;
      chip.textContent = label;
      meta.appendChild(chip);
    });
    content.appendChild(meta);
    if (result.pending_action) {
      const pending = document.createElement("div");
      pending.className = "pending-card";
      pending.innerHTML = `<div><strong>Action prepared—not executed</strong><p>${escapeHtml(result.pending_action.summary)}</p></div>`;
      const review = document.createElement("button");
      review.type = "button";
      review.textContent = "Review & confirm";
      review.addEventListener("click", () => openActionModal(result.pending_action));
      pending.appendChild(review);
      content.appendChild(pending);
    }
  }
  if (role === "user") row.append(content, avatar); else row.append(avatar, content);
  $("message-list").appendChild(row);
  $("message-list").scrollTop = $("message-list").scrollHeight;
}

function renderEvidence(result) {
  hide("empty-evidence");
  show("evidence-content");
  $("trace-id").textContent = result.trace_id.slice(0, 12);
  $("trace-id").title = result.trace_id;
  const tools = $("tool-list");
  tools.replaceChildren();
  result.tool_events.forEach((event) => {
    const item = document.createElement("li");
    item.className = "tool-item";
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = event.tool.replaceAll("_", " ");
    const summary = document.createElement("p");
    summary.textContent = event.summary;
    const timing = document.createElement("small");
    timing.textContent = `${event.status} · ${event.duration_ms} ms`;
    body.append(title, summary, timing);
    body.className = "tool-item-body";
    item.appendChild(body);
    tools.appendChild(item);
  });
  const sources = $("source-list");
  sources.replaceChildren();

  const citedIds = new Set(
    (result.answer.match(/\[D\d+\]/g) || [])
      .map((value) => value.slice(1, -1))
  );

  const citedSources = result.citations.filter(
    (citation) => citedIds.has(citation.citation_id)
  );

  if (!citedSources.length) {
    const empty = document.createElement("p");
    empty.className = "demo-note";
    empty.textContent = "No document passage was needed for this answer.";
    sources.appendChild(empty);
  }
  citedSources.forEach((citation) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.innerHTML = `<div class="source-top"><span class="source-id">${escapeHtml(citation.citation_id)}</span><span class="source-authority">${escapeHtml(citation.authority.replaceAll("_", " "))}</span></div><h4>${escapeHtml(citation.title)}</h4><p>${escapeHtml(citation.section)} · page ${citation.page}</p><blockquote>${escapeHtml(citation.excerpt)}</blockquote>`;
    sources.appendChild(card);
  });
}

async function sendMessage(text) {
  const message = String(text ?? $("message-input").value).trim();
  if (!message || state.busy) return;
  $("message-input").value = "";
  $("message-input").style.height = "auto";
  appendMessage("user", message, null);
  setBusy(true);
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: state.conversationId }),
    });
    state.conversationId = result.conversation_id;
    appendMessage("assistant", result.answer, result);
    renderEvidence(result);
  } catch (error) {
    appendMessage("assistant", `I couldn’t safely complete that request: ${error.message}`, null);
    toast(error.message);
  } finally {
    setBusy(false);
    $("message-input").focus();
  }
}

function openActionModal(action) {
  state.pendingAction = action;
  $("modal-summary").textContent = action.summary;
  $("modal-action-id").textContent = action.action_id;
  show("action-modal");
  $("confirm-action").focus();
}

function closeActionModal() {
  hide("action-modal");
  state.pendingAction = null;
}

async function resolveAction(kind) {
  if (!state.pendingAction) return;
  const action = state.pendingAction;
  $("confirm-action").disabled = true;
  $("cancel-action").disabled = true;
  try {
    const result = await api(`/api/actions/${encodeURIComponent(action.action_id)}/${kind}`, {
      method: "POST",
      body: "{}",
      headers: { "X-CSRF-Token": state.csrfToken },
    });
    closeActionModal();
    appendMessage("assistant", result.message, null);
    toast(result.message);
  } catch (error) {
    toast(error.message);
  } finally {
    $("confirm-action").disabled = false;
    $("cancel-action").disabled = false;
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  hide("login-error");
  $("login-button").disabled = true;
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: $("username").value, password: $("password").value }),
    });
    const session = await api("/api/session");
    openWorkspace(session);
  } catch (error) {
    $("login-error").textContent = error.message;
    show("login-error");
  } finally {
    $("login-button").disabled = false;
  }
});

$("logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => null);
  state.session = null;
  state.csrfToken = null;
  state.conversationId = null;
  openLogin(await api("/api/session"));
});

$("chat-form").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
$("message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
$("message-input").addEventListener("input", (event) => {
  event.target.style.height = "auto";
  event.target.style.height = `${Math.min(event.target.scrollHeight, 140)}px`;
});
$("confirm-action").addEventListener("click", () => resolveAction("confirm"));
$("cancel-action").addEventListener("click", () => resolveAction("cancel"));
$("action-modal").addEventListener("click", (event) => { if (event.target === $("action-modal")) closeActionModal(); });

(async function initialize() {
  try {
    const session = await api("/api/session");
    session.authenticated ? openWorkspace(session) : openLogin(session);
  } catch (error) {
    hide("loading-screen");
    openLogin({ demo_accounts: [] });
    $("login-error").textContent = `Backend unavailable: ${error.message}`;
    show("login-error");
  }
})();
