const params = new URLSearchParams(window.location.search);

const state = {
  token: localStorage.getItem("opengym_token") || "",
  role: localStorage.getItem("opengym_role") || "",
  me: null,
  members: [],
  windows: [],
  alerts: [],
  actions: [],
  users: [],
  memberDetail: null,
  lockStatus: null,
  lockLog: [],
  emailSettings: null,
  telegramSettings: null,
  checkInSettings: null,
  view: params.get("view") || "overview",
  selectedMemberId: params.get("member") || "",
  message: "",
  messageType: "",
  publicCheckInSession: null,
  publicCheckInAttempted: false,
  publicCheckInStep: 1,
  publicCheckInDraft: {
    rulesAccepted: false,
    checklist: {},
  },
};

const app = document.getElementById("app");
document.title = "Twenty4Seven-Gym";

const actionCopy = {
  resend: { success: "Code erneut versendet.", confirm: "" },
  deactivate: {
    success: "Access Window wurde vorzeitig deaktiviert.",
    confirm: "Dieses Access Window wirklich sofort deaktivieren?",
  },
  "emergency-code": {
    success: "Notfallcode wurde erzeugt.",
    confirm: "Jetzt einen einmaligen Notfallcode für dieses Zeitfenster erzeugen?",
  },
};

function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return fetch(path, { ...options, headers }).then(async (res) => {
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) throw new Error(data.detail || text || "Request failed");
    return data;
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function setMessage(text, type = "") {
  state.message = text;
  state.messageType = type;
  render();
}

function setPendingState(target, busy, busyLabel = "Wird verarbeitet…") {
  const buttons = target.matches?.("form")
    ? [...target.querySelectorAll('button[type="submit"], button:not([type])')]
    : [target];
  buttons.forEach((button) => {
    if (!button) return;
    if (!button.dataset.originalLabel) {
      button.dataset.originalLabel = button.textContent || "";
    }
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
    button.textContent = busy ? busyLabel : button.dataset.originalLabel;
  });
}

async function withPending(target, task, busyLabel) {
  setPendingState(target, true, busyLabel);
  try {
    return await task();
  } finally {
    setPendingState(target, false, busyLabel);
  }
}

function syncUrlState() {
  const next = new URLSearchParams();
  next.set("view", state.view);
  if (state.selectedMemberId) next.set("member", state.selectedMemberId);
  const query = next.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState({}, "", nextUrl);
}

function fmtDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("de-DE", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function pill(value) {
  const tone = /active|emailed|ready|admin|booked|success/i.test(value)
    ? "good"
    : /warn|scheduled|operator|pending|flagged|credentials/i.test(value)
      ? "warn"
      : /error|bad|canceled|expired|replaced|failed/i.test(value)
        ? "bad"
        : "info";
  return `<span class="pill ${tone}">${escapeHtml(value)}</span>`;
}

function icon(symbol) {
  return `<span class="icon-slot" aria-hidden="true">${symbol}</span>`;
}

function currentMember() {
  return state.memberDetail?.member || null;
}

function upcomingWindow() {
  return [...state.windows]
    .filter((item) => item.status === "scheduled" || item.status === "active")
    .sort((a, b) => new Date(a.dispatch_at) - new Date(b.dispatch_at))[0];
}

function urgentAlerts() {
  return state.alerts.filter((item) => item.severity === "error" || item.severity === "warning");
}

async function login(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const data = await api("./auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: form.get("email"),
        password: form.get("password"),
      }),
    });
    state.token = data.access_token;
    state.role = data.role;
    localStorage.setItem("opengym_token", data.access_token);
    localStorage.setItem("opengym_role", data.role);
    await loadAppData();
  }, "Anmeldung läuft…");
}

async function forgotPassword(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email") }),
    });
    setMessage("Reset-Link wurde angefordert. Prüfe dein Postfach.", "good");
  }, "Link wird gesendet…");
}

async function resetPassword(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const token = new URLSearchParams(window.location.search).get("token") || "";
    await api("./auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password: form.get("password") }),
    });
    setMessage("Passwort aktualisiert. Du kannst dich jetzt anmelden.", "good");
    window.history.replaceState({}, "", "./app");
    render();
  }, "Passwort wird gesetzt…");
}

async function loadAppData() {
  state.me = await api("./me");
  const requests = [
    api("./admin/members?limit=12"),
    api("./admin/access-windows?limit=12"),
    api("./admin/alerts?limit=12"),
    api("./admin/admin-actions?limit=12"),
    api("./admin/lock/status"),
    api("./admin/lock/log?limit=12"),
  ];
  if (state.role === "admin") {
    requests.push(
      api("./admin/users?limit=20"),
      api("./admin/system/email-settings"),
      api("./admin/system/telegram-settings"),
      api("./admin/system/check-in-settings"),
    );
  }
  const results = await Promise.all(requests);
  [state.members, state.windows, state.alerts, state.actions, state.lockStatus, state.lockLog] = results;
  if (state.role === "admin") {
    [state.users, state.emailSettings, state.telegramSettings, state.checkInSettings] = results.slice(6);
  }
  if (state.selectedMemberId) {
    await loadMemberDetail(state.selectedMemberId, false);
  } else {
    render();
  }
}

async function loadMemberDetail(memberId, rerender = true) {
  state.selectedMemberId = String(memberId);
  state.memberDetail = await api(`./admin/members/${memberId}`);
  syncUrlState();
  if (rerender) render();
}

function setView(view) {
  state.view = view;
  syncUrlState();
  render();
}

async function runWindowAction(windowId, action) {
  const config = actionCopy[action] || { success: `${action} abgeschlossen.`, confirm: "" };
  if (config.confirm && !window.confirm(config.confirm)) {
    return;
  }
  await api(`./admin/access-windows/${windowId}/${action}`, { method: "POST" });
  setMessage(config.success, "good");
  await loadAppData();
}

async function remoteOpen(trigger) {
  if (!window.confirm("Remote Open jetzt auslösen bzw. protokollieren?")) {
    return;
  }
  await withPending(trigger, async () => {
    const result = await api("./admin/remote-open", { method: "POST" });
    setMessage(
      result.dry_run ? "Remote Open im Dry-Run protokolliert." : "Türöffnung ausgelöst.",
      "good",
    );
    await loadAppData();
  }, "Remote Open läuft…");
}

async function runFullSync() {
  await api("./admin/sync", { method: "POST" });
  setMessage("Magicline-Sync ausgeführt.", "good");
  await loadAppData();
}

async function runProvisioning() {
  await api("./admin/provision", { method: "POST" });
  setMessage("Provisioning geprüft.", "good");
  await loadAppData();
}

async function memberSearch(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") || "").trim();
    state.members = await api(`./admin/members?email=${encodeURIComponent(email)}&limit=12`);
    state.memberDetail = null;
    state.selectedMemberId = "";
    syncUrlState();
    render();
  }, "Suche läuft…");
}

async function syncMember(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") || "").trim();
    await api(`./admin/sync/member?email=${encodeURIComponent(email)}`, { method: "POST" });
    setMessage("Mitgliedssync abgeschlossen.", "good");
    await loadAppData();
  }, "Sync läuft…");
}

async function createUser(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/users", {
      method: "POST",
      body: JSON.stringify({
        email: form.get("email"),
        password: form.get("password"),
        role: form.get("role"),
        is_active: true,
      }),
    });
    setMessage("Benutzer angelegt.", "good");
    await loadAppData();
  }, "Benutzer wird angelegt…");
}

async function updateSmtp(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/email-settings", {
      method: "PUT",
      body: JSON.stringify({
        smtp_host: form.get("smtp_host"),
        smtp_port: Number(form.get("smtp_port")),
        smtp_username: form.get("smtp_username"),
        smtp_password: form.get("smtp_password"),
        smtp_use_tls: true,
        smtp_from_email: form.get("smtp_from_email"),
      }),
    });
    setMessage("SMTP-Einstellungen gespeichert.", "good");
    await loadAppData();
  }, "SMTP wird gespeichert…");
}

async function testEmail(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/email-test", {
      method: "POST",
      body: JSON.stringify({ to_email: form.get("to_email") }),
    });
    setMessage("Test-E-Mail versendet.", "good");
  }, "Testmail wird gesendet…");
}

async function updateTelegram(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/telegram-settings", {
      method: "PUT",
      body: JSON.stringify({
        telegram_bot_token: form.get("telegram_bot_token"),
        telegram_chat_id: form.get("telegram_chat_id"),
      }),
    });
    setMessage("Telegram-Einstellungen gespeichert.", "good");
    await loadAppData();
  }, "Telegram wird gespeichert…");
}

async function testTelegram(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/telegram-test", {
      method: "POST",
      body: JSON.stringify({ message: form.get("message") }),
    });
    setMessage("Telegram-Testalarm gesendet.", "good");
  }, "Telegram-Test läuft…");
}

async function updateCheckInSettings(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/check-in-settings", {
      method: "PUT",
      body: JSON.stringify({
        enabled: form.get("enabled") === "on",
        title: form.get("title"),
        intro: form.get("intro"),
        rules_heading: form.get("rules_heading"),
        rules_body: form.get("rules_body"),
        checklist_heading: form.get("checklist_heading"),
        checklist_items: checklistItemsFromText(form.get("checklist_items")),
        success_message: form.get("success_message"),
      }),
    });
    setMessage("Check-in-Konfiguration gespeichert.", "good");
    await loadAppData();
  }, "Check-in wird gespeichert…");
}

async function loadPublicCheckInSession() {
  const token = new URLSearchParams(window.location.search).get("token");
  if (!token) {
    render();
    return;
  }
  state.publicCheckInAttempted = true;
  const session = await api(`./public/check-in/session?token=${encodeURIComponent(token)}`);
  state.publicCheckInSession = session;
  state.publicCheckInStep = session.window.is_confirmed ? 4 : 1;
  state.publicCheckInDraft = {
    rulesAccepted: session.window.is_confirmed,
    checklist: Object.fromEntries(
      (session.settings?.checklist_items || []).map((item) => [item.id, session.window.is_confirmed]),
    ),
  };
  render();
}

async function resolvePublicCheckIn(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const session = await api("./public/check-in/resolve", {
      method: "POST",
      body: JSON.stringify({
        email: form.get("email"),
        code: form.get("code"),
      }),
    });
    state.publicCheckInSession = session;
    state.publicCheckInAttempted = true;
    state.publicCheckInStep = session.window.is_confirmed ? 4 : 1;
    state.publicCheckInDraft = {
      rulesAccepted: session.window.is_confirmed,
      checklist: Object.fromEntries(
        (session.settings?.checklist_items || []).map((item) => [item.id, session.window.is_confirmed]),
      ),
    };
    window.history.replaceState({}, "", `./check-in?token=${encodeURIComponent(session.token)}`);
    setMessage("", "");
    render();
  }, "Check-in wird geladen…");
}

async function submitPublicCheckIn(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const checklist = (state.publicCheckInSession?.settings?.checklist_items || []).map((item) => ({
      id: item.id,
      checked: Boolean(state.publicCheckInDraft.checklist[item.id]),
    }));
    const result = await api("./public/check-in/submit", {
      method: "POST",
      body: JSON.stringify({
        token: state.publicCheckInSession.token,
        rules_accepted: state.publicCheckInDraft.rulesAccepted,
        checklist,
        entry_source: state.publicCheckInSession.entry_source,
      }),
    });
    state.publicCheckInSession.window.confirmed_at = result.check_in.confirmed_at;
    state.publicCheckInSession.window.source = result.check_in.source;
    state.publicCheckInSession.window.is_confirmed = true;
    state.publicCheckInStep = 4;
    setMessage(result.success_message, "good");
    render();
  }, "Bestätigung wird gesendet…");
}

function goToPublicCheckInStep(step) {
  state.publicCheckInStep = step;
  render();
}

function updatePublicRulesAccepted(checked) {
  state.publicCheckInDraft.rulesAccepted = checked;
  render();
}

function updatePublicChecklist(id, checked) {
  state.publicCheckInDraft.checklist[id] = checked;
  render();
}

function publicChecklistComplete() {
  const items = state.publicCheckInSession?.settings?.checklist_items || [];
  return items.every((item) => Boolean(state.publicCheckInDraft.checklist[item.id]));
}

function publicCheckInSteps() {
  return [
    { id: 1, label: "Start" },
    { id: 2, label: "Hausregeln" },
    { id: 3, label: "Checkliste" },
    { id: 4, label: "Abschluss" },
  ];
}

function logout() {
  localStorage.removeItem("opengym_token");
  localStorage.removeItem("opengym_role");
  state.token = "";
  state.role = "";
  state.me = null;
  state.memberDetail = null;
  state.selectedMemberId = "";
  state.emailSettings = null;
  state.telegramSettings = null;
  state.checkInSettings = null;
  state.view = "overview";
  syncUrlState();
  render();
}

function checklistItemsFromText(raw) {
  return String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((label, index) => ({
      id: label
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        || `item_${index + 1}`,
      label,
    }));
}

function attachCommonHandlers() {
  document.getElementById("logout-button")?.addEventListener("click", logout);
  document.getElementById("sync-button")?.addEventListener("click", () => runFullSync().catch(handleError));
  document.getElementById("provision-button")?.addEventListener("click", () => runProvisioning().catch(handleError));
  document.querySelectorAll("[data-remote-open]").forEach((button) => {
    button.addEventListener("click", (event) => remoteOpen(event.currentTarget).catch(handleError));
  });
  document.querySelectorAll("[data-view]").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (
        event.defaultPrevented
        || event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
      ) {
        return;
      }
      event.preventDefault();
      setView(link.dataset.view);
    });
  });
}

function attachOverviewHandlers() {
  document.querySelectorAll("[data-member-id]").forEach((button) => {
    button.addEventListener("click", () => loadMemberDetail(button.dataset.memberId).catch(handleError));
  });
}

function attachMembersHandlers() {
  document.getElementById("member-search-form")?.addEventListener("submit", (event) => memberSearch(event).catch(handleError));
  document.getElementById("member-sync-form")?.addEventListener("submit", (event) => syncMember(event).catch(handleError));
  document.querySelectorAll("[data-member-id]").forEach((button) => {
    button.addEventListener("click", () => loadMemberDetail(button.dataset.memberId).catch(handleError));
  });
  document.querySelectorAll("[data-window-action]").forEach((button) => {
    button.addEventListener("click", () => runWindowAction(button.dataset.windowId, button.dataset.windowAction).catch(handleError));
  });
}

function attachSettingsHandlers() {
  document.getElementById("create-user-form")?.addEventListener("submit", (event) => createUser(event).catch(handleError));
  document.getElementById("smtp-form")?.addEventListener("submit", (event) => updateSmtp(event).catch(handleError));
  document.getElementById("smtp-test-form")?.addEventListener("submit", (event) => testEmail(event).catch(handleError));
  document.getElementById("telegram-form")?.addEventListener("submit", (event) => updateTelegram(event).catch(handleError));
  document.getElementById("telegram-test-form")?.addEventListener("submit", (event) => testTelegram(event).catch(handleError));
  document.getElementById("checkin-form")?.addEventListener("submit", (event) => updateCheckInSettings(event).catch(handleError));
}

function handleError(error) {
  setMessage(error.message || "Unbekannter Fehler.", "bad");
}

function navButton(view, label, symbol) {
  const active = state.view === view ? "active" : "";
  const href = `./app?view=${encodeURIComponent(view)}${state.selectedMemberId ? `&member=${encodeURIComponent(state.selectedMemberId)}` : ""}`;
  return `
    <a
      class="nav-button ${active}"
      data-view="${view}"
      href="${href}"
      aria-current="${state.view === view ? "page" : "false"}"
    >
      <span>${icon(symbol)} ${escapeHtml(label)}</span>
    </a>
  `;
}

function renderStatusStrip() {
  const next = upcomingWindow();
  return `
    <div class="live-strip">
      <div class="live-card">
        <div class="live-label">Schloss</div>
        <div class="live-value">${escapeHtml(state.lockStatus?.connectivity || "unknown")}</div>
        <div class="subtle">${pill(state.lockStatus?.source || "system")} ${pill(state.lockStatus?.lock_state || "unknown")}</div>
      </div>
      <div class="live-card">
        <div class="live-label">Magicline</div>
        <div class="live-value">${state.members.length}</div>
        <div class="subtle">Aktive Mitgliedersicht</div>
      </div>
      <div class="live-card">
        <div class="live-label">Alarmniveau</div>
        <div class="live-value">${urgentAlerts().length}</div>
        <div class="subtle">${urgentAlerts().length ? "Handlungsbedarf" : "Derzeit ruhig"}</div>
      </div>
      <div class="live-card">
        <div class="live-label">Nächster Slot</div>
        <div class="live-value numberish">${next ? fmtDate(next.dispatch_at) : "—"}</div>
        <div class="subtle">${next ? `Mitglied ${next.member_id}` : "Kein offenes Fenster"}</div>
      </div>
    </div>
  `;
}

function renderOverview() {
  const next = upcomingWindow();
  return `
    <div class="dashboard-grid">
      <section class="metric-card span-3">
        <span class="eyebrow">Offene Fenster</span>
        <strong>${state.windows.length}</strong>
        <span class="subtle">Geplante oder aktive Zutrittskorridore</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">Warnungen</span>
        <strong>${urgentAlerts().length}</strong>
        <span class="subtle">Fehler, Warnungen, manuelle Eingriffe</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">Schlossstatus</span>
        <strong>${escapeHtml(state.lockStatus?.connectivity || "unknown")}</strong>
        <span class="subtle">Dry-Run bis Nuki-Credentials vorliegen</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">Nächste Freischaltung</span>
        <strong>${next ? fmtDate(next.dispatch_at) : "—"}</strong>
        <span class="subtle">${next ? `Window ${next.id}` : "Aktuell kein Termin offen"}</span>
      </section>

      <section class="panel span-8">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Jetzt Wichtig</h2>
            <p class="panel-kicker">Betriebsrelevante Elemente zuerst, damit Empfang und Betreiber schnell reagieren können.</p>
          </div>
        </div>
        <div class="stack">
          ${urgentAlerts().length ? urgentAlerts().map((alert) => `
            <div class="list-item">
              <div class="split">${pill(alert.severity)}<span class="subtle numberish">${fmtDate(alert.created_at)}</span></div>
              <h3>${escapeHtml(alert.kind)}</h3>
              <p class="subtle">${escapeHtml(alert.message)}</p>
            </div>
          `).join("") : `
            <div class="empty">Keine offenen Warn- oder Fehlerfälle. Das System ist betriebsbereit.</div>
          `}
        </div>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Letzte Schlossereignisse</h2>
            <p class="panel-kicker">Dry-Run und manuelle Eingriffe in einer kompakten Timeline.</p>
          </div>
        </div>
        <div class="stack">
          ${state.lockLog.length ? state.lockLog.slice(0, 5).map((entry) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(entry.action)}</strong><span class="subtle numberish">${fmtDate(entry.created_at)}</span></div>
              <p class="subtle">${escapeHtml(entry.actor_email)}</p>
            </div>
          `).join("") : '<div class="empty">Noch keine Schlossereignisse vorhanden.</div>'}
        </div>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Nächste Access Windows</h2>
            <p class="panel-kicker">Die nächsten Freigaben und Endzeiten im Überblick.</p>
          </div>
        </div>
        <div class="stack">
          ${state.windows.slice(0, 6).map((window) => `
            <div class="list-item">
              <div class="split">${pill(window.status)}<span class="subtle numberish">${fmtDate(window.dispatch_at)}</span></div>
              <h3>Mitglied ${escapeHtml(window.member_id)}</h3>
              <p class="subtle">Von ${fmtDate(window.starts_at)} bis ${fmtDate(window.ends_at)}</p>
            </div>
          `).join("") || '<div class="empty">Keine Access Windows vorhanden.</div>'}
        </div>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schneller Mitgliederzugriff</h2>
            <p class="panel-kicker">Direkter Sprung in Diagnose und manuelle Aktionen.</p>
          </div>
        </div>
        <div class="entity-list">
          ${state.members.map((member) => `
            <button type="button" class="list-item entity-button" data-member-id="${member.id}">
              <div class="split"><strong>${escapeHtml(`${member.first_name || ""} ${member.last_name || ""}`.trim() || member.email || `Mitglied ${member.id}`)}</strong>${pill(member.status || "unknown")}</div>
              <p class="subtle">${escapeHtml(member.email || "Keine E-Mail")} · Magicline ${escapeHtml(member.magicline_customer_id)}</p>
            </button>
          `).join("") || '<div class="empty">Keine Mitglieder geladen.</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderMemberCards() {
  return state.members.map((member) => {
    const active = currentMember()?.id === member.id ? "active" : "";
    const label = `${member.first_name || ""} ${member.last_name || ""}`.trim() || member.email || `Mitglied ${member.id}`;
    return `
      <button type="button" class="list-item entity-button ${active}" data-member-id="${member.id}">
        <div class="split"><strong>${escapeHtml(label)}</strong>${pill(member.status || "unknown")}</div>
        <p class="subtle">${escapeHtml(member.email || "Keine E-Mail")} · Magicline ${escapeHtml(member.magicline_customer_id)}</p>
      </button>
    `;
  }).join("");
}

function renderWindowActions(window) {
  return `
    <div class="action-group">
      <button type="button" class="secondary" data-window-id="${window.id}" data-window-action="resend">Code Neu Senden</button>
      <button type="button" class="warn" data-window-id="${window.id}" data-window-action="emergency-code">Notfallcode</button>
      <button type="button" class="bad" data-window-id="${window.id}" data-window-action="deactivate">Sofort Deaktivieren</button>
    </div>
  `;
}

function renderMembersView() {
  const detail = state.memberDetail;
  return `
    <div class="detail-layout">
      <div class="detail-column">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2 class="panel-title">Mitglieder</h2>
              <p class="panel-kicker">Suche, manueller Sync und Diagnostik pro Mitglied.</p>
            </div>
          </div>
          <form id="member-search-form" class="stack">
            <label for="member-search-email">Mitglied per E-Mail suchen
              <input id="member-search-email" name="email" type="email" autocomplete="off" spellcheck="false" inputmode="email" placeholder="dfrigewski@gmail.com…" />
            </label>
            <button type="submit">Mitglied Suchen</button>
          </form>
          <form id="member-sync-form" class="stack" style="margin-top: 16px;">
            <label for="member-sync-email">Gezielten Sync ausführen
              <input id="member-sync-email" name="email" type="email" autocomplete="off" spellcheck="false" inputmode="email" placeholder="Mitglied für Magicline-Sync…" required />
            </label>
            <button type="submit" class="secondary">Mitglied Syncen</button>
          </form>
          <div class="entity-list" style="margin-top: 18px;">
            ${renderMemberCards() || '<div class="empty">Keine Mitglieder vorhanden.</div>'}
          </div>
        </section>
      </div>

      <div class="detail-column">
        ${detail ? `
          <section class="detail-card">
            <p class="eyebrow">Mitgliederdetail</p>
            <h2 class="panel-title">${escapeHtml(`${detail.member.first_name || ""} ${detail.member.last_name || ""}`.trim() || detail.member.email || `Mitglied ${detail.member.id}`)}</h2>
            <div class="detail-meta">
              ${pill(detail.member.status || "unknown")}
              <span>Magicline ${escapeHtml(detail.member.magicline_customer_id)}</span>
              <span>${escapeHtml(detail.member.email || "Keine E-Mail")}</span>
              <span>Zuletzt synchronisiert ${fmtDate(detail.member.last_synced_at)}</span>
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Gebuchte Termine</h3>
                <p class="panel-kicker">Operative Sicht auf Freies Training und Statuswechsel.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.bookings.map((booking) => `
                <div class="list-item">
                  <div class="split">${pill(booking.booking_status)}<span class="subtle numberish">${fmtDate(booking.start_at)}</span></div>
                  <h4>${escapeHtml(booking.title)}</h4>
                  <p class="subtle">Von ${fmtDate(booking.start_at)} bis ${fmtDate(booking.end_at)}</p>
                </div>
              `).join("")}
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Zugangsfenster</h3>
                <p class="panel-kicker">Berechnete Korridore inklusive manueller Eingriffe.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.access_windows.map((window) => `
                <div class="list-item">
                  <div class="split">${pill(window.status)}<span class="subtle numberish">${fmtDate(window.dispatch_at)}</span></div>
                  <p class="subtle">Window ${window.id} · Bookings ${escapeHtml(window.booking_ids.join(", "))}</p>
                  <p class="subtle">Aktiv ab ${fmtDate(window.starts_at)} · Ende ${fmtDate(window.ends_at)}</p>
                  <p class="subtle">Check-in ${window.check_in_confirmed_at ? `bestätigt ${fmtDate(window.check_in_confirmed_at)}` : "offen"}</p>
                  ${window.check_in_checklist?.length ? `
                    <div class="stack compact-stack">
                      ${window.check_in_checklist.map((item) => `<p class="subtle">${item.checked ? "Ja" : "Nein"} · ${escapeHtml(item.label)}</p>`).join("")}
                    </div>
                  ` : ""}
                  ${renderWindowActions(window)}
                </div>
              `).join("") || '<div class="empty">Keine Access Windows vorhanden.</div>'}
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Code Historie</h3>
                <p class="panel-kicker">Letzte reguläre und Notfallcodes.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.access_codes.map((code) => `
                <div class="list-item">
                  <div class="split">${pill(code.status)}<span class="subtle numberish">${fmtDate(code.created_at)}</span></div>
                  <p class="code">••${escapeHtml(code.code_last4)} ${code.is_emergency ? "· emergency" : ""}</p>
                  <p class="subtle">E-Mail ${fmtDate(code.emailed_at)} · Gültig bis ${fmtDate(code.expires_at)}</p>
                </div>
              `).join("") || '<div class="empty">Noch keine Codes vorhanden.</div>'}
            </div>
          </section>
        ` : `
          <section class="detail-card">
            <h2 class="panel-title">Mitglied auswählen</h2>
            <p class="subtle">Wähle links ein Mitglied, um Buchungen, Access Windows und Codes zu prüfen.</p>
          </section>
        `}
      </div>
    </div>
  `;
}

function renderWindowsView() {
  return `
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2 class="panel-title">Access Windows</h2>
          <p class="panel-kicker">Komplette operative Liste mit Direktaktionen.</p>
        </div>
      </div>
      <div class="stack">
        ${state.windows.map((window) => `
          <div class="list-item">
            <div class="split">${pill(window.status)}<span class="subtle numberish">${fmtDate(window.dispatch_at)}</span></div>
            <h3>Mitglied ${escapeHtml(window.member_id)} · Window ${window.id}</h3>
            <p class="subtle">Von ${fmtDate(window.starts_at)} bis ${fmtDate(window.ends_at)} · ${escapeHtml(window.access_reason)}</p>
            <p class="subtle">Check-in ${window.check_in_confirmed_at ? `bestätigt ${fmtDate(window.check_in_confirmed_at)}` : "offen"}</p>
            ${renderWindowActions(window)}
          </div>
        `).join("") || '<div class="empty">Keine Access Windows vorhanden.</div>'}
      </div>
    </section>
  `;
}

function renderLockView() {
  return `
    <div class="dashboard-grid">
      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schlosszustand</h2>
            <p class="panel-kicker">Verlässliche Systemsicht bis die echten Nuki-Credentials aktiv sind.</p>
          </div>
        </div>
        <div class="stack">
          <div class="list-item">
            <div class="split"><strong>Smartlock</strong>${pill(state.lockStatus?.source || "system")}</div>
            <p class="subtle">ID ${escapeHtml(state.lockStatus?.smartlock_id || "—")}</p>
            <div class="row">
              ${pill(state.lockStatus?.connectivity || "unknown")}
              ${pill(state.lockStatus?.lock_state || "unknown")}
              ${pill(state.lockStatus?.battery_state || "unknown")}
            </div>
          </div>
          <div class="list-item">
            <strong>Best Practice</strong>
            <p class="subtle">Bis zum Go-live zeigt die Oberfläche belastbare Betriebsdaten statt erfundener Live-Telemetrie.</p>
          </div>
          ${state.role === "admin" ? '<button type="button" class="warn" data-remote-open>Remote Open Protokollieren</button>' : ""}
        </div>
      </section>
      <section class="panel span-8">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schloss Log</h2>
            <p class="panel-kicker">Remote Open, Code-Interventionen und Dry-Run-Aktivitäten.</p>
          </div>
        </div>
        <div class="stack">
          ${state.lockLog.map((entry) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(entry.action)}</strong><span class="subtle numberish">${fmtDate(entry.created_at)}</span></div>
              <p class="subtle">${escapeHtml(entry.actor_email)}</p>
              <p class="code">${escapeHtml(JSON.stringify(entry.payload || {}))}</p>
            </div>
          `).join("") || '<div class="empty">Noch keine Schlossereignisse vorhanden.</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderAlertsView() {
  return `
    <div class="dashboard-grid">
      <section class="panel span-5">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Warnungen & Fehler</h2>
            <p class="panel-kicker">Alle betrieblichen Hinweise, die den Ablauf oder Zutritt beeinflussen.</p>
          </div>
        </div>
        <div class="stack">
          ${state.alerts.map((alert) => `
            <div class="list-item">
              <div class="split">${pill(alert.severity)}<span class="subtle numberish">${fmtDate(alert.created_at)}</span></div>
              <h3>${escapeHtml(alert.kind)}</h3>
              <p class="subtle">${escapeHtml(alert.message)}</p>
            </div>
          `).join("") || '<div class="empty">Keine Alerts vorhanden.</div>'}
        </div>
      </section>
      <section class="panel span-7">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Betriebslog</h2>
            <p class="panel-kicker">Admin- und Operator-Aktionen in auditierbarer Reihenfolge.</p>
          </div>
        </div>
        <div class="stack">
          ${state.actions.map((action) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(action.action)}</strong><span class="subtle numberish">${fmtDate(action.created_at)}</span></div>
              <p class="subtle">${escapeHtml(action.actor_email)}</p>
              <p class="code">${escapeHtml(JSON.stringify(action.payload || {}))}</p>
            </div>
          `).join("") || '<div class="empty">Keine Aktionen protokolliert.</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderSettingsView() {
  if (state.role !== "admin") {
    return `
      <section class="detail-card">
        <h2 class="panel-title">Einstellungen</h2>
        <p class="subtle">Nur Admins dürfen Integrationen und Benutzer verwalten.</p>
      </section>
    `;
  }
  return `
    <div class="dashboard-grid">
      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Benutzer</h2>
            <p class="panel-kicker">Admin- und Operator-Konten sauber getrennt verwalten.</p>
          </div>
        </div>
        <form id="create-user-form" class="stack">
          <label for="new-user-email">E-Mail
            <input id="new-user-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="operator@frigew.ski…" required />
          </label>
          <label for="new-user-password">Startpasswort
            <input id="new-user-password" name="password" type="password" autocomplete="new-password" placeholder="Mindestens 12 Zeichen…" required />
          </label>
          <label for="new-user-role">Rolle
            <select id="new-user-role" name="role">
              <option value="operator">Operator</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <button type="submit">Benutzer Anlegen</button>
        </form>
        <div class="stack" style="margin-top: 16px;">
          ${state.users.map((user) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(user.email)}</strong>${pill(user.role)}</div>
              <p class="subtle">${user.is_active ? "Aktiv" : "Inaktiv"}</p>
            </div>
          `).join("")}
        </div>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">SMTP</h2>
            <p class="panel-kicker">Mitgliedercodes und Reset-Mails sauber zustellen.</p>
          </div>
        </div>
        <form id="smtp-form" class="stack">
          <label for="smtp-host">SMTP Host
            <input id="smtp-host" name="smtp_host" type="text" autocomplete="off" value="${escapeHtml(state.emailSettings?.smtp_host || "")}" placeholder="smtp.gmail.com…" />
          </label>
          <label for="smtp-port">SMTP Port
            <input id="smtp-port" name="smtp_port" type="number" inputmode="numeric" value="${escapeHtml(state.emailSettings?.smtp_port || 587)}" />
          </label>
          <label for="smtp-username">SMTP Benutzername
            <input id="smtp-username" name="smtp_username" type="text" autocomplete="username" value="${escapeHtml(state.emailSettings?.smtp_username || "")}" />
          </label>
          <label for="smtp-password">SMTP Passwort
            <input id="smtp-password" name="smtp_password" type="password" autocomplete="new-password" placeholder="Nur neu setzen, wenn nötig…" />
          </label>
          <label for="smtp-from">Absenderadresse
            <input id="smtp-from" name="smtp_from_email" type="email" autocomplete="email" spellcheck="false" inputmode="email" value="${escapeHtml(state.emailSettings?.smtp_from_email || "")}" />
          </label>
          <button type="submit">SMTP Speichern</button>
        </form>
        <form id="smtp-test-form" class="stack" style="margin-top: 16px;">
          <label for="smtp-test-email">Testempfänger
            <input id="smtp-test-email" name="to_email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="admin@example.com…" required />
          </label>
          <button type="submit" class="secondary">Testmail Senden</button>
        </form>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Telegram</h2>
            <p class="panel-kicker">Optionaler Alarmkanal für Warnungen, Fehler und Notfallcodes.</p>
          </div>
        </div>
        <form id="telegram-form" class="stack">
          <label for="telegram-bot-token">Bot Token
            <input id="telegram-bot-token" name="telegram_bot_token" type="password" autocomplete="off" value="" placeholder="${state.telegramSettings?.has_bot_token ? "Bot Token ist gesetzt…" : "Telegram Bot Token…"}" />
          </label>
          <label for="telegram-chat-id">Chat ID
            <input id="telegram-chat-id" name="telegram_chat_id" type="text" autocomplete="off" spellcheck="false" value="${escapeHtml(state.telegramSettings?.telegram_chat_id || "")}" placeholder="Telegram Chat ID…" />
          </label>
          <button type="submit">Telegram Speichern</button>
        </form>
        <form id="telegram-test-form" class="stack" style="margin-top: 16px;">
          <label for="telegram-test-message">Testnachricht
            <textarea id="telegram-test-message" name="message" placeholder="[Twenty4Seven-Gym] Testalarm…">[Twenty4Seven-Gym] Testalarm</textarea>
          </label>
          <button type="submit" class="secondary">Telegram Testen</button>
        </form>
      </section>

      <section class="panel span-12">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Check-in & Hausregeln</h2>
            <p class="panel-kicker">Öffentliche Member-Seite vor jedem Freies-Training-Block, inkl. Studio-QR und Mail-Link.</p>
          </div>
        </div>
        <div class="dashboard-grid">
          <div class="span-7">
            <form id="checkin-form" class="stack">
              <label for="checkin-enabled" class="checkbox-row">
                <input id="checkin-enabled" name="enabled" type="checkbox" ${state.checkInSettings?.enabled ? "checked" : ""} />
                <span>Check-in öffentlich aktivieren</span>
              </label>
              <label for="checkin-title">Titel
                <input id="checkin-title" name="title" type="text" autocomplete="off" value="${escapeHtml(state.checkInSettings?.title || "")}" required />
              </label>
              <label for="checkin-intro">Einleitung
                <textarea id="checkin-intro" name="intro" required>${escapeHtml(state.checkInSettings?.intro || "")}</textarea>
              </label>
              <label for="checkin-rules-heading">Hausregel-Überschrift
                <input id="checkin-rules-heading" name="rules_heading" type="text" autocomplete="off" value="${escapeHtml(state.checkInSettings?.rules_heading || "")}" required />
              </label>
              <label for="checkin-rules-body">Hausregeln
                <textarea id="checkin-rules-body" name="rules_body" required>${escapeHtml(state.checkInSettings?.rules_body || "")}</textarea>
              </label>
              <label for="checkin-checklist-heading">Checklisten-Überschrift
                <input id="checkin-checklist-heading" name="checklist_heading" type="text" autocomplete="off" value="${escapeHtml(state.checkInSettings?.checklist_heading || "")}" required />
              </label>
              <label for="checkin-items">Checklistenpunkte, je Zeile ein Punkt
                <textarea id="checkin-items" name="checklist_items" required>${escapeHtml((state.checkInSettings?.checklist_items || []).map((item) => item.label).join("\n"))}</textarea>
              </label>
              <label for="checkin-success-message">Erfolgstext
                <textarea id="checkin-success-message" name="success_message" required>${escapeHtml(state.checkInSettings?.success_message || "")}</textarea>
              </label>
              <button type="submit">Check-in Speichern</button>
            </form>
          </div>
          <div class="span-5">
            <div class="stack">
              <div class="list-item">
                <strong>Öffentlicher Link</strong>
                <p class="code">${escapeHtml(state.checkInSettings?.studio_check_in_url || "")}</p>
              </div>
              <div class="list-item qr-panel">
                <strong>Studio QR</strong>
                ${state.checkInSettings?.studio_qr_svg ? `<img class="qr-image" src="${state.checkInSettings.studio_qr_svg}" alt="QR-Code für den öffentlichen Check-in" />` : '<div class="empty">QR-Code nicht verfügbar.</div>'}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderMainContent() {
  if (state.view === "members") return renderMembersView();
  if (state.view === "windows") return renderWindowsView();
  if (state.view === "lock") return renderLockView();
  if (state.view === "alerts") return renderAlertsView();
  if (state.view === "settings") return renderSettingsView();
  return renderOverview();
}

function renderAuth() {
  app.innerHTML = `
    <section class="auth-shell">
      <div class="auth-grid">
        <div class="auth-panel">
          <p class="eyebrow">Twenty4Seven-Gym</p>
          <h1>Operations Console für 24/7 Zugang.</h1>
          <p class="subtle">Magicline, Nuki, Admin-Log, Alerts und manuelle Freigaben in einer Oberfläche mit klarer Betriebspriorität.</p>
        </div>
        <div class="auth-panel">
          <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Melde dich mit einem Admin- oder Operator-Konto an.")}</div>
          <form id="login-form" class="stack">
            <label for="login-email">E-Mail
              <input id="login-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="admin@frigew.ski…" required />
            </label>
            <label for="login-password">Passwort
              <input id="login-password" name="password" type="password" autocomplete="current-password" placeholder="Passwort…" required />
            </label>
            <button type="submit">Anmelden</button>
          </form>
          <form id="forgot-form" class="stack" style="margin-top: 16px;">
            <label for="forgot-email">Reset-Link per E-Mail
              <input id="forgot-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="Passwort zurücksetzen…" required />
            </label>
            <button type="submit" class="secondary">Reset-Link Anfordern</button>
          </form>
        </div>
      </div>
    </section>
  `;
  document.getElementById("login-form").addEventListener("submit", (event) => login(event).catch(handleError));
  document.getElementById("forgot-form").addEventListener("submit", (event) => forgotPassword(event).catch(handleError));
}

function renderPublicCheckIn() {
  const session = state.publicCheckInSession;
  if (!session) {
    app.innerHTML = `
      <section class="public-shell">
        <div class="public-hero">
          <p class="eyebrow">Twenty4Seven-Gym</p>
          <h1>Check-in vor dem Training.</h1>
          <p class="subtle">Bitte bestätige vor jedem gebuchten Freies-Training-Block die Hausregeln und die Checkliste.</p>
        </div>
        <div class="auth-panel">
          <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Scanne den QR-Code im Studio oder nutze den Link aus deiner Zugangs-Mail.")}</div>
          <form id="public-checkin-resolve-form" class="stack">
            <label for="public-email">E-Mail
              <input id="public-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="mitglied@example.com…" required />
            </label>
            <label for="public-code">Zugangscode
              <input id="public-code" name="code" type="text" autocomplete="one-time-code" inputmode="numeric" spellcheck="false" placeholder="Dein aktueller Code…" required />
            </label>
            <button type="submit">Trainingsblock Laden</button>
          </form>
        </div>
      </section>
    `;
    document.getElementById("public-checkin-resolve-form")?.addEventListener("submit", (event) => resolvePublicCheckIn(event).catch(handleError));
    return;
  }

  const steps = publicCheckInSteps();
  const step = session.window.is_confirmed ? 4 : state.publicCheckInStep;
  const checklistItems = session.settings.checklist_items || [];
  const checklistComplete = publicChecklistComplete();
  const completedCount = checklistItems.filter((item) => state.publicCheckInDraft.checklist[item.id]).length;
  let content = "";

  if (step === 1) {
    content = `
      <section class="detail-card funnel-card">
        <p class="eyebrow">Schritt 1 von 4</p>
        <h2 class="panel-title">Trainingsblock prüfen</h2>
        <p class="subtle">Vor jedem gebuchten Freies-Training-Block führst du hier den Check-in durch. Der Zugangscode wurde separat versendet und bleibt davon unabhängig.</p>
        <div class="funnel-summary">
          <div class="summary-item">
            <span class="summary-label">Mitglied</span>
            <strong>${escapeHtml(session.window.member_first_name || "Mitglied")}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Zeitfenster</span>
            <strong>${fmtDate(session.window.starts_at)} bis ${fmtDate(session.window.ends_at)}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Zugangsstatus</span>
            <strong>${pill(session.window.status)}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Einstieg</span>
            <strong>${escapeHtml(session.entry_source === "studio-qr" ? "Studio QR" : "Mail-Link")}</strong>
          </div>
        </div>
        <div class="funnel-actions">
          <button type="button" id="public-step-next">Check-in starten</button>
        </div>
      </section>
    `;
  } else if (step === 2) {
    content = `
      <section class="detail-card funnel-card">
        <p class="eyebrow">Schritt 2 von 4</p>
        <h2 class="panel-title">${escapeHtml(session.settings.rules_heading)}</h2>
        <p class="subtle">Lies die Regeln vollständig und bestätige sie bewusst, bevor du fortfährst.</p>
        <div class="rules-panel">
          <div class="rules-body">${escapeHtml(session.settings.rules_body).replace(/\n/g, "<br />")}</div>
        </div>
        <label class="checkbox-row funnel-checkbox" for="rules-accepted-step">
          <input id="rules-accepted-step" type="checkbox" ${state.publicCheckInDraft.rulesAccepted ? "checked" : ""} />
          <span>Ich habe die Hausregeln gelesen und bestätige die Einhaltung.</span>
        </label>
        <div class="funnel-actions">
          <button type="button" id="public-step-back" class="secondary">Zurück</button>
          <button type="button" id="public-step-next" ${state.publicCheckInDraft.rulesAccepted ? "" : "disabled"}>Weiter zur Checkliste</button>
        </div>
      </section>
    `;
  } else if (step === 3) {
    content = `
      <section class="detail-card funnel-card">
        <p class="eyebrow">Schritt 3 von 4</p>
        <h2 class="panel-title">${escapeHtml(session.settings.checklist_heading)}</h2>
        <p class="subtle">Bestätige jeden Punkt einzeln. So wird der Zustand des Studios vor deinem Trainingsblock sauber dokumentiert.</p>
        <div class="funnel-progress-note">${completedCount} von ${checklistItems.length} Punkten bestätigt</div>
        <div class="checklist-stack funnel-checklist">
          ${checklistItems.map((item, index) => `
            <label class="checklist-tile" for="check-${escapeHtml(item.id)}">
              <span class="checklist-index">0${index + 1}</span>
              <input id="check-${escapeHtml(item.id)}" data-check-item="${escapeHtml(item.id)}" type="checkbox" ${state.publicCheckInDraft.checklist[item.id] ? "checked" : ""} />
              <span class="checklist-copy">${escapeHtml(item.label)}</span>
            </label>
          `).join("")}
        </div>
        <div class="funnel-actions">
          <button type="button" id="public-step-back" class="secondary">Zurück</button>
          <button type="button" id="public-step-next" ${checklistComplete ? "" : "disabled"}>Weiter zum Abschluss</button>
        </div>
      </section>
    `;
  } else {
    content = `
      <section class="detail-card funnel-card">
        <p class="eyebrow">Schritt 4 von 4</p>
        <h2 class="panel-title">${session.window.is_confirmed ? "Check-in abgeschlossen" : "Abschluss und Bestätigung"}</h2>
        <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || (session.window.is_confirmed ? session.settings.success_message : "Prüfe deine Angaben und bestätige den Check-in verbindlich."))}</div>
        <div class="funnel-summary review-grid">
          <div class="summary-item">
            <span class="summary-label">Hausregeln</span>
            <strong>${state.publicCheckInDraft.rulesAccepted ? "Bestätigt" : "Offen"}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Checkliste</span>
            <strong>${completedCount} / ${checklistItems.length} bestätigt</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Trainingsblock</span>
            <strong>${fmtDate(session.window.starts_at)} bis ${fmtDate(session.window.ends_at)}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">Quelle</span>
            <strong>${escapeHtml(session.window.source || session.entry_source)}</strong>
          </div>
        </div>
        ${session.window.is_confirmed
          ? `<div class="list-item"><strong>Bestätigt am</strong><p class="subtle">${fmtDate(session.window.confirmed_at)} · ${escapeHtml(session.window.source || "public-check-in")}</p></div>`
          : `
            <form id="public-checkin-submit-form" class="stack">
              <div class="funnel-actions">
                <button type="button" id="public-step-back" class="secondary">Zurück</button>
                <button type="submit">Verbindlich bestätigen</button>
              </div>
            </form>
          `}
      </section>
    `;
  }

  app.innerHTML = `
    <section class="public-shell">
      <div class="public-hero">
        <p class="eyebrow">Twenty4Seven-Gym</p>
        <h1>${escapeHtml(session.settings.title)}</h1>
        <p class="subtle">${escapeHtml(session.settings.intro)}</p>
        <div class="public-meta">
          ${pill(session.window.status)}
          <span>${escapeHtml(session.window.member_first_name || "Mitglied")}</span>
          <span>${fmtDate(session.window.starts_at)} bis ${fmtDate(session.window.ends_at)}</span>
        </div>
        <div class="funnel-steps" aria-label="Check-in Fortschritt">
          ${steps.map((item) => `
            <div class="funnel-step ${step === item.id ? "active" : ""} ${step > item.id || session.window.is_confirmed ? "done" : ""}">
              <span class="funnel-step-index">${item.id}</span>
              <span>${escapeHtml(item.label)}</span>
            </div>
          `).join("")}
        </div>
      </div>
      ${content}
    </section>
  `;
  document.getElementById("public-checkin-submit-form")?.addEventListener("submit", (event) => submitPublicCheckIn(event).catch(handleError));
  document.getElementById("public-step-next")?.addEventListener("click", () => {
    if (step < 4) {
      goToPublicCheckInStep(step + 1);
    }
  });
  document.getElementById("public-step-back")?.addEventListener("click", () => {
    if (step > 1) {
      goToPublicCheckInStep(step - 1);
    }
  });
  document.getElementById("rules-accepted-step")?.addEventListener("change", (event) => {
    updatePublicRulesAccepted(event.currentTarget.checked);
  });
  document.querySelectorAll("[data-check-item]").forEach((input) => {
    input.addEventListener("change", (event) => {
      updatePublicChecklist(input.dataset.checkItem, event.currentTarget.checked);
    });
  });
}

function renderReset() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  app.innerHTML = `
    <section class="reset-shell">
      <p class="eyebrow">Twenty4Seven-Gym Reset</p>
      <h1>Neues Passwort setzen.</h1>
      <p class="subtle code">${escapeHtml(token ? `Token erkannt: ${token.slice(0, 12)}…` : "Kein Token vorhanden.")}</p>
      <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Setze hier ein neues Passwort für dein Konto.")}</div>
      <form id="reset-form" class="stack">
        <label for="reset-password">Neues Passwort
          <input id="reset-password" name="password" type="password" autocomplete="new-password" placeholder="Mindestens 12 Zeichen…" required />
        </label>
        <button type="submit">Passwort Setzen</button>
      </form>
    </section>
  `;
  document.getElementById("reset-form").addEventListener("submit", (event) => resetPassword(event).catch(handleError));
}

function renderApp() {
  const current = currentMember();
  app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <p class="eyebrow">Twenty4Seven-Gym</p>
          <h1 class="brand-title">Studiozugang unter Kontrolle.</h1>
          <p class="subtle">Responsive Betriebsoberfläche für Empfang, Backoffice und mobile Intervention.</p>
        </div>

        <div class="mobile-toolbar" aria-label="Mobile Navigation">
          ${navButton("overview", "Start", "◎")}
          ${navButton("members", "Mitglieder", "◌")}
          ${navButton("windows", "Windows", "◐")}
          ${navButton("lock", "Schloss", "◍")}
          ${navButton("alerts", "Alerts", "△")}
          ${navButton("settings", "Einstellungen", "□")}
        </div>

        <nav class="sidebar-nav" aria-label="Primäre Navigation">
          ${navButton("overview", "Betrieb", "◎")}
          ${navButton("members", "Mitglieder", "◌")}
          ${navButton("windows", "Access Windows", "◐")}
          ${navButton("lock", "Schloss", "◍")}
          ${navButton("alerts", "Alerts & Audit", "△")}
          ${navButton("settings", "Einstellungen", "□")}
        </nav>

        <div class="sidebar-footer">
          <p class="eyebrow">Session</p>
          <p><strong>${escapeHtml(state.me?.email || "")}</strong></p>
          <div class="row">${pill(state.me?.role || "unknown")} ${pill("Nuki dry-run")}</div>
          ${current ? `<p class="subtle" style="margin-top: 12px;">Aktiv ausgewählt: ${escapeHtml(current.email || `Mitglied ${current.id}`)}</p>` : ""}
        </div>
      </aside>

      <section class="content">
        <header class="topbar">
          <div class="hero-card">
            <div class="hero-copy">
              <div>
                <p class="eyebrow">Operations Console</p>
                <h2 class="hero-title">Twenty4Seven-Gym</h2>
              </div>
              <p class="subtle">Goldstandard für den operativen Alltag: kritische Zustände zuerst, klare Eingriffe, mobile Bedienbarkeit ohne Funktionsverlust.</p>
              <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "System bereit. Wähle links einen Bereich oder prüfe die nächsten Freischaltungen.")}</div>
              <div class="hero-actions">
                <button type="button" id="sync-button">Magicline Sync</button>
                <button type="button" id="provision-button" class="secondary">Due Codes Prüfen</button>
                ${state.role === "admin" ? '<button type="button" class="warn" data-remote-open>Remote Open</button>' : ""}
                <button type="button" id="logout-button" class="secondary">Logout</button>
              </div>
            </div>
            <div class="hero-copy">
              <div>
                <p class="eyebrow">Aktiver Kontext</p>
                <h3 class="panel-title">${escapeHtml(current?.email || state.me?.email || "Kein Mitglied gewählt")}</h3>
                <p class="subtle">${current ? "Mitgliederdetail aktiv" : "Nutze Mitglieder- oder Windows-Sicht für gezielte Aktionen."}</p>
              </div>
              <div class="row">
                ${pill(state.me?.role || "unknown")}
                ${pill(state.lockStatus?.connectivity || "unknown")}
                ${pill(`${state.windows.length} windows`)}
              </div>
            </div>
          </div>
          ${renderStatusStrip()}
        </header>

        <main id="app-main">
          ${renderMainContent()}
        </main>
      </section>
    </div>
  `;

  attachCommonHandlers();
  if (state.view === "overview") attachOverviewHandlers();
  if (state.view === "members") attachMembersHandlers();
  if (state.view === "settings") attachSettingsHandlers();
}

function render() {
  if (window.location.pathname.endsWith("/check-in")) {
    if (
      !state.publicCheckInSession
      && !state.publicCheckInAttempted
      && new URLSearchParams(window.location.search).get("token")
    ) {
      app.innerHTML = `
        <section class="auth-shell">
          <div class="auth-panel">
            <p class="eyebrow">Twenty4Seven-Gym</p>
            <h1>Check-in wird geladen…</h1>
            <p class="subtle">Dein Trainingsblock wird vorbereitet.</p>
          </div>
        </section>
      `;
      loadPublicCheckInSession().catch(handleError);
      return;
    }
    renderPublicCheckIn();
    return;
  }
  if (window.location.pathname.endsWith("/reset-password")) {
    renderReset();
    return;
  }
  if (!state.token) {
    renderAuth();
    return;
  }
  if (!state.me) {
    app.innerHTML = `
      <section class="auth-shell">
        <div class="auth-panel">
          <p class="eyebrow">Twenty4Seven-Gym</p>
          <h1>Daten werden geladen…</h1>
          <p class="subtle">Betriebsdaten, Schlossstatus und Alerts werden vorbereitet.</p>
        </div>
      </section>
    `;
    loadAppData().catch((error) => {
      localStorage.removeItem("opengym_token");
      localStorage.removeItem("opengym_role");
      state.token = "";
      state.role = "";
      setMessage(error.message, "bad");
    });
    return;
  }
  renderApp();
}

render();
