import os

with open("app_final.js", "r") as f:
    content = f.read()

# Replace the attachSettingsHandlers function
old_handlers = """function attachSettingsHandlers() {
  document.getElementById(\"smtp-form\")?.addEventListener(\"submit\", (event) => updateSmtp(event).catch(handleError));
  document.getElementById(\"telegram-form\")?.addEventListener(\"submit\", (event) => updateTelegram(event).catch(handleError));
  document.getElementById(\"nuki-form\")?.addEventListener(\"submit\", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById(\"magicline-form\")?.addEventListener(\"submit\", (event) => updateMagiclineSettings(event).catch(handleError));
  
  document.getElementById(\"branding-social-form\")?.addEventListener(\"submit\", (event) => updateBrandingSocial(event).catch(handleError));
  document.getElementById(\"branding-colors-form\")?.addEventListener(\"submit\", (event) => updateBrandingColors(event).catch(handleError));
  document.getElementById(\"branding-logo-form\")?.addEventListener(\"submit\", (event) => updateBrandingLogoLink(event).catch(handleError));
  document.getElementById(\"logo-upload\")?.addEventListener(\"change\", (event) => uploadLogo(event).catch(handleError));

  const editorEl = document.getElementById(\"email-editor\");
  if (editorEl && typeof Quill !== \"undefined\") {
    const quill = new Quill(\"#email-editor\", {
      theme: \"snow\",
      modules: {
        toolbar: [
          [{ header: [1, 2, 3, false] }],
          [\"bold\", \"italic\", \"underline\", \"strike\"],
          [{ color: [] }, { background: [] }],
          [{ list: \"ordered\" }, { list: \"bullet\" }],
          [\"clean\"],
        ],
      },
    });

    const hiddenInput = document.getElementById(\"tpl-access-code-body\");
    if (hiddenInput && hiddenInput.value) {
      quill.root.innerHTML = hiddenInput.value;
    }

    const updatePreview = () => {
      const content = quill.root.innerHTML;
      if (hiddenInput) hiddenInput.value = content;
      
      const frame = document.getElementById(\"email-preview-frame\");
      if (frame) {
        const logoImg = state.brandingSettings?.logo_url 
          ? `<img src=\"${state.brandingSettings.logo_url}\" alt=\"Logo\" style=\"max-width: 200px; height: auto; display: block; margin: 0 auto;\">`
          : \"\";
        
        const logoHtml = state.brandingSettings?.logo_link_url
          ? `<a href=\"${state.brandingSettings.logo_link_url}\" style=\"text-decoration:none;\">${logoImg}</a>`
          : logoImg;
        
        const socials = [
          [\"instagram\", state.brandingSettings?.instagram_url],
          [\"facebook\", state.brandingSettings?.facebook_url],
          [\"tiktok\", state.brandingSettings?.tiktok_url],
          [\"youtube\", state.brandingSettings?.youtube_url],
        ].filter(s => s[1]);

        let socialHtml = \"\";
        if (socials.length > 0) {
          const baseUrl = window.location.origin;
          const items = socials.map(([name, url]) => 
            `<a href=\"${url}\" style=\"margin: 0 10px; text-decoration: none;\"><img src=\"${baseUrl}/assets/icon-${name}.png\" alt=\"${name}\" width=\"24\" height=\"24\"></a>`
          ).join(\"\");
          socialHtml = `<div style=\"text-align:center; padding: 20px; border-top: 1px solid #eee; margin-top: 20px;\">${items}</div>`;
        }

        const footerTextHtml = state.brandingSettings?.footer_text
          ? `<div style=\"padding: 20px; text-align: center; font-size: 13px; color: #64748b; border-top: 1px solid #eee;\">${state.brandingSettings.footer_text.replace(/\\n/g, \"<br>\")}</div>`
          : \"\";

        const fullHtml = `
          <!DOCTYPE html>
          <html>
            <head>
              <style>
                body { font-family: sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: ${state.brandingSettings?.body_bg_color || \"#f9f9f9\"}; }
                .wrapper { width: 100%; table-layout: fixed; background-color: ${state.brandingSettings?.body_bg_color || \"#f9f9f9\"}; padding-bottom: 40px; padding-top: 40px; }
                .main { width: 100%; max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
                .header { background-color: ${state.brandingSettings?.header_bg_color || \"#ffffff\"}; border-bottom: 1px solid #eee; }
                .content { padding: 30px; }
                .footer-container { background-color: ${state.brandingSettings?.footer_bg_color || \"#ffffff\"}; }
                .legal { padding: 20px; text-align: center; font-size: 12px; color: #94a3b8; }
                .btn { display:inline-block; padding: 12px 24px; background-color: ${state.brandingSettings?.accent_color || \"#2563eb\"}; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; }
              </style>
            </head>
            <body>
              <div class=\"wrapper\">
                <div class=\"main\">
                  <div class=\"header\">
                    <div style=\"text-align:center; padding: 20px;\">${logoHtml}</div>
                  </div>
                  <div class=\"content\">
                    ${content.replace(/{member_name}/g, \"Max Mustermann\")
                             .replace(/{code}/g, \"123456\")
                             .replace(/{valid_from}/g, \"01.01.2026, 10:00 Uhr\")
                             .replace(/{valid_until}/g, \"01.01.2026, 12:00 Uhr\")
                             .replace(/{check_in_url}/g, \"#\")}
                  </div>
                  <div class=\"footer-container\">
                    ${socialHtml}
                    ${footerTextHtml}
                  </div>
                </div>
                <div class=\"legal\">
                  &copy; ${new Date().getFullYear()} Studio Access Management
                </div>
              </div>
            </body>
          </html>
        `;
        frame.srcdoc = fullHtml;
      }
    };

    quill.on(\"text-change\", updatePreview);
    document.getElementById(\"email-template-preview-btn\")?.addEventListener(\"click\", updatePreview);
    // Initial preview
    setTimeout(updatePreview, 100);
  }

  document.getElementById(\"email-template-form\")?.addEventListener(\"submit\", (event) => updateEmailTemplate(event).catch(handleError));
}"""

new_handlers = """function attachSettingsHandlers() {
  document.getElementById(\"smtp-form\")?.addEventListener(\"submit\", (event) => updateSmtp(event).catch(handleError));
  document.getElementById(\"telegram-form\")?.addEventListener(\"submit\", (event) => updateTelegram(event).catch(handleError));
  document.getElementById(\"nuki-form\")?.addEventListener(\"submit\", (event) => updateNukiSettings(event).catch(handleError));
  document.getElementById(\"magicline-form\")?.addEventListener(\"submit\", (event) => updateMagiclineSettings(event).catch(handleError));
  
  document.getElementById(\"save-branding-core\")?.addEventListener(\"click\", (event) => updateBrandingColors(event).catch(handleError));
  document.getElementById(\"save-full-template\")?.addEventListener(\"click\", () => saveFullTemplateAction().catch(handleError));
  document.getElementById(\"send-test-email-btn\")?.addEventListener(\"click\", () => sendTestEmailAction().catch(handleError));
  document.getElementById(\"logo-upload\")?.addEventListener(\"change\", (event) => uploadLogo(event).catch(handleError));

  const editorEl = document.getElementById(\"email-editor\");
  if (editorEl && typeof Quill !== \"undefined\") {
    const quill = new Quill(\"#email-editor\", {
      theme: \"snow\",
      modules: {
        toolbar: [
          [{ header: [1, 2, false] }],
          [\"bold\", \"italic\", \"underline\"],
          [{ color: [] }, { background: [] }],
          [{ list: \"ordered\" }, { list: \"bullet\" }],
          [\"link\", \"clean\"],
        ],
      },
    });

    const hiddenInput = document.getElementById(\"tpl-access-code-body\");
    if (hiddenInput && hiddenInput.value) {
      quill.root.innerHTML = hiddenInput.value;
    }

    const updatePreview = () => {
      const content = quill.root.innerHTML;
      if (hiddenInput) hiddenInput.value = content;
      
      const frame = document.getElementById(\"email-preview-frame\");
      if (frame) {
        const logoImg = state.brandingSettings?.logo_url 
          ? `<img src=\"${state.brandingSettings.logo_url}\" alt=\"Logo\" style=\"max-width: 200px; height: auto; display: block; margin: 0 auto;\">`
          : \"\";
        
        const logoHtml = state.brandingSettings?.logo_link_url
          ? `<a href=\"${state.brandingSettings.logo_link_url}\" style=\"text-decoration:none;\">${logoImg}</a>`
          : logoImg;
        
        const socials = [
          [\"instagram\", document.getElementById(\"social-ig\")?.value || state.brandingSettings?.instagram_url],
          [\"facebook\", document.getElementById(\"social-fb\")?.value || state.brandingSettings?.facebook_url],
        ].filter(s => s[1]);

        let socialHtml = \"\";
        if (socials.length > 0) {
          const baseUrl = window.location.origin;
          const items = socials.map(([name, url]) => 
            `<a href=\"${url}\" style=\"margin: 0 10px; text-decoration: none;\"><img src=\"${baseUrl}/assets/icon-${name}.png\" alt=\"${name}\" width=\"24\" height=\"24\"></a>`
          ).join(\"\");
          socialHtml = `<div style=\"text-align:center; padding: 20px; border-top: 1px solid #eee; margin-top: 20px;\">${items}</div>`;
        }

        const footerText = document.getElementById(\"footer-text-input\")?.value || state.brandingSettings?.footer_text || \"\";
        const footerTextHtml = footerText
          ? `<div style=\"padding: 20px; text-align: center; font-size: 13px; color: #64748b; border-top: 1px solid #eee;\">${footerText.replace(/\\n/g, \"<br>\")}</div>`
          : \"\";

        const fullHtml = `
          <!DOCTYPE html>
          <html>
            <head>
              <style>
                body { font-family: sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: ${document.getElementById(\"color-body\")?.value || state.brandingSettings?.body_bg_color || \"#f9f9f9\"}; }
                .wrapper { width: 100%; table-layout: fixed; background-color: ${document.getElementById(\"color-body\")?.value || state.brandingSettings?.body_bg_color || \"#f9f9f9\"}; padding-bottom: 40px; padding-top: 40px; }
                .main { width: 100%; max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
                .header { background-color: ${document.getElementById(\"color-header\")?.value || state.brandingSettings?.header_bg_color || \"#ffffff\"}; border-bottom: 1px solid #eee; }
                .content { padding: 30px; }
                .footer-container { background-color: ${document.getElementById(\"color-footer\")?.value || state.brandingSettings?.footer_bg_color || \"#ffffff\"}; }
                .legal { padding: 20px; text-align: center; font-size: 12px; color: #94a3b8; }
                .btn { display:inline-block; padding: 12px 24px; background-color: ${document.getElementById(\"color-accent\")?.value || state.brandingSettings?.accent_color || \"#2563eb\"}; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; }
              </style>
            </head>
            <body>
              <div class=\"wrapper\">
                <div class=\"main\">
                  <div class=\"header\">
                    <div style=\"text-align:center; padding: 20px;\">${logoHtml}</div>
                  </div>
                  <div class=\"content\">
                    ${content.replace(/{member_name}/g, \"Max Mustermann\")
                             .replace(/{code}/g, \"123456\")
                             .replace(/{valid_from}/g, \"01.01.2026, 10:00 Uhr\")
                             .replace(/{valid_until}/g, \"01.01.2026, 12:00 Uhr\")
                             .replace(/{check_in_url}/g, '<a href=\"#\" class=\"btn\">Check-In & Hausordnung</a>')}
                  </div>
                  <div class=\"footer-container\">
                    ${socialHtml}
                    ${footerTextHtml}
                  </div>
                </div>
                <div class=\"legal\">&copy; ${new Date().getFullYear()} Studio Access Management</div>
              </div>
            </body>
          </html>
        `;
        frame.srcdoc = fullHtml;
      }
    };

    quill.on(\"text-change\", updatePreview);
    
    // Placeholder insertion
    document.querySelectorAll(\".placeholder-chip\").forEach(chip => {
      chip.addEventListener(\"click\", () => {
        const range = quill.getSelection();
        if (range) {
          quill.insertText(range.index, chip.dataset.insert);
        } else {
          quill.insertText(quill.getLength() - 1, chip.dataset.insert);
        }
      });
    });

    // Preview Toggles
    document.querySelectorAll(\".preview-toggle\").forEach(btn => {
      btn.addEventListener(\"click\", () => {
        document.querySelectorAll(\".preview-toggle\").forEach(b => b.classList.remove(\"active\"));
        btn.classList.add(\"active\");
        const previewWindow = document.getElementById(\"preview-window\");
        if (btn.dataset.mode === \"mobile\") previewWindow.classList.add(\"mobile\");
        else previewWindow.classList.remove(\"mobile\");
      });
    });

    // Initial preview
    setTimeout(updatePreview, 100);
  }
}"""

if old_handlers in content:
    content = content.replace(old_handlers, new_handlers)
    with open("app_final.js", "w") as f:
        f.write(content)
    print("SUCCESS: Function replaced.")
else:
    print("ERROR: Function not found. Possible whitespace mismatch.")
    # Fallback: simple search and replace if possible, but let's see.
    # I will write the final file completely now because I have it all.
