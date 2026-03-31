import os
import re

# Read the local app.js
with open("src/nuki_integration/static/assets/app.js", "r") as f:
    content = f.read()

# 1. Update setMessage to be in-place
old_setMessage = """function setMessage(text, type = "") {
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
}"""

new_setMessage = """function setMessage(text, type = "") {
  state.message = text;
  state.messageType = type;
  clearTimeout(_messageDismissTimer);
  
  const msgEl = document.querySelector(".message");
  if (msgEl) {
    msgEl.textContent = text;
    msgEl.className = `message ${type} mt-16`;
    msgEl.style.display = text ? "block" : "none";
  } else {
    render();
  }

  if (text && type !== "bad") {
    _messageDismissTimer = setTimeout(() => {
      state.message = "";
      state.messageType = "";
      const msgEl2 = document.querySelector(".message");
      if (msgEl2) {
        msgEl2.textContent = "";
        msgEl2.style.display = "none";
      } else {
        render();
      }
    }, 5000);
  }
}"""

# 2. Add professional SaaS functions
new_saas_functions = """
async function updateBrandingSocial(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        instagram_url: document.getElementById("social-ig")?.value || "",
        facebook_url: document.getElementById("social-fb")?.value || "",
        footer_text: document.getElementById("footer-text-input")?.value || "",
      }),
    });
    setMessage("Branding Details gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}

async function updateBrandingColors(event) {
  event.preventDefault();
  await withPending(event.currentTarget, async () => {
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        accent_color: document.getElementById("color-accent")?.value,
        header_bg_color: document.getElementById("color-header")?.value,
        body_bg_color: document.getElementById("color-body")?.value,
        footer_bg_color: document.getElementById("color-footer")?.value,
        logo_link_url: document.getElementById("logo-link-url-input")?.value || "",
      }),
    });
    setMessage("Design-Einstellungen gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}

async function sendTestEmailAction() {
  await withPending(document.getElementById("send-test-email-btn"), async () => {
    await api("./admin/system/email-test-code", {
      method: "POST",
      body: JSON.stringify({ to_email: state.me.email }),
    });
    setMessage(`Test-E-Mail an ${state.me.email} gesendet.`, "good");
  }, "Wird gesendet…");
}

async function saveFullTemplateAction() {
  const content = document.getElementById("tpl-access-code-body")?.value || "";
  await withPending(document.getElementById("save-full-template"), async () => {
    await api("./admin/system/branding", {
      method: "PUT",
      body: JSON.stringify({
        instagram_url: document.getElementById("social-ig")?.value || "",
        facebook_url: document.getElementById("social-fb")?.value || "",
        footer_text: document.getElementById("footer-text-input")?.value || "",
      }),
    });
    await api("./admin/system/email-template", {
      method: "PUT",
      body: JSON.stringify({
        header_html: state.emailTemplate?.header_html || "",
        body_html: state.emailTemplate?.body_html || "",
        footer_html: state.emailTemplate?.footer_html || "",
        access_code_body_html: content,
        reset_body_html: state.emailTemplate?.reset_body_html || "",
      }),
    });
    setMessage("E-Mail Vorlage und Branding gespeichert.", "good");
    await loadAppData();
  }, "Wird gespeichert…");
}
"""

# 3. Final attachSettingsHandlers
new_attachSettingsHandlers = """function attachSettingsHandlers() {
  document.getElementById("smtp-form")?.addEventListener("submit", (event) => updateSmtp(event).catch(handleError));
  document.getElementById("telegram-form")?.addEventListener("submit", (event) => updateTelegram(event).catch(handleError));
  document.getElementById("nuki-form")?.addEventListener("submit", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById("magicline-form")?.addEventListener("submit", (event) => updateMagiclineSettings(event).catch(handleError));
  
  document.getElementById("save-branding-core")?.addEventListener("click", (event) => updateBrandingColors(event).catch(handleError));
  document.getElementById("save-full-template")?.addEventListener("click", () => saveFullTemplateAction().catch(handleError));
  document.getElementById("send-test-email-btn")?.addEventListener("click", () => sendTestEmailAction().catch(handleError));
  document.getElementById("logo-upload")?.addEventListener("change", (event) => uploadLogo(event).catch(handleError));

  const editorEl = document.getElementById("email-editor");
  if (editorEl && typeof Quill !== "undefined") {
    const quill = new Quill("#email-editor", {
      theme: "snow",
      modules: {
        toolbar: [
          [{ header: [1, 2, false] }],
          ["bold", "italic", "underline"],
          [{ color: [] }, { background: [] }],
          [{ list: "ordered" }, { list: "bullet" }],
          ["link", "clean"],
        ],
      },
    });

    const hiddenInput = document.getElementById("tpl-access-code-body");
    if (hiddenInput && (hiddenInput.value || state.emailTemplate?.access_code_body_html)) {
      quill.root.innerHTML = hiddenInput.value || state.emailTemplate.access_code_body_html;
    }

    const updatePreview = () => {
      const htmlContent = quill.root.innerHTML;
      if (hiddenInput) hiddenInput.value = htmlContent;
      
      // PERSIST TO STATE IMMEDIATELY so re-renders don't wipe it
      if (state.emailTemplate) {
        state.emailTemplate.access_code_body_html = htmlContent;
      }
      
      const frame = document.getElementById("email-preview-frame");
      if (frame) {
        const logoImg = state.brandingSettings?.logo_url 
          ? `<img src="${state.brandingSettings.logo_url}" alt="Logo" style="max-width: 200px; height: auto; display: block; margin: 0 auto;">`
          : "";
        
        const logoHtml = state.brandingSettings?.logo_link_url
          ? `<a href="${state.brandingSettings.logo_link_url}" style="text-decoration:none;">${logoImg}</a>`
          : logoImg;
        
        const socials = [
          ["instagram", document.getElementById("social-ig")?.value || state.brandingSettings?.instagram_url],
          ["facebook", document.getElementById("social-fb")?.value || state.brandingSettings?.facebook_url],
        ].filter(s => s[1]);

        let socialHtml = "";
        if (socials.length > 0) {
          const baseUrl = window.location.origin;
          const items = socials.map(([name, url]) => 
            `<a href="${url}" style="margin: 0 10px; text-decoration: none;"><img src="${baseUrl}/assets/icon-${name}.png" alt="${name}" width="24" height="24"></a>`
          ).join("");
          socialHtml = `<div style="text-align:center; padding: 20px; border-top: 1px solid #eee; margin-top: 20px;">${items}</div>`;
        }

        const footerText = document.getElementById("footer-text-input")?.value || state.brandingSettings?.footer_text || "";
        const footerTextHtml = footerText
          ? `<div style="padding: 20px; text-align: center; font-size: 13px; color: #64748b; border-top: 1px solid #eee;">${footerText.replace(/\\n/g, "<br>")}</div>`
          : "";

        const fullHtml = `
          <!DOCTYPE html>
          <html>
            <head>
              <style>
                body { font-family: sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: ${document.getElementById("color-body")?.value || state.brandingSettings?.body_bg_color || "#f9f9f9"}; }
                .wrapper { width: 100%; table-layout: fixed; background-color: ${document.getElementById("color-body")?.value || state.brandingSettings?.body_bg_color || "#f9f9f9"}; padding-bottom: 40px; padding-top: 40px; }
                .main { width: 100%; max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
                .header { background-color: ${document.getElementById("color-header")?.value || state.brandingSettings?.header_bg_color || "#ffffff"}; border-bottom: 1px solid #eee; }
                .content { padding: 30px; }
                .footer-container { background-color: ${document.getElementById("color-footer")?.value || state.brandingSettings?.footer_bg_color || "#ffffff"}; }
                .legal { padding: 20px; text-align: center; font-size: 12px; color: #94a3b8; }
                .btn { display:inline-block; padding: 12px 24px; background-color: ${document.getElementById("color-accent")?.value || state.brandingSettings?.accent_color || "#2563eb"}; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; }
              </style>
            </head>
            <body>
              <div class="wrapper">
                <div class="main">
                  <div class="header">
                    <div style="text-align:center; padding: 20px;">\${logoHtml}</div>
                  </div>
                  <div class="content">
                    \${htmlContent.replace(/{member_name}/g, "Max Mustermann")
                             .replace(/{code}/g, "123456")
                             .replace(/{valid_from}/g, "01.01.2026, 10:00 Uhr")
                             .replace(/{valid_until}/g, "01.01.2026, 12:00 Uhr")
                             .replace(/{check_in_url}/g, '<a href="#" class="btn">Check-In & Hausordnung</a>')}
                  </div>
                  <div class="footer-container">
                    \${socialHtml}
                    \${footerTextHtml}
                  </div>
                </div>
                <div class="legal">&copy; \${new Date().getFullYear()} Studio Access Management</div>
              </div>
            </body>
          </html>
        `;
        frame.srcdoc = fullHtml;
      }
    };

    quill.on("text-change", updatePreview);
    
    // Placeholder insertion
    document.querySelectorAll(".placeholder-chip").forEach(chip => {
      chip.addEventListener("click", () => {
        const range = quill.getSelection();
        if (range) {
          quill.insertText(range.index, chip.dataset.insert);
        } else {
          quill.insertText(quill.getLength() - 1, chip.dataset.insert);
        }
        updatePreview();
      });
    });

    // Preview Toggles
    document.querySelectorAll(".preview-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".preview-toggle").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const previewWindow = document.getElementById("preview-window");
        if (btn.dataset.mode === "mobile") previewWindow.classList.add("mobile");
        else previewWindow.classList.remove("mobile");
      });
    });

    // Initial preview
    setTimeout(updatePreview, 100);
  }
}"""

# 4. Final renderSettingsView
new_renderSettingsView = """function renderSettingsView() {
  if (state.role !== "admin") {
    return `<section class="detail-card"><h2 class="panel-title">Zugriff verweigert</h2><p class="subtle">Nur Administratoren können die Systemeinstellungen verwalten.</p></section>`;
  }
  return `
    <div class="stack" style="gap: 32px;">
      <!-- Integration Section -->
      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">Kern-Integrationen</h2>
          <p class="panel-kicker">Verbindung zu Magicline, Nuki und E-Mail-Server.</p>
        </div>
        <div class="dashboard-grid">
          <div class="span-6">
            <form id="magicline-form" class="stack">
              <h3 class="health-label">Magicline API</h3>
              <label>API-URL <input name="magicline_base_url" type="text" value="${escapeHtml(state.magiclineSettings?.magicline_base_url || "")}" placeholder="https://app.magicline.com" /></label>
              <label>API Key <input name="magicline_api_key" type="password" placeholder="\${state.magiclineSettings?.has_api_key ? "Key konfiguriert" : "Magicline API Key"}" /></label>
              <label>Studio ID <input name="magicline_studio_id" type="number" value="\${state.magiclineSettings?.magicline_studio_id ?? 0}" /></label>
              <button type="submit">Magicline Speichern</button>
            </form>
          </div>
          <div class="span-6">
            <form id="nuki-form" class="stack">
              <h3 class="health-label">Nuki Smartlock</h3>
              <label>API Token <input name="nuki_api_token" type="password" placeholder="\${state.nukiSettings?.has_api_token ? "Token konfiguriert" : "Nuki Web API Token"}" /></label>
              <label>Smartlock ID <input name="nuki_smartlock_id" type="number" value="\${state.nukiSettings?.nuki_smartlock_id ?? 0}" /></label>
              <label class="checkbox-row"><input name="nuki_dry_run" type="checkbox" \${state.nukiSettings?.nuki_dry_run ? "checked" : ""} /><span>Testmodus (Dry Run)</span></label>
              <button type="submit">Nuki Speichern</button>
            </form>
          </div>
          <div class="span-6 mt-24">
            <form id="smtp-form" class="stack">
              <h3 class="health-label">E-Mail Server (SMTP)</h3>
              <label>Host <input name="smtp_host" type="text" value="\${escapeHtml(state.emailSettings?.smtp_host || "")}" /></label>
              <div class="split">
                <label>Port <input name="smtp_port" type="number" value="\${state.emailSettings?.smtp_port || 587}" /></label>
                <label class="checkbox-row mt-24"><input name="smtp_use_tls" type="checkbox" \${state.emailSettings?.smtp_use_tls ? "checked" : ""} /><span>TLS</span></label>
              </div>
              <label>Benutzername <input name="smtp_username" type="text" value="\${escapeHtml(state.emailSettings?.smtp_username || "")}" /></label>
              <label>Passwort <input name="smtp_password" type="password" placeholder="Passwort" /></label>
              <label>Absender-E-Mail <input name="smtp_from_email" type="email" value="\${escapeHtml(state.emailSettings?.smtp_from_email || "")}" /></label>
              <button type="submit">SMTP Speichern</button>
            </form>
          </div>
          <div class="span-6 mt-24">
            <form id="telegram-form" class="stack">
              <h3 class="health-label">Telegram Benachrichtigungen</h3>
              <label>Bot Token <input name="telegram_bot_token" type="password" placeholder="\${state.telegramSettings?.has_bot_token ? "Token konfiguriert" : "Bot Token"}" /></label>
              <label>Chat ID <input name="telegram_chat_id" type="text" value="\${escapeHtml(state.telegramSettings?.telegram_chat_id || "")}" /></label>
              <button type="submit">Telegram Speichern</button>
            </form>
          </div>
        </div>
      </section>

      <!-- Branding & Editor Section -->
      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">Design & Kundenkommunikation</h2>
          <p class="panel-kicker">Passen Sie das Erscheinungsbild Ihrer E-Mails an Ihr Studio an.</p>
        </div>
        
        <div class="editor-shell">
          <div class="editor-workspace">
            <div class="branding-grid">
              <div class="stack">
                <h3 class="health-label">Studio Logo</h3>
                <div class="logo-preview-container">
                  \${state.brandingSettings?.logo_url ? `<img src="\${state.brandingSettings.logo_url}" class="logo-preview">` : '<p class="subtle">Kein Logo</p>'}
                </div>
                <input type="file" id="logo-upload" accept="image/*" class="secondary" />
                <label>Logo Link URL <input id="logo-link-url-input" type="url" value="\${escapeHtml(state.brandingSettings?.logo_link_url || "")}" placeholder="https://..." /></label>
              </div>
              
              <div class="stack">
                <h3 class="health-label">Farben & Kontrast</h3>
                <div class="color-picker-grid">
                  <div class="color-input-item"><label>Akzent</label><input type="color" id="color-accent" value="\${state.brandingSettings?.accent_color || "#2563eb"}" /></div>
                  <div class="color-input-item"><label>Header</label><input type="color" id="color-header" value="\${state.brandingSettings?.header_bg_color || "#ffffff"}" /></div>
                  <div class="color-input-item"><label>Hintergrund</label><input type="color" id="color-body" value="\${state.brandingSettings?.body_bg_color || "#f9f9f9"}" /></div>
                  <div class="color-input-item"><label>Footer</label><input type="color" id="color-footer" value="\${state.brandingSettings?.footer_bg_color || "#ffffff"}" /></div>
                </div>
                <button type="button" id="save-branding-core" class="secondary mt-8">Design speichern</button>
              </div>
            </div>

            <div class="stack mt-24">
              <h3 class="health-label">E-Mail Inhalt (WYSIWYG)</h3>
              <div id="email-editor"></div>
              <input type="hidden" id="tpl-access-code-body" value="\${escapeHtml(state.emailTemplate?.access_code_body_html || "")}" />
              
              <div class="placeholder-chip-container">
                <span class="subtle" style="width: 100%; margin-bottom: 4px;">Variablen einfügen:</span>
                <button type="button" class="placeholder-chip" data-insert="{member_name}">Mitgliedsname</button>
                <button type="button" class="placeholder-chip" data-insert="{code}">Zugangscode</button>
                <button type="button" class="placeholder-chip" data-insert="{valid_from}">Gültig ab</button>
                <button type="button" class="placeholder-chip" data-insert="{valid_until}">Gültig bis</button>
                <button type="button" class="placeholder-chip" data-insert="{check_in_url}">Check-In Button</button>
              </div>
            </div>

            <div class="stack mt-24">
              <h3 class="health-label">Social Media & Footer</h3>
              <div class="branding-grid">
                <label>Instagram <input id="social-ig" type="url" value="\${escapeHtml(state.brandingSettings?.instagram_url || "")}" /></label>
                <label>Facebook <input id="social-fb" type="url" value="\${escapeHtml(state.brandingSettings?.facebook_url || "")}" /></label>
              </div>
              <label>Footer Text (Adresse / Impressum)
                <textarea id="footer-text-input" rows="3">\${escapeHtml(state.brandingSettings?.footer_text || "")}</textarea>
              </label>
              <div class="row" style="gap: 12px;">
                <button type="button" id="save-full-template">E-Mail Vorlage Speichern</button>
                <button type="button" id="send-test-email-btn" class="secondary">Test-Mail an mich</button>
              </div>
            </div>
          </div>

          <div class="editor-preview-pane">
            <div class="preview-controls">
              <button type="button" class="preview-toggle active" data-mode="desktop">Desktop</button>
              <button type="button" class="preview-toggle" data-mode="mobile">Mobil</button>
            </div>
            <div class="preview-window" id="preview-window">
              <iframe id="email-preview-frame" title="Vorschau"></iframe>
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}"""

# Apply replacements
content = content.replace("function setMessage(text, type = \"\") {", new_setMessage if "function setMessage(text, type = \"\") {" in content else "function setMessage(text, type = \"\") {")
# This is a bit complex due to nested braces. 
# I will instead look for markers or just use re.sub for functions if possible.

# Let's try to just write the final file. I have most parts.
# I'll construct it from what I read.

with open("src/nuki_integration/static/assets/app.js", "w") as f:
    # This is risky if I miss something.
    # Better: use re to find function boundaries.
    pass

# I'll just use the patch script to replace EXACT strings found earlier.
# If I can't find them, I'll use a broader search.
