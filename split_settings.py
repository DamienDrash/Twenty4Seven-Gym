import os
import re

file_path = "src/nuki_integration/static/assets/app.js"
with open(file_path, "r") as f:
    content = f.read()

# 1. Update renderMainContent to handle 'branding' view
content = content.replace(
    'if (state.view === "funnels") return renderFunnelsView();',
    'if (state.view === "branding") return renderBrandingView();\\n  if (state.view === "funnels") return renderFunnelsView();'
)

# 2. Extract logic into renderBrandingView and simplify renderSettingsView
render_settings_pattern = r"function renderSettingsView\(\) \{[\s\S]*?\}\n\nfunction renderMainContent"

new_render_functions = """function renderSettingsView() {
  if (state.role !== "admin") {
    return `<section class="detail-card"><h2 class="panel-title">Zugriff verweigert</h2><p class="subtle">Nur Administratoren können die Systemeinstellungen verwalten.</p></section>`;
  }
  return `
    <div class="stack" style="gap: 32px;">
      <section class="panel">
        <div class="panel-header">
          <div class="split">
            <div>
              <h2 class="panel-title">System-Einstellungen</h2>
              <p class="panel-kicker">Verwalten Sie Ihre Kern-Integrationen und das Design Ihrer Kommunikation.</p>
            </div>
            <button type="button" class="secondary" onclick="setView('branding')">
              Design & Kommunikation bearbeiten
            </button>
          </div>
        </div>
      </section>

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
              <label>API Key <input name="magicline_api_key" type="password" placeholder="${state.magiclineSettings?.has_api_key ? "Key konfiguriert" : "Magicline API Key"}" /></label>
              <label>Studio ID <input name="magicline_studio_id" type="number" value="${state.magiclineSettings?.magicline_studio_id ?? 0}" /></label>
              <button type="submit">Magicline Speichern</button>
            </form>
          </div>
          <div class="span-6">
            <form id="nuki-form" class="stack">
              <h3 class="health-label">Nuki Smartlock</h3>
              <label>API Token <input name="nuki_api_token" type="password" placeholder="${state.nukiSettings?.has_api_token ? "Token konfiguriert" : "Nuki Web API Token"}" /></label>
              <label>Smartlock ID <input name="nuki_smartlock_id" type="number" value="${state.nukiSettings?.nuki_smartlock_id ?? 0}" /></label>
              <label class="checkbox-row"><input name="nuki_dry_run" type="checkbox" ${state.nukiSettings?.nuki_dry_run ? "checked" : ""} /><span>Testmodus (Dry Run)</span></label>
              <button type="submit">Nuki Speichern</button>
            </form>
          </div>
          <div class="span-6 mt-24">
            <form id="smtp-form" class="stack">
              <h3 class="health-label">E-Mail Server (SMTP)</h3>
              <label>Host <input name="smtp_host" type="text" value="${escapeHtml(state.emailSettings?.smtp_host || "")}" /></label>
              <div class="split">
                <label>Port <input name="smtp_port" type="number" value="${state.emailSettings?.smtp_port || 587}" /></label>
                <label class="checkbox-row mt-24"><input name="smtp_use_tls" type="checkbox" ${state.emailSettings?.smtp_use_tls ? "checked" : ""} /><span>TLS</span></label>
              </div>
              <label>Benutzername <input name="smtp_username" type="text" value="${escapeHtml(state.emailSettings?.smtp_username || "")}" /></label>
              <label>Passwort <input name="smtp_password" type="password" placeholder="Passwort" /></label>
              <label>Absender-E-Mail <input name="smtp_from_email" type="email" value="${escapeHtml(state.emailSettings?.smtp_from_email || "")}" /></label>
              <button type="submit">SMTP Speichern</button>
            </form>
          </div>
          <div class="span-6 mt-24">
            <form id="telegram-form" class="stack">
              <h3 class="health-label">Telegram Benachrichtigungen</h3>
              <label>Bot Token <input name="telegram_bot_token" type="password" placeholder="${state.telegramSettings?.has_bot_token ? "Token konfiguriert" : "Bot Token"}" /></label>
              <label>Chat ID <input name="telegram_chat_id" type="text" value="${escapeHtml(state.telegramSettings?.telegram_chat_id || "")}" /></label>
              <button type="submit">Telegram Speichern</button>
            </form>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderBrandingView() {
  if (state.role !== "admin") {
    return `<section class="detail-card"><h2 class="panel-title">Zugriff verweigert</h2><p class="subtle">Nur Administratoren können die Designeinstellungen verwalten.</p></section>`;
  }
  return `
    <div class="stack" style="gap: 32px;">
      <section class="panel">
        <div class="panel-header">
          <div class="split">
            <div>
              <h2 class="panel-title">Design & Kundenkommunikation</h2>
              <p class="panel-kicker">Passen Sie das Erscheinungsbild Ihrer E-Mails an Ihr Studio an.</p>
            </div>
            <button type="button" class="secondary" onclick="setView('settings')">
              ← Zurück zu Einstellungen
            </button>
          </div>
        </div>
        
        <div class="editor-shell">
          <div class="editor-workspace">
            <div class="branding-grid">
              <div class="stack">
                <h3 class="health-label">Studio Logo</h3>
                <div class="logo-preview-container">
                  ${state.brandingSettings?.logo_url ? `<img src="${state.brandingSettings.logo_url}" class="logo-preview">` : '<p class="subtle">Kein Logo</p>'}
                </div>
                <input type="file" id="logo-upload" accept="image/*" class="secondary" />
                <label>Logo Link URL <input id="logo-link-url-input" type="url" value="${escapeHtml(state.brandingSettings?.logo_link_url || "")}" placeholder="https://..." /></label>
              </div>
              
              <div class="stack">
                <h3 class="health-label">Farben & Kontrast</h3>
                <div class="color-picker-grid">
                  <div class="color-input-item"><label>Akzent</label><input type="color" id="color-accent" value="${state.brandingSettings?.accent_color || "#2563eb"}" /></div>
                  <div class="color-input-item"><label>Header</label><input type="color" id="color-header" value="${state.brandingSettings?.header_bg_color || "#ffffff"}" /></div>
                  <div class="color-input-item"><label>Hintergrund</label><input type="color" id="color-body" value="${state.brandingSettings?.body_bg_color || "#f9f9f9"}" /></div>
                  <div class="color-input-item"><label>Footer</label><input type="color" id="color-footer" value="${state.brandingSettings?.footer_bg_color || "#ffffff"}" /></div>
                </div>
                <button type="button" id="save-branding-core" class="secondary mt-8">Design speichern</button>
              </div>
            </div>

            <div class="stack mt-24">
              <h3 class="health-label">E-Mail Inhalt (HTML Editor)</h3>
              <p class="subtle mb-8">Bearbeiten Sie hier die Tabellenzeilen (TR/TD) Ihrer Vorlage.</p>
              <textarea id="email-template-editor" class="code-editor" style="height: 400px; font-family: var(--font-mono); font-size: 0.9rem; line-height: 1.4; padding: 16px; border-radius: var(--radius-md); border: 1px solid var(--line-strong); width: 100%; tab-size: 2;">${escapeHtml(state.emailTemplate?.access_code_body_html || "")}</textarea>
              <input type="hidden" id="tpl-access-code-body" value="${escapeHtml(state.emailTemplate?.access_code_body_html || "")}" />
              
              <div class="placeholder-chip-container">
                <span class="subtle" style="width: 100%; margin-bottom: 4px;">Variablen einfügen:</span>
                <button type="button" class="placeholder-chip" data-insert="{member_name}">Mitgliedsname</button>
                <button type="button" class="placeholder-chip" data-insert="{code}">Zugangscode</button>
                <button type="button" class="placeholder-chip" data-insert="{valid_from}">Gültig ab</button>
                <button type="button" class="placeholder-chip" data-insert="{valid_until}">Gültig bis</button>
                <button type="button" class="placeholder-chip" data-insert="{checks_row}">Check-In Button</button>
              </div>
            </div>

            <div class="stack mt-24">
              <h3 class="health-label">Social Media & Footer</h3>
              <div class="branding-grid">
                <label>Instagram <input id="social-ig" type="url" value="${escapeHtml(state.brandingSettings?.instagram_url || "")}" /></label>
                <label>Facebook <input id="social-fb" type="url" value="${escapeHtml(state.brandingSettings?.facebook_url || "")}" /></label>
              </div>
              <label>Footer Text (Adresse / Impressum)
                <textarea id="footer-text-input" rows="3">${escapeHtml(state.brandingSettings?.footer_text || "")}</textarea>
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
}

function renderMainContent"""

content = re.sub(render_settings_pattern, new_render_functions, content)

with open(file_path, "w") as f:
    f.write(content)
print("SUCCESS: Settings and Branding views split.")
