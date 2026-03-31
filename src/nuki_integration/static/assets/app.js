/* ================================================================
   STUDIO ACCESS PLATFORM — SAAS GOLD STANDARD
   Frontend v3.0 — Modular & Widget-Based
   ================================================================ */

const params = new URLSearchParams(window.location.search);

const state = {
  token: localStorage.getItem("opengym_token") || "",
  role: localStorage.getItem("opengym_role") || "",
  me: null,
  members: [],
  windows: [],
  alerts: [],
  actions: [],
  memberDetail: null,
  lockStatus: null,
  lockLog: [],
  emailSettings: null,
  emailTemplate: null,
  telegramSettings: null,
  nukiSettings: null,
  magiclineSettings: null,
  brandingSettings: null,
  studioLinks: null,
  view: params.get("view") || "overview",
  authMode: "login",
  selectedMemberId: params.get("member") || "",
  memberPage: 0,
  memberLimit: 15,
  memberSearch: "",
  message: "",
  messageType: "",
  // Public /checks session
  checksSession: null,
  checksAttempted: false,
  checksWindowId: null,
  checksFunnelType: null,
  checksFunnel: null,
  checksFunnelStep: 0,
  checksFunnelDraft: {},
  checksStepError: null,
  checksLoading: false,
  // Admin Funnel Builder
  funnelsList: [],
  funnelDetail: null,
  selectedFunnelId: null,
  stepEditorId: null,
};

const app = document.getElementById("app");
document.title = "OPEN-GYM Access";

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

// --- API & Utilities ---

function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const isFormData = options.body instanceof FormData;
  if (isFormData) delete headers["Content-Type"];

  return fetch(path, { ...options, headers }).then(async (res) => {
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) throw new Error(data.detail || text || "Request failed");
    return data;
  });
}

function escapeHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function fmtDate(v) {
  if (!v) return "—";
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" }).format(new Date(v));
}

function syncUrlState() {
  const next = new URLSearchParams();
  next.set("view", state.view);
  if (state.selectedMemberId) next.set("member", state.selectedMemberId);
  window.history.replaceState({}, "", `${window.location.pathname}?${next.toString()}`);
}

let _msgTimer = null;
function setMessage(text, type = "") {
  state.message = text;
  state.messageType = type;
  clearTimeout(_msgTimer);
  render();
  if (text && type !== "bad") {
    _msgTimer = setTimeout(() => { state.message = ""; state.messageType = ""; render(); }, 5000);
  }
}

function handleError(err) { setMessage(err.message || "Unbekannter Fehler", "bad"); }

function setPendingState(target, busy, busyLabel = "Wird verarbeitet…") {
  const buttons = target instanceof HTMLElement && target.matches?.("form")
    ? [...target.querySelectorAll('button[type="submit"], button:not([type])')]
    : [target].filter(el => el instanceof HTMLElement);
  buttons.forEach((button) => {
    if (!button) return;
    if (!button.dataset.originalLabel) {
      button.dataset.originalLabel = button.innerHTML || "";
    }
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
    button.innerHTML = busy ? `<span class="spinner" style="width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;display:inline-block;animation:spin 1s linear infinite;margin-right:8px;"></span>${busyLabel}` : button.dataset.originalLabel;
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

function showConfirm(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.style = "position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999;padding:20px;";
    overlay.innerHTML = `
      <div class="widget" style="width:100%;max-width:400px;background:white;">
        <div class="widget-body" style="padding:24px;text-align:center;">
          <p style="margin-bottom:24px;font-size:1.1rem;font-weight:500;">${escapeHtml(message)}</p>
          <div style="display:flex;gap:12px;justify-content:center;">
            <button class="btn btn-secondary" id="confirm-cancel">Abbrechen</button>
            <button class="btn btn-primary" id="confirm-ok" style="background:var(--error);border-color:var(--error);">Bestätigen</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector("#confirm-ok").onclick = () => { overlay.remove(); resolve(true); };
    overlay.querySelector("#confirm-cancel").onclick = () => { overlay.remove(); resolve(false); };
  });
}

// --- UI Helpers & Components ---

function icon(name, size = 20) {
  const icons = {
    overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>',
    members: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    windows: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
    alerts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    funnels: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>',
    sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  };
  return (icons[name] || icons.overview).replace("<svg ", `<svg width="${size}" height="${size}" `);
}

function badge(v) {
  const tone = /active|aktiv|success|online|verbunden|admin|ready|bereit|abgeschlossen/i.test(v) ? "success" :
               /warn|scheduled|operator|pending|flagged|credentials|eingriff|prüfen|entriegelt|falle/i.test(v) ? "warning" :
               /error|fehler|bad|canceled|expired|replaced|failed|inaktiv|offline|fehlend|nicht/i.test(v) ? "error" : "";
  return `<span class="badge ${tone ? `badge-${tone}` : ""}">${escapeHtml(v)}</span>`;
}

function StatCard(label, value, trend, type) {
  return `
    <div class="widget stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value">
        ${escapeHtml(value)}
        ${trend ? `<span class="badge badge-${type}" style="font-size: 0.7rem;">${escapeHtml(trend)}</span>` : ""}
      </div>
    </div>
  `;
}

function Widget(title, content, footer = "") {
  return `
    <div class="widget">
      <div class="widget-header"><h3 class="widget-title">${escapeHtml(title)}</h3></div>
      <div class="widget-body">${content}</div>
      ${footer ? `<div class="widget-footer">${footer}</div>` : ""}
    </div>
  `;
}

function getMemberName(memberId) {
  const m = state.members.find((m) => m.id === memberId);
  if (!m) return `Mitglied #${memberId}`;
  return `${m.first_name || ""} ${m.last_name || ""}`.trim() || m.email || `Mitglied #${memberId}`;
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
  await withPending(trigger || document.body, async () => {
    const result = await api("./admin/remote-open", { method: "POST" });
    setMessage(
      result.dry_run ? "Remote Open im Dry-Run protokolliert." : "Türöffnung ausgelöst.",
      "success",
    );
    await loadAppData();
  }, "Remote Open läuft…");
}

async function sync() {
  await withPending(document.body, async () => {
    await api("./admin/sync", { method: "POST" });
    setMessage("Sync ausgeführt.", "success");
    await loadAppData();
  }, "Sync läuft…");
}

// --- Auth logic ---

async function login(e) {
  e.preventDefault();
  const f = new FormData(e.currentTarget);
  const d = await api("./auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(f)) });
  state.token = d.access_token; state.role = d.role;
  localStorage.setItem("opengym_token", d.access_token);
  localStorage.setItem("opengym_role", d.role);
  await bootstrap();
}

async function logout() {
  localStorage.clear(); location.reload();
}

// --- Admin Views ---

function renderOverview() {
  const urgent = state.alerts.filter(a => a.severity === "error" || a.severity === "warning");
  const next = [...state.windows].filter(w => w.status === "scheduled" || w.status === "active").sort((a,b) => new Date(a.dispatch_at) - new Date(b.dispatch_at))[0];
  
  return `
    <div class="dashboard-grid">
      <div class="span-3">${StatCard("Fenster", state.windows.length, "Gesamt", "success")}</div>
      <div class="span-3">${StatCard("Mitglieder", state.members.length, "Sync", "success")}</div>
      <div class="span-3">${StatCard("Alarme", state.alerts.length, state.alerts.length ? "Prüfen" : "OK", state.alerts.length ? "error" : "success")}</div>
      <div class="span-3">${StatCard("Smartlock", state.lockStatus?.stateName || "—", state.lockStatus?.connectivity, state.lockStatus?.connectivity === "online" ? "success" : "error")}</div>

      <div class="span-8">
        ${Widget("Wichtige Meldungen", `
          <div class="stack">
            ${urgent.length ? urgent.map(a => `
              <div style="display: flex; justify-content: space-between; align-items: center; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); margin-bottom: 8px; background: white;">
                <div><strong>${escapeHtml(a.kind)}</strong><div class="text-muted" style="font-size: 0.8rem;">${fmtDate(a.created_at)}</div><div style="font-size:0.85rem;margin-top:4px;">${escapeHtml(a.message)}</div></div>
                ${badge(a.severity)}
              </div>
            `).join("") : '<div class="empty">Keine kritischen Meldungen.</div>'}
          </div>
        `, `<button class="btn btn-secondary" onclick="state.view='alerts';render()">Alle ansehen</button>`)}
      </div>

      <div class="span-4">
        ${Widget("Nächster Zugang", `
          ${next ? `
            <div class="stack">
              <div style="font-size: 1.25rem; font-weight: 700; margin-bottom: 8px;">${fmtDate(next.dispatch_at).split(",")[1]} Uhr</div>
              <div style="margin-bottom: 12px;">${badge(next.status)}</div>
              <div class="text-muted" style="font-size: 0.9rem;">
                ${escapeHtml(getMemberName(next.member_id))}<br>
                Fenster: #${next.id}
              </div>
            </div>
          ` : '<div class="empty">Keine anstehenden Zeitfenster.</div>'}
        `, `<button class="btn btn-secondary" onclick="state.view='windows';render()">Fenster verwalten</button>`)}
      </div>

      <div class="span-6">
        ${Widget("Fernsteuerung", `
          <div class="stack" style="gap: 16px;">
            <div style="display:flex;justify-content:space-between"><span>Schloss</span><strong>${badge(state.lockStatus?.stateName || "—")}</strong></div>
            <div style="display:flex;justify-content:space-between"><span>Tür (Sensor)</span><strong>${state.lockStatus?.door_state || "Kein Sensor"}</strong></div>
            <div style="display:flex;justify-content:space-between"><span>Batterie</span><strong>${state.lockStatus?.batteryCritical ? '<span style="color:var(--error)">Kritisch</span>' : (state.lockStatus?.battery_state || "Normal")}</strong></div>
            <div class="text-muted" style="font-size:0.75rem;border-top:1px solid var(--border);padding-top:12px;">
              Letztes Update: ${state.lockStatus?.last_update ? fmtDate(state.lockStatus.last_update) : "—"}
            </div>
            <button class="btn btn-primary" onclick="remoteOpen(this)">Studio remote öffnen</button>
          </div>
        `)}
      </div>

      <div class="span-6">
        ${Widget("System-Integrationen", `
          <div class="stack" style="gap: 12px;">
            <div style="display:flex;justify-content:space-between"><span>Magicline API</span>${badge(state.magiclineSettings?.has_api_key ? "Aktiv" : "Inaktiv")}</div>
            <div style="display:flex;justify-content:space-between"><span>Nuki API</span>${badge(state.nukiSettings?.has_api_token ? "Aktiv" : "Inaktiv")}</div>
            <div style="display:flex;justify-content:space-between"><span>E-Mail (SMTP)</span>${badge(state.emailSettings?.smtp_host ? "Bereit" : "Fehlend")}</div>
          </div>
        `)}
      </div>
    </div>
  `;
}

async function loadMemberDetail(id, rerender = true) {
  if (state.selectedMemberId === String(id)) {
    state.selectedMemberId = ""; state.memberDetail = null;
  } else {
    state.selectedMemberId = String(id);
    state.memberDetail = await api(`./admin/members/${id}`);
  }
  syncUrlState();
  if (rerender) render();
}

function renderMembers() {
  return `
    <div class="widget">
      <div class="widget-header">
        <div style="display:flex;gap:12px;width:100%;">
          <div style="position:relative;flex:1;">
            <span style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text-muted);">${icon("search", 16)}</span>
            <input type="text" class="input" style="padding-left:36px;" placeholder="Mitglieder suchen..." value="${state.memberSearch}" oninput="state.memberSearch=this.value;loadMembers()">
          </div>
          <button class="btn btn-secondary" onclick="sync()">${icon("sync", 16)} Magicline Sync</button>
        </div>
      </div>
      <div class="widget-body">
        <div class="stack">
          ${state.members.map(m => {
            const isSelected = state.selectedMemberId === String(m.id);
            const d = isSelected ? state.memberDetail : null;
            return `
              <div class="list-item" style="padding:16px;border:1px solid ${isSelected ? 'var(--primary)' : 'var(--border)'};border-radius:var(--radius-md);margin-bottom:8px;background:white;cursor:pointer;" onclick="loadMemberDetail(${m.id})">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                  <div>
                    <strong style="font-size:1.1rem;">${escapeHtml(m.first_name)} ${escapeHtml(m.last_name)}</strong>
                    <div class="text-muted">${escapeHtml(m.email)}</div>
                  </div>
                  <div style="text-align:right;">
                    ${badge(m.status || "aktiv")}
                    <div style="font-size:0.7rem;margin-top:4px;" class="text-muted">${m.has_xxlarge ? "XX-Large" : "Standard"}</div>
                  </div>
                </div>
                
                ${isSelected && d ? `
                  <div style="margin-top:20px;padding-top:20px;border-top:1px solid var(--border);cursor:default;" onclick="event.stopPropagation()">
                    <div class="dashboard-grid" style="gap:16px;margin-bottom:24px;">
                      <div class="span-4" style="background:var(--bg-main);padding:12px;border-radius:var(--radius-md);">
                        <div class="text-muted" style="font-size:0.75rem;margin-bottom:4px;">Magicline ID</div>
                        <div style="font-weight:600;">${escapeHtml(d.member.magicline_customer_id)}</div>
                      </div>
                      <div class="span-4" style="background:var(--bg-main);padding:12px;border-radius:var(--radius-md);">
                        <div class="text-muted" style="font-size:0.75rem;margin-bottom:4px;">Letzter Sync</div>
                        <div style="font-weight:600;">${fmtDate(d.member.last_synced_at)}</div>
                      </div>
                      <div class="span-4" style="background:var(--bg-main);padding:12px;border-radius:var(--radius-md);">
                        <div class="text-muted" style="font-size:0.75rem;margin-bottom:4px;">Status</div>
                        <div>${badge(d.member.status)}</div>
                      </div>
                    </div>

                    <div class="stack" style="gap:16px;">
                      <h4 style="margin:0;font-size:0.9rem;">Zugangsfenster</h4>
                      ${d.access_windows.map(w => `
                        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg-main);">
                          <div>
                            <div style="font-weight:600;">Fenster #${w.id}</div>
                            <div class="text-muted" style="font-size:0.8rem;">${fmtDate(w.starts_at)} – ${fmtDate(w.ends_at)}</div>
                          </div>
                          <div style="display:flex;gap:8px;align-items:center;">
                            ${badge(w.status)}
                            <div class="dropdown" style="position:relative;">
                              <button class="btn btn-secondary" style="padding:4px 8px;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">Aktionen</button>
                              <div style="display:none;position:absolute;right:0;top:100%;background:white;border:1px solid var(--border);border-radius:var(--radius-md);box-shadow:var(--shadow-md);z-index:10;min-width:160px;">
                                <button class="nav-item" style="padding:8px 12px;font-size:0.85rem;" onclick="runWindowAction(${w.id}, 'resend')">Code neu senden</button>
                                <button class="nav-item" style="padding:8px 12px;font-size:0.85rem;" onclick="runWindowAction(${w.id}, 'emergency-code')">Notfallcode</button>
                                <button class="nav-item" style="padding:8px 12px;font-size:0.85rem;color:var(--error);" onclick="runWindowAction(${w.id}, 'deactivate')">Deaktivieren</button>
                              </div>
                            </div>
                          </div>
                        </div>
                      `).join("") || '<div class="text-muted">Keine Fenster vorhanden.</div>'}
                    </div>
                  </div>
                ` : ""}
              </div>
            `;
          }).join("")}
        </div>
      </div>
      <div class="widget-footer" style="display:flex;justify-content:space-between;align-items:center;">
        <button class="btn btn-secondary" onclick="if(state.memberPage>0){state.memberPage--;loadMembers()}">Zurück</button>
        <span class="text-muted">Seite ${state.memberPage + 1}</span>
        <button class="btn btn-secondary" onclick="state.memberPage++;loadMembers()">Weiter</button>
      </div>
    </div>
  `;
}

async function loadMembers() {
  const query = new URLSearchParams({ limit: state.memberLimit, offset: state.memberPage * state.memberLimit });
  if (state.memberSearch) query.set("email", state.memberSearch);
  state.members = await api(`./admin/members?${query.toString()}`);
  render();
}

function renderWindows() {
  return `
    <div class="widget">
      <div class="widget-header"><h3 class="widget-title">Zugangsfenster (Letzte 50)</h3></div>
      <div class="widget-body">
        <div class="stack">
          ${state.windows.map(w => `
            <div class="list-item" style="padding:16px;border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:8px;background:white;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <div style="font-weight:700;">${escapeHtml(getMemberName(w.member_id))}</div>
                ${badge(w.status)}
              </div>
              <div class="text-muted" style="font-size:0.9rem;margin-bottom:12px;">
                ${fmtDate(w.starts_at)} – ${fmtDate(w.ends_at)}<br>
                Grund: ${escapeHtml(w.access_reason)}
              </div>
              <div style="display:flex;gap:8px;">
                <button class="btn btn-secondary" style="padding:6px 10px;font-size:0.8rem;" onclick="runWindowAction(${w.id}, 'resend')">Code neu</button>
                <button class="btn btn-secondary" style="padding:6px 10px;font-size:0.8rem;" onclick="runWindowAction(${w.id}, 'emergency-code')">Notfall</button>
                <button class="btn btn-secondary" style="padding:6px 10px;font-size:0.8rem;color:var(--error);" onclick="runWindowAction(${w.id}, 'deactivate')">Deaktivieren</button>
              </div>
            </div>
          `).join("") || '<div class="empty">Keine Zugangsfenster.</div>'}
        </div>
      </div>
    </div>
  `;
}

function renderAlerts() {
  return `
    <div class="dashboard-grid">
      <div class="span-6">
        ${Widget("Warnungen & Fehler", `
          <div class="stack">
            ${state.alerts.map(a => `
              <div class="list-item" style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:8px;background:white;">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                  <strong>${escapeHtml(a.kind)}</strong>
                  ${badge(a.severity)}
                </div>
                <div class="text-muted" style="font-size:0.85rem;">${escapeHtml(a.message)}</div>
                <div class="text-muted" style="font-size:0.75rem;margin-top:4px;">${fmtDate(a.created_at)}</div>
              </div>
            `).join("") || '<div class="empty">Keine Alerts.</div>'}
          </div>
        `)}
      </div>
      <div class="span-6">
        ${Widget("Betriebslog", `
          <div class="stack">
            ${state.actions.map(a => `
              <div class="list-item" style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:8px;background:white;">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                  <strong>${escapeHtml(a.action)}</strong>
                  <span class="text-muted" style="font-size:0.75rem;">${fmtDate(a.created_at)}</span>
                </div>
                <div class="text-muted" style="font-size:0.85rem;">${escapeHtml(a.actor_email)}</div>
                <pre style="font-size:0.7rem;background:var(--bg-main);padding:8px;margin-top:8px;border-radius:4px;overflow:auto;">${escapeHtml(JSON.stringify(a.payload))}</pre>
              </div>
            `).join("") || '<div class="empty">Keine Log-Einträge.</div>'}
          </div>
        `)}
      </div>
    </div>
  `;
}

function renderSettings() {
  if (state.role !== "admin") return `<div class="empty">Nur Admins können Einstellungen sehen.</div>`;
  return `
    <div class="stack" style="gap:24px;">
      <div class="dashboard-grid">
        <div class="span-6">
          ${Widget("Magicline API", `
            <form class="stack" style="gap:16px;" onsubmit="updateMagicline(event)">
              <label>API URL <input name="magicline_base_url" class="input" value="${escapeHtml(state.magiclineSettings?.magicline_base_url)}"></label>
              <label>API Key <input name="magicline_api_key" type="password" class="input" placeholder="${state.magiclineSettings?.has_api_key?'Key konfiguriert':''}"></label>
              <label>Studio ID <input name="magicline_studio_id" type="number" class="input" value="${state.magiclineSettings?.magicline_studio_id}"></label>
              <button class="btn btn-primary">Magicline Speichern</button>
            </form>
          `)}
        </div>
        <div class="span-6">
          ${Widget("Nuki Smartlock", `
            <form class="stack" style="gap:16px;" onsubmit="updateNuki(event)">
              <label>API Token <input name="nuki_api_token" type="password" class="input" placeholder="${state.nukiSettings?.has_api_token?'Token konfiguriert':''}"></label>
              <label>Smartlock ID <input name="nuki_smartlock_id" type="number" class="input" value="${state.nukiSettings?.nuki_smartlock_id}"></label>
              <label style="display:flex;align-items:center;gap:8px;"><input name="nuki_dry_run" type="checkbox" ${state.nukiSettings?.nuki_dry_run?'checked':''}> Testmodus (Dry Run)</label>
              <button class="btn btn-primary">Nuki Speichern</button>
            </form>
          `)}
        </div>
        <div class="span-6">
          ${Widget("E-Mail Server (SMTP)", `
            <form class="stack" style="gap:16px;" onsubmit="updateSmtp(event)">
              <label>Host <input name="smtp_host" class="input" value="${escapeHtml(state.emailSettings?.smtp_host)}"></label>
              <div style="display:flex;gap:12px;">
                <label style="flex:1;">Port <input name="smtp_port" type="number" class="input" value="${state.emailSettings?.smtp_port}"></label>
                <label style="flex:1;display:flex;align-items:center;gap:8px;margin-top:24px;"><input name="smtp_use_tls" type="checkbox" ${state.emailSettings?.smtp_use_tls?'checked':''}> TLS</label>
              </div>
              <label>Benutzername <input name="smtp_username" class="input" value="${escapeHtml(state.emailSettings?.smtp_username)}"></label>
              <label>Passwort <input name="smtp_password" type="password" class="input" placeholder="Passwort"></label>
              <label>Absender <input name="smtp_from_email" type="email" class="input" value="${escapeHtml(state.emailSettings?.smtp_from_email)}"></label>
              <button class="btn btn-primary">SMTP Speichern</button>
            </form>
          `)}
        </div>
        <div class="span-6">
          ${Widget("Telegram Alarme", `
            <form class="stack" style="gap:16px;" onsubmit="updateTelegram(event)">
              <label>Bot Token <input name="telegram_bot_token" type="password" class="input" placeholder="${state.telegramSettings?.has_bot_token?'Token konfiguriert':''}"></label>
              <label>Chat ID <input name="telegram_chat_id" class="input" value="${escapeHtml(state.telegramSettings?.telegram_chat_id)}"></label>
              <button class="btn btn-primary">Telegram Speichern</button>
            </form>
          `)}
        </div>
      </div>
      <button class="btn btn-secondary" style="width:100%;" onclick="state.view='branding';render()">Design & Branding anpassen</button>
    </div>
  `;
}

async function updateMagicline(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    await api("./admin/system/magicline-settings", { method: "PUT", body: JSON.stringify(Object.fromEntries(f)) });
    setMessage("Magicline Einstellungen gespeichert.", "success");
    await loadAppData();
  });
}

async function updateNuki(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    const d = Object.fromEntries(f);
    d.nuki_dry_run = d.nuki_dry_run === "on";
    await api("./admin/system/nuki-settings", { method: "PUT", body: JSON.stringify(d) });
    setMessage("Nuki Einstellungen gespeichert.", "success");
    await loadAppData();
  });
}

async function updateSmtp(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    const d = Object.fromEntries(f);
    d.smtp_use_tls = d.smtp_use_tls === "on";
    d.smtp_port = parseInt(d.smtp_port);
    await api("./admin/system/email-settings", { method: "PUT", body: JSON.stringify(d) });
    setMessage("SMTP Einstellungen gespeichert.", "success");
    await loadAppData();
  });
}

async function updateTelegram(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    await api("./admin/system/telegram-settings", { method: "PUT", body: JSON.stringify(Object.fromEntries(f)) });
    setMessage("Telegram Einstellungen gespeichert.", "success");
    await loadAppData();
  });
}

function renderBranding() {
  return `
    <div class="dashboard-grid">
      <div class="span-4">
        ${Widget("Studio Logo", `
          <div style="text-align:center;margin-bottom:20px;">
            ${state.brandingSettings?.logo_url ? `<img src="${state.brandingSettings.logo_url}" style="max-width:100%;max-height:100px;border-radius:4px;">` : '<div class="empty">Kein Logo</div>'}
          </div>
          <input type="file" id="logo-file" style="display:none;" onchange="uploadLogo(this)">
          <button class="btn btn-secondary" style="width:100%;" onclick="document.getElementById('logo-file').click()">Bild hochladen</button>
          <div style="margin-top:16px;">
            <label>Logo Link URL <input id="branding-logo-link" class="input" value="${escapeHtml(state.brandingSettings?.logo_link_url || "")}" onchange="updateBrandingColors()"></label>
          </div>
        `)}
        
        <div style="margin-top:24px;">
          ${Widget("Farben", `
            <div class="stack" style="gap:12px;">
              <div style="display:flex;justify-content:space-between;align-items:center;"><span>Akzent</span><input type="color" id="branding-accent" value="${state.brandingSettings?.accent_color || "#4f46e5"}" onchange="updateBrandingColors()"></div>
              <div style="display:flex;justify-content:space-between;align-items:center;"><span>Header</span><input type="color" id="branding-header" value="${state.brandingSettings?.header_bg_color || "#ffffff"}" onchange="updateBrandingColors()"></div>
              <div style="display:flex;justify-content:space-between;align-items:center;"><span>Background</span><input type="color" id="branding-body" value="${state.brandingSettings?.body_bg_color || "#f9f9f9"}" onchange="updateBrandingColors()"></div>
              <div style="display:flex;justify-content:space-between;align-items:center;"><span>Footer</span><input type="color" id="branding-footer" value="${state.brandingSettings?.footer_bg_color || "#ffffff"}" onchange="updateBrandingColors()"></div>
            </div>
          `)}
        </div>
      </div>

      <div class="span-8">
        ${Widget("E-Mail Vorlage", `
          <div class="stack" style="gap:16px;">
            <p class="text-muted" style="font-size:0.9rem;">Bearbeiten Sie hier das Design der Zugangs-E-Mails.</p>
            <textarea id="tpl-editor" class="input" style="height:300px;font-family:var(--font-mono);font-size:0.85rem;" oninput="updateTplPreview()">${escapeHtml(state.emailTemplate?.access_code_body_html)}</textarea>
            <div style="display:flex;flex-wrap:wrap;gap:8px;">
              <button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px;" onclick="insertTpl('{member_name}')">{member_name}</button>
              <button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px;" onclick="insertTpl('{code}')">{code}</button>
              <button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px;" onclick="insertTpl('{valid_from}')">{valid_from}</button>
              <button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px;" onclick="insertTpl('{valid_until}')">{valid_until}</button>
              <button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px;" onclick="insertTpl('{checks_row}')">{checks_row}</button>
            </div>
            <div style="margin-top:16px;">
              <label>Instagram URL <input id="branding-ig" class="input" value="${escapeHtml(state.brandingSettings?.instagram_url)}"></label>
              <label style="margin-top:12px;display:block;">Facebook URL <input id="branding-fb" class="input" value="${escapeHtml(state.brandingSettings?.facebook_url)}"></label>
              <label style="margin-top:12px;display:block;">Footer Text <textarea id="branding-footer-text" class="input">${escapeHtml(state.brandingSettings?.footer_text)}</textarea></label>
            </div>
            <div style="display:flex;gap:12px;margin-top:16px;">
              <button class="btn btn-primary" style="flex:1;" onclick="saveBranding()">Alles speichern</button>
              <button class="btn btn-secondary" style="flex:1;" onclick="testEmail()">Test-Mail</button>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}

async function uploadLogo(el) {
  const file = el.files[0]; if (!file) return;
  const f = new FormData(); f.append("file", file);
  const { url } = await api("./admin/media/upload", { method: "POST", body: f });
  await api("./admin/system/branding", { method: "PUT", body: JSON.stringify({ logo_url: url }) });
  setMessage("Logo hochgeladen.", "success");
  await loadAppData();
}

async function updateBrandingColors() {
  const p = {
    accent_color: document.getElementById("branding-accent").value,
    header_bg_color: document.getElementById("branding-header").value,
    body_bg_color: document.getElementById("branding-body").value,
    footer_bg_color: document.getElementById("branding-footer").value,
    logo_link_url: document.getElementById("branding-logo-link").value,
  };
  await api("./admin/system/branding", { method: "PUT", body: JSON.stringify(p) });
}

async function saveBranding() {
  await updateBrandingColors();
  const template = { access_code_body_html: document.getElementById("tpl-editor").value };
  await api("./admin/system/email-template", { method: "PUT", body: JSON.stringify(template) });
  const branding = {
    instagram_url: document.getElementById("branding-ig").value,
    facebook_url: document.getElementById("branding-fb").value,
    footer_text: document.getElementById("branding-footer-text").value,
  };
  await api("./admin/system/branding", { method: "PUT", body: JSON.stringify(branding) });
  setMessage("Branding & Template gespeichert.", "success");
  await loadAppData();
}

function insertTpl(tag) {
  const el = document.getElementById("tpl-editor");
  const s = el.selectionStart;
  el.value = el.value.substring(0, s) + tag + el.value.substring(el.selectionEnd);
  el.focus();
}

function renderFunnels() {
  return `
    <div class="widget">
      <div class="widget-header">
        <h3 class="widget-title">Check-in Funnels</h3>
        <button class="btn btn-primary" onclick="createFunnel()">+ Funnel erstellen</button>
      </div>
      <div class="widget-body">
        <div class="stack">
          ${state.funnelsList.map(f => {
            const isSelected = state.selectedFunnelId === f.id;
            return `
              <div class="list-item" style="padding:16px;border:1px solid ${isSelected?'var(--primary)':'var(--border)'};border-radius:var(--radius-md);margin-bottom:8px;background:white;cursor:pointer;" onclick="loadFunnel(${f.id})">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                  <div>
                    <strong style="font-size:1.1rem;">${escapeHtml(f.name)}</strong>
                    <div class="text-muted">${escapeHtml(f.slug)} · ${escapeHtml(f.funnel_type)}</div>
                  </div>
                  ${isSelected ? icon("chevron", 20) : ""}
                </div>
                ${isSelected && state.funnelDetail ? `
                  <div style="margin-top:20px;padding-top:20px;border-top:1px solid var(--border);" onclick="event.stopPropagation()">
                    <div class="stack" style="gap:12px;">
                      ${state.funnelDetail.steps.map(s => `
                        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px;background:var(--bg-main);border-radius:var(--radius-md);">
                          <div>
                            <strong>${s.step_order}. ${escapeHtml(s.title)}</strong>
                            <div class="text-muted" style="font-size:0.8rem;">${s.requires_note?'Notizpflicht':''} ${s.requires_photo?'Fotopflicht':''}</div>
                          </div>
                          <button class="btn btn-secondary" style="padding:4px 8px;" onclick="editStep(${s.id})">Edit</button>
                        </div>
                      `).join("")}
                      <button class="btn btn-secondary" onclick="addStep()">+ Schritt hinzufügen</button>
                    </div>
                  </div>
                ` : ""}
              </div>
            `;
          }).join("") || '<div class="empty">Keine Funnels vorhanden.</div>'}
        </div>
      </div>
    </div>
  `;
}

async function loadFunnel(id) {
  if (state.selectedFunnelId === id) { state.selectedFunnelId = null; state.funnelDetail = null; }
  else {
    state.selectedFunnelId = id;
    state.funnelDetail = await api(`./admin/funnels/${id}`);
  }
  render();
}

// --- Public Views (/checks) ---

function renderChecksResolve() {
  app.innerHTML = `
    <div class="checks-shell" style="justify-content:center; align-items:center;">
      <div class="funnel-container" style="width:100%; max-width:400px;">
        <header style="text-align:center;">
          <div class="brand-logo" style="margin:0 auto 16px;">24</div>
          <h1 class="funnel-title">Studio Check-In</h1>
        </header>
        <form id="checks-resolve-form" class="stack" style="gap:16px;">
          <input name="email" type="email" class="input" placeholder="E-Mail" required />
          <input name="code" type="text" class="input" placeholder="Zugangscode" required style="text-align:center; font-weight:700;" />
          <button type="submit" class="btn btn-primary">Anmelden</button>
        </form>
      </div>
    </div>
  `;
  document.getElementById("checks-resolve-form")?.addEventListener("submit", resolveChecks);
}

async function resolveChecks(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    const session = await api("./public/checks/resolve", { method: "POST", body: JSON.stringify(Object.fromEntries(f)) });
    state.checksSession = session; render();
  });
}

function renderChecksFlow() {
  if (state.checksFunnel && state.checksWindowId) return renderChecksFunnel();
  
  const wins = state.checksSession.access_windows || [];
  app.innerHTML = `
    <div class="checks-shell">
      <div class="funnel-container">
        <header style="text-align:center;">
          <div class="brand-logo" style="margin:0 auto 16px;">24</div>
          <h1 class="funnel-title">Deine Studio-Checkins</h1>
          <p class="text-muted" style="margin-top:8px;">Wähle ein Training aus, um den Check-in zu starten.</p>
        </header>
        
        <div class="stack" style="gap:16px;">
          ${wins.map(w => {
            const isDone = w.check_in_confirmed_at;
            return `
              <div style="padding:16px;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--bg-main);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                  <strong style="font-size:1.1rem;">${fmtDate(w.starts_at).split(",")[1]} Uhr</strong>
                  ${badge(isDone ? "Abgeschlossen" : "Bereit")}
                </div>
                <div class="text-muted" style="font-size:0.9rem;margin-bottom:16px;">${escapeHtml(w.access_reason)}</div>
                ${isDone ? `
                  <div style="color:var(--success);font-weight:600;text-align:center;">✓ Check-in erfolgreich</div>
                ` : `
                  <button class="btn btn-primary" style="width:100%;" onclick="startChecksFunnel(${w.id}, 'checkin')">Check-in starten</button>
                `}
              </div>
            `;
          }).join("") || '<div class="empty">Keine aktiven Fenster gefunden.</div>'}
        </div>
      </div>
    </div>
  `;
}

async function startChecksFunnel(winId, type) {
  state.checksLoading = true; render();
  try {
    const f = await api(`./public/checks/funnel/${type}`);
    state.checksFunnel = f;
    state.checksWindowId = winId;
    state.checksFunnelType = type;
    state.checksFunnelStep = 0;
    state.checksFunnelDraft = {};
    f.steps.forEach(s => state.checksFunnelDraft[s.id] = { checked: false, note: "" });
  } catch (e) { handleError(e); }
  state.checksLoading = false; render();
}

function renderChecksFunnel() {
  const f = state.checksFunnel;
  const step = f.steps[state.checksFunnelStep - 1];
  const isLast = state.checksFunnelStep === f.steps.length;
  
  let content = "";
  if (state.checksFunnelStep === 0) {
    content = `
      <h2 class="funnel-title">${escapeHtml(f.name)}</h2>
      <div class="funnel-body">${escapeHtml(f.description || "Bitte bestätige die folgenden Punkte für deinen Zutritt.")}</div>
      <button class="btn btn-primary" onclick="state.checksFunnelStep++;render()">Verstanden, los geht's</button>
    `;
  } else if (state.checksFunnelStep > f.steps.length) {
    content = `
      <div style="text-align:center;">
        <div style="width:64px;height:64px;background:var(--success-dim);color:var(--success);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 24px;">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <h2 class="funnel-title">Viel Erfolg beim Training!</h2>
        <p class="text-muted">Dein Check-in wurde erfolgreich erfasst. Du kannst das Studio nun betreten.</p>
        <button class="btn btn-secondary" style="margin-top:24px;" onclick="state.checksFunnel=null;render()">Zurück zur Übersicht</button>
      </div>
    `;
  } else {
    const draft = state.checksFunnelDraft[step.id];
    content = `
      <div class="step-indicator">
        ${f.steps.map((_, i) => `<div class="step-dot ${i+1 <= state.checksFunnelStep ? 'active' : ''}"></div>`).join("")}
      </div>
      <h2 class="funnel-title">${escapeHtml(step.title)}</h2>
      ${step.image_path ? `<img src="${step.image_path}" style="width:100%;border-radius:var(--radius-lg);margin:16px 0;">` : ""}
      <div class="funnel-body">${escapeHtml(step.body || "")}</div>
      
      <div class="stack" style="gap:16px;margin-top:24px;">
        ${step.requires_note ? `
          <textarea class="input" placeholder="Deine Anmerkung..." oninput="state.checksFunnelDraft[${step.id}].note=this.value">${escapeHtml(draft.note)}</textarea>
        ` : `
          <label style="display:flex;align-items:center;gap:12px;padding:16px;border:1px solid var(--border);border-radius:var(--radius-md);cursor:pointer;">
            <input type="checkbox" style="width:20px;height:20px;" ${draft.checked?'checked':''} onchange="state.checksFunnelDraft[${step.id}].checked=this.checked;render()">
            <span>Ich bestätige dies.</span>
          </label>
        `}
        <div style="display:flex;gap:12px;">
          <button class="btn btn-secondary" onclick="state.checksFunnelStep--;render()">Zurück</button>
          <button class="btn btn-primary" style="flex:1;" ${(!draft.checked && !step.requires_note) || (step.requires_note && !draft.note.trim()) ? 'disabled' : ''} onclick="onNextStep()">${isLast ? 'Check-in abschließen' : 'Weiter'}</button>
        </div>
      </div>
    `;
  }

  app.innerHTML = `
    <div class="checks-shell">
      <div class="funnel-container">${content}</div>
    </div>
  `;
}

async function onNextStep() {
  if (state.checksFunnelStep < state.checksFunnel.steps.length) {
    state.checksFunnelStep++; render();
  } else {
    await submitChecksFunnel();
  }
}

async function submitChecksFunnel() {
  await withPending(document.querySelector(".btn-primary"), async () => {
    const payload = {
      window_id: state.checksWindowId,
      funnel_type: state.checksFunnelType,
      results: Object.entries(state.checksFunnelDraft).map(([stepId, d]) => ({
        step_id: parseInt(stepId),
        checked: d.checked,
        note: d.note
      }))
    };
    const res = await api("./public/checks/submit", { method: "POST", body: JSON.stringify(payload) });
    state.checksSession = res.session;
    state.checksFunnelStep++;
    render();
  }, "Wird gespeichert…");
}

// --- Main Render & App logic ---

function render() {
  if (window.location.pathname.includes("/checks") || params.has("token")) {
    if (!state.checksSession) renderChecksResolve();
    else renderChecksFlow();
    return;
  }

  if (!state.token) {
    if (params.has("view") && params.get("view") === "reset") renderReset();
    else renderAuth();
    return;
  }

  const viewLabels = { 
    overview: "Dashboard", 
    members: "Mitglieder", 
    windows: "Zugangsfenster", 
    alerts: "System-Alarme",
    settings: "System-Einstellungen",
    branding: "Design & Kommunikation",
    funnels: "Check-in Funnels"
  };
  
  app.innerHTML = `
    <div class="app-container">
      <aside class="sidebar">
        <div class="sidebar-header"><div class="brand-logo">24</div><span class="brand-name">OPEN-GYM</span></div>
        <nav class="sidebar-nav">
          ${navItem("overview", "Dashboard")}
          ${navItem("members", "Mitglieder")}
          ${navItem("windows", "Fenster")}
          ${navItem("alerts", "Alarme")}
          ${navItem("funnels", "Funnels")}
          ${navItem("settings", "Einstellungen")}
        </nav>
        <div class="sidebar-footer">
          <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 12px; padding: 0 12px;">
            Angemeldet als<br>
            <strong style="color: var(--text-main)">${escapeHtml(state.me?.email || "Administrator")}</strong>
          </div>
          <button class="nav-item" onclick="logout()" style="color:var(--error)">${icon("logout", 18)} Abmelden</button>
        </div>
      </aside>
      <main class="main-content" id="app-main">
        <header class="top-nav">
          <div style="display:flex;align-items:center;gap:16px;">
            <button class="btn btn-secondary" style="padding:8px;display:none;" id="sidebar-toggle">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
            </button>
            <h1 class="page-title">${viewLabels[state.view] || "Plattform"}</h1>
          </div>
          <div style="display:flex;gap:12px;align-items:center;">
            ${state.nukiSettings?.nuki_dry_run ? badge("Testmodus") : badge("Live")}
            <button class="btn btn-secondary" onclick="sync()">${icon("sync", 16)} Sync</button>
          </div>
        </header>
        <div class="content-body">
          ${state.message ? `<div class="badge badge-${state.messageType==='bad'?'error':'success'}" style="margin-bottom:24px;padding:12px 16px;display:block;border-radius:var(--radius-md);">${escapeHtml(state.message)}</div>` : ""}
          ${
            state.view === "overview" ? renderOverview() : 
            state.view === "members" ? renderMembers() : 
            state.view === "windows" ? renderWindows() :
            state.view === "alerts" ? renderAlerts() :
            state.view === "settings" ? renderSettings() :
            state.view === "branding" ? renderBranding() :
            state.view === "funnels" ? renderFunnels() :
            "View not implemented"
          }
        </div>
      </main>
    </div>
  `;

  // Attach dynamic listeners
  document.getElementById("sidebar-toggle")?.addEventListener("click", () => {
    document.querySelector(".sidebar").classList.toggle("open");
  });
}

async function loadAppData() {
  try {
    const results = await Promise.allSettled([
      api("./me"),
      api("./admin/access-windows?limit=50"),
      api(`./admin/members?limit=${state.memberLimit}&offset=${state.memberPage * state.memberLimit}${state.memberSearch ? `&email=${encodeURIComponent(state.memberSearch)}` : ""}`),
      api("./admin/alerts?limit=50"),
      api("./admin/admin-actions?limit=50"),
      api("./admin/lock/status"),
      api("./admin/lock/log?limit=50")
    ]);

    if (results[0].status === "fulfilled") state.me = results[0].value;
    if (results[1].status === "fulfilled") state.windows = results[1].value || [];
    if (results[2].status === "fulfilled") state.members = results[2].value || [];
    if (results[3].status === "fulfilled") state.alerts = results[3].value || [];
    if (results[4].status === "fulfilled") state.actions = results[4].value || [];
    if (results[5].status === "fulfilled") state.lockStatus = results[5].value || { stateName: "Offline", connectivity: "offline" };
    if (results[6].status === "fulfilled") state.lockLog = results[6].value || [];

    if (state.role === "admin") {
      const adminResults = await Promise.allSettled([
        api("./admin/system/email-settings"),
        api("./admin/system/email-template"),
        api("./admin/system/telegram-settings"),
        api("./admin/system/nuki-settings"),
        api("./admin/system/magicline-settings"),
        api("./admin/funnels"),
        api("./admin/system/branding")
      ]);
      if (adminResults[0].status === "fulfilled") state.emailSettings = adminResults[0].value;
      if (adminResults[1].status === "fulfilled") state.emailTemplate = adminResults[1].value;
      if (adminResults[2].status === "fulfilled") state.telegramSettings = adminResults[2].value;
      if (adminResults[3].status === "fulfilled") state.nukiSettings = adminResults[3].value;
      if (adminResults[4].status === "fulfilled") state.magiclineSettings = adminResults[4].value;
      if (adminResults[5].status === "fulfilled") state.funnelsList = adminResults[5].value;
      if (adminResults[6].status === "fulfilled") state.brandingSettings = adminResults[6].value;
    }

    if (state.selectedMemberId) {
      await loadMemberDetail(state.selectedMemberId, false);
    }
    render();
  } catch (err) {
    if (err.message.includes("401")) logout();
    else handleError(err);
  }
}

function navItem(view, label) {
  return `
    <button class="nav-item ${state.view===view?'active':''}" onclick="state.view='${view}';syncUrlState();render()">
      <span class="nav-icon">${icon(view, 18)}</span>
      <span>${label}</span>
    </button>
  `;
}

async function testEmail() {
  await withPending(document.body, async () => {
    await api("./admin/system/email-test-code", { method: "POST", body: JSON.stringify({ to_email: state.me.email }) });
    setMessage("Test-Mail gesendet.", "success");
  });
}

function updateTplPreview() {
  // Logic for updating preview would go here if an iframe was used.
  // For now, let's keep it as a placeholder to avoid errors.
}

async function createFunnel() {
  const name = prompt("Name des neuen Funnels:"); if (!name) return;
  const type = prompt("Typ (checkin/checkout):", "checkin"); if (!type) return;
  const res = await api("./admin/funnels", { method: "POST", body: JSON.stringify({ name, funnel_type: type, slug: name.toLowerCase().replace(/ /g, "-") }) });
  state.funnelsList.push(res);
  await loadFunnel(res.id);
}

async function addStep() {
  const title = prompt("Titel des Schritts:"); if (!title) return;
  const body = prompt("Beschreibung:");
  const order = state.funnelDetail.steps.length + 1;
  await api(`./admin/funnels/${state.selectedFunnelId}/steps`, { 
    method: "POST", 
    body: JSON.stringify({ title, body, step_order: order, requires_note: false, requires_photo: false }) 
  });
  state.funnelDetail = await api(`./admin/funnels/${state.selectedFunnelId}`);
  render();
}

async function editStep(stepId) {
  const step = state.funnelDetail.steps.find(s => s.id === stepId);
  const title = prompt("Titel des Schritts:", step.title); if (!title) return;
  const body = prompt("Beschreibung:", step.body);
  const reqNote = confirm("Notizpflicht?");
  await api(`./admin/funnels/${state.selectedFunnelId}/steps/${stepId}`, { 
    method: "PUT", 
    body: JSON.stringify({ title, body, step_order: step.step_order, requires_note: reqNote, requires_photo: false }) 
  });
  state.funnelDetail = await api(`./admin/funnels/${state.selectedFunnelId}`);
  render();
}

async function bootstrap() {
  try {
    if (window.location.pathname.includes("/checks") || params.has("token")) {
      const token = params.get("token");
      if (token) {
        try {
          state.checksSession = await api(`./public/checks/session?token=${encodeURIComponent(token)}`);
        } catch (e) { 
          console.error("Session load failed", e);
          setMessage("Sitzung konnte nicht geladen werden.", "bad");
        }
      }
      render();
      return;
    }
    
    if (state.token) {
      await loadAppData();
    } else {
      render();
    }
  } catch (err) {
    console.error("Bootstrap error", err);
    if (err.message.includes("401")) {
      logout();
    } else {
      render(); // Always render at least the auth screen
    }
  }
}

function renderAuth() {
  const isForgot = state.authMode === "forgot";
  app.innerHTML = `
    <div class="checks-shell" style="justify-content:center; align-items:center;">
      <div class="funnel-container" style="width:100%; max-width:400px;">
        <header style="text-align:center;">
          <div class="brand-logo" style="margin:0 auto 16px;">24</div>
          <h1 class="funnel-title">${isForgot ? 'Passwort vergessen' : 'Admin Login'}</h1>
        </header>
        
        ${state.message ? `<div class="badge badge-${state.messageType==='bad'?'error':'success'}" style="padding:10px;text-align:center;">${escapeHtml(state.message)}</div>` : ""}

        <form id="auth-form" class="stack" style="gap:16px;">
          <input name="email" type="email" class="input" placeholder="E-Mail" required />
          ${!isForgot ? `<input name="password" type="password" class="input" placeholder="Passwort" required />` : ""}
          <button type="submit" class="btn btn-primary">${isForgot ? 'Reset-Link anfordern' : 'Anmelden'}</button>
          <button type="button" class="btn btn-secondary" onclick="state.authMode='${isForgot ? 'login' : 'forgot'}';render()">${isForgot ? 'Zurück zum Login' : 'Passwort vergessen?'}</button>
        </form>
      </div>
    </div>
  `;
  document.getElementById("auth-form")?.addEventListener("submit", isForgot ? forgotPassword : login);
}

async function forgotPassword(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    await api("./auth/forgot-password", { method: "POST", body: JSON.stringify({ email: f.get("email") }) });
    setMessage("Reset-Link wurde versendet.", "success");
  });
}

function renderReset() {
  app.innerHTML = `
    <div class="checks-shell" style="justify-content:center; align-items:center;">
      <div class="funnel-container" style="width:100%; max-width:400px;">
        <h1 class="funnel-title">Passwort neu setzen</h1>
        <form id="reset-form" class="stack" style="gap:16px;" onsubmit="resetPassword(event)">
          <input name="password" type="password" class="input" placeholder="Neues Passwort" required />
          <button type="submit" class="btn btn-primary">Passwort speichern</button>
        </form>
      </div>
    </div>
  `;
}

async function resetPassword(e) {
  e.preventDefault();
  await withPending(e.currentTarget, async () => {
    const f = new FormData(e.currentTarget);
    const token = params.get("token");
    await api("./auth/reset-password", { method: "POST", body: JSON.stringify({ token, password: f.get("password") }) });
    setMessage("Passwort aktualisiert.", "success");
    state.authMode = "login"; render();
  });
}

bootstrap();
