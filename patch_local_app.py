import os
import re

file_path = "src/nuki_integration/static/assets/app.js"
with open(file_path, "r") as f:
    content = f.read()

new_handlers = """function attachSettingsHandlers() {
  document.getElementById("smtp-form")?.addEventListener("submit", (event) => updateSmtp(event).catch(handleError));
  document.getElementById("telegram-form")?.addEventListener("submit", (event) => updateTelegram(event).catch(handleError));
  document.getElementById("nuki-form")?.addEventListener("submit", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById("magicline-form")?.addEventListener("submit", (event) => updateMagiclineSettings(event).catch(handleError));
  
  document.getElementById("save-branding-core")?.addEventListener("click", (event) => updateBrandingColors(event).catch(handleError));
  document.getElementById("save-full-template")?.addEventListener("click", () => saveFullTemplateAction().catch(handleError));
  document.getElementById("send-test-email-btn")?.addEventListener("click", () => sendTestEmailAction().catch(handleError));
  document.getElementById("logo-upload")?.addEventListener("change", (event) => uploadLogo(event).catch(handleError));

  const editorEl = document.getElementById("email-template-editor");
  if (editorEl) {
    const hiddenInput = document.getElementById("tpl-access-code-body");
    
    const updatePreview = () => {
      const htmlContent = editorEl.value;
      if (hiddenInput) hiddenInput.value = htmlContent;
      
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
            `<td style="padding:0 10px;"><a href="${url}" title="${name.charAt(0).toUpperCase() + name.slice(1)}" style="display:inline-block;"><img src="${baseUrl}/assets/icon-${name}.png" alt="${name}" width="26" height="26"></a></td>`
          ).join("");
          socialHtml = `<tr><td class="fbk" style="background-color:#000000;padding:40px 40px 0;text-align:center;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto 28px auto;"><tr>${items}</tr></table></td></tr>`;
        }

        const footerText = document.getElementById("footer-text-input")?.value || state.brandingSettings?.footer_text || "Heidestraße 11, 10557 Berlin<br>030 30106609";
        
        const fullHtml = `
          <!DOCTYPE html>
          <html>
            <head>
              <meta charset="UTF-8">
              <style type="text/css">
                body, table, td, p, a, li { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
                table, td { mso-table-lspace:0pt; mso-table-rspace:0pt; border-collapse:collapse; }
                img { -ms-interpolation-mode:bicubic; border:0; display:block; outline:none; }
                * { box-sizing:border-box; }
                body { margin:0!important; padding:0!important; background-color:#f0ede9!important; width:100%!important; }
                .fbb { background-color:#f0ede9!important; }
                .fbw { background-color:#ffffff!important; }
                .fbk { background-color:#000000!important; }
                .fcw { color:#ffffff!important; }
                .fcb { color:#000000!important; }
                .fcd { color:#3a3a3a!important; }
                .fcm { color:#7a7a7a!important; }
                .fbd { border-color:#e4e0db!important; }
                .fbtn { background-color:#b5ac9e!important; }
                @media only screen and (max-width:620px) {
                  .wrapper { width:100%!important; max-width:100%!important; }
                  .h1 { font-size:27px!important; line-height:1.25!important; }
                  .ph { padding-left:24px!important; padding-right:24px!important; }
                  .p-hero { padding:36px 24px 28px!important; }
                  .p-sec { padding:28px 24px!important; }
                  .p-cta { padding:4px 24px 40px!important; }
                }
              </style>
            </head>
            <body>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f0ede9;" class="fbb">
                <tr><td align="center" style="padding:28px 16px;">
                  <table role="presentation" class="wrapper" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
                    <tr>
                      <td class="fbk" style="background-color:#000000;padding:22px 40px;text-align:center;">
                        <a href="${state.brandingSettings?.logo_link_url || "#"}" style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;letter-spacing:4px;color:#ffffff;text-decoration:none;text-transform:uppercase;" class="fcw">
                          ${logoImg || "GETIMPULSE"}
                        </a>
                      </td>
                    </tr>
                    
                    ${htmlContent.replace(/{member_name}/g, "Max Mustermann")
                                 .replace(/{code}/g, "123456")
                                 .replace(/{valid_from}/g, "01.01.2026, 10:00 Uhr")
                                 .replace(/{valid_until}/g, "01.01.2026, 12:00 Uhr")
                                 .replace(/{checks_row}/g, '<a href="#" class="fbtn" style="display:inline-block;background-color:#b5ac9e;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:16px 48px;border-radius:6px;">Check-In & Hausordnung</a>')
                                 .replace(/{check_in_url}/g, "#")}
                    
                    <tr>
                      <td class="fbk" style="background-color:#000000;padding:40px 40px 32px;text-align:center;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
                          <tr><td style="border-top:1px solid #2c2c2c;font-size:0;line-height:0;">&nbsp;</td></tr>
                        </table>
                        <p class="fcw" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#ffffff;margin:0 0 10px 0;line-height:1.5;letter-spacing:0.5px;">Get-Impulse Berlin GmbH</p>
                        <div class="fcm" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 24px 0;line-height:1.7;">
                          ${footerText.replace(/\n/g, "<br>")}
                        </div>
                        <p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#4a4a4a;margin:0;line-height:1.7;">
                          Du erhältst diese E-Mail aufgrund deiner Mitgliedschaft oder Buchung.<br>
                          <a href="#" style="color:#7a7a7a;text-decoration:underline;">Datenschutz</a>&nbsp;&middot;&nbsp;<a href="#" style="color:#7a7a7a;text-decoration:underline;">Impressum</a>
                        </p>
                      </td>
                    </tr>
                  </table>
                </td></tr>
              </table>
            </body>
          </html>
        `;
        frame.srcdoc = fullHtml;
      }
    };

    editorEl.addEventListener("input", updatePreview);
    
    document.querySelectorAll(".placeholder-chip").forEach(chip => {
      chip.addEventListener("click", () => {
        const start = editorEl.selectionStart;
        const end = editorEl.selectionEnd;
        const text = editorEl.value;
        const before = text.substring(0, start);
        const after  = text.substring(end, text.length);
        editorEl.value = before + chip.dataset.insert + after;
        editorEl.selectionStart = editorEl.selectionEnd = start + chip.dataset.insert.length;
        editorEl.focus();
        updatePreview();
      });
    });

    document.querySelectorAll(".preview-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".preview-toggle").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const previewWindow = document.getElementById("preview-window");
        if (btn.dataset.mode === "mobile") previewWindow.classList.add("mobile");
        else previewWindow.classList.remove("mobile");
      });
    });

    setTimeout(updatePreview, 100);
  }

  document.getElementById("email-template-form")?.addEventListener("submit", (event) => updateEmailTemplate(event).catch(handleError));
}"""

# Use regex to replace the function block reliably
pattern = r"function attachSettingsHandlers\(\) \{[\s\S]*?\}\n\nfunction handleError"
replacement = new_handlers + "\n\nfunction handleError"
content = re.sub(pattern, replacement, content)

with open(file_path, "w") as f:
    f.write(content)
print("SUCCESS: Local app.js patched.")
