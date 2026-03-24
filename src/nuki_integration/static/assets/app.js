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
  emailTemplate: null,
  telegramSettings: null,
  nukiSettings: null,
  magiclineSettings: null,
  studioLinks: null,
  view: params.get("view") || "overview",
  selectedMemberId: params.get("member") || "",
  message: "",
  messageType: "",
  // ── New /checks public shell ───────────────────────────────────
  checksSession: null,
  checksAttempted: false,
  checksWindowId: null,
  checksFunnelType: null,
  checksFunnel: null,
  checksFunnelStep: 0,
  checksFunnelDraft: {},
  checksStepError: null,
  checksLoading: false,
  // ── Admin Funnel Builder ───────────────────────────────────────
  funnelsList: [],
  funnelDetail: null,
  selectedFunnelId: null,
  stepEditorId: null,
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

let _messageDismissTimer = null;
function setMessage(text, type = "") {
  state.message = text;
  state.messageType = type;
  clearTimeout(_messageDismissTimer);
  if (text && type !== "bad") {
    _messageDismissTimer = setTimeout(() => {
      state.message = "";
      state.messageType = "";
      render();
    }, 5000);
  }
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

const STATUS_LABELS = {
  active: "Aktiv",
  inactive: "Inaktiv",
  pending: "Ausstehend",
  expired: "Abgelaufen",
  locked: "Gesperrt",
  done: "Abgeschlossen",
  unknown: "Unbekannt",
};

function translateStatus(s) {
  return STATUS_LABELS[s?.toLowerCase()] ?? s;
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
  state.message = "";
  state.messageType = "";
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
  }, "Authentifizierung…");
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
  const [members, windows, alerts, actions, lockStatus, lockLog] = await Promise.all([
    api("./admin/members?limit=50"),
    api("./admin/access-windows?limit=50"),
    api("./admin/alerts?limit=50"),
    api("./admin/admin-actions?limit=50"),
    api("./admin/lock/status"),
    api("./admin/lock/log?limit=50"),
  ]);
  state.members = members;
  state.windows = windows;
  state.alerts = alerts;
  state.actions = actions;
  state.lockStatus = lockStatus;
  state.lockLog = lockLog;
  if (state.role === "admin") {
    const [users, emailSettings, emailTemplate, telegramSettings, nukiSettings, magiclineSettings, studioLinks, funnelsList] = await Promise.all([
      api("./admin/users?limit=20"),
      api("./admin/system/email-settings"),
      api("./admin/system/email-template"),
      api("./admin/system/telegram-settings"),
      api("./admin/system/nuki-settings"),
      api("./admin/system/magicline-settings"),
      api("./admin/system/studio-links"),
      api("./admin/funnels"),
    ]);
    state.users = users;
    state.emailSettings = emailSettings;
    state.emailTemplate = emailTemplate;
    state.telegramSettings = telegramSettings;
    state.nukiSettings = nukiSettings;
    state.magiclineSettings = magiclineSettings;
    state.studioLinks = studioLinks;
    state.funnelsList = funnelsList;
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

function showConfirm(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.innerHTML = `
      <div class="confirm-dialog">
        <p class="confirm-message">${escapeHtml(message)}</p>
        <div class="action-group">
          <button type="button" class="secondary" id="confirm-cancel">Abbrechen</button>
          <button type="button" class="warn" id="confirm-ok">Bestätigen</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const cleanup = (result) => { overlay.remove(); resolve(result); };
    overlay.querySelector("#confirm-ok").addEventListener("click", () => cleanup(true));
    overlay.querySelector("#confirm-cancel").addEventListener("click", () => cleanup(false));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) cleanup(false); });
  });
}

async function runWindowAction(windowId, action) {
  const config = actionCopy[action] || { success: `${action} abgeschlossen.`, confirm: "" };
  if (config.confirm) {
    const confirmed = await showConfirm(config.confirm);
    if (!confirmed) return;
  }
  await api(`./admin/access-windows/${windowId}/${action}`, { method: "POST" });
  setMessage(config.success, "good");
  await loadAppData();
}

async function remoteOpen(trigger) {
  if (!await showConfirm("Remote Open jetzt auslösen bzw. protokollieren?")) {
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
  setMessage("Magioline-Sync ausgeführt.", "good");
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
    setMessage("Magioline-Sync für Mitglied abgeschlossen.", "good");
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
        smtp_use_tls: form.get("smtp_use_tls") === "on",
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
  const form = new FormData(event.currentTarget);
  const chatId = String(form.get("telegram_chat_id") || "").trim();
  if (chatId && !/^-?\d+$/.test(chatId)) {
    setMessage("Chat ID muss eine numerische Telegram-ID sein (z.B. -1001234567890)", "bad");
    return;
  }
  await withPending(event.currentTarget, async () => {
    await api("./admin/system/telegram-settings", {
      method: "PUT",
      body: JSON.stringify({
        telegram_bot_token: form.get("telegram_bot_token"),
        telegram_chat_id: chatId,
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

async function updateNukiSettings(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/nuki-settings", {
      method: "PUT",
      body: JSON.stringify({
        nuki_api_token: form.get("nuki_api_token") || "",
        nuki_smartlock_id: parseInt(form.get("nuki_smartlock_id") || "0", 10),
        nuki_dry_run: form.get("nuki_dry_run") === "on",
      }),
    });
    setMessage("Nuki-Einstellungen gespeichert.", "good");
    await loadAppData();
  }, "Nuki wird gespeichert…");
}

async function updateMagiclineSettings(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/magicline-settings", {
      method: "PUT",
      body: JSON.stringify({
        magicline_base_url: form.get("magicline_base_url") || "",
        magicline_api_key: form.get("magicline_api_key") || "",
        magicline_webhook_api_key: form.get("magicline_webhook_api_key") || "",
        magicline_studio_id: parseInt(form.get("magicline_studio_id") || "0", 10),
        magicline_studio_name: form.get("magicline_studio_name") || "",
        magicline_relevant_appointment_title: form.get("magicline_relevant_appointment_title") || "Freies Training",
      }),
    });
    setMessage("Magicline-Einstellungen gespeichert.", "good");
    await loadAppData();
  }, "Magicline wird gespeichert…");
}

async function updateEmailTemplate(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/email-template", {
      method: "PUT",
      body: JSON.stringify({
        header_html: form.get("header_html") || "",
        body_html: form.get("body_html") || "",
        footer_html: form.get("footer_html") || "",
      }),
    });
    setMessage("E-Mail-Template gespeichert.", "good");
    await loadAppData();
  }, "Template wird gespeichert…");
}

function logout() {
  localStorage.removeItem("opengym_token");
  localStorage.removeItem("opengym_role");
  Object.assign(state, {
    token: "", role: "", me: null, members: [], windows: [], alerts: [],
    actions: [], users: [], memberDetail: null, selectedMemberId: "",
    lockStatus: null, lockLog: [], emailSettings: null, emailTemplate: null, telegramSettings: null,
    nukiSettings: null, magiclineSettings: null, studioLinks: null, funnelsList: [], funnelDetail: null,
    selectedFunnelId: null, stepEditorId: null, view: "overview",
    message: "", messageType: "",
  });
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

function attachWindowActionHandlers() {
  document.querySelectorAll("[data-window-action]").forEach((button) => {
    button.addEventListener("click", () => runWindowAction(button.dataset.windowId, button.dataset.windowAction).catch(handleError));
  });
}

function attachMembersHandlers() {
  document.getElementById("member-search-form")?.addEventListener("submit", (event) => memberSearch(event).catch(handleError));
  document.getElementById("member-sync-form")?.addEventListener("submit", (event) => syncMember(event).catch(handleError));
  document.querySelectorAll("[data-member-id]").forEach((button) => {
    button.addEventListener("click", () => loadMemberDetail(button.dataset.memberId).catch(handleError));
  });
  attachWindowActionHandlers();
}

function attachSettingsHandlers() {
  document.getElementById("create-user-form")?.addEventListener("submit", (event) => createUser(event).catch(handleError));
  document.getElementById("smtp-form")?.addEventListener("submit", (event) => updateSmtp(event).catch(handleError));
  document.getElementById("smtp-test-form")?.addEventListener("submit", (event) => testEmail(event).catch(handleError));
  document.getElementById("telegram-form")?.addEventListener("submit", (event) => updateTelegram(event).catch(handleError));
  document.getElementById("telegram-test-form")?.addEventListener("submit", (event) => testTelegram(event).catch(handleError));
  document.getElementById("nuki-form")?.addEventListener("submit", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById("magicline-form")?.addEventListener("submit", (event) => updateMagiclineSettings(event).catch(handleError));
  document.getElementById("email-template-form")?.addEventListener("submit", (event) => updateEmailTemplate(event).catch(handleError));
  document.getElementById("email-template-preview-btn")?.addEventListener("click", () => {
    const header = document.getElementById("tpl-header")?.value || "";
    const body = document.getElementById("tpl-body")?.value || "";
    const footer = document.getElementById("tpl-footer")?.value || "";
    const html = `<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${header}${body}${footer}</body></html>`;
    const frame = document.getElementById("email-preview-frame");
    if (frame) frame.srcdoc = html;
  });
}

function handleError(error) {
  setMessage(error.message || "Unbekannter Fehler.", "bad");
}

function navButton(view, label) {
  const active = state.view === view ? "active" : "";
  const href = `./app?view=${encodeURIComponent(view)}${state.selectedMemberId ? `&member=${encodeURIComponent(state.selectedMemberId)}` : ""}`;
  return `
    <a
      class="nav-button ${active}"
      data-view="${view}"
      href="${href}"
      ${state.view === view ? 'aria-current="page"' : ""}
    >
      <span class="nav-label">${escapeHtml(label)}</span>
    </a>
  `;
}

function renderStatusStrip() {
  const next = upcomingWindow();
  const alertCount = urgentAlerts().length;
  return `
    <div class="live-strip">
      <div class="live-card">
        <div class="live-label">SCHLOSS</div>
        <div class="live-value">
          <span class="live-dot ${/credentials|unknown/i.test(state.lockStatus?.connectivity || "") ? "warn" : ""}"></span>
          ${escapeHtml(state.lockStatus?.connectivity || "unknown")}
        </div>
        <div class="subtle lock-status-row">
          <span class="lock-status-pair"><span class="lock-status-key">Quelle</span>${pill(state.lockStatus?.source || "–")}</span>
          <span class="lock-status-pair"><span class="lock-status-key">Schloss</span>${pill(state.lockStatus?.lock_state || "–")}</span>
        </div>
      </div>
      <div class="live-card">
        <div class="live-label">MITGLIEDER</div>
        <div class="live-value">${state.members.length}</div>
        <div class="subtle">${state.members.length > 0 ? "Aktive Sicht geladen" : "Noch keine Mitglieder synchronisiert"}</div>
      </div>
      <div class="live-card">
        <div class="live-label">ALARME</div>
        <div class="live-value">
          ${alertCount > 0 ? `<span class="live-dot bad"></span>` : `<span class="live-dot"></span>`}
          ${alertCount}
        </div>
        <div class="subtle">${alertCount ? "Handlungsbedarf" : "Keine Anomalien"}</div>
      </div>
      <div class="live-card">
        <div class="live-label">NÄCHSTER SLOT</div>
        <div class="live-value numberish">${next ? fmtDate(next.dispatch_at) : "—"}</div>
        <div class="subtle">${next ? `Member ${next.member_id}` : "Kein offenes Fenster"}</div>
      </div>
    </div>
  `;
}

function renderOverview() {
  const next = upcomingWindow();
  const urgent = urgentAlerts();
  return `
    <div class="dashboard-grid">
      <section class="metric-card span-3">
        <span class="eyebrow">OFFENE FENSTER</span>
        <strong>${state.windows.length}</strong>
        <span class="subtle">Geplant oder aktiv</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">WARNUNGEN</span>
        <strong>${urgent.length}</strong>
        <span class="subtle">Fehler + Warnungen</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">SCHLOSS</span>
        <strong>${escapeHtml(state.lockStatus?.connectivity || "—")}</strong>
        <span class="subtle">Dry-Run aktiv</span>
      </section>
      <section class="metric-card span-3">
        <span class="eyebrow">NÄCHSTE FREISCHALTUNG</span>
        <strong class="numberish">${next ? fmtDate(next.dispatch_at) : "—"}</strong>
        <span class="subtle">${next ? `Window ${next.id}` : "Kein Termin offen"}</span>
      </section>

      <section class="panel span-8">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Jetzt Wichtig</h2>
            <p class="panel-kicker">Betriebsrelevante Alerts — sofortige Sichtbarkeit für Eingriffe.</p>
          </div>
        </div>
        <div class="stack">
          ${urgent.length ? urgent.map((alert) => `
            <div class="list-item">
              <div class="split">${pill(alert.severity)}<span class="subtle numberish">${fmtDate(alert.created_at)}</span></div>
              <h3>${escapeHtml(alert.kind)}</h3>
              <p class="subtle">${escapeHtml(alert.message)}</p>
            </div>
          `).join("") : `
            <div class="empty">Keine offenen Warn- oder Fehlerfälle</div>
          `}
        </div>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schloss Log</h2>
            <p class="panel-kicker">Letzte Dry-Run- und manuelle Eingriffe.</p>
          </div>
        </div>
        <div class="stack">
          ${state.lockLog.length ? state.lockLog.slice(0, 5).map((entry) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(entry.action)}</strong><span class="subtle numberish">${fmtDate(entry.created_at)}</span></div>
              <p class="subtle">${escapeHtml(entry.actor_email)}</p>
            </div>
          `).join("") : '<div class="empty">Keine Schlossereignisse</div>'}
        </div>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Access Windows</h2>
            <p class="panel-kicker">Nächste Freigaben und Endzeiten im Überblick.</p>
          </div>
        </div>
        <div class="stack">
          ${state.windows.slice(0, 6).map((w) => `
            <div class="list-item">
              <div class="split">${pill(w.status)}<span class="subtle numberish">${fmtDate(w.dispatch_at)}</span></div>
              <h3>Member ${escapeHtml(w.member_id)} · Window ${w.id}</h3>
              <p class="subtle">${fmtDate(w.starts_at)} → ${fmtDate(w.ends_at)}</p>
            </div>
          `).join("") || '<div class="empty">Keine Access Windows</div>'}
        </div>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schnellzugriff</h2>
            <p class="panel-kicker">Direkter Sprung zu Diagnose und manuellen Aktionen.</p>
          </div>
        </div>
        <div class="stack">
          <a class="list-item quick-link" data-view="lock" href="./app?view=lock">
            <div class="split"><strong>Schloss öffnen</strong><span class="subtle">${escapeHtml(state.lockStatus?.connectivity || "–")}</span></div>
            <p class="subtle">Nuki-Steuerung und Remote Open</p>
          </a>
          <a class="list-item quick-link" data-view="alerts" href="./app?view=alerts">
            <div class="split"><strong>Alerts prüfen</strong>${urgentAlerts().length > 0 ? pill(`${urgentAlerts().length} offen`) : '<span class="subtle">Keine Alarme</span>'}</div>
            <p class="subtle">Warnungen, Fehler und Betriebslog</p>
          </a>
          <a class="list-item quick-link" data-view="members" href="./app?view=members">
            <div class="split"><strong>Mitglieder synchronisieren</strong><span class="subtle">${state.members.length} geladen</span></div>
            <p class="subtle">Magioline-Sync und Mitgliederverwaltung</p>
          </a>
          ${state.members.length > 0 ? state.members.slice(0, 3).map((member) => `
            <button type="button" class="list-item entity-button" data-member-id="${member.id}">
              <div class="split"><strong>${escapeHtml(`${member.first_name || ""} ${member.last_name || ""}`.trim() || member.email || `Member ${member.id}`)}</strong>${pill(member.status || "unknown")}</div>
              <p class="subtle">${escapeHtml(member.email || "Keine E-Mail")}</p>
            </button>
          `).join("") : ""}
        </div>
      </section>
    </div>
  `;
}

function renderMemberCards() {
  return state.members.map((member) => {
    const active = currentMember()?.id === member.id ? "active" : "";
    const label = `${member.first_name || ""} ${member.last_name || ""}`.trim() || member.email || `Member ${member.id}`;
    return `
      <button type="button" class="list-item entity-button ${active}" data-member-id="${member.id}">
        <div class="split"><strong>${escapeHtml(label)}</strong>${pill(member.status || "unknown")}</div>
        <p class="subtle">${escapeHtml(member.email || "Keine E-Mail")} · Magioline-ID ${escapeHtml(member.magicline_customer_id)}</p>
      </button>
    `;
  }).join("");
}

function renderWindowActions(w) {
  return `
    <div class="action-group mt-8">
      <button type="button" class="secondary" data-window-id="${w.id}" data-window-action="resend">Code Neu Senden</button>
      <button type="button" class="warn" data-window-id="${w.id}" data-window-action="emergency-code">Notfallcode</button>
      <button type="button" class="bad" data-window-id="${w.id}" data-window-action="deactivate">Deaktivieren</button>
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
              <p class="panel-kicker">Suche, Sync und Einzelansicht per E-Mail.</p>
            </div>
          </div>
          <div class="stack">
            <form id="member-search-form" class="stack">
              <label for="member-search-email">Mitglied suchen
                <input id="member-search-email" name="email" type="email" autocomplete="off" spellcheck="false" inputmode="email" placeholder="member@example.com" />
              </label>
              <button type="submit">Suchen</button>
            </form>
            <form id="member-sync-form" class="stack">
              <label for="member-sync-email">Gezielter Sync
                <input id="member-sync-email" name="email" type="email" autocomplete="off" spellcheck="false" inputmode="email" placeholder="Magioline-Sync für E-Mail…" required />
              </label>
              <button type="submit" class="secondary">Mitglied Syncen</button>
            </form>
          </div>
          <div class="entity-list" class="mt-14">
            ${renderMemberCards() || '<div class="empty">Keine Mitglieder vorhanden</div>'}
          </div>
        </section>
      </div>

      <div class="detail-column">
        ${detail ? `
          <section class="detail-card">
            <p class="eyebrow">// MITGLIED</p>
            <h2 class="panel-title">${escapeHtml(`${detail.member.first_name || ""} ${detail.member.last_name || ""}`.trim() || detail.member.email || `Member ${detail.member.id}`)}</h2>
            <div class="detail-meta">
              ${pill(detail.member.status || "unknown")}
              <span>Magioline-ID ${escapeHtml(detail.member.magicline_customer_id)}</span>
              <span>${escapeHtml(detail.member.email || "Keine E-Mail")}</span>
              <span class="numberish">Sync ${fmtDate(detail.member.last_synced_at)}</span>
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Buchungshistorie</h3>
                <p class="panel-kicker">Freies Training und Statuswechsel aus Magicline.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.bookings.map((booking) => `
                <div class="list-item">
                  <div class="split">${pill(booking.booking_status)}<span class="subtle numberish">${fmtDate(booking.start_at)}</span></div>
                  <h4>${escapeHtml(booking.title)}</h4>
                  <p class="subtle">${fmtDate(booking.start_at)} → ${fmtDate(booking.end_at)}</p>
                </div>
              `).join("") || '<div class="empty">Keine Buchungen</div>'}
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Zugangsfenster</h3>
                <p class="panel-kicker">Berechnete Korridore inkl. manueller Eingriffe.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.access_windows.map((w) => `
                <div class="list-item">
                  <div class="split">${pill(w.status)}<span class="subtle numberish">${fmtDate(w.dispatch_at)}</span></div>
                  <p class="subtle">Window ${w.id} · Bookings: ${escapeHtml(w.booking_ids.join(", "))}</p>
                  <p class="subtle">${fmtDate(w.starts_at)} → ${fmtDate(w.ends_at)}</p>
                  <p class="subtle">Check-in: ${w.check_in_confirmed_at ? `bestätigt ${fmtDate(w.check_in_confirmed_at)}` : "offen"}</p>
                  ${w.check_in_checklist?.length ? `
                    <div class="stack compact-stack" class="mt-6">
                      ${w.check_in_checklist.map((item) => `<p class="subtle">${item.checked ? "✓" : "✗"} ${escapeHtml(item.label)}</p>`).join("")}
                    </div>
                  ` : ""}
                  ${renderWindowActions(w)}
                </div>
              `).join("") || '<div class="empty">Keine Access Windows</div>'}
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Code-Historie</h3>
                <p class="panel-kicker">Reguläre und Notfallcodes im Verlauf.</p>
              </div>
            </div>
            <div class="stack">
              ${detail.access_codes.map((code) => `
                <div class="list-item">
                  <div class="split">${pill(code.status)}<span class="subtle numberish">${fmtDate(code.created_at)}</span></div>
                  <p class="code">••••${escapeHtml(code.code_last4)}${code.is_emergency ? " · EMERGENCY" : ""}</p>
                  <p class="subtle">Versendet ${fmtDate(code.emailed_at)} · Läuft ab ${fmtDate(code.expires_at)}</p>
                </div>
              `).join("") || '<div class="empty">Noch keine Codes</div>'}
            </div>
          </section>
        ` : `
          <section class="detail-card">
            <p class="eyebrow">// KEIN MEMBER AKTIV</p>
            <h2 class="panel-title">Member auswählen</h2>
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
          <p class="panel-kicker">Operative Gesamtliste mit Direktaktionen pro Eintrag.</p>
        </div>
      </div>
      <div class="stack">
        ${state.windows.map((w) => `
          <div class="list-item">
            <div class="split">${pill(w.status)}<span class="subtle numberish">${fmtDate(w.dispatch_at)}</span></div>
            <h3>Member ${escapeHtml(w.member_id)} · Window ${w.id}</h3>
            <p class="subtle">${fmtDate(w.starts_at)} → ${fmtDate(w.ends_at)} · ${escapeHtml(w.access_reason)}</p>
            <p class="subtle">Check-in: ${w.check_in_confirmed_at ? `bestätigt ${fmtDate(w.check_in_confirmed_at)}` : "offen"}</p>
            ${renderWindowActions(w)}
          </div>
        `).join("") || '<div class="empty">Keine Access Windows vorhanden</div>'}
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
            <p class="panel-kicker">Systemsicht bis die echten Nuki-Credentials aktiv sind.</p>
          </div>
        </div>
        <div class="stack">
          <div class="list-item">
            <div class="split">
              <strong>Smartlock ${escapeHtml(state.lockStatus?.smartlock_id || "—")}</strong>
              ${pill(state.lockStatus?.source || "system")}
            </div>
            <div class="lock-detail-grid mt-8">
              <span class="lock-detail-pair"><span class="lock-status-key">Verbindung</span>${pill(state.lockStatus?.connectivity || "–")}</span>
              <span class="lock-detail-pair"><span class="lock-status-key">Schloss</span>${pill(state.lockStatus?.lock_state || "–")}</span>
              <span class="lock-detail-pair"><span class="lock-status-key">Akku</span>${pill(state.lockStatus?.battery_state || "–")}</span>
            </div>
          </div>
          <div class="list-item">
            <p class="eyebrow">// DRY-RUN MODUS</p>
            <p class="subtle">Bis zum Go-live zeigt die Konsole belastbare Betriebsdaten ohne Live-Telemetrie.</p>
          </div>
          ${state.role === "admin" ? `
            <button type="button" class="warn" data-remote-open>Remote Open Protokollieren</button>
          ` : ""}
        </div>
      </section>
      <section class="panel span-8">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schloss-Log</h2>
            <p class="panel-kicker">Remote Open, Code-Interventionen und Dry-Run-Aktivitäten.</p>
          </div>
        </div>
        <div class="stack">
          ${state.lockLog.map((entry) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(entry.action)}</strong><span class="subtle numberish">${fmtDate(entry.created_at)}</span></div>
              <p class="subtle">${escapeHtml(entry.actor_email)}</p>
              ${Object.keys(entry.payload || {}).length ? `<p class="code">${escapeHtml(Object.entries(entry.payload).map(([k, v]) => `${k}: ${v}`).join(" · "))}</p>` : ""}
            </div>
          `).join("") || '<div class="empty">Keine Schlossereignisse</div>'}
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
            <h2 class="panel-title">Warnungen &amp; Fehler</h2>
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
          `).join("") || '<div class="empty">Keine Alerts vorhanden</div>'}
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
          `).join("") || '<div class="empty">Keine Aktionen protokolliert</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderSettingsView() {
  if (state.role !== "admin") {
    return `
      <section class="detail-card">
        <p class="eyebrow">// ZUGRIFF VERWEIGERT</p>
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
            <p class="panel-kicker">Admin- und Operator-Konten verwalten.</p>
          </div>
        </div>
        <form id="create-user-form" class="stack">
          <label for="new-user-email">E-Mail
            <input id="new-user-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="operator@example.com" required />
          </label>
          <label for="new-user-password">Startpasswort
            <input id="new-user-password" name="password" type="password" autocomplete="new-password" placeholder="Mindestens 12 Zeichen" required />
          </label>
          <label for="new-user-role">Rolle
            <select id="new-user-role" name="role">
              <option value="operator">Operator</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <button type="submit">Benutzer Anlegen</button>
        </form>
        <div class="stack" class="mt-14">
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
            <p class="panel-kicker">Mitgliedercodes und Reset-Mails konfigurieren.</p>
          </div>
        </div>
        <form id="smtp-form" class="stack">
          <label for="smtp-host">Host
            <input id="smtp-host" name="smtp_host" type="text" autocomplete="off" value="${escapeHtml(state.emailSettings?.smtp_host || "")}" placeholder="z.B. smtp.gmail.com" />
          </label>
          <label for="smtp-port">Port
            <input id="smtp-port" name="smtp_port" type="number" inputmode="numeric" value="${escapeHtml(state.emailSettings?.smtp_port || 587)}" />
          </label>
          <label for="smtp-username">Benutzername
            <input id="smtp-username" name="smtp_username" type="text" autocomplete="username" value="${escapeHtml(state.emailSettings?.smtp_username || "")}" />
          </label>
          <label for="smtp-password">Passwort
            <input id="smtp-password" name="smtp_password" type="password" autocomplete="new-password" placeholder="Nur neu setzen wenn nötig" />
          </label>
          <label for="smtp-from">Absenderadresse
            <input id="smtp-from" name="smtp_from_email" type="email" autocomplete="email" spellcheck="false" inputmode="email" value="${escapeHtml(state.emailSettings?.smtp_from_email || "")}" />
          </label>
          <label class="checkbox-row" for="smtp-use-tls">
            <input id="smtp-use-tls" name="smtp_use_tls" type="checkbox" ${state.emailSettings?.smtp_use_tls !== false ? "checked" : ""} />
            <span>TLS verwenden</span>
          </label>
          <button type="submit">SMTP Speichern</button>
        </form>
        <form id="smtp-test-form" class="stack" class="mt-14">
          <label for="smtp-test-email">Testempfänger
            <input id="smtp-test-email" name="to_email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="test@example.com" required />
          </label>
          <button type="submit" class="secondary">Testmail Senden</button>
        </form>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Telegram</h2>
            <p class="panel-kicker">Alarmkanal für Warnungen, Fehler und Notfallcodes.</p>
          </div>
        </div>
        <form id="telegram-form" class="stack">
          <label for="telegram-bot-token">Bot Token
            <input id="telegram-bot-token" name="telegram_bot_token" type="password" autocomplete="off" placeholder="${state.telegramSettings?.has_bot_token ? "Token gesetzt — neu eingeben zum Überschreiben" : "Telegram Bot Token"}" />
          </label>
          <label for="telegram-chat-id">Chat ID
            <input id="telegram-chat-id" name="telegram_chat_id" type="text" autocomplete="off" spellcheck="false" inputmode="numeric"
              value="${escapeHtml(state.telegramSettings?.telegram_chat_id || "")}"
              placeholder="z.B. -1001234567890"
              pattern="-?[0-9]+"
              title="Chat ID muss eine numerische Telegram-ID sein (z.B. -1001234567890)" />
            <span class="field-hint">Die Chat ID findest du über @userinfobot in Telegram</span>
          </label>
          <button type="submit">Telegram Speichern</button>
        </form>
        <form id="telegram-test-form" class="stack" class="mt-14">
          <label for="telegram-test-message">Testnachricht
            <textarea id="telegram-test-message" name="message" placeholder="[T247GYM] Testalarm">[T247GYM] Testalarm</textarea>
          </label>
          <button type="submit" class="secondary">Telegram Testen</button>
        </form>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Nuki</h2>
            <p class="panel-kicker">Smartlock-API und Trockenlauffunktion konfigurieren.</p>
          </div>
        </div>
        <form id="nuki-form" class="stack">
          <label for="nuki-api-token">API Token
            <input id="nuki-api-token" name="nuki_api_token" type="password" autocomplete="off"
              placeholder="${state.nukiSettings?.has_api_token ? "Token gesetzt — neu eingeben zum Überschreiben" : "Nuki Web API Token"}" />
          </label>
          <label for="nuki-smartlock-id">Smartlock ID
            <input id="nuki-smartlock-id" name="nuki_smartlock_id" type="number" inputmode="numeric"
              value="${escapeHtml(state.nukiSettings?.nuki_smartlock_id ?? 0)}" />
          </label>
          <label class="checkbox-row" for="nuki-dry-run">
            <input id="nuki-dry-run" name="nuki_dry_run" type="checkbox" ${state.nukiSettings?.nuki_dry_run !== false ? "checked" : ""} />
            <span>Dry Run (kein echtes Öffnen)</span>
          </label>
          <button type="submit">Nuki Speichern</button>
        </form>
      </section>

      <section class="panel span-4">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Magicline</h2>
            <p class="panel-kicker">Mitgliederverwaltung und Webhook-Anbindung konfigurieren.</p>
          </div>
        </div>
        <form id="magicline-form" class="stack">
          <label for="magicline-base-url">API-URL
            <input id="magicline-base-url" name="magicline_base_url" type="text" autocomplete="off"
              value="${escapeHtml(state.magiclineSettings?.magicline_base_url || "")}"
              placeholder="https://app.magicline.com" />
          </label>
          <label for="magicline-api-key">API Key
            <input id="magicline-api-key" name="magicline_api_key" type="password" autocomplete="off"
              placeholder="${state.magiclineSettings?.has_api_key ? "Key gesetzt — neu eingeben zum Überschreiben" : "Magicline API Key"}" />
          </label>
          <label for="magicline-webhook-key">Webhook Key
            <input id="magicline-webhook-key" name="magicline_webhook_api_key" type="password" autocomplete="off"
              placeholder="${state.magiclineSettings?.has_webhook_key ? "Key gesetzt — neu eingeben zum Überschreiben" : "Magicline Webhook Key"}" />
          </label>
          <label for="magicline-studio-id">Studio ID
            <input id="magicline-studio-id" name="magicline_studio_id" type="number" inputmode="numeric"
              value="${escapeHtml(state.magiclineSettings?.magicline_studio_id ?? 0)}" />
          </label>
          <label for="magicline-studio-name">Studio Name
            <input id="magicline-studio-name" name="magicline_studio_name" type="text"
              value="${escapeHtml(state.magiclineSettings?.magicline_studio_name || "")}"
              placeholder="z.B. Twenty4Seven GmbH" />
          </label>
          <label for="magicline-appointment-title">Terminbezeichnung
            <input id="magicline-appointment-title" name="magicline_relevant_appointment_title" type="text"
              value="${escapeHtml(state.magiclineSettings?.magicline_relevant_appointment_title || "Freies Training")}"
              placeholder="z.B. Freies Training" />
          </label>
          <button type="submit">Magicline Speichern</button>
        </form>
      </section>

      <section class="panel span-12">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">E-Mail Template</h2>
            <p class="panel-kicker">Professionelles HTML-Layout für alle ausgehenden E-Mails.</p>
          </div>
          <button type="button" id="email-template-preview-btn" class="secondary">Vorschau</button>
        </div>
        <form id="email-template-form" class="stack">
          <div class="template-editor-grid">
            <div class="template-editor-col">
              <label for="tpl-header">Header HTML
                <span class="field-hint">Styles + Außentabelle + Marken-Header-Zeile</span>
                <textarea id="tpl-header" name="header_html" class="code-editor" rows="12" spellcheck="false" autocomplete="off">${escapeHtml(state.emailTemplate?.header_html || "")}</textarea>
              </label>
              <label for="tpl-body">Body HTML
                <span class="field-hint">Inhalt-Zeilen (&lt;tr&gt;-Elemente). Platzhalter: {{&nbsp;contact.first_name&nbsp;}}</span>
                <textarea id="tpl-body" name="body_html" class="code-editor" rows="16" spellcheck="false" autocomplete="off">${escapeHtml(state.emailTemplate?.body_html || "")}</textarea>
              </label>
              <label for="tpl-footer">Footer HTML
                <span class="field-hint">Footer-Zeile + schließende Tabellen-Tags</span>
                <textarea id="tpl-footer" name="footer_html" class="code-editor" rows="10" spellcheck="false" autocomplete="off">${escapeHtml(state.emailTemplate?.footer_html || "")}</textarea>
              </label>
              <button type="submit">Template Speichern</button>
            </div>
            <div class="template-preview-col">
              <p class="eyebrow">// VORSCHAU</p>
              <iframe id="email-preview-frame" class="email-preview-frame" title="E-Mail Vorschau" sandbox="allow-same-origin"></iframe>
            </div>
          </div>
        </form>
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
  if (state.view === "funnels") return renderFunnelsView();
  return renderOverview();
}

function renderAuth() {
  app.innerHTML = `
    <section class="auth-shell">
      <div class="auth-grid">
        <div class="auth-panel">
          <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
          <h1>Studio Access Control.</h1>
          <p class="subtle">Magicline-Sync, Nuki-Keypad, Alerts und manuelle Freigaben — in einer operativen Konsole mit klarer Priorität.</p>
        </div>
        <div class="auth-panel">
          <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Anmeldung mit Admin- oder Operator-Konto.")}</div>
          <form id="login-form" class="stack" class="mt-14">
            <label for="login-email">E-Mail
              <input id="login-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="admin@example.com" required />
            </label>
            <label for="login-password">Passwort
              <input id="login-password" name="password" type="password" autocomplete="current-password" placeholder="Passwort" required />
            </label>
            <button type="submit">Anmelden</button>
          </form>
          <form id="forgot-form" class="stack" class="mt-14">
            <label for="forgot-email">Passwort zurücksetzen
              <input id="forgot-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="E-Mail für Reset-Link" required />
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


function renderReset() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  app.innerHTML = `
    <section class="reset-shell">
      <div class="auth-panel">
        <p class="eyebrow">// PASSWORT RESET</p>
        <h1>Neues Passwort setzen.</h1>
        <p class="subtle code">${escapeHtml(token ? `Token: ${token.slice(0, 12)}…` : "Kein Token vorhanden.")}</p>
      </div>
      <div class="auth-panel">
        <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Setze hier ein neues Passwort für dein Konto.")}</div>
        <form id="reset-form" class="stack" class="mt-14">
          <label for="reset-password">Neues Passwort
            <input id="reset-password" name="password" type="password" autocomplete="new-password" placeholder="Mindestens 12 Zeichen" required />
          </label>
          <button type="submit">Passwort Setzen</button>
        </form>
      </div>
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
          <div class="brand-terminal">
            <span class="eyebrow">// STUDIO ACCESS CONTROL</span>
            <h1 class="brand-title">T247GYM<span class="blink-cursor"></span></h1>
            <p class="brand-status">SYSTEM ONLINE</p>
          </div>
        </div>

        <div class="mobile-toolbar" aria-label="Mobile Navigation">
          ${navButton("overview", "Betrieb")}
          ${navButton("members", "Mitglieder")}
          ${navButton("windows", "Windows")}
          ${navButton("lock", "Schloss")}
          ${navButton("alerts", "Alerts")}
          ${navButton("funnels", "Funnels")}
          ${navButton("settings", "Config")}
        </div>

        <nav class="sidebar-nav" aria-label="Primäre Navigation">
          ${navButton("overview", "Betrieb")}
          ${navButton("members", "Mitglieder")}
          ${navButton("windows", "Windows")}
          ${navButton("lock", "Schloss")}
          ${navButton("alerts", "Alerts")}
          ${navButton("funnels", "Funnels")}
          ${navButton("settings", "Einstellungen")}
        </nav>

        <div class="sidebar-footer">
          <p class="eyebrow">// SESSION</p>
          <strong>${escapeHtml(state.me?.email || "")}</strong>
          <div class="row" class="mt-8">${pill(state.me?.role || "unknown")} ${pill("Nuki dry-run")}</div>
          ${current ? `<p class="subtle" class="mt-10">&gt; ${escapeHtml(current.email || `Member ${current.id}`)}</p>` : ""}
        </div>
      </aside>

      <section class="content">
        <header class="topbar">
          <div class="hero-card">
            <div class="hero-copy">
              <div>
                <p class="eyebrow">// OPERATIONS CONSOLE</p>
                <h2 class="hero-title">Twenty4Seven-Gym</h2>
              </div>
              <p class="subtle">Kritische Zustände zuerst. Klare Eingriffe. Mobile Bedienbarkeit ohne Funktionsverlust.</p>
              <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "System bereit. Wähle links einen Bereich oder prüfe die nächsten Freischaltungen.")}</div>
              <div class="hero-actions">
                <button type="button" id="sync-button">Magioline Sync</button>
                <button type="button" id="provision-button" class="secondary">Due Codes</button>
                ${state.role === "admin" ? '<button type="button" class="warn" data-remote-open>Remote Open</button>' : ""}
                <button type="button" id="logout-button" class="secondary">Logout</button>
              </div>
            </div>
            <div class="hero-copy">
              <div>
                <p class="eyebrow">// AKTIVER KONTEXT</p>
                <h3 class="panel-title">${escapeHtml(current?.email || state.me?.email || "Kein Member aktiv")}</h3>
                <p class="subtle">${current ? "Member-Detail geladen" : "Nutze Mitglieder- oder Windows-Sicht für gezielte Aktionen."}</p>
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
  if (state.view === "windows") attachWindowActionHandlers();
  if (state.view === "settings") attachSettingsHandlers();
  if (state.view === "funnels") attachFunnelHandlers();
}

// ================================================================
// NEW /checks PUBLIC SHELL
// ================================================================

function applyChecksSession(session) {
  state.checksSession = session;
  state.checksAttempted = true;
  state.checksWindowId = null;
  state.checksFunnelType = null;
  state.checksFunnel = null;
  state.checksFunnelStep = 0;
  state.checksFunnelDraft = {};
  state.checksLoading = false;
  window.history.replaceState({}, "", `./checks?token=${encodeURIComponent(session.token)}`);
  render();
}

async function resolveChecks(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const session = await api("./public/checks/resolve", {
      method: "POST",
      body: JSON.stringify({
        email: form.get("email"),
        code: String(form.get("code")).trim(),
      }),
    });
    applyChecksSession(session);
  }, "Wird geprüft…");
}

async function loadChecksSessionFromUrl() {
  const token = new URLSearchParams(window.location.search).get("token");
  if (!token) { render(); return; }
  state.checksAttempted = true;
  try {
    const session = await api(`./public/checks/session?token=${encodeURIComponent(token)}`);
    applyChecksSession(session);
  } catch (err) {
    state.checksAttempted = true;
    setMessage(err.message || "Session konnte nicht geladen werden.", "bad");
  }
}

async function startChecksFunnel(windowId, funnelType) {
  if (state.checksLoading) return;
  state.checksLoading = true;
  state.checksWindowId = windowId;
  state.checksFunnelType = funnelType;
  state.checksFunnelStep = 0;
  state.checksFunnelDraft = {};
  state.checksFunnel = null;
  state.checksStepError = null;
  render();
  try {
    const funnel = await api(`./public/checks/funnel/${funnelType}`);
    state.checksFunnel = funnel;
    for (const step of funnel.steps || []) {
      state.checksFunnelDraft[step.id] = { checked: false, note: "" };
    }
  } catch (err) {
    state.checksWindowId = null;
    state.checksFunnelType = null;
    state.checksLoading = false;
    setMessage(err.message || "Funnel konnte nicht geladen werden.", "bad");
    return;
  }
  state.checksLoading = false;
  render();
}

function backToChecksWindowList() {
  state.checksWindowId = null;
  state.checksFunnelType = null;
  state.checksFunnel = null;
  state.checksFunnelStep = 0;
  state.checksFunnelDraft = {};
  state.checksStepError = null;
  render();
}

function updateChecksDraft(stepId, field, value, skipRender = false) {
  if (!state.checksFunnelDraft[stepId]) {
    state.checksFunnelDraft[stepId] = { checked: false, note: "" };
  }
  state.checksFunnelDraft[stepId][field] = value;
  if (!skipRender) render();
}

let _draftDebounce = null;
function updateChecksDraftDebounced(stepId, field, value) {
  if (!state.checksFunnelDraft[stepId]) {
    state.checksFunnelDraft[stepId] = { checked: false, note: "" };
  }
  state.checksFunnelDraft[stepId][field] = value;
  clearTimeout(_draftDebounce);
  _draftDebounce = setTimeout(() => {
    const step = state.checksFunnel?.steps?.find((s) => s.id === stepId);
    if (step) {
      const canProceed = !step.requires_note || (value && value.trim().length > 0);
      const btn = document.getElementById("checks-funnel-next");
      if (btn) btn.disabled = !canProceed;
    }
  }, 120);
}

async function submitChecksFunnel() {
  const steps = Object.entries(state.checksFunnelDraft).map(([stepId, data]) => ({
    step_id: parseInt(stepId),
    checked: data.checked || false,
    note: data.note || "",
  }));
  const result = await api(
    `./public/checks/window/${state.checksWindowId}/${state.checksFunnelType}`,
    {
      method: "POST",
      body: JSON.stringify({
        token: state.checksSession.token,
        window_id: state.checksWindowId,
        funnel_type: state.checksFunnelType,
        steps,
      }),
    },
  );
  const win = state.checksSession.windows.find((w) => w.id === state.checksWindowId);
  if (win) {
    if (result.funnel_type === "checkin") win.checkin_confirmed_at = result.confirmed_at;
    if (result.funnel_type === "checkout") win.checkout_confirmed_at = result.confirmed_at;
  }
  const totalSteps = state.checksFunnel?.steps?.length || 0;
  state.checksFunnelStep = totalSteps + 1;
  render();
}

function checksProgressBar() {
  const steps = state.checksFunnel?.steps || [];
  const total = steps.length;
  const items = [
    { label: "Übersicht", full: "Übersicht", step: 0 },
    ...steps.map((s, i) => ({
      label: s.title,
      full: s.title,
      step: i + 1,
    })),
    { label: "Abschluss", full: "Abschluss", step: total + 1 },
  ];
  return `
    <div class="checks-funnel-progress" aria-label="Fortschritt">
      ${items.map((item) => `
        <div class="checks-progress-step
          ${state.checksFunnelStep === item.step ? "active" : ""}
          ${state.checksFunnelStep > item.step ? "done" : ""}
        " title="${escapeHtml(item.full)}">${state.checksFunnelStep > item.step ? "✓ " : ""}${escapeHtml(item.label)}</div>
      `).join("")}
    </div>
  `;
}

function renderChecksStepOverview() {
  const win = state.checksSession.windows.find((w) => w.id === state.checksWindowId);
  const funnel = state.checksFunnel;
  const funnelLabel = state.checksFunnelType === "checkin" ? "Check-In" : "Check-Out";
  return `
    <section class="detail-card funnel-card">
      <p class="eyebrow">// ${funnelLabel.toUpperCase()} — ÜBERSICHT</p>
      <h2 class="panel-title">Dein Trainingsfenster</h2>
      ${funnel.description ? `<p class="subtle" class="mt-6">${escapeHtml(funnel.description)}</p>` : ""}
      <div class="funnel-overview-grid">
        <div class="summary-item">
          <span class="summary-label">Zeitfenster</span>
          <strong>${fmtDate(win?.starts_at)} → ${fmtDate(win?.ends_at)}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Status</span>
          <strong>${pill(translateStatus(win?.status || "unknown"))}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Schritte</span>
          <strong>${funnel.steps.length}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Typ</span>
          <strong>${escapeHtml(funnelLabel)}</strong>
        </div>
      </div>
      <div class="funnel-actions" class="mt-18">
        <button type="button" id="checks-funnel-back" class="secondary">Zurück</button>
        <button type="button" id="checks-funnel-next">${funnelLabel} starten</button>
      </div>
    </section>
  `;
}

function renderChecksStepN(stepIndex) {
  const step = state.checksFunnel.steps[stepIndex - 1];
  if (!step) return "";
  const draft = state.checksFunnelDraft[step.id] || { checked: false, note: "" };
  const totalSteps = state.checksFunnel.steps.length;
  const isLast = stepIndex === totalSteps;
  const canProceed = step.requires_note
    ? Boolean(draft.note && draft.note.trim().length > 0)
    : Boolean(draft.checked);
  const stepError = state.checksStepError;
  return `
    <section class="detail-card funnel-card">
      <p class="eyebrow">// SCHRITT ${stepIndex} VON ${totalSteps}</p>
      <h2 class="panel-title">${escapeHtml(step.title)}</h2>
      ${step.body ? `<div class="step-body-content" class="mt-12">${escapeHtml(step.body).replace(/\n/g, "<br />")}</div>` : ""}
      ${step.image_path ? `<img class="step-image" src="${escapeHtml(step.image_path)}" alt="${escapeHtml(step.title)}" />` : ""}
      ${step.requires_note ? `
        <div class="mt-14">
          <label for="step-note-${step.id}">Deine Beobachtung (Pflichtfeld)
            <textarea id="step-note-${step.id}" class="step-note-field${stepError ? " input-error" : ""}" data-checks-note="${step.id}"
              placeholder="Beschreibe kurz deine Beobachtung…"
              aria-required="true" aria-describedby="step-note-error-${step.id}">${escapeHtml(draft.note || "")}</textarea>
          </label>
          ${stepError ? `<span id="step-note-error-${step.id}" class="field-error-msg" role="alert" aria-live="polite">${escapeHtml(stepError)}</span>` : ""}
        </div>
      ` : `
        <label class="checkbox-row checkbox-row-block"
          for="checks-check-${step.id}">
          <input id="checks-check-${step.id}" type="checkbox" data-checks-check="${step.id}"
            ${draft.checked ? "checked" : ""} />
          <span>Punkt bestätigt</span>
        </label>
        ${stepError ? `<span class="field-error-msg" role="alert" aria-live="polite">${escapeHtml(stepError)}</span>` : ""}
      `}
      <div class="funnel-actions" class="mt-16">
        <button type="button" id="checks-funnel-back" class="secondary">Zurück</button>
        <button type="button" id="checks-funnel-next"
          ${canProceed ? "" : 'disabled aria-disabled="true"'}
          title="${canProceed ? "" : "Bitte zuerst bestätigen"}">
          ${isLast ? "Abschließen" : "Weiter"}
        </button>
      </div>
    </section>
  `;
}

function renderChecksStepDone() {
  const win = state.checksSession.windows.find((w) => w.id === state.checksWindowId);
  const funnelLabel = state.checksFunnelType === "checkin" ? "Check-In" : "Check-Out";
  const successText = state.checksFunnelType === "checkin"
    ? "Check-In erfolgreich erfasst. Gutes Training!"
    : "Check-Out erfolgreich erfasst. Danke und bis zum nächsten Mal!";
  return `
    <section class="detail-card funnel-card">
      <p class="eyebrow">// ${funnelLabel.toUpperCase()} ABGESCHLOSSEN</p>
      <h2 class="panel-title">${escapeHtml(funnelLabel)} bestätigt.</h2>
      <div class="message good" class="mt-12">${escapeHtml(successText)}</div>
      <div class="funnel-overview-grid" class="mt-14">
        <div class="summary-item">
          <span class="summary-label">Zeitfenster</span>
          <strong>${fmtDate(win?.starts_at)} → ${fmtDate(win?.ends_at)}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Schritte</span>
          <strong>${state.checksFunnel?.steps?.length || 0} abgeschlossen</strong>
        </div>
      </div>
      <div class="funnel-actions" class="mt-16">
        <button type="button" id="checks-funnel-back-list">Zurück zur Übersicht</button>
      </div>
    </section>
  `;
}

function renderChecksWindowList() {
  const session = state.checksSession;
  app.innerHTML = `
    <section class="public-shell">
      <div class="public-hero">
        <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
        <h1 class="hero-title">Hallo, ${escapeHtml(session.member_name?.trim() || "Mitglied")}.</h1>
        <p class="subtle">Deine Trainingsfenster. Starte den Check-In vor dem Training und den Check-Out danach.</p>
        <div class="public-meta">
          <span>${escapeHtml(session.member_email?.trim() || "–")}</span>
          <span>${session.windows.length} Fenster geladen</span>
        </div>
      </div>
      <div class="public-form-wrap">
        ${state.message ? `<div class="message ${state.messageType} mb-14" aria-live="polite">${escapeHtml(state.message)}</div>` : ""}
        ${session.windows.length ? session.windows.map((win) => {
          const checkinDone = !!win.checkin_confirmed_at;
          const checkoutDone = !!win.checkout_confirmed_at;
          const canCheckin = win.has_checkin_funnel && !checkinDone;
          const canCheckout = win.has_checkout_funnel && checkinDone && !checkoutDone;
          return `
            <div class="checks-window-card">
              <div class="split">
                <strong>${fmtDate(win.starts_at)} → ${fmtDate(win.ends_at)}</strong>
                ${pill(translateStatus(win.status))}
              </div>
              <p class="subtle" class="mt-4">${win.booking_count > 1 ? `${win.booking_count} Buchungen · ` : ""}${escapeHtml(win.access_reason)}</p>
              <div class="checks-status-row">
                <div class="checks-status-badge ${checkinDone ? "done" : "pending"}">
                  ${checkinDone ? "✓" : "○"} Check-In
                  ${checkinDone ? `<span>${fmtDate(win.checkin_confirmed_at)}</span>` : ""}
                </div>
                <div class="checks-status-badge ${checkoutDone ? "done" : "pending"}">
                  ${checkoutDone ? "✓" : "○"} Check-Out
                  ${checkoutDone ? `<span>${fmtDate(win.checkout_confirmed_at)}</span>` : ""}
                </div>
              </div>
              <div class="action-group" class="mt-12">
                ${canCheckin
                  ? `<button type="button" class="good" data-checks-checkin data-window-id="${win.id}">Check-In starten</button>`
                  : win.has_checkin_funnel
                    ? `<div class="status-done-badge" aria-label="Check-In abgeschlossen">✓ Check-In abgeschlossen</div>`
                    : ""}
                ${canCheckout
                  ? `<button type="button" class="secondary" data-checks-checkout data-window-id="${win.id}">Check-Out starten</button>`
                  : win.has_checkout_funnel && checkinDone
                    ? `<div class="status-done-badge" aria-label="Check-Out abgeschlossen">✓ Check-Out abgeschlossen</div>`
                    : win.has_checkout_funnel
                      ? `<button type="button" disabled aria-disabled="true" class="secondary" title="Erst Check-In abschließen">Check-Out gesperrt</button>`
                      : ""}
              </div>
            </div>
          `;
        }).join("") : '<div class="empty">Keine aktiven oder bevorstehenden Trainingsfenster</div>'}
      </div>
    </section>
  `;
  document.querySelectorAll("[data-checks-checkin]").forEach((btn) => {
    btn.addEventListener("click", () =>
      startChecksFunnel(parseInt(btn.dataset.windowId), "checkin").catch(handleError));
  });
  document.querySelectorAll("[data-checks-checkout]").forEach((btn) => {
    btn.addEventListener("click", () =>
      startChecksFunnel(parseInt(btn.dataset.windowId), "checkout").catch(handleError));
  });
}

function renderChecksFunnel() {
  const funnel = state.checksFunnel;
  if (!funnel) {
    app.innerHTML = `
      <section class="public-shell">
        <div class="public-hero">
          <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
          <h1 class="hero-title">Funnel wird geladen…</h1>
        </div>
      </section>`;
    return;
  }
  const totalSteps = funnel.steps.length;
  const isDone = state.checksFunnelStep > totalSteps;
  const funnelLabel = state.checksFunnelType === "checkin" ? "Check-In" : "Check-Out";
  let stepContent = isDone
    ? renderChecksStepDone()
    : state.checksFunnelStep === 0
      ? renderChecksStepOverview()
      : renderChecksStepN(state.checksFunnelStep);

  app.innerHTML = `
    <section class="public-shell">
      <div class="public-hero">
        <p class="eyebrow">// TWENTY4SEVEN-GYM — ${escapeHtml(funnelLabel.toUpperCase())}</p>
        <h1 class="hero-title">${escapeHtml(funnel.template_name)}</h1>
        ${checksProgressBar()}
      </div>
      <div class="funnel-step-wrap">${stepContent}</div>
    </section>
  `;

  if (isDone) {
    document.getElementById("checks-funnel-back-list")?.addEventListener("click", backToChecksWindowList);
    return;
  }
  document.getElementById("checks-funnel-next")?.addEventListener("click", () => {
    const step = funnel.steps[state.checksFunnelStep - 1];
    if (step) {
      const draft = state.checksFunnelDraft[step.id] || { checked: false, note: "" };
      if (step.requires_note && !draft.note?.trim()) {
        state.checksStepError = "Bitte beschreibe kurz deine Beobachtung.";
        render();
        document.getElementById(`step-note-${step.id}`)?.focus();
        return;
      }
      if (!step.requires_note && !draft.checked) {
        state.checksStepError = "Bitte bestätige diesen Punkt bevor du weitermachst.";
        render();
        return;
      }
    }
    state.checksStepError = null;
    if (state.checksFunnelStep >= totalSteps) {
      submitChecksFunnel().catch(handleError);
    } else {
      state.checksFunnelStep += 1;
      render();
    }
  });
  document.getElementById("checks-funnel-back")?.addEventListener("click", () => {
    state.checksStepError = null;
    if (state.checksFunnelStep <= 0) {
      backToChecksWindowList();
    } else {
      state.checksFunnelStep -= 1;
      render();
    }
  });
  document.querySelectorAll("[data-checks-note]").forEach((ta) => {
    ta.addEventListener("input", (e) => {
      state.checksStepError = null;
      updateChecksDraftDebounced(parseInt(ta.dataset.checksNote), "note", e.currentTarget.value);
    });
  });
  document.querySelectorAll("[data-checks-check]").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      state.checksStepError = null;
      updateChecksDraft(parseInt(cb.dataset.checksCheck), "checked", e.currentTarget.checked);
    });
  });
}

function renderChecksResolve() {
  app.innerHTML = `
    <section class="public-shell">
      <div class="public-hero">
        <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
        <h1 class="hero-title">Studio Check-In / Check-Out.</h1>
        <p class="subtle">Melde dich mit deiner E-Mail und deinem Zugangscode an.</p>
      </div>
      <div class="public-form-wrap">
        <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "E-Mail und Zugangscode aus deiner Mail eingeben.")}</div>
        <form id="checks-resolve-form" class="stack" class="mt-16">
          <label for="checks-email">E-Mail
            <input id="checks-email" name="email" type="email" autocomplete="email" spellcheck="false" inputmode="email" placeholder="deine@email.de" required />
          </label>
          <label for="checks-code">Zugangscode
            <input id="checks-code" name="code" type="text" autocomplete="one-time-code" inputmode="numeric" spellcheck="false" placeholder="6-stelliger Code aus deiner Mail" required />
          </label>
          <button type="submit">Anmelden</button>
        </form>
      </div>
    </section>
  `;
  document.getElementById("checks-resolve-form")
    ?.addEventListener("submit", (e) => resolveChecks(e).catch(handleError));
}

function renderChecks() {
  if (!state.checksSession) {
    renderChecksResolve();
  } else if (state.checksFunnel !== null && state.checksWindowId !== null) {
    renderChecksFunnel();
  } else {
    renderChecksWindowList();
  }
}

// ================================================================
// ADMIN FUNNEL BUILDER
// ================================================================

async function loadFunnelDetail(id) {
  state.selectedFunnelId = parseInt(id);
  state.stepEditorId = null;
  state.funnelDetail = await api(`./admin/funnels/${id}`);
  render();
}

async function saveFunnelTemplate(event, templateId) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const nameVal = String(form.get("name") || "");
    const slugVal = String(form.get("slug") || "").trim() ||
      nameVal.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const payload = {
      name: nameVal,
      slug: slugVal,
      funnel_type: form.get("funnel_type"),
      description: String(form.get("description") || "") || null,
    };
    let result;
    if (templateId) {
      result = await api(`./admin/funnels/${templateId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      result = await api("./admin/funnels", { method: "POST", body: JSON.stringify(payload) });
    }
    state.funnelsList = await api("./admin/funnels");
    state.selectedFunnelId = result.id;
    state.funnelDetail = await api(`./admin/funnels/${result.id}`);
    setMessage("Funnel gespeichert.", "good");
  }, "Speichere…");
}

async function saveFunnelStep(event, templateId, stepEditorId) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    const payload = {
      template_id: templateId,
      step_order: parseInt(form.get("step_order")) || 1,
      title: form.get("title"),
      body: String(form.get("body") || "") || null,
      image_path: String(form.get("image_path") || "") || null,
      requires_note: form.get("requires_note") === "on",
      requires_photo: form.get("requires_photo") === "on",
    };
    const isNew = !stepEditorId || stepEditorId === "new";
    if (isNew) {
      await api(`./admin/funnels/${templateId}/steps`, { method: "POST", body: JSON.stringify(payload) });
    } else {
      await api(`./admin/funnels/${templateId}/steps/${stepEditorId}`, { method: "PUT", body: JSON.stringify(payload) });
    }
    state.funnelDetail = await api(`./admin/funnels/${templateId}`);
    state.stepEditorId = null;
    setMessage("Schritt gespeichert.", "good");
  }, "Speichere…");
}

async function deleteFunnelStep(templateId, stepId) {
  if (!await showConfirm("Schritt löschen?")) return;
  await api(`./admin/funnels/${templateId}/steps/${stepId}`, { method: "DELETE" });
  state.funnelDetail = await api(`./admin/funnels/${templateId}`);
  state.stepEditorId = null;
  setMessage("Schritt gelöscht.", "good");
  render();
}

function attachFunnelHandlers() {
  document.getElementById("funnel-create-form")
    ?.addEventListener("submit", (e) => saveFunnelTemplate(e, null).catch(handleError));
  document.querySelectorAll("[data-funnel-id]").forEach((btn) => {
    btn.addEventListener("click", () => loadFunnelDetail(btn.dataset.funnelId).catch(handleError));
  });
  document.getElementById("step-add-btn")?.addEventListener("click", () => {
    state.stepEditorId = "new";
    render();
  });
  document.getElementById("step-editor-cancel")?.addEventListener("click", () => {
    state.stepEditorId = null;
    render();
  });
  document.getElementById("step-editor-form")
    ?.addEventListener("submit", (e) =>
      saveFunnelStep(e, state.selectedFunnelId, state.stepEditorId).catch(handleError));
  document.querySelectorAll("[data-step-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.stepEditorId = parseInt(btn.dataset.stepEdit);
      render();
    });
  });
  document.querySelectorAll("[data-step-delete]").forEach((btn) => {
    btn.addEventListener("click", () =>
      deleteFunnelStep(state.selectedFunnelId, parseInt(btn.dataset.stepDelete)).catch(handleError));
  });
  document.getElementById("qr-download-btn")?.addEventListener("click", downloadChecksQr);
  document.getElementById("qr-download-png-medium")?.addEventListener("click", () => downloadChecksQrPng("medium"));
  document.getElementById("qr-download-png-large")?.addEventListener("click", () => downloadChecksQrPng("large"));
  document.getElementById("qr-download-png-print")?.addEventListener("click", () => downloadChecksQrPng("print"));
}

function renderStepEditor() {
  const isNew = !state.stepEditorId || state.stepEditorId === "new";
  const existingStep = isNew ? null
    : state.funnelDetail?.steps?.find((s) => s.id === state.stepEditorId);
  const nextOrder = isNew
    ? Math.max(0, ...(state.funnelDetail?.steps?.map((s) => s.step_order) || [0])) + 1
    : existingStep?.step_order || 1;
  return `
    <section class="step-editor-card">
      <div class="panel-header">
        <h3 class="panel-title">${isNew ? "Neuer Schritt" : "Schritt bearbeiten"}</h3>
        <button type="button" id="step-editor-cancel" class="secondary">Abbrechen</button>
      </div>
      <form id="step-editor-form" class="stack">
        <label for="step-order">Reihenfolge
          <input id="step-order" name="step_order" type="number" inputmode="numeric" value="${nextOrder}" min="1" required />
        </label>
        <label for="step-title">Titel
          <input id="step-title" name="title" type="text" autocomplete="off"
            value="${escapeHtml(existingStep?.title || "")}" placeholder="Schritt-Überschrift" required />
        </label>
        <label for="step-body">Inhalt / Beschreibung
          <textarea id="step-body" name="body"
            placeholder="Text, der dem Member angezeigt wird…">${escapeHtml(existingStep?.body || "")}</textarea>
        </label>
        <label for="step-image">Bild-URL (optional)
          <input id="step-image" name="image_path" type="text" autocomplete="off"
            value="${escapeHtml(existingStep?.image_path || "")}"
            placeholder="/media/uploads/bild.jpg oder https://…" />
        </label>
        <label class="checkbox-row" for="step-requires-note">
          <input id="step-requires-note" name="requires_note" type="checkbox"
            ${existingStep?.requires_note ? "checked" : ""} />
          <span>Notiz erforderlich — Member muss Text eingeben</span>
        </label>
        <label class="checkbox-row" for="step-requires-photo">
          <input id="step-requires-photo" name="requires_photo" type="checkbox"
            ${existingStep?.requires_photo ? "checked" : ""} />
          <span>Foto erforderlich</span>
        </label>
        <button type="submit">${isNew ? "Schritt Anlegen" : "Schritt Speichern"}</button>
      </form>
    </section>
  `;
}

function downloadChecksQr() {
  const dataUri = state.studioLinks?.checks_qr_svg;
  if (!dataUri) return;
  const a = document.createElement("a");
  a.href = dataUri;
  a.download = "t247gym-checks-qr.svg";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function downloadChecksQrPng(size) {
  const link = document.createElement("a");
  link.href = `./admin/system/checks-qr.png?size=${size}`;
  link.download = `t247gym-checks-qr-${size}.png`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function renderFunnelsView() {
  const templates = state.funnelsList || [];
  const detail = state.funnelDetail;
  return `
    <div class="detail-layout">
      <div class="detail-column">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2 class="panel-title">Funnel-Templates</h2>
              <p class="panel-kicker">Check-In und Check-Out Funnels für /checks definieren. Der jeweils neueste pro Typ ist aktiv.</p>
            </div>
          </div>
          <form id="funnel-create-form" class="stack">
            <label for="funnel-name">Name
              <input id="funnel-name" name="name" type="text" autocomplete="off"
                placeholder="z.B. Standard Check-In" required />
            </label>
            <label for="funnel-slug">Slug (auto aus Name wenn leer)
              <input id="funnel-slug" name="slug" type="text" autocomplete="off"
                placeholder="standard-check-in" />
            </label>
            <label for="funnel-type">Typ
              <select id="funnel-type" name="funnel_type">
                <option value="checkin">Check-In</option>
                <option value="checkout">Check-Out</option>
              </select>
            </label>
            <label for="funnel-desc">Beschreibung
              <input id="funnel-desc" name="description" type="text" autocomplete="off"
                placeholder="Optionale Beschreibung…" />
            </label>
            <button type="submit">Funnel Erstellen</button>
          </form>
          <div class="entity-list" class="mt-16">
            ${templates.length ? templates.map((t) => `
              <button type="button"
                class="list-item entity-button ${state.selectedFunnelId === t.id ? "active" : ""}"
                data-funnel-id="${t.id}">
                <div class="split">
                  <strong>${escapeHtml(t.name)}</strong>
                  ${pill(t.funnel_type)}
                </div>
                <p class="subtle">${escapeHtml(t.slug)}</p>
              </button>
            `).join("") : '<div class="empty">Noch keine Funnels angelegt</div>'}
          </div>
        </section>
      </div>

      <div class="detail-column">
        ${detail ? `
          <section class="detail-card">
            <p class="eyebrow">// ${detail.template.funnel_type.toUpperCase()}-FUNNEL</p>
            <h2 class="panel-title">${escapeHtml(detail.template.name)}</h2>
            ${detail.template.description
              ? `<p class="subtle" class="mt-4">${escapeHtml(detail.template.description)}</p>`
              : ""}
            <div class="detail-meta" class="mt-10">
              ${pill(detail.template.funnel_type)}
              <span class="code">${escapeHtml(detail.template.slug)}</span>
              <span>${detail.steps.length} Schritte</span>
            </div>
          </section>

          <section class="detail-card">
            <div class="panel-header">
              <div>
                <h3 class="panel-title">Schritte</h3>
                <p class="panel-kicker">Reihenfolge per step_order. Inhalt: Text, Bild, Notiz-Pflicht.</p>
              </div>
              <button type="button" id="step-add-btn" class="secondary">+ Schritt</button>
            </div>
            <div class="stack">
              ${detail.steps.length ? detail.steps.map((step) => `
                <div class="list-item">
                  <div class="split">
                    <strong>${escapeHtml(step.step_order + ". " + step.title)}</strong>
                    <div class="row">
                      ${step.requires_note ? `<span class="pill info">Notiz</span>` : ""}
                      ${step.requires_photo ? `<span class="pill info">Foto</span>` : ""}
                      ${step.image_path ? `<span class="pill good">Bild</span>` : ""}
                    </div>
                  </div>
                  ${step.body
                    ? `<p class="subtle" class="mt-4">${escapeHtml(step.body.slice(0, 100))}${step.body.length > 100 ? "…" : ""}</p>`
                    : ""}
                  <div class="action-group mt-8">
                    <button type="button" class="secondary" data-step-edit="${step.id}">Bearbeiten</button>
                    <button type="button" class="bad" data-step-delete="${step.id}">Löschen</button>
                  </div>
                </div>
              `).join("") : '<div class="empty">Noch keine Schritte — klicke "+ Schritt"</div>'}
            </div>
          </section>

          ${state.stepEditorId !== null ? renderStepEditor() : ""}
        ` : `
          <section class="detail-card">
            <p class="eyebrow">// FUNNEL EDITOR</p>
            <h2 class="panel-title">Funnel auswählen</h2>
            <p class="subtle">Wähle links einen Funnel um Schritte zu bearbeiten, oder lege einen neuen an.</p>
            <p class="subtle mt-10">Der jeweils neueste Funnel pro Typ ist aktiv auf <a class="code code-link" href="../checks" target="_blank">/checks</a>.</p>
          </section>
        `}

        <section class="detail-card">
          <p class="eyebrow">// ÖFFENTLICHER LINK</p>
          <h3 class="panel-title">Studio Check-In/Out</h3>
          <p class="subtle mt-6">Dieser Link führt Mitglieder direkt zu ihrer Check-In/Check-Out-Seite. QR-Code für Aushang, Flyerdruck oder digitale Kommunikation.</p>
          <div class="studio-link-block mt-12">
            <p class="code studio-link-url">${escapeHtml(state.studioLinks?.checks_url || "–")}</p>
          </div>
          <div class="qr-download-panel mt-14">
            ${state.studioLinks?.checks_qr_svg
              ? `<img class="qr-image-lg" src="${state.studioLinks.checks_qr_svg}" alt="QR-Code für /checks" />`
              : '<div class="empty">QR wird geladen…</div>'
            }
            <div class="qr-download-actions mt-10">
              <div class="action-group">
                <button type="button" id="qr-download-btn" class="secondary" ${state.studioLinks?.checks_qr_svg ? "" : "disabled"}>
                  SVG
                </button>
                <button type="button" id="qr-download-png-medium" class="secondary" ${state.studioLinks?.checks_qr_svg ? "" : "disabled"}>
                  PNG 600 px
                </button>
                <button type="button" id="qr-download-png-large" class="secondary" ${state.studioLinks?.checks_qr_svg ? "" : "disabled"}>
                  PNG 1000 px
                </button>
                <button type="button" id="qr-download-png-print" class="secondary" ${state.studioLinks?.checks_qr_svg ? "" : "disabled"}>
                  PNG 2000 px
                </button>
              </div>
              <p class="subtle mt-6">SVG: Vektorformat, beliebig skalierbar. PNG: für E-Mail (600 px), Aushang (1000 px) oder Druckqualität (2000 px).</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  `;
}

// ================================================================
// MAIN RENDER DISPATCHER
// ================================================================

function render() {
  if (window.location.pathname.endsWith("/checks")) {
    if (
      !state.checksSession
      && !state.checksAttempted
      && new URLSearchParams(window.location.search).get("token")
    ) {
      app.innerHTML = `
        <section class="auth-shell">
          <div class="auth-panel">
            <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
            <h1>Session wird geladen…</h1>
            <p class="subtle">Deine Trainingsfenster werden vorbereitet.</p>
          </div>
        </section>
      `;
      loadChecksSessionFromUrl().catch(handleError);
      return;
    }
    renderChecks();
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
          <p class="eyebrow">// TWENTY4SEVEN-GYM</p>
          <h1>Wird initialisiert…</h1>
          <p class="subtle">Betriebsdaten, Schlossstatus und Alerts werden geladen.</p>
        </div>
      </section>
    `;
    loadAppData().catch((error) => {
      localStorage.removeItem("opengym_token");
      localStorage.removeItem("opengym_role");
      state.token = "";
      state.role = "";
      const isAuthError = /expired|signature|bearer|credentials|inactive/i.test(error.message);
      setMessage(isAuthError ? "Sitzung abgelaufen. Bitte erneut anmelden." : error.message, isAuthError ? "" : "bad");
    });
    return;
  }
  renderApp();
}

render();
