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
  studioLinks: null,
  view: params.get("view") || "overview",
  selectedMemberId: params.get("member") || "",
  memberPage: 0,
  memberLimit: 15,
  memberSearch: "",
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
  const tone = /active|aktiv|emailed|ready|bereit|admin|booked|success|verbunden|online/i.test(value)
    ? "good"
    : /warn|scheduled|operator|pending|flagged|credentials|eingriff|prüfen/i.test(value)
      ? "warn"
      : /error|fehler|bad|canceled|expired|replaced|failed|inaktiv|offline|fehlend|nicht/i.test(value)
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

function getMemberName(memberId) {
  const m = state.members.find((m) => m.id === memberId);
  if (!m) return `Mitglied #${memberId}`;
  return `${m.first_name || ""} ${m.last_name || ""}`.trim() || m.email || `Mitglied #${memberId}`;
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

async function loadMembers() {
  const query = new URLSearchParams();
  query.set("limit", state.memberLimit);
  query.set("offset", state.memberPage * state.memberLimit);
  if (state.memberSearch) query.set("email", state.memberSearch);
  state.members = await api(`./admin/members?${query.toString()}`);
  render();
}

async function loadAppData() {
  state.me = await api("./me");
  const [members, windows, alerts, actions, lockStatus, lockLog] = await Promise.all([
    api(`./admin/members?limit=${state.memberLimit}&offset=${state.memberPage * state.memberLimit}${state.memberSearch ? `&email=${encodeURIComponent(state.memberSearch)}` : ""}`),
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
    const [emailSettings, emailTemplate, telegramSettings, nukiSettings, magiclineSettings, studioLinks, funnelsList, brandingSettings] = await Promise.all([
      api("./admin/system/email-settings"),
      api("./admin/system/email-template"),
      api("./admin/system/telegram-settings"),
      api("./admin/system/nuki-settings"),
      api("./admin/system/magicline-settings"),
      api("./admin/system/studio-links"),
      api("./admin/funnels"),
      api("./admin/system/branding"),
    ]);
    state.emailSettings = emailSettings;
    state.emailTemplate = emailTemplate;
    state.telegramSettings = telegramSettings;
    state.nukiSettings = nukiSettings;
    state.magiclineSettings = magiclineSettings;
    state.studioLinks = studioLinks;
    state.funnelsList = funnelsList;
    state.brandingSettings = brandingSettings;
  }
  if (state.selectedMemberId) {
    await loadMemberDetail(state.selectedMemberId, false);
  } else {
    render();
  }
}

async function loadMemberDetail(memberId, rerender = true) {
  if (state.selectedMemberId === String(memberId)) {
    state.selectedMemberId = "";
    state.memberDetail = null;
  } else {
    state.selectedMemberId = String(memberId);
    state.memberDetail = await api(`./admin/members/${memberId}`);
  }
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
    state.memberSearch = String(form.get("email") || "").trim();
    state.memberPage = 0;
    state.memberDetail = null;
    state.selectedMemberId = "";
    await loadMembers();
    syncUrlState();
  }, "Suche läuft…");
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

async function testResetEmail(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/email-test-reset", {
      method: "POST",
      body: JSON.stringify({ to_email: form.get("to_email_reset") }),
    });
    setMessage("Passwort-Reset-Testmail versendet.", "good");
  }, "Testmail wird gesendet…");
}

async function testCodeEmail(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/email-test-code", {
      method: "POST",
      body: JSON.stringify({ to_email: form.get("to_email_code") }),
    });
    setMessage("Zugangscode-Testmail versendet.", "good");
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
        access_code_body_html: form.get("access_code_body_html") || "",
        reset_body_html: form.get("reset_body_html") || "",
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
  document.getElementById("members-sync-trigger")?.addEventListener("click", () => runFullSync().catch(handleError));
  
  document.getElementById("prev-page")?.addEventListener("click", async () => {
    if (state.memberPage > 0) {
      state.memberPage--;
      await loadMembers();
    }
  });
  
  document.getElementById("next-page")?.addEventListener("click", async () => {
    state.memberPage++;
    await loadMembers();
  });

  document.querySelectorAll("[data-member-id]").forEach((button) => {
    button.addEventListener("click", () => loadMemberDetail(button.dataset.memberId).catch(handleError));
  });
  attachWindowActionHandlers();
}

async function updateBrandingSocial(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        instagram_url: form.get("instagram_url") || "",
        facebook_url: form.get("facebook_url") || "",
        tiktok_url: form.get("tiktok_url") || "",
        youtube_url: form.get("youtube_url") || "",
        footer_text: form.get("footer_text") || "",
      }),
    });
    setMessage("Branding Details gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}

async function updateBrandingColors(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        accent_color: form.get("accent_color"),
        header_bg_color: form.get("header_bg_color"),
        body_bg_color: form.get("body_bg_color"),
        footer_bg_color: form.get("footer_bg_color"),
      }),
    });
    setMessage("Farben gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}

async function updateBrandingLogoLink(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    const form = new FormData(event.currentTarget);
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        logo_link_url: form.get("logo_link_url") || "",
      }),
    });
    setMessage("Logo-Link gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}

async function uploadLogo(event) {
  const file = event.target.files[0];
  if (!file) return;
  
  await withPending(document.getElementById("branding-logo-form"), async () => {
    const formData = new FormData();
    formData.append("file", file);
    
    const { url } = await api("./admin/media/upload", {
      method: "POST",
      body: formData,
    }, true); // true = don't set Content-Type header manually for FormData
    
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({ logo_url: url }),
    });
    
    setMessage("Logo erfolgreich hochgeladen.", "good");
    await loadAppData();
  }, "Logo wird hochgeladen…");
}

function attachSettingsHandlers() {
  document.getElementById("smtp-form")?.addEventListener("submit", (event) => updateSmtp(event).catch(handleError));
  document.getElementById("telegram-form")?.addEventListener("submit", (event) => updateTelegram(event).catch(handleError));
  document.getElementById("nuki-form")?.addEventListener("submit", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById("magicline-form")?.addEventListener("submit", (event) => updateMagiclineSettings(event).catch(handleError));
  
  document.getElementById("branding-social-form")?.addEventListener("submit", (event) => updateBrandingSocial(event).catch(handleError));
  document.getElementById("branding-colors-form")?.addEventListener("submit", (event) => updateBrandingColors(event).catch(handleError));
  document.getElementById("branding-logo-form")?.addEventListener("submit", (event) => updateBrandingLogoLink(event).catch(handleError));
  document.getElementById("logo-upload")?.addEventListener("change", (event) => uploadLogo(event).catch(handleError));

  const editorEl = document.getElementById("email-editor");
  if (editorEl && typeof Quill !== "undefined") {
    const quill = new Quill("#email-editor", {
      theme: "snow",
      modules: {
        toolbar: [
          [{ header: [1, 2, 3, false] }],
          ["bold", "italic", "underline", "strike"],
          [{ color: [] }, { background: [] }],
          [{ list: "ordered" }, { list: "bullet" }],
          ["clean"],
        ],
      },
    });

    const hiddenInput = document.getElementById("tpl-access-code-body");
    if (hiddenInput && hiddenInput.value) {
      quill.root.innerHTML = hiddenInput.value;
    }

    const updatePreview = () => {
      const content = quill.root.innerHTML;
      if (hiddenInput) hiddenInput.value = content;
      
      const frame = document.getElementById("email-preview-frame");
      if (frame) {
        const logoImg = state.brandingSettings?.logo_url 
          ? `<img src="${state.brandingSettings.logo_url}" alt="Logo" style="max-width: 200px; height: auto; display: block; margin: 0 auto;">`
          : "";
        
        const logoHtml = state.brandingSettings?.logo_link_url
          ? `<a href="${state.brandingSettings.logo_link_url}" style="text-decoration:none;">${logoImg}</a>`
          : logoImg;
        
        const socials = [
          ["instagram", state.brandingSettings?.instagram_url],
          ["facebook", state.brandingSettings?.facebook_url],
          ["tiktok", state.brandingSettings?.tiktok_url],
          ["youtube", state.brandingSettings?.youtube_url],
        ].filter(s => s[1]);

        let socialHtml = "";
        if (socials.length > 0) {
          const baseUrl = window.location.origin;
          const items = socials.map(([name, url]) => 
            `<a href="${url}" style="margin: 0 10px; text-decoration: none;"><img src="${baseUrl}/assets/icon-${name}.png" alt="${name}" width="24" height="24"></a>`
          ).join("");
          socialHtml = `<div style="text-align:center; padding: 20px; border-top: 1px solid #eee; margin-top: 20px;">${items}</div>`;
        }

        const footerTextHtml = state.brandingSettings?.footer_text
          ? `<div style="padding: 20px; text-align: center; font-size: 13px; color: #64748b; border-top: 1px solid #eee;">${state.brandingSettings.footer_text.replace(/\n/g, "<br>")}</div>`
          : "";

        const fullHtml = `
          <!DOCTYPE html>
          <html>
            <head>
              <style>
                body { font-family: sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: ${state.brandingSettings?.body_bg_color || "#f9f9f9"}; }
                .wrapper { width: 100%; table-layout: fixed; background-color: ${state.brandingSettings?.body_bg_color || "#f9f9f9"}; padding-bottom: 40px; padding-top: 40px; }
                .main { width: 100%; max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
                .header { background-color: ${state.brandingSettings?.header_bg_color || "#ffffff"}; border-bottom: 1px solid #eee; }
                .content { padding: 30px; }
                .footer-container { background-color: ${state.brandingSettings?.footer_bg_color || "#ffffff"}; }
                .legal { padding: 20px; text-align: center; font-size: 12px; color: #94a3b8; }
                .btn { display:inline-block; padding: 12px 24px; background-color: ${state.brandingSettings?.accent_color || "#2563eb"}; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; }
              </style>
            </head>
            <body>
              <div class="wrapper">
                <div class="main">
                  <div class="header">
                    <div style="text-align:center; padding: 20px;">${logoHtml}</div>
                  </div>
                  <div class="content">
                    ${content.replace(/{member_name}/g, "Max Mustermann")
                             .replace(/{code}/g, "123456")
                             .replace(/{valid_from}/g, "01.01.2026, 10:00 Uhr")
                             .replace(/{valid_until}/g, "01.01.2026, 12:00 Uhr")
                             .replace(/{check_in_url}/g, "#")}
                  </div>
                  <div class="footer-container">
                    ${socialHtml}
                    ${footerTextHtml}
                  </div>
                </div>
                <div class="legal">
                  &copy; ${new Date().getFullYear()} Studio Access Management
                </div>
              </div>
            </body>
          </html>
        `;
        frame.srcdoc = fullHtml;
      }
    };

    quill.on("text-change", updatePreview);
    document.getElementById("email-template-preview-btn")?.addEventListener("click", updatePreview);
    // Initial preview
    setTimeout(updatePreview, 100);
  }

  document.getElementById("email-template-form")?.addEventListener("submit", (event) => updateEmailTemplate(event).catch(handleError));
}

function handleError(error) {
  setMessage(error.message || "Unbekannter Fehler.", "bad");
}

function icon(name) {
  const icons = {
    overview: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" /></svg>',
    members: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" /></svg>',
    windows: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" /></svg>',
    lock: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>',
    alerts: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" /></svg>',
    funnels: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0m-9.75 0h9.75" /></svg>',
    settings: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 0 1 0 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.75 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.759 0 0 1 0-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281Z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" /></svg>',
  };
  return icons[name] || "";
}

function navButton(view, label) {
  const active = state.view === view ? "active" : "";
  // If view is 'members', we don't want to carry over the selected member ID to reset the view
  const href = `./app?view=${encodeURIComponent(view)}${view !== "members" && state.selectedMemberId ? `&member=${encodeURIComponent(state.selectedMemberId)}` : ""}`;
  return `
    <a
      class="nav-button ${active}"
      data-view="${view}"
      href="${href}"
      ${state.view === view ? 'aria-current="page"' : ""}
    >
      <span class="nav-icon">${icon(view)}</span>
      <span class="nav-label">${escapeHtml(label)}</span>
    </a>
  `;
}

function renderStatusStrip() {
  const next = upcomingWindow();
  const alertCount = urgentAlerts().length;
  const isLockOnline = state.lockStatus?.connectivity === "online";
  
  return `
    <div class="live-strip">
      <div class="live-card">
        <div class="live-label">Smartlock Status</div>
        <div class="live-value">
          <span class="live-dot ${isLockOnline ? "" : "warn"}"></span>
          ${isLockOnline ? "Online" : "Prüfen"}
        </div>
        <p class="subtle">${translateStatus(state.lockStatus?.lock_state) || "Unbekannt"}</p>
      </div>
      <div class="live-card">
        <div class="live-label">Synchronisierte Mitglieder</div>
        <div class="live-value">${state.members.length}</div>
        <p class="subtle">${state.members.length > 0 ? "Datenbestand aktuell" : "Keine Daten"}</p>
      </div>
      <div class="live-card">
        <div class="live-label">System Alarme</div>
        <div class="live-value">
          <span class="live-dot ${alertCount > 0 ? "bad" : ""}"></span>
          ${alertCount}
        </div>
        <p class="subtle">${alertCount ? "Handlungsbedarf" : "Keine Fehler"}</p>
      </div>
      <div class="live-card">
        <div class="live-label">Nächster Zugang</div>
        <div class="live-value">${next ? fmtDate(next.dispatch_at).split(",")[1] : "—"}</div>
        <p class="subtle">${next ? `Member ${next.member_id}` : "Keine Termine"}</p>
      </div>
    </div>
  `;
}

function renderOverview() {
  const urgent = urgentAlerts();
  return `
    <div class="dashboard-grid">
      <section class="panel span-12">
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

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Access Windows</h2>
            <p class="panel-kicker">Nächste Freigaben im Überblick.</p>
          </div>
        </div>
        <div class="stack">
          ${state.windows.slice(0, 10).map((w) => `
            <div class="list-item">
              <div class="split">${pill(w.status)}<span class="subtle numberish">${fmtDate(w.dispatch_at)}</span></div>
              <h3>${escapeHtml(getMemberName(w.member_id))}</h3>
              <p class="subtle">${fmtDate(w.starts_at)} → ${fmtDate(w.ends_at)}</p>
            </div>
          `).join("") || '<div class="empty">Keine Access Windows</div>'}
        </div>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Schloss Log</h2>
            <p class="panel-kicker">Letzte manuelle Eingriffe.</p>
          </div>
        </div>
        <div class="stack">
          ${state.lockLog.length ? state.lockLog.slice(0, 10).map((entry) => `
            <div class="list-item">
              <div class="split"><strong>${escapeHtml(entry.action)}</strong><span class="subtle numberish">${fmtDate(entry.created_at)}</span></div>
              <p class="subtle">${escapeHtml(entry.actor_email)}</p>
            </div>
          `).join("") : '<div class="empty">Keine Schlossereignisse</div>'}
        </div>
      </section>
    </div>
  `;
}

function renderMemberCards() {
  return state.members.map((member) => {
    const isSelected = state.selectedMemberId === String(member.id);
    const activeClass = isSelected ? "active" : "";
    const name = `${member.first_name || ""} ${member.last_name || ""}`.trim() || "Unbenannt";
    const planLabel = member.has_xxlarge ? "XX-Large" : member.has_free_training_product ? "Free Training" : "Standard";
    
    let detailsHtml = "";
    if (isSelected && state.memberDetail) {
      const d = state.memberDetail;
      detailsHtml = `
        <div class="member-expanded-content mt-24">
          <div class="lock-detail-grid">
            <div class="health-item">
              <span class="health-label">Magicline-ID</span>
              <span class="numberish">${escapeHtml(d.member.magicline_customer_id)}</span>
            </div>
            <div class="health-item">
              <span class="health-label">Mitgliedschaft</span>
              <span>${d.member.has_xxlarge ? "XX-Large" : d.member.has_free_training_product ? "Free Training" : "Standard"}</span>
            </div>
            <div class="health-item">
              <span class="health-label">Letzter Sync</span>
              <span class="numberish">${fmtDate(d.member.last_synced_at)}</span>
            </div>
          </div>

          <div class="stack mt-24">
            <div class="tabs-header"><span class="subtle">Buchungen</span></div>
            ${d.bookings.slice(0, 5).map((b) => `
              <div class="list-item secondary">
                <div class="split">
                  <span><strong>${escapeHtml(b.title)}</strong><br><small class="subtle">${fmtDate(b.start_at)}</small></span>
                  ${pill(b.booking_status)}
                </div>
              </div>
            `).join("") || '<p class="subtle">Keine Buchungen</p>'}
          </div>

          <div class="stack mt-24">
            <div class="tabs-header"><span class="subtle">Zugangsfenster</span></div>
            ${d.access_windows.map((w) => `
              <div class="list-item secondary">
                <div class="split">
                  <span><strong>Fenster #${w.id}</strong><br><small class="subtle">${fmtDate(w.starts_at)} – ${fmtDate(w.ends_at)}</small></span>
                  ${pill(w.status)}
                </div>
                ${renderWindowActions(w)}
              </div>
            `).join("") || '<p class="subtle">Keine Fenster</p>'}
          </div>

          <div class="stack mt-24">
            <div class="tabs-header"><span class="subtle">Nuki Codes</span></div>
            ${d.access_codes.map((c) => `
              <div class="list-item secondary">
                <div class="split">
                  <code class="numberish">••••${escapeHtml(c.code_last4)}</code>
                  ${pill(c.status)}
                </div>
              </div>
            `).join("") || '<p class="subtle">Keine Codes</p>'}
          </div>
        </div>
      `;
    }

    return `
      <div class="list-item entity-button ${activeClass}" data-member-id="${member.id}" style="cursor: pointer; display: block;">
        <div class="split">
          <div class="stack" style="gap: 4px;">
            <strong style="font-size: 1.1rem;">${escapeHtml(name)}</strong>
            <span class="subtle">${escapeHtml(member.email || "Keine E-Mail")}</span>
          </div>
          <div class="stack" style="gap: 6px; align-items: flex-end;">
            ${pill(member.status || "unknown")}
            <span class="pill info" style="font-size: 0.7rem; padding: 2px 8px;">${planLabel}</span>
          </div>
        </div>
        ${detailsHtml}
      </div>
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
  return `
    <div class="dashboard-grid">
      <section class="panel span-12">
        <div class="panel-header">
          <div class="split">
            <div>
              <h2 class="panel-title">Mitgliederverwaltung</h2>
              <p class="panel-kicker">Übersicht aller synchronisierten Studio-Kunden. Klicken Sie auf ein Mitglied für Details.</p>
            </div>
            <button type="button" id="members-sync-trigger" class="secondary">
              <span class="nav-icon">${icon("funnels")}</span>
              Magicline Sync
            </button>
          </div>
        </div>
        <div class="stack">
          <form id="member-search-form" class="search-box">
            <div class="search-input-wrapper">
              <span class="search-icon">${icon("overview")}</span>
              <input id="member-search-email" name="email" type="email" autocomplete="off" spellcheck="false" inputmode="email" placeholder="Nach E-Mail Adresse suchen..." value="${escapeHtml(state.memberSearch || "")}" />
            </div>
            <button type="submit">Suchen</button>
          </form>
        </div>
        <div class="entity-list mt-24">
          ${renderMemberCards() || '<div class="empty">Keine Mitglieder gefunden.</div>'}
        </div>
        <div class="pagination mt-24">
          <button type="button" id="prev-page" class="secondary" ${state.memberPage === 0 ? "disabled" : ""}>Zurück</button>
          <span class="subtle">Seite ${state.memberPage + 1}</span>
          <button type="button" id="next-page" class="secondary" ${state.members.length < state.memberLimit ? "disabled" : ""}>Weiter</button>
        </div>
      </section>
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
            <h3>${escapeHtml(getMemberName(w.member_id))}</h3>
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
            ${state.lockStatus?.dry_run ? `<p class="panel-kicker">Systemsicht bis die echten Nuki-Credentials aktiv sind.</p>` : ""}
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
          ${state.lockStatus?.dry_run ? `
            <div class="list-item">
              <p class="eyebrow">// DRY-RUN MODUS</p>
              <p class="subtle">Bis zum Go-live zeigt die Konsole belastbare Betriebsdaten ohne Live-Telemetrie.</p>
            </div>
          ` : ""}
          ${state.role === "admin" ? `
            <button type="button" class="warn" data-remote-open>${state.lockStatus?.dry_run ? "Remote Open Protokollieren" : "Live Fernöffnung"}</button>
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
        <h2 class="panel-title">Zugriff verweigert</h2>
        <p class="subtle">Nur Administratoren können die Systemeinstellungen verwalten.</p>
      </section>
    `;
  }
  return `
    <div class="dashboard-grid">
      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">E-Mail (SMTP)</h2>
            <p class="panel-kicker">Konfiguration für den Versand von Zugangscodes.</p>
          </div>
        </div>
        <form id="smtp-form" class="stack">
          <label for="smtp-host">SMTP Host
            <input id="smtp-host" name="smtp_host" type="text" autocomplete="off" value="${escapeHtml(state.emailSettings?.smtp_host || "")}" placeholder="z.B. smtp.gmail.com" />
          </label>
          <label for="smtp-port">Port
            <input id="smtp-port" name="smtp_port" type="number" inputmode="numeric" value="${escapeHtml(state.emailSettings?.smtp_port || 587)}" />
          </label>
          <label for="smtp-username">Benutzername
            <input id="smtp-username" name="smtp_username" type="text" autocomplete="username" value="${escapeHtml(state.emailSettings?.smtp_username || "")}" />
          </label>
          <label for="smtp-password">Passwort
            <input id="smtp-password" name="smtp_password" type="password" autocomplete="new-password" placeholder="Passwort" />
          </label>
          <label for="smtp-from">Absender-E-Mail
            <input id="smtp-from" name="smtp_from_email" type="email" autocomplete="email" spellcheck="false" inputmode="email" value="${escapeHtml(state.emailSettings?.smtp_from_email || "")}" />
          </label>
          <label class="checkbox-row" for="smtp-use-tls">
            <input id="smtp-use-tls" name="smtp_use_tls" type="checkbox" ${state.emailSettings?.smtp_use_tls !== false ? "checked" : ""} />
            <span>TLS Verschlüsselung</span>
          </label>
          <button type="submit">Speichern</button>
        </form>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Telegram Alarme</h2>
            <p class="panel-kicker">Konfiguration des Alarm-Kanals.</p>
          </div>
        </div>
        <form id="telegram-form" class="stack">
          <label for="telegram-bot-token">Bot Token
            <input id="telegram-bot-token" name="telegram_bot_token" type="password" autocomplete="off" placeholder="${state.telegramSettings?.has_bot_token ? "Token konfiguriert" : "Telegram Bot Token"}" />
          </label>
          <label for="telegram-chat-id">Chat ID
            <input id="telegram-chat-id" name="telegram_chat_id" type="text" autocomplete="off" spellcheck="false" inputmode="numeric"
              value="${escapeHtml(state.telegramSettings?.telegram_chat_id || "")}"
              placeholder="z.B. -1001234567890" />
          </label>
          <button type="submit">Speichern</button>
        </form>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Nuki Smartlock</h2>
            <p class="panel-kicker">Smartlock-API und Einstellungen.</p>
          </div>
        </div>
        <form id="nuki-form" class="stack">
          <label for="nuki-api-token">API Token
            <input id="nuki-api-token" name="nuki_api_token" type="password" autocomplete="off"
              placeholder="${state.nukiSettings?.has_api_token ? "Token konfiguriert" : "Nuki Web API Token"}" />
          </label>
          <label for="nuki-smartlock-id">Smartlock ID
            <input id="nuki-smartlock-id" name="nuki_smartlock_id" type="number" inputmode="numeric"
              value="${escapeHtml(state.nukiSettings?.nuki_smartlock_id ?? 0)}" />
          </label>
          <label class="checkbox-row" for="nuki-dry-run">
            <input id="nuki-dry-run" name="nuki_dry_run" type="checkbox" ${state.nukiSettings?.nuki_dry_run !== false ? "checked" : ""} />
            <span>Testmodus (Dry Run)</span>
          </label>
          <button type="submit">Speichern</button>
        </form>
      </section>

      <section class="panel span-6">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Magicline</h2>
            <p class="panel-kicker">Integration der Mitgliederverwaltung.</p>
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
              placeholder="${state.magiclineSettings?.has_api_key ? "Key konfiguriert" : "Magicline API Key"}" />
          </label>
          <label for="magicline-studio-id">Studio ID
            <input id="magicline-studio-id" name="magicline_studio_id" type="number" inputmode="numeric"
              value="${escapeHtml(state.magiclineSettings?.magicline_studio_id ?? 0)}" />
          </label>
          <button type="submit">Speichern</button>
        </form>
      </section>

      <section class="panel span-12">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">Studio Branding</h2>
            <p class="panel-kicker">Markenidentität für E-Mails und Kundenkommunikation.</p>
          </div>
        </div>
        <div class="dashboard-grid">
          <div class="span-6">
            <form id="branding-logo-form" class="stack">
              <label>Studio Logo
                <div class="logo-preview-container mb-8">
                  ${state.brandingSettings?.logo_url ? `<img src="${state.brandingSettings.logo_url}" alt="Logo" class="logo-preview">` : '<div class="empty">Kein Logo hochgeladen</div>'}
                </div>
                <input type="file" id="logo-upload" accept="image/*" class="secondary" />
              </label>
              <label for="logo-link-url">Logo Link (Optional)
                <input id="logo-link-url" name="logo_link_url" type="url" value="${escapeHtml(state.brandingSettings?.logo_link_url || "")}" placeholder="https://ihre-webseite.de" />
              </label>
              <button type="submit" class="secondary">Link speichern</button>
            </form>
            
            <form id="branding-colors-form" class="stack mt-24">
              <div class="split">
                <label for="accent-color">Akzentfarbe
                  <input type="color" id="accent-color" name="accent_color" value="${state.brandingSettings?.accent_color || "#2563eb"}" />
                </label>
                <label for="header-bg">Header Hintergrund
                  <input type="color" id="header-bg" name="header_bg_color" value="${state.brandingSettings?.header_bg_color || "#ffffff"}" />
                </label>
              </div>
              <div class="split">
                <label for="body-bg">E-Mail Hintergrund
                  <input type="color" id="body-bg" name="body_bg_color" value="${state.brandingSettings?.body_bg_color || "#f9f9f9"}" />
                </label>
                <label for="footer-bg">Footer Hintergrund
                  <input type="color" id="footer-bg" name="footer_bg_color" value="${state.brandingSettings?.footer_bg_color || "#ffffff"}" />
                </label>
              </div>
              <button type="submit">Farben speichern</button>
            </form>
          </div>
          <div class="span-6">
            <form id="branding-social-form" class="stack">
              <label for="social-instagram">Instagram URL
                <input id="social-instagram" name="instagram_url" type="url" value="${escapeHtml(state.brandingSettings?.instagram_url || "")}" placeholder="https://instagram.com/..." />
              </label>
              <label for="social-facebook">Facebook URL
                <input id="social-facebook" name="facebook_url" type="url" value="${escapeHtml(state.brandingSettings?.facebook_url || "")}" placeholder="https://facebook.com/..." />
              </label>
              <label for="social-tiktok">TikTok URL
                <input id="social-tiktok" name="tiktok_url" type="url" value="${escapeHtml(state.brandingSettings?.tiktok_url || "")}" placeholder="https://tiktok.com/@..." />
              </label>
              <label for="social-youtube">YouTube URL
                <input id="social-youtube" name="youtube_url" type="url" value="${escapeHtml(state.brandingSettings?.youtube_url || "")}" placeholder="https://youtube.com/c/..." />
              </label>
              <label for="footer-text">Footer Text (Adresse, Impressum)
                <textarea id="footer-text" name="footer_text" rows="4" placeholder="Ihr Studio Name&#10;Musterstraße 1&#10;12345 Stadt">${escapeHtml(state.brandingSettings?.footer_text || "")}</textarea>
              </label>
              <button type="submit">Details speichern</button>
            </form>
          </div>
        </div>
      </section>

      <section class="panel span-12">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">E-Mail Vorlagen</h2>
            <p class="panel-kicker">Design der ausgehenden Benachrichtigungen für Zugangscodes.</p>
          </div>
        </div>
        <div class="template-editor-container">
          <div class="editor-main">
            <form id="email-template-form" class="stack">
              <label for="tpl-access-code-body">Inhalt der E-Mail (WYSIWYG Editor)
                <div id="email-editor" style="height: 300px; background: white; color: black;"></div>
                <input type="hidden" id="tpl-access-code-body" name="access_code_body_html" value="${escapeHtml(state.emailTemplate?.access_code_body_html || "")}" />
              </label>
              <div class="row mt-16" style="gap: 12px;">
                <button type="submit">Template Speichern</button>
                <button type="button" id="email-template-preview-btn" class="secondary">Vorschau aktualisieren</button>
              </div>
            </form>
            <div class="mt-24">
              <p class="subtle"><strong>Verfügbare Platzhalter:</strong></p>
              <div class="row mt-8" style="gap: 8px; flex-wrap: wrap;">
                <code class="code">{member_name}</code>
                <code class="code">{code}</code>
                <code class="code">{valid_from}</code>
                <code class="code">{valid_until}</code>
                <code class="code">{check_in_url}</code>
              </div>
            </div>
          </div>
          <div class="editor-preview">
            <p class="health-label mb-8">Live Vorschau</p>
            <div class="preview-frame-wrapper">
              <iframe id="email-preview-frame" title="E-Mail Vorschau"></iframe>
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
  if (state.view === "funnels") return renderFunnelsView();
  return renderOverview();
}

function renderAuth() {
  app.innerHTML = `
    <section class="auth-shell">
      <h1>Studio Access</h1>
      <p class="subtle" style="text-align: center; margin-bottom: 24px;">Melden Sie sich an, um die Studio-Zugänge zu verwalten.</p>
      
      <div class="message ${state.messageType}" aria-live="polite">${escapeHtml(state.message || "Bitte geben Sie Ihre Zugangsdaten ein.")}</div>
      
      <form id="login-form" class="stack mt-16">
        <label for="login-email">E-Mail Adresse
          <input id="login-email" name="email" type="email" required autocomplete="email" placeholder="ihre@email.de" />
        </label>
        <label for="login-password">Passwort
          <input id="login-password" name="password" type="password" required autocomplete="current-password" placeholder="••••••••" />
        </label>
        <button type="submit">Anmelden</button>
      </form>
      <form id="forgot-form" class="stack mt-16">
        <label for="forgot-email">Passwort vergessen?
          <input id="forgot-email" name="email" type="email" required autocomplete="email" placeholder="E-Mail für Reset-Link" />
        </label>
        <button type="submit" class="secondary">Reset-Link anfordern</button>
      </form>
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
          <h1 class="brand-title">Studio Access</h1>
        </div>

        <div class="mobile-toolbar" aria-label="Mobile Navigation">
          ${navButton("overview", "Dashboard")}
          ${navButton("members", "Mitglieder")}
          ${navButton("windows", "Zugangsfenster")}
          ${navButton("lock", "Schloss")}
          ${navButton("alerts", "Alarme")}
          ${navButton("funnels", "Funnels")}
          ${navButton("settings", "Einstellungen")}
        </div>

        <nav class="sidebar-nav" aria-label="Hauptnavigation">
          ${navButton("overview", "Dashboard")}
          ${navButton("members", "Mitglieder")}
          ${navButton("windows", "Zugangsfenster")}
          ${navButton("lock", "Schloss")}
          ${navButton("alerts", "Alarme")}
          ${navButton("funnels", "Funnels")}
          ${navButton("settings", "Einstellungen")}
        </nav>

        <div class="sidebar-footer">
          <p class="subtle">Angemeldet als:</p>
          <strong style="display: block; margin-bottom: 4px;">${escapeHtml(state.me?.email || "Admin")}</strong>
          <div class="row">
            ${state.nukiSettings?.nuki_dry_run ? pill("Testmodus") : pill("Live-Betrieb")}
          </div>
          
          <button type="button" id="logout-button" class="nav-button mt-16" style="color: var(--bad); padding: 8px 0; border-top: 1px solid var(--line); border-radius: 0;">
            <span class="nav-icon"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="18" height="18"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3 0 3-3m0 0-3-3m3 3H9" /></svg></span>
            <span class="nav-label">Abmelden</span>
          </button>
        </div>
      </aside>

      <section class="content">
        <header class="topbar">
          <div class="hero-card">
            <div class="hero-copy">
              <h2 class="hero-title">Studio Management</h2>
              <p class="subtle">Zentrale Steuerung Ihrer Studiozugänge und Systemintegrationen.</p>
              
              <div class="system-health-row mt-16">
                <div class="health-item">
                  <span class="health-label">Nuki Smartlock</span>
                  ${pill(state.lockStatus?.connectivity === "online" ? "Verbunden" : "Eingriff nötig")}
                </div>
                <div class="health-item">
                  <span class="health-label">Magicline API</span>
                  ${pill(state.magiclineSettings?.has_api_key ? "Aktiv" : "Nicht konfiguriert")}
                </div>
                <div class="health-item">
                  <span class="health-label">E-Mail Service</span>
                  ${pill(state.emailSettings?.smtp_host ? "Bereit" : "Inaktiv")}
                </div>
              </div>

              ${state.message ? `<div class="message ${state.messageType} mt-16" aria-live="polite">${escapeHtml(state.message)}</div>` : ""}
            </div>
            <div class="hero-copy">
              <h3 class="panel-title">${escapeHtml(current?.email || "System-Status")}</h3>
              <p class="subtle">${current ? "Mitglieds-Detailansicht aktiv" : "Übersicht der Studio-Parameter"}</p>
              <div class="system-health-row mt-12">
                <div class="health-item">
                  <span class="health-label">Fenster</span>
                  ${pill(`${state.windows.length} aktiv`)}
                </div>
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
