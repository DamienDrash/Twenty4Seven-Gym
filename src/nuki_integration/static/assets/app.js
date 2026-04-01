/* ================================================================
   TWENTY4SEVEN-GYM — Frontend v4.1
   Warm Minimal · Vertical Stepper · Branding Editor · Live Preview
   ================================================================ */

const QS = new URLSearchParams(location.search);

const S = {
  token: localStorage.getItem("t247_token") || "",
  role: localStorage.getItem("t247_role") || "",
  me: null,
  view: QS.get("view") || "overview",
  msg: "", msgType: "",

  members: [], memberPage: 0, memberSearch: "",
  memberDetail: null, selectedMemberId: QS.get("member") || "",
  windows: [], alerts: [], actions: [],

  lockStatus: null,
  emailSettings: null, telegramSettings: null,
  nukiSettings: null, magiclineSettings: null,
  brandingSettings: null, emailTemplate: null,
  funnelsList: [],
  funnelDetail: null, selectedFunnelId: null, stepEditorId: null,

  // /checks
  ck: null, ckFunnel: null, ckWindowId: null, ckFunnelType: null,
  ckStep: 0, ckDraft: {}, ckLoading: false, ckStepError: null,
};

const $ = (s, p) => (p || document).querySelector(s);
const $$ = (s, p) => [...(p || document).querySelectorAll(s)];
const app = document.getElementById("app");

/* ── Helpers ───────────────────────────────────────────────────── */

function api(path, opts = {}) {
  const h = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (S.token) h.Authorization = `Bearer ${S.token}`;
  if (opts.body instanceof FormData) delete h["Content-Type"];
  return fetch(path, { ...opts, headers: h }).then(async r => {
    const t = await r.text();
    const d = t ? JSON.parse(t) : {};
    if (!r.ok) throw new Error(d.detail || t || "Request failed");
    return d;
  });
}

const esc = v => String(v ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function fmtDt(v) {
  if (!v) return "—";
  const d = new Date(v);
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" })
    + ", " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
function fmtTime(v) { return v ? new Date(v).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "—"; }
function fmtDay(v) { return v ? new Date(v).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" }) : "—"; }

let _t;
function toast(text, type = "success") {
  S.msg = text; S.msgType = type; clearTimeout(_t); render();
  if (type !== "error") _t = setTimeout(() => { S.msg = ""; render(); }, 5000);
}

function badge(v) {
  const t = /active|aktiv|success|online|admin|ready|bereit|abgeschlossen|emailed|live/i.test(v) ? "success"
    : /warn|scheduled|operator|pending|flagged|prüfen|entriegelt/i.test(v) ? "warning"
    : /error|canceled|expired|replaced|failed|offline|inaktiv/i.test(v) ? "error"
    : "neutral";
  return `<span class="badge badge-${t}">${esc(v)}</span>`;
}

function memberName(id) {
  const m = S.members.find(m => m.id === id);
  if (!m) return `#${id}`;
  return `${m.first_name || ""} ${m.last_name || ""}`.trim() || m.email || `#${id}`;
}

function syncUrl() {
  const u = new URLSearchParams();
  u.set("view", S.view);
  if (S.selectedMemberId) u.set("member", S.selectedMemberId);
  history.replaceState({}, "", `${location.pathname}?${u}`);
}

function confirmDialog(msg) {
  return new Promise(res => {
    const el = document.createElement("div");
    el.className = "overlay";
    el.innerHTML = `<div class="dialog"><div class="dialog-title">Bestätigung</div><div class="dialog-text">${esc(msg)}</div><div class="dialog-actions"><button class="btn btn-outline" id="c-no">Abbrechen</button><button class="btn btn-accent" id="c-yes">Bestätigen</button></div></div>`;
    document.body.appendChild(el);
    $("#c-yes", el).onclick = () => { el.remove(); res(true); };
    $("#c-no", el).onclick = () => { el.remove(); res(false); };
  });
}

async function withBtn(btn, task, label = "…") {
  if (!btn) return task();
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span style="width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;display:inline-block;animation:spin .8s linear infinite;margin-right:6px;"></span>${label}`;
  try { return await task(); } finally { btn.disabled = false; btn.innerHTML = orig; }
}

/* ── SVG Icons ─────────────────────────────────────────────────── */
const ICONS = {
  overview: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
  members: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  windows: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  alerts: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  funnels: '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.604.852.997 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  branding: '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  sync: '<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  menu: '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  dots: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
};
function ico(name, size = 18) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
}

/* ═══════════════════════════════════════════════════════════════
   AUTH
   ═══════════════════════════════════════════════════════════════ */

function renderAuth() {
  const forgot = S.view === "forgot";
  app.innerHTML = `<div class="auth-shell"><div class="auth-card">
    <div class="checks-brand"><div class="checks-brand-mark">24</div><div class="checks-title">${forgot ? "Passwort vergessen" : "Admin Login"}</div><div class="checks-subtitle">Twenty4Seven-Gym Betriebskonsole</div></div>
    ${S.msg ? `<div class="toast toast-${S.msgType === "error" ? "error" : "success"}">${esc(S.msg)}</div>` : ""}
    <form id="auth-form" style="display:flex;flex-direction:column;gap:14px;">
      <div class="field"><label>E-Mail</label><input name="email" type="email" class="input" required></div>
      ${!forgot ? '<div class="field"><label>Passwort</label><input name="password" type="password" class="input" required></div>' : ""}
      <button type="submit" class="btn btn-dark btn-block btn-lg">${forgot ? "Reset-Link senden" : "Anmelden"}</button>
      <button type="button" class="btn btn-ghost btn-block" onclick="S.view='${forgot ? "login" : "forgot"}';render()">${forgot ? "Zurück zum Login" : "Passwort vergessen?"}</button>
    </form>
  </div></div>`;
  $("#auth-form").addEventListener("submit", forgot ? doForgot : doLogin);
}

async function doLogin(e) {
  e.preventDefault();
  await withBtn($('button[type="submit"]', e.target), async () => {
    const f = Object.fromEntries(new FormData(e.target));
    const d = await api("./auth/login", { method: "POST", body: JSON.stringify(f) });
    S.token = d.access_token; S.role = d.role;
    localStorage.setItem("t247_token", d.access_token);
    localStorage.setItem("t247_role", d.role);
    await bootstrap();
  }, "Anmelden…");
}

async function doForgot(e) {
  e.preventDefault();
  await withBtn($('button[type="submit"]', e.target), async () => {
    await api("./auth/forgot-password", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
    toast("Reset-Link gesendet");
  }, "Senden…");
}

function doLogout() { localStorage.clear(); location.reload(); }

/* ═══════════════════════════════════════════════════════════════
   ADMIN VIEWS
   ═══════════════════════════════════════════════════════════════ */

function nav(view, label, icon) {
  return `<button class="nav-btn ${S.view === view ? "active" : ""}" onclick="S.view='${view}';syncUrl();render()">${ico(icon || view)} ${label}</button>`;
}

function renderAdmin() {
  const views = {
    overview: renderOverview, members: renderMembers, windows: renderWindows,
    alerts: renderAlerts, funnels: renderFunnels, branding: renderBranding, settings: renderSettings,
  };
  const titles = {
    overview: "Dashboard", members: "Mitglieder", windows: "Zugangsfenster",
    alerts: "Alarme & Audit", funnels: "Check-in Funnels", branding: "Design & E-Mail", settings: "Einstellungen",
  };

  app.innerHTML = `
    <div class="sidebar-overlay" onclick="$('.sidebar').classList.remove('open');this.classList.remove('open')"></div>
    <div class="app-layout">
      <aside class="sidebar">
        <div class="sidebar-brand"><div class="sidebar-brand-mark">24</div><span class="sidebar-brand-name">OPEN-GYM</span></div>
        <nav class="sidebar-nav">
          <div class="sidebar-section-label">Betrieb</div>
          ${nav("overview", "Dashboard", "overview")}
          ${nav("members", "Mitglieder", "members")}
          ${nav("windows", "Fenster", "windows")}
          <div class="sidebar-section-label">System</div>
          ${nav("alerts", "Alarme", "alerts")}
          ${nav("funnels", "Funnels", "funnels")}
          ${nav("branding", "Design", "branding")}
          ${nav("settings", "Einstellungen", "settings")}
        </nav>
        <div class="sidebar-footer">
          <div class="sidebar-user">Angemeldet als<strong>${esc(S.me?.email || "")}</strong></div>
          <button class="nav-btn" onclick="doLogout()" style="color:var(--error)">${ico("logout")} Abmelden</button>
        </div>
      </aside>
      <main class="main-area">
        <header class="topbar">
          <div style="display:flex;align-items:center;gap:12px;">
            <button class="sidebar-toggle" onclick="$('.sidebar').classList.toggle('open');$('.sidebar-overlay').classList.toggle('open')">${ico("menu")}</button>
            <span class="topbar-title">${titles[S.view] || ""}</span>
          </div>
          <div class="topbar-actions">
            ${S.nukiSettings?.nuki_dry_run ? badge("Testmodus") : badge("Live")}
            <button class="btn btn-outline btn-sm" onclick="doSync(this)">${ico("sync", 14)} Sync</button>
          </div>
        </header>
        <div class="page-body">
          ${S.msg ? `<div class="toast toast-${S.msgType === "error" ? "error" : "success"}">${esc(S.msg)}</div>` : ""}
          ${(views[S.view] || renderOverview)()}
        </div>
      </main>
    </div>`;

  // Post-render hooks
  if (S.view === "branding") setTimeout(initBrandingEditor, 50);
  if (S.view === "funnels") attachFunnelHandlers();
}

async function doSync(btn) {
  await withBtn(btn, async () => { await api("./admin/sync", { method: "POST" }); toast("Magicline Sync abgeschlossen"); await loadData(); }, "Sync…");
}

/* ── Overview ──────────────────────────────────────────────────── */

function renderOverview() {
  const active = S.windows.filter(w => w.status === "scheduled" || w.status === "active");
  const next = active.sort((a, b) => new Date(a.dispatch_at) - new Date(b.dispatch_at))[0];
  const urgent = S.alerts.filter(a => a.severity === "error" || a.severity === "warning").slice(0, 5);
  const ls = S.lockStatus || {};
  return `<div class="grid grid-12">
    <div class="col-3"><div class="card stat"><div class="stat-label">Fenster</div><div class="stat-value">${S.windows.length}</div><div class="stat-sub">Gesamt</div></div></div>
    <div class="col-3"><div class="card stat"><div class="stat-label">Mitglieder</div><div class="stat-value">${S.members.length}</div><div class="stat-sub">Synchronisiert</div></div></div>
    <div class="col-3"><div class="card stat"><div class="stat-label">Alarme</div><div class="stat-value">${S.alerts.length}</div><div class="stat-sub">${S.alerts.length ? "Prüfen" : "Alles OK"}</div></div></div>
    <div class="col-3"><div class="card stat"><div class="stat-label">Schloss</div><div class="stat-value" style="font-size:16px;">${badge(ls.stateName || "—")}</div><div class="stat-sub">${ls.battery_state || "—"}</div></div></div>
    <div class="col-8"><div class="card"><div class="card-header"><span class="card-title">Aktuelle Meldungen</span></div><div class="card-body" style="padding:0;">
      ${urgent.length ? urgent.map(a => `<div class="list-row"><div><strong style="font-size:13px;">${esc(a.kind)}</strong><div style="font-size:12px;color:var(--text-muted);margin-top:2px;">${esc(a.message).slice(0, 120)}</div></div><div style="text-align:right;">${badge(a.severity)}<div style="font-size:11px;color:var(--text-muted);margin-top:4px;">${fmtDt(a.created_at)}</div></div></div>`).join("") : '<div class="empty">Keine kritischen Meldungen</div>'}
    </div></div></div>
    <div class="col-4"><div class="card"><div class="card-header"><span class="card-title">Nächster Zugang</span></div><div class="card-body">
      ${next ? `<div style="text-align:center;"><div style="font-size:24px;font-weight:800;margin-bottom:4px;">${fmtTime(next.dispatch_at)}</div><div style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">${fmtDay(next.dispatch_at)}</div>${badge(next.status)}<div style="margin-top:12px;font-size:13px;">${esc(memberName(next.member_id))}</div></div>` : '<div class="empty">Kein anstehender Zugang</div>'}
    </div><div class="card-footer" style="text-align:center;"><button class="btn btn-outline btn-sm" onclick="S.view='windows';syncUrl();render()">Alle Fenster</button></div></div></div>
    <div class="col-6"><div class="card"><div class="card-header"><span class="card-title">Fernsteuerung</span></div><div class="card-body" style="display:flex;flex-direction:column;gap:12px;">
      <div style="display:flex;justify-content:space-between;"><span>Schloss</span><strong>${badge(ls.stateName || "—")}</strong></div>
      <div style="display:flex;justify-content:space-between;"><span>Tür</span><strong>${esc(ls.door_state || "Kein Sensor")}</strong></div>
      <div style="display:flex;justify-content:space-between;"><span>Batterie</span><strong>${ls.batteryCritical ? '<span style="color:var(--error)">Kritisch!</span>' : esc(ls.battery_state || "—")}</strong></div>
      <button class="btn btn-dark btn-block" onclick="doRemoteOpen(this)" style="margin-top:8px;">${ico("lock", 16)} Remote öffnen</button>
    </div></div></div>
    <div class="col-6"><div class="card"><div class="card-header"><span class="card-title">Integrationen</span></div><div class="card-body" style="display:flex;flex-direction:column;gap:12px;">
      <div style="display:flex;justify-content:space-between;"><span>Magicline</span>${badge(S.magiclineSettings?.has_api_key ? "Aktiv" : "Fehlt")}</div>
      <div style="display:flex;justify-content:space-between;"><span>Nuki</span>${badge(S.nukiSettings?.has_api_token ? "Aktiv" : "Fehlt")}</div>
      <div style="display:flex;justify-content:space-between;"><span>E-Mail</span>${badge(S.emailSettings?.smtp_host ? "Bereit" : "Fehlt")}</div>
      <div style="display:flex;justify-content:space-between;"><span>Telegram</span>${badge(S.telegramSettings?.has_bot_token ? "Aktiv" : "Inaktiv")}</div>
    </div></div></div>
  </div>`;
}

async function doRemoteOpen(btn) {
  if (!await confirmDialog("Türöffnung jetzt auslösen?")) return;
  await withBtn(btn, async () => { const r = await api("./admin/remote-open", { method: "POST" }); toast(r.dry_run ? "Dry-Run: Remote Open protokolliert" : "Türöffnung ausgelöst"); await loadData(); }, "Öffne…");
}

/* ── Members ───────────────────────────────────────────────────── */

function renderMembers() {
  return `<div class="card">
    <div class="card-header" style="gap:12px;"><div style="position:relative;flex:1;"><span style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text-muted);pointer-events:none;">${ico("search", 16)}</span><input class="input" style="padding-left:36px;" placeholder="E-Mail suchen…" value="${esc(S.memberSearch)}" oninput="S.memberSearch=this.value;S.memberPage=0;loadMembers()"></div></div>
    <div class="card-body" style="padding:0;">
      ${S.members.map(m => {
        const sel = S.selectedMemberId === String(m.id);
        const d = sel ? S.memberDetail : null;
        return `<div class="list-row list-row-clickable" style="flex-direction:column;align-items:stretch;${sel ? "background:var(--accent-dim);border-left:3px solid var(--accent);" : ""}" onclick="toggleMember(${m.id})">
          <div style="display:flex;justify-content:space-between;align-items:center;"><div><strong>${esc(m.first_name)} ${esc(m.last_name)}</strong><div style="font-size:12px;color:var(--text-muted);">${esc(m.email)}</div></div><div style="text-align:right;">${badge(m.status || "aktiv")}<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${m.has_xxlarge ? "XXLARGE" : "Standard"}</div></div></div>
          ${sel && d ? renderMemberExpanded(d) : ""}
        </div>`;
      }).join("") || '<div class="empty">Keine Mitglieder gefunden</div>'}
    </div>
    <div class="card-footer" style="display:flex;justify-content:space-between;align-items:center;">
      <button class="btn btn-outline btn-sm" onclick="if(S.memberPage>0){S.memberPage--;loadMembers()}" ${S.memberPage === 0 ? "disabled" : ""}>Zurück</button>
      <span style="font-size:12px;color:var(--text-muted);">Seite ${S.memberPage + 1}</span>
      <button class="btn btn-outline btn-sm" onclick="S.memberPage++;loadMembers()">Weiter</button>
    </div>
  </div>`;
}

function renderMemberExpanded(d) {
  return `<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);" onclick="event.stopPropagation()">
    <div class="grid grid-3" style="margin-bottom:20px;">
      <div style="background:var(--bg);padding:12px;border-radius:var(--radius);"><div style="font-size:11px;color:var(--text-muted);margin-bottom:2px;">Magicline ID</div><div style="font-weight:700;">${esc(d.member.magicline_customer_id)}</div></div>
      <div style="background:var(--bg);padding:12px;border-radius:var(--radius);"><div style="font-size:11px;color:var(--text-muted);margin-bottom:2px;">Letzter Sync</div><div style="font-weight:600;font-size:12px;">${fmtDt(d.member.last_synced_at)}</div></div>
      <div style="background:var(--bg);padding:12px;border-radius:var(--radius);"><div style="font-size:11px;color:var(--text-muted);margin-bottom:2px;">Entitlement</div><div>${d.member.has_xxlarge ? badge("XXLARGE") : badge("Standard")}</div></div>
    </div>
    <div style="font-weight:700;font-size:13px;margin-bottom:10px;">Zugangsfenster</div>
    ${d.access_windows.map(w => `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:var(--bg);border-radius:var(--radius);margin-bottom:6px;">
      <div><span style="font-weight:600;">Fenster #${w.id}</span><div style="font-size:12px;color:var(--text-muted);">${fmtDt(w.starts_at)} – ${fmtDt(w.ends_at)}</div></div>
      <div style="display:flex;gap:6px;align-items:center;">${badge(w.status)}
        <div class="dropdown"><button class="btn btn-outline btn-sm btn-icon" onclick="event.stopPropagation();this.nextElementSibling.classList.toggle('open')">${ico("dots", 14)}</button>
          <div class="dropdown-menu"><button class="dropdown-item" onclick="windowAction(${w.id},'resend')">Code neu senden</button><button class="dropdown-item" onclick="windowAction(${w.id},'emergency-code')">Notfallcode</button><button class="dropdown-item dropdown-item-danger" onclick="windowAction(${w.id},'deactivate')">Deaktivieren</button></div></div></div>
    </div>`).join("") || '<div style="font-size:13px;color:var(--text-muted);">Keine Fenster</div>'}
  </div>`;
}

async function toggleMember(id) {
  if (S.selectedMemberId === String(id)) { S.selectedMemberId = ""; S.memberDetail = null; }
  else { S.selectedMemberId = String(id); S.memberDetail = await api(`./admin/members/${id}`); }
  syncUrl(); render();
}

async function loadMembers() {
  const q = new URLSearchParams({ limit: 15, offset: S.memberPage * 15 });
  if (S.memberSearch) q.set("email", S.memberSearch);
  S.members = await api(`./admin/members?${q}`); render();
}

const ACTION_COPY = { resend: { ok: "Code versendet", confirm: "" }, deactivate: { ok: "Fenster deaktiviert", confirm: "Dieses Fenster jetzt deaktivieren?" }, "emergency-code": { ok: "Notfallcode erzeugt", confirm: "Einmaligen Notfallcode erzeugen?" } };

async function windowAction(id, action) {
  const c = ACTION_COPY[action] || { ok: "OK", confirm: "" };
  if (c.confirm && !await confirmDialog(c.confirm)) return;
  await api(`./admin/access-windows/${id}/${action}`, { method: "POST" }); toast(c.ok); await loadData();
}

/* ── Windows / Alerts ──────────────────────────────────────────── */

function renderWindows() {
  return `<div class="card"><div class="card-header"><span class="card-title">Alle Zugangsfenster</span></div><div class="card-body" style="padding:0;">
    ${S.windows.map(w => `<div class="list-row"><div><strong>${esc(memberName(w.member_id))}</strong><div style="font-size:12px;color:var(--text-muted);">${fmtDt(w.starts_at)} – ${fmtDt(w.ends_at)}</div></div><div style="display:flex;gap:8px;align-items:center;">${badge(w.status)}<div class="dropdown"><button class="btn btn-outline btn-sm btn-icon" onclick="this.nextElementSibling.classList.toggle('open')">${ico("dots", 14)}</button><div class="dropdown-menu"><button class="dropdown-item" onclick="windowAction(${w.id},'resend')">Code neu</button><button class="dropdown-item" onclick="windowAction(${w.id},'emergency-code')">Notfall</button><button class="dropdown-item dropdown-item-danger" onclick="windowAction(${w.id},'deactivate')">Deaktivieren</button></div></div></div></div>`).join("") || '<div class="empty">Keine Zugangsfenster</div>'}
  </div></div>`;
}

function renderAlerts() {
  return `<div class="grid grid-2">
    <div class="card"><div class="card-header"><span class="card-title">Alarme</span></div><div class="card-body" style="padding:0;max-height:600px;overflow-y:auto;">
      ${S.alerts.map(a => `<div class="list-row" style="flex-direction:column;align-items:stretch;"><div style="display:flex;justify-content:space-between;"><strong style="font-size:13px;">${esc(a.kind)}</strong>${badge(a.severity)}</div><div style="font-size:12px;color:var(--text-muted);margin-top:4px;">${esc(a.message).slice(0, 200)}</div><div style="font-size:11px;color:var(--text-muted);margin-top:4px;">${fmtDt(a.created_at)}</div></div>`).join("") || '<div class="empty">Keine Alarme</div>'}
    </div></div>
    <div class="card"><div class="card-header"><span class="card-title">Admin-Aktionen</span></div><div class="card-body" style="padding:0;max-height:600px;overflow-y:auto;">
      ${S.actions.map(a => `<div class="list-row" style="flex-direction:column;align-items:stretch;"><div style="display:flex;justify-content:space-between;"><strong style="font-size:13px;">${esc(a.action)}</strong><span style="font-size:11px;color:var(--text-muted);">${fmtDt(a.created_at)}</span></div><div style="font-size:12px;color:var(--text-muted);margin-top:2px;">${esc(a.actor_email)}</div></div>`).join("") || '<div class="empty">Keine Aktionen</div>'}
    </div></div>
  </div>`;
}

/* ── Funnels ───────────────────────────────────────────────────── */

function renderFunnels() {
  return `<div class="grid grid-2">
    <div class="card"><div class="card-header"><span class="card-title">Funnel-Templates</span><button class="btn btn-accent btn-sm" onclick="createFunnel()">+ Neuer Funnel</button></div>
      <div class="card-body" style="padding:0;">
        ${S.funnelsList.map(f => `<div class="list-row list-row-clickable ${S.selectedFunnelId === f.id ? "active" : ""}" data-funnel-id="${f.id}" style="${S.selectedFunnelId === f.id ? "background:var(--accent-dim);border-left:3px solid var(--accent);" : ""}"><div><strong>${esc(f.name)}</strong><div style="font-size:12px;color:var(--text-muted);">${esc(f.funnel_type)} · ${esc(f.slug)}</div></div>${badge(f.funnel_type)}</div>`).join("") || '<div class="empty">Noch keine Funnels</div>'}
      </div>
    </div>
    <div>
      ${S.funnelDetail ? renderFunnelDetail() : '<div class="card"><div class="card-body"><div class="empty">Funnel links auswählen oder neu erstellen</div></div></div>'}
    </div>
  </div>`;
}

function renderFunnelDetail() {
  const d = S.funnelDetail;
  return `
    <div class="card" style="margin-bottom:16px;"><div class="card-header"><span class="card-title">${esc(d.template.name)}</span>${badge(d.template.funnel_type)}</div>
      <div class="card-body">
        ${d.template.description ? `<p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">${esc(d.template.description)}</p>` : ""}
        <div style="font-size:12px;color:var(--text-muted);">Slug: <code>${esc(d.template.slug)}</code> · ${d.steps.length} Schritte</div>
      </div>
    </div>
    <div class="card"><div class="card-header"><span class="card-title">Schritte</span><button class="btn btn-accent btn-sm" id="step-add-btn">+ Schritt</button></div>
      <div class="card-body" style="padding:0;">
        ${d.steps.map(s => `<div class="list-row" style="flex-direction:column;align-items:stretch;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div><strong>${s.step_order}. ${esc(s.title)}</strong>
              ${s.step_type && s.step_type !== "confirmation" ? `<span class="step-type-badge step-type-${s.step_type}">${_stepTypeLabel(s.step_type)}</span>` : ""}
            </div>
            <div style="display:flex;gap:4px;">
              ${s.requires_note ? badge("Notiz") : ""} ${s.is_mandatory === false ? badge("Optional") : ""}
              <button class="btn btn-outline btn-sm" data-step-edit="${s.id}">Edit</button>
              <button class="btn btn-outline btn-sm" style="color:var(--error)" data-step-delete="${s.id}">×</button>
            </div>
          </div>
          ${s.body ? `<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">${esc(s.body.slice(0, 100))}${s.body.length > 100 ? "…" : ""}</div>` : ""}
        </div>`).join("") || '<div class="empty">Noch keine Schritte</div>'}
      </div>
    </div>
    ${S.stepEditorId !== null ? renderStepEditor() : ""}`;
}

function renderStepEditor() {
  const isNew = !S.stepEditorId || S.stepEditorId === "new";
  const ex = isNew ? null : S.funnelDetail?.steps?.find(s => s.id === S.stepEditorId);
  const nextOrder = isNew ? Math.max(0, ...(S.funnelDetail?.steps?.map(s => s.step_order) || [0])) + 1 : ex?.step_order || 1;
  return `<div class="card" style="margin-top:16px;border-color:var(--accent);"><div class="card-header"><span class="card-title">${isNew ? "Neuer Schritt" : "Schritt bearbeiten"}</span><button class="btn btn-ghost btn-sm" id="step-editor-cancel">Abbrechen</button></div>
    <div class="card-body"><form id="step-editor-form" style="display:flex;flex-direction:column;gap:12px;">
      <div class="grid grid-2">
        <div class="field"><label>Reihenfolge</label><input name="step_order" type="number" class="input" value="${nextOrder}" min="1" required></div>
        <div class="field"><label>Typ</label><select name="step_type" class="input"><option value="confirmation" ${ex?.step_type === "confirmation" ? "selected" : ""}>Bestätigung</option><option value="house_rules" ${ex?.step_type === "house_rules" ? "selected" : ""}>Hausordnung</option><option value="text" ${ex?.step_type === "text" ? "selected" : ""}>Text</option><option value="image" ${ex?.step_type === "image" ? "selected" : ""}>Bild</option><option value="video" ${ex?.step_type === "video" ? "selected" : ""}>Video</option></select></div>
      </div>
      <div class="field"><label>Titel</label><input name="title" class="input" value="${esc(ex?.title || "")}" required></div>
      <div class="field"><label>Inhalt / Beschreibung</label><textarea name="body" class="input" rows="4">${esc(ex?.body || "")}</textarea></div>
      <div class="grid grid-2">
        <div class="field"><label>Bild-URL</label><input name="image_path" class="input" value="${esc(ex?.image_path || "")}"></div>
        <div class="field"><label>Video-URL</label><input name="video_url" class="input" value="${esc(ex?.video_url || "")}" placeholder="YouTube/Vimeo Link"></div>
      </div>
      <div style="display:flex;gap:16px;flex-wrap:wrap;">
        <label style="display:flex;align-items:center;gap:6px;"><input name="is_mandatory" type="checkbox" ${ex?.is_mandatory !== false ? "checked" : ""}> Pflichtschritt</label>
        <label style="display:flex;align-items:center;gap:6px;"><input name="requires_note" type="checkbox" ${ex?.requires_note ? "checked" : ""}> Notiz erforderlich</label>
        <label style="display:flex;align-items:center;gap:6px;"><input name="requires_photo" type="checkbox" ${ex?.requires_photo ? "checked" : ""}> Foto erforderlich</label>
      </div>
      <button type="submit" class="btn btn-accent">${isNew ? "Schritt anlegen" : "Speichern"}</button>
    </form></div></div>`;
}

function attachFunnelHandlers() {
  $$("[data-funnel-id]").forEach(el => el.addEventListener("click", () => loadFunnelDetail(el.dataset.funnelId)));
  $("#step-add-btn")?.addEventListener("click", () => { S.stepEditorId = "new"; render(); });
  $("#step-editor-cancel")?.addEventListener("click", () => { S.stepEditorId = null; render(); });
  $("#step-editor-form")?.addEventListener("submit", e => saveFunnelStep(e).catch(err => toast(err.message, "error")));
  $$("[data-step-edit]").forEach(b => b.addEventListener("click", () => { S.stepEditorId = parseInt(b.dataset.stepEdit); render(); }));
  $$("[data-step-delete]").forEach(b => b.addEventListener("click", () => deleteFunnelStep(parseInt(b.dataset.stepDelete))));
}

async function loadFunnelDetail(id) { S.selectedFunnelId = parseInt(id); S.stepEditorId = null; S.funnelDetail = await api(`./admin/funnels/${id}`); render(); }

async function createFunnel() {
  const name = prompt("Name:"); if (!name) return;
  const type = prompt("Typ (checkin / checkout):", "checkin"); if (!type) return;
  await api("./admin/funnels", { method: "POST", body: JSON.stringify({ name, funnel_type: type, slug: name.toLowerCase().replace(/\s+/g, "-") }) });
  S.funnelsList = await api("./admin/funnels"); toast("Funnel erstellt"); render();
}

async function saveFunnelStep(e) {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  f.template_id = S.selectedFunnelId;
  f.step_order = parseInt(f.step_order) || 1;
  f.is_mandatory = !!f.is_mandatory;
  f.requires_note = !!f.requires_note;
  f.requires_photo = !!f.requires_photo;
  const isNew = !S.stepEditorId || S.stepEditorId === "new";
  const url = isNew ? `./admin/funnels/${S.selectedFunnelId}/steps` : `./admin/funnels/${S.selectedFunnelId}/steps/${S.stepEditorId}`;
  await api(url, { method: isNew ? "POST" : "PUT", body: JSON.stringify(f) });
  S.funnelDetail = await api(`./admin/funnels/${S.selectedFunnelId}`);
  S.stepEditorId = null; toast("Schritt gespeichert"); render();
}

async function deleteFunnelStep(stepId) {
  if (!await confirmDialog("Schritt löschen?")) return;
  await api(`./admin/funnels/${S.selectedFunnelId}/steps/${stepId}`, { method: "DELETE" });
  S.funnelDetail = await api(`./admin/funnels/${S.selectedFunnelId}`); S.stepEditorId = null; toast("Schritt gelöscht"); render();
}

/* ═══════════════════════════════════════════════════════════════
   BRANDING / EMAIL EDITOR
   ═══════════════════════════════════════════════════════════════ */

function renderBranding() {
  if (S.role !== "admin") return '<div class="empty">Nur Admins</div>';
  const b = S.brandingSettings || {};
  const tpl = S.emailTemplate || {};
  return `<div class="editor-layout">
    <div class="editor-panel">
      <div class="card"><div class="card-header"><span class="card-title">Studio-Identität</span></div><div class="card-body">
        <div class="field"><label>Studio Logo</label>
          <div class="logo-preview-box">${b.logo_url ? `<img src="${esc(b.logo_url)}" alt="Logo">` : '<span style="color:var(--text-muted);font-size:13px;">Kein Logo</span>'}</div>
          <input type="file" id="logo-upload" accept="image/*" class="input" style="margin-top:8px;">
          <input id="logo-link" class="input" style="margin-top:8px;" placeholder="Logo-Link URL" value="${esc(b.logo_link_url || "")}">
        </div>
        <div class="field" style="margin-top:20px;"><label>Farbschema</label>
          <div class="color-grid">
            <div class="color-item"><label>Akzent</label><input type="color" id="c-accent" value="${b.accent_color || "#b5ac9e"}"></div>
            <div class="color-item"><label>Header</label><input type="color" id="c-header" value="${b.header_bg_color || "#000000"}"></div>
            <div class="color-item"><label>Body</label><input type="color" id="c-body" value="${b.body_bg_color || "#f0ede9"}"></div>
            <div class="color-item"><label>Footer</label><input type="color" id="c-footer" value="${b.footer_bg_color || "#000000"}"></div>
          </div>
        </div>
        <div class="field" style="margin-top:20px;"><label>Social Media</label>
          <input id="s-ig" class="input" placeholder="Instagram" value="${esc(b.instagram_url || "")}" style="margin-bottom:6px;">
          <input id="s-fb" class="input" placeholder="Facebook" value="${esc(b.facebook_url || "")}" style="margin-bottom:6px;">
          <input id="s-tt" class="input" placeholder="TikTok" value="${esc(b.tiktok_url || "")}" style="margin-bottom:6px;">
          <input id="s-yt" class="input" placeholder="YouTube" value="${esc(b.youtube_url || "")}">
        </div>
        <div class="field" style="margin-top:20px;"><label>Footer-Text</label>
          <textarea id="footer-text" class="input" rows="3" placeholder="Adresse, Telefon…">${esc(b.footer_text || "")}</textarea>
        </div>
        <button class="btn btn-accent btn-block" style="margin-top:16px;" onclick="saveBranding(this)">Branding speichern</button>
      </div></div>
      <div class="card"><div class="card-header"><span class="card-title">Zugangscode-Email</span></div><div class="card-body">
        <textarea id="tpl-body" class="code-editor" style="min-height:350px;">${esc(tpl.access_code_body_html || "")}</textarea>
        <div style="margin-top:12px;"><label style="margin-bottom:6px;">Variablen:</label>
          <div class="placeholder-chips">
            <button class="placeholder-chip" onclick="insertPlaceholder('{member_name}')">{member_name}</button>
            <button class="placeholder-chip" onclick="insertPlaceholder('{code}')">{code}</button>
            <button class="placeholder-chip" onclick="insertPlaceholder('{valid_from}')">{valid_from}</button>
            <button class="placeholder-chip" onclick="insertPlaceholder('{valid_until}')">{valid_until}</button>
            <button class="placeholder-chip" onclick="insertPlaceholder('{checks_row}')">{checks_row}</button>
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;">
          <button class="btn btn-accent" onclick="saveTemplate(this)">Template speichern</button>
          <button class="btn btn-outline" onclick="sendTestEmail(this)">Test-Mail</button>
        </div>
      </div></div>
    </div>
    <div class="editor-panel">
      <div class="card" style="position:sticky;top:calc(var(--header-h) + 20px);"><div class="card-header"><span class="card-title">Vorschau</span>
        <div class="preview-toggle-bar" style="margin:0;"><button class="preview-toggle-btn active" onclick="setPreviewMode('desktop',this)">Desktop</button><button class="preview-toggle-btn" onclick="setPreviewMode('mobile',this)">Mobil</button></div>
      </div><div class="preview-frame" id="preview-frame"><iframe id="preview-iframe" title="Email-Vorschau"></iframe></div></div>
    </div>
  </div>`;
}

function initBrandingEditor() {
  ["c-accent","c-header","c-body","c-footer","s-ig","s-fb","s-tt","s-yt","footer-text","tpl-body"].forEach(id => {
    document.getElementById(id)?.addEventListener("input", updateEmailPreview);
  });
  document.getElementById("logo-upload")?.addEventListener("change", async e => {
    const file = e.target.files[0]; if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try { const r = await api("./admin/media/upload", { method: "POST", body: fd }); await api("./admin/system/branding", { method: "PUT", body: JSON.stringify({ logo_url: r.url }) }); toast("Logo hochgeladen"); await loadData(); } catch (err) { toast(err.message, "error"); }
  });
  setTimeout(updateEmailPreview, 100);
}

function updateEmailPreview() {
  const iframe = document.getElementById("preview-iframe"); if (!iframe) return;
  const body = document.getElementById("tpl-body")?.value || "";
  const b = S.brandingSettings || {};
  const accent = document.getElementById("c-accent")?.value || b.accent_color || "#b5ac9e";
  const footerText = document.getElementById("footer-text")?.value || b.footer_text || "";
  const logoUrl = b.logo_url || "";
  const logoHtml = logoUrl ? `<img src="${logoUrl}" alt="Logo" style="max-width:200px;height:auto;display:block;margin:0 auto;">` : "GETIMPULSE";
  const rendered = body.replace(/{member_name}/g, "Max Mustermann").replace(/{code}/g, "826491").replace(/{valid_from}/g, "1. April 2026, 10:00 Uhr").replace(/{valid_until}/g, "1. April 2026, 12:30 Uhr").replace(/{checks_url}/g, "#").replace(/{checks_row}/g, `<a href="#" style="display:inline-block;background:${accent};color:#fff;font-family:Arial,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:16px 48px;border-radius:6px;">Check-In & Hausordnung</a>`);
  iframe.srcdoc = `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body{margin:0;padding:0;background:#f0ede9;font-family:Arial,sans-serif;}</style></head><body><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0ede9;"><tr><td align="center" style="padding:28px 16px;"><table width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;"><tr><td style="background:#000;padding:22px 40px;text-align:center;"><span style="font-family:Arial,sans-serif;font-size:18px;font-weight:700;letter-spacing:4px;color:#fff;text-transform:uppercase;">${logoHtml}</span></td></tr>${rendered}<tr><td style="background:#000;padding:40px;text-align:center;"><p style="font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#fff;margin:0 0 10px;">Get-Impulse Berlin GmbH</p><p style="font-family:Arial,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 16px;line-height:1.7;">${footerText.replace(/\n/g, "<br>")}</p><p style="font-family:Arial,sans-serif;font-size:11px;color:#4a4a4a;">&copy; 2026 TWENTY4SEVEN-GYM</p></td></tr></table></td></tr></table></body></html>`;
}

function setPreviewMode(mode, btn) { $$(".preview-toggle-btn").forEach(b => b.classList.remove("active")); btn.classList.add("active"); const f = document.getElementById("preview-frame"); if (mode === "mobile") f?.classList.add("mobile"); else f?.classList.remove("mobile"); }

function insertPlaceholder(text) { const el = document.getElementById("tpl-body"); if (!el) return; const s = el.selectionStart; el.value = el.value.substring(0, s) + text + el.value.substring(el.selectionEnd); el.selectionStart = el.selectionEnd = s + text.length; el.focus(); updateEmailPreview(); }

async function saveBranding(btn) {
  await withBtn(btn, async () => {
    await api("./admin/system/branding", { method: "PUT", body: JSON.stringify({
      accent_color: document.getElementById("c-accent")?.value, header_bg_color: document.getElementById("c-header")?.value,
      body_bg_color: document.getElementById("c-body")?.value, footer_bg_color: document.getElementById("c-footer")?.value,
      logo_link_url: document.getElementById("logo-link")?.value || "",
      instagram_url: document.getElementById("s-ig")?.value || "", facebook_url: document.getElementById("s-fb")?.value || "",
      tiktok_url: document.getElementById("s-tt")?.value || "", youtube_url: document.getElementById("s-yt")?.value || "",
      footer_text: document.getElementById("footer-text")?.value || "",
    }) }); toast("Branding gespeichert"); await loadData();
  }, "Speichern…");
}

async function saveTemplate(btn) {
  const body = document.getElementById("tpl-body")?.value || "";
  await withBtn(btn, async () => {
    await api("./admin/system/email-template", { method: "PUT", body: JSON.stringify({
      header_html: S.emailTemplate?.header_html || "", body_html: S.emailTemplate?.body_html || "",
      footer_html: S.emailTemplate?.footer_html || "", access_code_body_html: body, reset_body_html: S.emailTemplate?.reset_body_html || "",
    }) }); toast("Template gespeichert"); await loadData();
  }, "Speichern…");
}

async function sendTestEmail(btn) {
  await withBtn(btn, async () => { await api("./admin/system/email-test-code", { method: "POST", body: JSON.stringify({ to_email: S.me.email }) }); toast(`Test-Mail an ${S.me.email} gesendet`); }, "Sende…");
}

/* ── Settings ──────────────────────────────────────────────────── */

function renderSettings() {
  if (S.role !== "admin") return '<div class="empty">Nur Admins</div>';
  return `<div class="grid grid-2">
    <div class="card"><div class="card-header"><span class="card-title">Magicline API</span></div><div class="card-body"><form onsubmit="saveSettings(event,'magicline')"><div class="field"><label>API URL</label><input name="magicline_base_url" class="input" value="${esc(S.magiclineSettings?.magicline_base_url)}"></div><div class="field"><label>API Key</label><input name="magicline_api_key" type="password" class="input" placeholder="${S.magiclineSettings?.has_api_key ? "••• konfiguriert" : ""}"></div><div class="field"><label>Studio ID</label><input name="magicline_studio_id" type="number" class="input" value="${S.magiclineSettings?.magicline_studio_id || 0}"></div><button class="btn btn-accent btn-block">Speichern</button></form></div></div>
    <div class="card"><div class="card-header"><span class="card-title">Nuki Smartlock</span></div><div class="card-body"><form onsubmit="saveSettings(event,'nuki')"><div class="field"><label>API Token</label><input name="nuki_api_token" type="password" class="input" placeholder="${S.nukiSettings?.has_api_token ? "••• konfiguriert" : ""}"></div><div class="field"><label>Smartlock ID</label><input name="nuki_smartlock_id" type="number" class="input" value="${S.nukiSettings?.nuki_smartlock_id || 0}"></div><div class="field"><label style="display:flex;align-items:center;gap:8px;"><input name="nuki_dry_run" type="checkbox" ${S.nukiSettings?.nuki_dry_run ? "checked" : ""}> Testmodus</label></div><button class="btn btn-accent btn-block">Speichern</button></form></div></div>
    <div class="card"><div class="card-header"><span class="card-title">E-Mail (SMTP)</span></div><div class="card-body"><form onsubmit="saveSettings(event,'smtp')"><div class="field"><label>Host</label><input name="smtp_host" class="input" value="${esc(S.emailSettings?.smtp_host)}"></div><div class="grid grid-2"><div class="field"><label>Port</label><input name="smtp_port" type="number" class="input" value="${S.emailSettings?.smtp_port || 587}"></div><div class="field"><label style="display:flex;align-items:center;gap:8px;margin-top:26px;"><input name="smtp_use_tls" type="checkbox" ${S.emailSettings?.smtp_use_tls ? "checked" : ""}> TLS</label></div></div><div class="field"><label>Benutzer</label><input name="smtp_username" class="input" value="${esc(S.emailSettings?.smtp_username)}"></div><div class="field"><label>Passwort</label><input name="smtp_password" type="password" class="input"></div><div class="field"><label>Absender</label><input name="smtp_from_email" type="email" class="input" value="${esc(S.emailSettings?.smtp_from_email)}"></div><button class="btn btn-accent btn-block">Speichern</button></form></div></div>
    <div class="card"><div class="card-header"><span class="card-title">Telegram</span></div><div class="card-body"><form onsubmit="saveSettings(event,'telegram')"><div class="field"><label>Bot Token</label><input name="telegram_bot_token" type="password" class="input" placeholder="${S.telegramSettings?.has_bot_token ? "••• konfiguriert" : ""}"></div><div class="field"><label>Chat ID</label><input name="telegram_chat_id" class="input" value="${esc(S.telegramSettings?.telegram_chat_id)}"></div><button class="btn btn-accent btn-block">Speichern</button></form></div></div>
  </div>`;
}

async function saveSettings(e, type) {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  if (type === "nuki") { d.nuki_dry_run = !!d.nuki_dry_run; d.nuki_smartlock_id = +d.nuki_smartlock_id; }
  if (type === "smtp") { d.smtp_use_tls = !!d.smtp_use_tls; d.smtp_port = +d.smtp_port; }
  if (type === "magicline") { d.magicline_studio_id = +d.magicline_studio_id; }
  const ep = { magicline: "magicline-settings", nuki: "nuki-settings", smtp: "email-settings", telegram: "telegram-settings" };
  await withBtn($('button[type="submit"]', e.target), async () => { await api(`./admin/system/${ep[type]}`, { method: "PUT", body: JSON.stringify(d) }); toast("Gespeichert"); await loadData(); }, "Speichern…");
}

/* ═══════════════════════════════════════════════════════════════
   /CHECKS — VERTICAL STEPPER
   ═══════════════════════════════════════════════════════════════ */

function _stepTypeLabel(t) { return ({ house_rules: "Hausordnung", video: "Video", image: "Bild", text: "Info", confirmation: "Bestätigung" })[t] || "Schritt"; }

function _toEmbedUrl(url) {
  if (!url) return "";
  const yt = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]+)/);
  if (yt) return `https://www.youtube.com/embed/${yt[1]}`;
  const vm = url.match(/vimeo\.com\/(\d+)/);
  if (vm) return `https://player.vimeo.com/video/${vm[1]}`;
  return url;
}

function renderChecksLogin() {
  app.innerHTML = `<div class="checks-shell"><div class="checks-container"><div class="checks-card"><div class="checks-card-body">
    <div class="checks-brand"><div class="checks-brand-mark">24</div><div class="checks-title">Studio Check-In</div><div class="checks-subtitle">Gib deinen Zugangscode ein, um dein Training zu starten.</div></div>
    ${S.msg ? `<div class="toast toast-${S.msgType === "error" ? "error" : "success"}">${esc(S.msg)}</div>` : ""}
    <form id="ck-form" style="display:flex;flex-direction:column;gap:14px;">
      <div class="field"><label>E-Mail</label><input name="email" type="email" class="input" required></div>
      <div class="field"><label>Zugangscode</label><input name="code" class="input" required style="text-align:center;font-weight:800;font-size:18px;letter-spacing:6px;" maxlength="12"></div>
      <button type="submit" class="btn btn-dark btn-block btn-lg">Anmelden</button>
    </form>
  </div></div></div></div>`;
  $("#ck-form").addEventListener("submit", async e => {
    e.preventDefault();
    try { S.ck = await api("./public/checks/resolve", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target))) }); render(); }
    catch (err) { toast(err.message, "error"); }
  });
}

function renderChecksWindows() {
  const wins = S.ck.windows || [];
  app.innerHTML = `<div class="checks-shell"><div class="checks-container">
    <div class="checks-brand" style="margin-bottom:24px;"><div class="checks-brand-mark">24</div><div class="checks-title">Hallo, ${esc(S.ck.member_name)}!</div><div class="checks-subtitle">Wähle dein Training für Check-in oder Check-out.</div></div>
    ${wins.map(w => {
      const ciDone = !!w.checkin_confirmed_at;
      const coDone = !!w.checkout_confirmed_at;
      return `<div class="checks-window">
        <div class="checks-time-block"><span class="checks-time-main">${fmtTime(w.starts_at)}</span><span class="checks-time-arrow">→</span><span class="checks-time-main">${fmtTime(w.ends_at)}</span></div>
        <div class="checks-date">${fmtDay(w.starts_at)} · ${badge(w.status)}</div>
        <div class="checks-status-row">
          <div class="checks-status-item ${ciDone ? "done" : "pending"}">${ciDone ? ico("check", 16) : "○"} Check-in ${ciDone ? `· ${fmtDt(w.checkin_confirmed_at)}` : ""}</div>
          <div class="checks-status-item ${coDone ? "done" : "pending"}">${coDone ? ico("check", 16) : "○"} Check-out ${coDone ? `· ${fmtDt(w.checkout_confirmed_at)}` : ""}</div>
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;">
          ${w.has_checkin_funnel && !ciDone ? `<button class="btn btn-dark btn-block" onclick="startFunnel(${w.id},'checkin')">Check-in starten</button>` : w.has_checkin_funnel && ciDone ? `<span class="badge badge-success" style="padding:8px 16px;">✓ Check-in erledigt</span>` : ""}
          ${w.has_checkout_funnel && ciDone && !coDone ? `<button class="btn btn-outline btn-block" onclick="startFunnel(${w.id},'checkout')">Check-out</button>` : ""}
        </div>
      </div>`;
    }).join("") || '<div class="empty">Keine aktiven Trainingsblöcke</div>'}
  </div></div>`;
}

async function startFunnel(winId, type) {
  try {
    const f = await api(`./public/checks/funnel/${type}`);
    S.ckFunnel = f; S.ckWindowId = winId; S.ckFunnelType = type;
    S.ckStep = 0; S.ckDraft = {}; S.ckStepError = null;
    (f.steps || []).forEach(s => { S.ckDraft[s.id] = { checked: false, note: "" }; });
    render();
  } catch (err) { toast(err.message, "error"); }
}

function renderVerticalStepper() {
  const f = S.ckFunnel; const steps = f.steps || []; const total = steps.length;
  if (S.ckStep === 0) return renderStepperIntro(f);
  if (S.ckStep > total) return renderStepperSuccess();

  const cur = steps[S.ckStep - 1];
  const draft = S.ckDraft[cur.id] || { checked: false, note: "" };
  const isLast = S.ckStep === total;
  const canProceed = cur.requires_note ? draft.note.trim().length > 0 : draft.checked;
  const label = S.ckFunnelType === "checkout" ? "Check-out" : "Check-in";

  app.innerHTML = `<div class="checks-shell"><div class="checks-container" style="max-width:520px;"><div class="checks-card"><div class="checks-card-body">
    <div class="stepper" role="list">
      ${steps.map((step, i) => {
        const n = i + 1; const done = n < S.ckStep; const active = n === S.ckStep;
        const cls = done ? "done" : active ? "active" : "";
        return `<div class="stepper-step ${cls}" role="listitem">
          <div class="stepper-dot">${done ? ico("check", 12) : `<span class="stepper-step-number">${n}</span>`}</div>
          <div class="stepper-header"><span class="stepper-title">${esc(step.title)}</span>
            ${step.step_type && step.step_type !== "confirmation" ? `<span class="step-type-badge step-type-${step.step_type}">${_stepTypeLabel(step.step_type)}</span>` : ""}
          </div>
          ${active ? renderActiveStep(step, draft) : ""}
        </div>`;
      }).join("")}
    </div>
    <div class="stepper-nav">
      <button class="btn btn-outline" onclick="stepperBack()">Zurück</button>
      <button class="btn btn-dark" id="step-next" ${canProceed ? "" : "disabled"}>${isLast ? `${label} abschließen` : "Weiter"}</button>
    </div>
  </div></div></div></div>`;

  // Attach handlers
  const noteEl = document.getElementById("step-note");
  const nextBtn = document.getElementById("step-next");
  if (noteEl) { noteEl.addEventListener("input", () => { S.ckDraft[cur.id].note = noteEl.value; if (nextBtn) nextBtn.disabled = cur.requires_note && !noteEl.value.trim(); }); noteEl.focus(); }
  if (nextBtn) nextBtn.addEventListener("click", () => { if (S.ckStep >= total) submitFunnel(); else { S.ckStep++; render(); } });
}

function renderActiveStep(step, draft) {
  const st = step.step_type || "confirmation";
  let html = '<div class="stepper-body">';
  if (st === "house_rules") {
    html += `<div class="house-rules-container"><h3>${esc(step.title)}</h3>${step.body ? `<div>${step.body}</div>` : ""}</div>
      <div class="check-row ${draft.checked ? "checked" : ""}" onclick="toggleStep(${step.id})"><input type="checkbox" ${draft.checked ? "checked" : ""} tabindex="-1"><span class="check-row-label">Ich habe die Hausordnung gelesen und akzeptiere die Regeln.</span></div>`;
  } else if (st === "video" && step.video_url) {
    html += `${step.body ? `<p>${esc(step.body)}</p>` : ""}<div class="step-video-container"><iframe src="${esc(_toEmbedUrl(step.video_url))}" allowfullscreen loading="lazy"></iframe></div>
      <div class="check-row ${draft.checked ? "checked" : ""}" onclick="toggleStep(${step.id})"><input type="checkbox" ${draft.checked ? "checked" : ""} tabindex="-1"><span class="check-row-label">Video angesehen und verstanden.</span></div>`;
  } else if (step.requires_note) {
    html += `${step.body ? `<p>${esc(step.body)}</p>` : ""}${step.image_path ? `<img src="${esc(step.image_path)}" class="step-image">` : ""}
      <div class="field"><label>Deine Anmerkung</label><textarea class="input" id="step-note" placeholder="Notiz eingeben…">${esc(draft.note)}</textarea></div>`;
  } else {
    html += `${step.body ? `<p>${esc(step.body)}</p>` : ""}${step.image_path ? `<img src="${esc(step.image_path)}" class="step-image">` : ""}
      <div class="check-row ${draft.checked ? "checked" : ""}" onclick="toggleStep(${step.id})"><input type="checkbox" ${draft.checked ? "checked" : ""} tabindex="-1"><span class="check-row-label">Ich bestätige diesen Punkt.</span></div>`;
  }
  return html + "</div>";
}

function toggleStep(stepId) { S.ckDraft[stepId].checked = !S.ckDraft[stepId].checked; render(); }

function stepperBack() { if (S.ckStep <= 0) { S.ckFunnel = null; render(); } else { S.ckStep--; render(); } }

function renderStepperIntro(f) {
  const label = S.ckFunnelType === "checkout" ? "Check-out" : "Check-in";
  const cnt = (f.steps || []).length;
  app.innerHTML = `<div class="checks-shell"><div class="checks-container" style="max-width:480px;"><div class="checks-card"><div class="checks-card-body" style="text-align:center;">
    <div class="checks-brand-mark" style="margin:0 auto 20px;">24</div>
    <div class="checks-title">${esc(f.template_name)}</div>
    <div class="checks-subtitle" style="margin-bottom:24px;">${esc(f.description || `${cnt} Schritte zum ${label}.`)}</div>
    <div style="background:var(--bg);border-radius:var(--radius);padding:16px;margin-bottom:24px;text-align:left;">
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);margin-bottom:8px;">${label} · ${cnt} Schritte</div>
      ${(f.steps || []).map((s, i) => `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;font-size:13px;">
        <span style="width:20px;height:20px;border-radius:50%;border:2px solid var(--border);display:grid;place-items:center;font-size:10px;font-weight:800;color:var(--text-muted);flex-shrink:0;">${i + 1}</span>
        <span>${esc(s.title)}</span>
        ${s.step_type === "house_rules" ? '<span class="step-type-badge step-type-house-rules">Hausordnung</span>' : ""}
        ${s.step_type === "video" ? '<span class="step-type-badge step-type-video">Video</span>' : ""}
      </div>`).join("")}
    </div>
    <button class="btn btn-dark btn-block btn-lg" onclick="S.ckStep++;render()">${label} starten</button>
    <button class="btn btn-ghost btn-block" style="margin-top:8px;" onclick="S.ckFunnel=null;render()">Abbrechen</button>
  </div></div></div></div>`;
}

function renderStepperSuccess() {
  const label = S.ckFunnelType === "checkout" ? "Check-out" : "Check-in";
  const msg = S.ckFunnelType === "checkout" ? "Danke und bis bald! Dein Check-out wurde erfasst." : "Dein Check-in wurde erfasst. Viel Erfolg beim Training!";
  app.innerHTML = `<div class="checks-shell"><div class="checks-container" style="max-width:460px;"><div class="checks-card"><div class="checks-card-body" style="text-align:center;">
    <div class="success-icon">${ico("check", 32)}</div><div class="checks-title">${label} bestätigt</div><div class="checks-subtitle" style="margin-bottom:24px;">${esc(msg)}</div>
    <button class="btn btn-outline btn-block" onclick="S.ckFunnel=null;reloadChecks()">Zurück zur Übersicht</button>
  </div></div></div></div>`;
}

async function submitFunnel() {
  const btn = document.getElementById("step-next");
  await withBtn(btn, async () => {
    await api(`./public/checks/window/${S.ckWindowId}/${S.ckFunnelType}`, { method: "POST", body: JSON.stringify({
      token: S.ck.token, window_id: S.ckWindowId, funnel_type: S.ckFunnelType,
      steps: Object.entries(S.ckDraft).map(([id, d]) => ({ step_id: parseInt(id), checked: d.checked, note: d.note })),
    }) }); S.ckStep++; render();
  }, "Wird gespeichert…");
}

async function reloadChecks() { try { S.ck = await api(`./public/checks/session?token=${encodeURIComponent(S.ck.token)}`); } catch {} render(); }

/* ═══════════════════════════════════════════════════════════════
   MAIN RENDER + BOOTSTRAP
   ═══════════════════════════════════════════════════════════════ */

function render() {
  if (location.pathname.includes("/checks") || QS.has("token")) {
    if (S.ckFunnel) return renderVerticalStepper();
    if (S.ck) return renderChecksWindows();
    return renderChecksLogin();
  }
  if (location.pathname.includes("/reset-password")) return renderResetPw();
  if (!S.token) return renderAuth();
  return renderAdmin();
}

function renderResetPw() {
  app.innerHTML = `<div class="auth-shell"><div class="auth-card"><div class="checks-brand"><div class="checks-brand-mark">24</div><div class="checks-title">Passwort neu setzen</div></div>
    ${S.msg ? `<div class="toast toast-${S.msgType === "error" ? "error" : "success"}">${esc(S.msg)}</div>` : ""}
    <form id="reset-form" style="display:flex;flex-direction:column;gap:14px;"><div class="field"><label>Neues Passwort</label><input name="password" type="password" class="input" required minlength="12"></div><button type="submit" class="btn btn-dark btn-block btn-lg">Passwort speichern</button></form>
  </div></div>`;
  $("#reset-form")?.addEventListener("submit", async e => { e.preventDefault(); await withBtn($('button[type="submit"]', e.target), async () => { await api("./auth/reset-password", { method: "POST", body: JSON.stringify({ token: QS.get("token"), password: Object.fromEntries(new FormData(e.target)).password }) }); toast("Passwort gespeichert"); }, "Speichern…"); });
}

async function loadData() {
  try {
    const [me, wins, members, alerts, actions, lockSt] = await Promise.all([
      api("./me"), api("./admin/access-windows?limit=50"),
      api(`./admin/members?limit=15&offset=${S.memberPage * 15}${S.memberSearch ? `&email=${encodeURIComponent(S.memberSearch)}` : ""}`),
      api("./admin/alerts?limit=50"), api("./admin/admin-actions?limit=50"),
      api("./admin/lock/status").catch(() => ({ stateName: "Offline", connectivity: "offline" })),
    ]);
    Object.assign(S, { me, windows: wins, members, alerts, actions, lockStatus: lockSt });
    if (S.role === "admin") {
      const [es, tpl, ts, ns, ms, fl, br] = await Promise.all([
        api("./admin/system/email-settings").catch(() => null), api("./admin/system/email-template").catch(() => null),
        api("./admin/system/telegram-settings").catch(() => null), api("./admin/system/nuki-settings").catch(() => null),
        api("./admin/system/magicline-settings").catch(() => null), api("./admin/funnels").catch(() => []),
        api("./admin/system/branding").catch(() => null),
      ]);
      Object.assign(S, { emailSettings: es, emailTemplate: tpl, telegramSettings: ts, nukiSettings: ns, magiclineSettings: ms, funnelsList: fl, brandingSettings: br });
    }
    if (S.selectedMemberId) S.memberDetail = await api(`./admin/members/${S.selectedMemberId}`).catch(() => null);
    render();
  } catch (err) { if (err.message.includes("401")) doLogout(); else { toast(err.message, "error"); render(); } }
}

async function bootstrap() {
  if (location.pathname.includes("/checks") || QS.has("token")) {
    const tk = QS.get("token");
    if (tk) try { S.ck = await api(`./public/checks/session?token=${encodeURIComponent(tk)}`); } catch {}
    return render();
  }
  if (location.pathname.includes("/reset-password")) return render();
  if (S.token) await loadData(); else render();
}

bootstrap();
